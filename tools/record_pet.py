#!/usr/bin/env python3
"""Record only this QA pet's native Cocoa windows, at real playback speed."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import random
import sys
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scenes', default='feed,left,right,top,bottom',
                        help='comma-separated scenes; includes magic-dragon, magic-prop, magic_daily_*, '
                             'flight-interrupt, inspect-dragon_snack_berry and inspect-life_comic_turn')
    parser.add_argument('--seed', type=int, default=7, help='reproducible optional action variants')
    parser.add_argument('--seconds', type=float, default=16.0,
                        help='real-time duration per scene, for example 8, 10 or 16 seconds')
    parser.add_argument('--edge-micro', action='store_true',
                        help='trigger an awake edge micro-animation at 4s instead of edge sleep')
    parser.add_argument('--activity-seconds', type=float, default=8.0,
                        help='QA main-loop duration for work/music endings; authored FPS and tails stay unchanged')
    args = parser.parse_args()
    scenes = [scene.strip() for scene in args.scenes.split(',')]
    if not math.isfinite(args.seconds) or args.seconds <= 1:
        parser.error('--seconds must be finite and greater than 1')
    if not math.isfinite(args.activity_seconds) or args.activity_seconds <= 0:
        parser.error('--activity-seconds must be finite and positive')
    sys.path.insert(0, str(args.source.resolve()))
    os.environ['QT_QPA_PLATFORM'] = 'cocoa'
    from PIL import Image, ImageDraw
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtGui import QCursor, QImage
    from PySide6.QtWidgets import QApplication
    import AppKit
    import objc
    from regular.desktop_pet import PetWidget, SpriteBank
    from regular.pet_brain import Step
    from regular.pet_repertoire import available
    from regular.pet_routines import ACTIVITY_ENDINGS, DAILY_MAGIC

    adopted = available()
    daily = {name for name in adopted if name.startswith('daily_')}
    air_scenes = {'air-fire', 'air-flight', 'air-catch', 'air-miss'}
    throw_scenes = {'throw', 'drop'}
    magic_scenes = {'magic-dragon': None, 'magic-prop': 'prop',
                    **{name: name for name in DAILY_MAGIC if name in adopted}}
    inspect_scenes = {f'inspect-{name}': name for name in ('dragon_snack_berry', 'life_comic_turn')
                      if name in adopted}
    focused_scenes = set(magic_scenes) | set(inspect_scenes) | {'flight-interrupt'}
    activity_scenes = {result: (source, result)
                       for source, endings in reversed(tuple(ACTIVITY_ENDINGS.items()))
                       for result in endings[:-1]}
    activity_scenes.update({
        'work-think-clear': ('life_laptop_think', 'ending_work_clear'),
        'work-quiet': ('life_laptop_type', None),
        'ukulele-quiet': ('dragon_music_ukulele', None),
        'kalimba-quiet': ('dragon_music_kalimba', None),
        'work-next': ('life_laptop_type', 'ending_work_clear'),
        'ukulele-next-result': ('dragon_music_ukulele', 'ending_ukulele_resolve'),
    })
    activity_scenes = {name: (source, result) for name, (source, result) in activity_scenes.items()
                       if source in adopted and (result is None or result in adopted)
                       and ACTIVITY_ENDINGS[source][-1] in adopted}
    unknown = (set(scenes) - {'feed', 'left', 'right', 'top', 'bottom', 'natural', 'next'}
               - daily - air_scenes - throw_scenes - activity_scenes.keys() - focused_scenes)
    if unknown:
        parser.error('unknown or unadopted scene: ' + ', '.join(sorted(unknown)))

    class BlockedClient:
        def __getattr__(self, name):
            raise AssertionError('QA client must never access a service: ' + name)

    app = QApplication([])
    if app.platformName() != 'cocoa':
        raise RuntimeError('native Cocoa is required')
    area = app.primaryScreen().availableGeometry()
    primary_height = float(AppKit.NSScreen.screens()[0].frame().size.height)
    args.out.mkdir(parents=True, exist_ok=True)

    def native_window(widget):
        view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
        return view.window()

    def native_position(widget):
        frame = native_window(widget).frame()
        return (round(frame.origin.x), round(primary_height - frame.origin.y - frame.size.height))

    def grab(widget):
        qimage = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        image = Image.frombytes('RGBA', (qimage.width(), qimage.height()), bytes(qimage.bits()),
                               'raw', 'RGBA', qimage.bytesPerLine())
        return image.resize((widget.width(), widget.height()), Image.Resampling.LANCZOS)

    for scene in scenes:
        output = args.out / scene
        output.mkdir(parents=True, exist_ok=True)
        clock = time.monotonic
        pet = PetWidget(BlockedClient(), quit_on_close=False, rng=random.Random(args.seed), clock=clock,
                        sprites=SpriteBank(args.source / 'regular/assets/pet'))
        for timer in (pet.animation, pet.poll, pet.presence_poll):
            timer.stop()
        submitted = []
        pet._submit = lambda key, operation: submitted.append(key)  # never execute any client operation
        pet.csrf = 'offline-qa-only'
        brain = pet.brain
        center = area.center()
        brain.place(center.x())
        brain._queue(Step('idle', 60), now=clock(), interrupt=True)
        brain.energy = 0.9
        brain.presence.circadian = '白天精神不错'
        pet.show()
        pet._sync_windows(force=True)
        if scene in ('left', 'right'):
            cx = area.left() + 150 if scene == 'left' else area.right() - 150
            cy = center.y() + 100
        elif scene == 'top':
            cx, cy = center.x(), area.top() + 180
        else:
            cx, cy = center.x(), area.bottom() - 140
        crop = (round(cx - 300), round(cy - 260), 600, 440)
        pointer = [QPoint(center.x(), center.y())]
        frames, records, events, errors, programmes = [], [], set(), [], []
        scene_metadata, kick_calls = {}, []
        started, next_capture = clock(), 0.0
        finished_feed = False
        drag_target = {'left': (area.left() - 40, center.y() - 80),
                       'right': (area.right() + 40, center.y() - 80),
                       'top': (center.x(), area.top() - 200),
                       'bottom': (center.x(), area.bottom() + 80)}.get(scene)
        timer = QTimer()
        timer.setInterval(16)
        if scene in ('left', 'right', 'top'):
            crop = (round(cx - 300), area.top() - 50, 600, area.height() + 100)
        if scene in ('left', 'right'):
            brain.place(cx)
            pet._sync_windows(force=True)
        if scene in air_scenes or scene == 'flight-interrupt':
            brain.place(center.x() - 150)
            pet._sync_windows(force=True)
            crop = (area.left() - 30, max(area.top(), area.bottom() - 620), area.width() + 60, 640)
        if scene == 'magic-dragon':
            crop = (center.x() - 400, max(area.top(), area.bottom() - 620), 800, 640)
        air_target = (brain.x + brain.figure * 0.9, brain.floor - brain.figure * 2.1)
        if scene in throw_scenes:
            brain.place(center.x() - 150)
            pet._sync_windows(force=True)
            crop = (area.left() - 30, area.top() - 30, area.width() + 60, area.height() + 60)
        throw_origin = (brain.x, brain.floor - 100)
        if scene == 'magic-prop':
            brain.place(brain.left + brain.figure * 0.9)
            pet._sync_windows(force=True)
            crop = (area.left() - 30, max(area.top(), area.bottom() - 620), area.width() + 60, 640)

        def record_programme():
            return [asdict(step) for step in (brain.step, *brain.plan) if step is not None]

        def prop_state(item):
            return {key: getattr(item, key) for key in
                    ('id', 'kind', 'x', 'y', 'radius', 'vx', 'vy', 'grounded', 'dragging', 'born', 'ttl')}

        real_kick = brain.props.kick

        def observe_kick(prop_id, now, direction):
            before = next((prop_state(p) for p in brain.props.items if p.id == prop_id), None)
            result = real_kick(prop_id, now, direction)
            kick_calls.append({'t': now - started, 'prop_id': prop_id, 'direction': direction,
                               'result': result, 'before': before,
                               'after': next((prop_state(p) for p in brain.props.items if p.id == prop_id), None)})
            return result

        def start_magic(now):
            kind = magic_scenes[scene]
            if scene == 'magic-prop':
                # A real persistent toy, settled on the floor well beyond walking range.
                prop_id = brain.props.emit('ball', brain.right - brain.figure * 0.9,
                                           brain.floor, now, brain.scale)
                item = next(p for p in brain.props.items if p.id == prop_id)
                item.y, item.vx, item.vy, item.grounded = brain.floor - item.radius, 0.0, 0.0, True
                item.ttl = max(item.ttl, args.seconds + 10)
                scene_metadata['initial_prop'] = prop_state(item)
                if item not in brain._far_prop_candidates(now):
                    raise AssertionError('screen is too narrow for a genuine far-prop magic trip')
            scene_metadata.update({'entrypoint': 'brain.magic', 'requested_kind': kind,
                                   'dragon_shown_before': brain._magic_dragon_shown})
            accepted = brain.magic(now, kind=kind)
            scene_metadata.update({'accepted': accepted, 'initial_programme': record_programme(),
                                   'magic_used': sorted(brain._magic_used)})
            programmes.extend(record_programme())
            if not accepted:
                raise AssertionError('real magic entry rejected the requested scene: ' + scene)
            if scene == 'magic-dragon' and (scene_metadata['dragon_shown_before']
                                            or 'dragon' not in brain._magic_used):
                raise AssertionError('first default magic did not choose dragon form')

        def start_inspect(now):
            clip = inspect_scenes[scene]
            duration = 6.0 if adopted[clip]['loop'] else None
            brain._queue(Step(clip, duration), Step('idle', 60), now=now, interrupt=True)
            programmes.extend(record_programme())
            scene_metadata.update({'entrypoint': 'brain._queue', 'inspected_clip': clip,
                                   'main_seconds': duration, 'initial_programme': record_programme()})

        def interrupt_flight(now):
            # Expose the real toolbar for one frame. PetWidget.step reads it through
            # _attention and invokes set_attention; the normal air toolbar rule then
            # hides it. Do not replace the return clip or force the mode ourselves.
            scene_metadata.update({'interrupt_at': now - started, 'before_interrupt': {
                'clip': brain.clip, 'mode': brain.mode, 'x': brain.x, 'y': brain.y,
                'altitude': brain.floor - brain.y}, 'entrypoint': 'PetWidget toolbar attention'})
            pet._place_toolbar()
            pet.toolbar.show()

        def start_daily(now):
            duration = max(1.0, args.seconds - 2.5) if adopted[scene]['loop'] else None
            steps = [Step(scene, duration)]
            if scene == 'daily_book_hug_sleep' and 'daily_book_sleep_hold' in adopted:
                steps.append(Step('daily_book_sleep_hold', max(1.0, args.seconds - 6.0)))
            brain._queue(*steps, Step('idle', 60), now=now, interrupt=True)

        def start_routine(now):
            # Compare the real button signal against the natural routine entry
            # with the same history and RNG, without changing authored timings.
            brain.rng.seed(7)
            brain._last_story_at, brain._rests_since_story = now, 0
            brain._ambient_families.clear()
            brain.repertoire.bags.clear()
            brain.repertoire.recent.clear()
            if scene == 'next':
                pet.toolbar.play.click()
            else:
                brain._queue(*brain._routine_steps(now), now=now, interrupt=True)
            programmes.extend({'clip': step.clip, 'duration': step.duration}
                              for step in (brain.step, *brain.plan) if step is not None)

        def start_flight(now):
            # Force only the rare branch, preserving its real authored speed,
            # movement, preparation and landing.
            with patch.object(brain.rng, 'random', return_value=0.0 if scene == 'air-fire' else 1.0):
                steps = brain._flight_steps(now)
            brain._queue(*steps, Step('idle', 60), now=now, interrupt=True)
            programmes.extend({'clip': step.clip, 'duration': step.duration}
                              for step in (brain.step, *brain.plan) if step is not None)

        def start_activity(now):
            source, result = activity_scenes[scene]
            secondary = result == ACTIVITY_ENDINGS[source][1]
            brain.energy = 0.4 if secondary else 0.9
            if result == 'ending_kalimba_drowsy':
                brain.presence.circadian = '午后有点困'
            brain.repertoire.recent.clear()
            # Force only which branch is inspected. The real selector, queue,
            # transitions, four-frame cadence and cleanup run unchanged.
            draws = [1.0] if result is None else [0.0, 0.999 if secondary else 0.0]
            with patch.object(brain.rng, 'random', side_effect=draws):
                steps = brain._activity_steps(source, args.activity_seconds)
            expected = [source, *([result] if result else []), ACTIVITY_ENDINGS[source][-1]]
            if [step.clip for step in steps] != expected:
                raise AssertionError('activity branch did not match the requested QA scene')
            brain._queue(*steps, Step('idle', 60), now=now, interrupt=True)
            programmes.extend({'clip': step.clip, 'duration': step.duration}
                              for step in (brain.step, *brain.plan) if step is not None)

        def change_activity():
            # Use the actual toolbar signal. A neutral destination makes a prop
            # reappearing or a restarted cleanup unambiguous in this recording.
            with patch.object(brain, '_routine_steps', return_value=[Step('idle', 60)]):
                pet.toolbar.play.click()

        def once(key, elapsed, at, action):
            if elapsed >= at and key not in events:
                events.add(key)
                action()

        def capture(now, elapsed):
            canvas = Image.new('RGBA', crop[2:], '#e2e7eb')
            body_bounds = None
            portals = list(pet.portal_layer.windows.values())
            own_windows = [pair[0] for pair in portals] + [pet, pet.fx]
            own_windows += list(pet.props.windows.values()) + [pair[1] for pair in portals] + [pet.bubble]
            for widget in own_windows:
                if widget.isVisible():
                    x, y = native_position(widget)
                    captured = grab(widget)
                    canvas.alpha_composite(captured, (x - crop[0], y - crop[1]))
                    if widget is pet:
                        bounds = captured.getchannel('A').getbbox()
                        if bounds:
                            body_bounds = [bounds[0] + x, bounds[1] + y, bounds[2] + x, bounds[3] + y]
            draw = ImageDraw.Draw(canvas)
            rect = (area.left() - crop[0], area.top() - crop[1],
                    area.right() + 1 - crop[0], area.bottom() + 1 - crop[1])
            draw.rectangle(rect, outline='#d85b65', width=1)
            if scene.startswith('air-'):
                px, py = pointer[0].x() - crop[0], pointer[0].y() - crop[1]
                draw.polygon([(px, py), (px + 3, py + 18), (px + 8, py + 12),
                              (px + 15, py + 12)], fill='white', outline='#173b32')
            draw.text((8, 8), f'{scene}  {elapsed:05.2f}s  {brain.mode}  {brain.clip}', fill='#173b32')
            frames.append(canvas.convert('RGB'))
            wx, wy = native_position(pet)
            anchor = pet.window_anchor()
            strip, index, mirrored, transform = brain.pose(now)
            records.append({'t': round(elapsed, 4), 'mode': brain.mode, 'clip': brain.clip,
                            'strip': strip, 'frame': index, 'mirrored': mirrored,
                            'clip_elapsed': round(now - brain.clip_started, 4),
                            'step_elapsed': round(now - brain.step_started, 4),
                            'brain': [brain.x, brain.y], 'qt_window': [pet.x(), pet.y()],
                            'visible_body_bounds': body_bounds,
                            'native_window': [wx, wy], 'window_anchor': [anchor.x(), anchor.y()],
                            'actual_anchor': [wx + anchor.x(), wy + anchor.y()],
                            'anchor_error': [wx + anchor.x() - brain.x, wy + anchor.y() - brain.y],
                            'pointer': [pointer[0].x(), pointer[0].y()],
                            'air_play': brain.air_play_active,
                            'air_caught': (brain._air_chase or {}).get('caught'),
                            'edge_phase': (brain._edge_exit or {}).get('phase'),
                            'warp_phase': (brain.warp or {}).get('phase'),
                            'warp_cause': (brain.warp or {}).get('cause'),
                            'props': [prop_state(item) for item in brain.props.items],
                            'chasing_prop': brain._chasing_prop,
                            'attention': brain.attention,
                            'step_hold': brain.step.hold if brain.step else None,
                            'step_target': ([brain.step.target_x, brain.step.target_y] if brain.step else None),
                            'clip_family': adopted.get(brain.clip, {}).get('family'),
                            'portals': [{'id': p.id, 'x': p.x, 'y': p.y, 'open': p.openness(now)}
                                        for p in brain.portals],
                            'input_passthrough': bool(native_window(pet).ignoresMouseEvents()),
                            'bubble_passthrough': (bool(native_window(pet.bubble).ignoresMouseEvents())
                                                   if pet.bubble.isVisible() else None),
                            'scale': [transform['sx'], transform['sy']], 'lift': pet.lift})
            mask = pet.mask()
            records[-1].update({'rotation': transform['rot'], 'offset_y': transform['dy'],
                               'configured_scale': brain.scale,
                               'mask_unrestricted': mask.isEmpty(),
                               'mask_bounds': list(mask.boundingRect().getRect())})

        def tick():
            nonlocal next_capture, finished_feed
            try:
                now = clock()
                elapsed = now - started
                if scene == 'feed':
                    once('feed', elapsed, 1.0, pet.feed)
                    once('receipt', elapsed, 2.2, lambda: pet._finished('care', {
                        'ok': True, 'value': {'care': {'warmth': 0.26, 'updated_at': now},
                        'care_event': {'kind': 'care', 'id': 'offline-cookie-1', 'action': 'feed',
                                       'result': 'gain', 'text': '吃饱了，心情也好了。'}}}))
                elif scene in daily:
                    once('scene', elapsed, 1.0, lambda: start_daily(now))
                elif scene in magic_scenes:
                    once('scene', elapsed, 1.0, lambda: start_magic(now))
                elif scene in inspect_scenes:
                    once('scene', elapsed, 1.0, lambda: start_inspect(now))
                elif scene in ('natural', 'next'):
                    once('scene', elapsed, 1.0, lambda: start_routine(now))
                elif scene in activity_scenes:
                    once('scene', elapsed, 1.0, lambda: start_activity(now))
                    source, result = activity_scenes[scene]
                    if scene == 'work-next':
                        once('next-main', elapsed, 4.0, change_activity)
                    elif scene == 'ukulele-next-result' and brain.clip == result:
                        once('next-result', now - brain.clip_started, 0.8, change_activity)
                    if scene in ('work-next', 'ukulele-next-result') and brain.clip == ACTIVITY_ENDINGS[source][-1]:
                        for key, at in (('next-cleanup', 0.8), ('next-cleanup-again', 1.3)):
                            once(key, now - brain.clip_started, at, change_activity)
                elif scene in ('air-fire', 'air-flight'):
                    once('scene', elapsed, 1.0, lambda: start_flight(now))
                elif scene == 'flight-interrupt':
                    once('scene', elapsed, 1.0, lambda: start_flight(now))
                    if (brain.mode == 'fly' and brain.floor - brain.y >= brain.figure * 0.7
                            and now - brain.step_started >= 0.6):
                        once('interrupt', elapsed, 0.0, lambda: interrupt_flight(now))
                elif scene in throw_scenes:
                    once('press', elapsed, 1.0, lambda: brain.press(now, *throw_origin))
                    if 1.04 <= elapsed < 2.8:
                        lift = min(1.0, (elapsed - 1.04) / 0.96)
                        flick = max(0.0, (elapsed - 2.5) / 0.3) if scene == 'throw' else 0.0
                        pointer[0] = QPoint(round(throw_origin[0] + 240 * flick),
                                            round(throw_origin[1] - 350 * lift - 200 * flick))
                        brain.drag(now, pointer[0].x(), pointer[0].y())
                    once('release', elapsed, 2.8,
                         lambda: brain.release(now, pointer[0].x(), pointer[0].y()))
                elif scene in ('air-catch', 'air-miss'):
                    if scene == 'air-miss':
                        once('existing-bubble', elapsed, 0.5,
                             lambda: brain.say('陪你待一会儿。', now=now, seconds=15, kind='say'))
                    if 1.0 <= elapsed < 2.4:
                        offset = 14 if int((elapsed - 1.0) * 8) % 2 else -14
                        pointer[0] = QPoint(round(air_target[0] + offset), round(air_target[1]))
                    elif elapsed >= 2.4:
                        if scene == 'air-miss' and brain.clip == 'air_play_reach':
                            events.add('pointer-escaped')
                        offset = brain.figure * 2 if 'pointer-escaped' in events else 0
                        pointer[0] = QPoint(round(air_target[0] + offset), round(air_target[1]))
                else:
                    once('press', elapsed, 1.0, lambda: brain.press(now, brain.x, brain.y - 100))
                    once('lift', elapsed, 1.1, lambda: brain.drag(now, brain.x, center.y()))
                    if elapsed >= 1.2 and 'release' not in events:
                        pointer[0] = QPoint(*drag_target)
                        brain.drag(now, *drag_target)
                    once('release', elapsed, 1.45, lambda: brain.release(now, *drag_target))
                    if 'release' in events:
                        pointer[0] = QPoint(center.x(), center.y())
                    edge_event = 'micro' if args.edge_micro else 'sleep'
                    if elapsed >= 4.0 and brain.mode == 'hidden' and edge_event not in events:
                        events.add(edge_event)
                        if args.edge_micro:
                            brain._hidden_sleep_at = now + args.seconds + 10
                            brain._hidden_micro_at = now
                        else:
                            brain._hidden_sleep_at = now
                    once('wake', elapsed, 9.0, lambda: brain.poke(now))
                pet.step(now)
                if (scene == 'flight-interrupt' and 'interrupt' in events
                        and 'after_interrupt' not in scene_metadata):
                    scene_metadata['after_interrupt'] = {
                        'clip': brain.clip, 'mode': brain.mode, 'attention': brain.attention,
                        'programme': record_programme()}
                # Keep the comparison ending idle; do not append an unrelated random programme.
                end_after = (12.0 if scene in air_scenes else 3.0 if scene in throw_scenes else 2.2 if scene == 'feed'
                             else 1.0 if scene in daily or scene in activity_scenes else 9.0)
                if (scene not in focused_scenes and not finished_feed and elapsed > end_after
                        and brain.mode == 'ground' and brain.clip == 'idle'):
                    finished_feed = True
                    brain._queue(Step('idle', 60), now=now, interrupt=True)
                if elapsed >= next_capture:
                    capture(now, elapsed)
                    next_capture += 1 / 12
                if elapsed >= args.seconds:
                    timer.stop()
                    app.quit()
            except Exception as error:
                errors.append(error)
                timer.stop()
                app.quit()

        timer.timeout.connect(tick)
        with ExitStack() as stack:
            stack.enter_context(patch.object(QCursor, 'pos', side_effect=lambda: pointer[0]))
            if scene in focused_scenes:
                # Only suppress the unrelated programme after the authored tail.
                stack.enter_context(patch.object(brain, '_choose', return_value=[Step('idle', 60)]))
            if scene == 'magic-prop':
                stack.enter_context(patch.object(brain.props, 'kick', side_effect=observe_kick))
            timer.start()
            app.exec()
        pet.close()
        app.processEvents()
        if errors:
            raise errors[0]
        checks = {}
        if scene in focused_scenes:
            checks['returned_to_ground_idle'] = records[-1]['mode'] == 'ground' and records[-1]['clip'] == 'idle'
            if scene in magic_scenes:
                checks['real_magic_accepted'] = scene_metadata.get('accepted') is True
                if scene == 'magic-dragon':
                    checks['default_first_dragon'] = (scene_metadata.get('dragon_shown_before') is False
                                                       and 'dragon' in scene_metadata.get('magic_used', ()))
                    checks['dragon_and_recovery_shown'] = (
                        any(r['clip'] == 'form_dragon' for r in records)
                        and any(r['clip'] in ('form_recover', 'form_tidy') for r in records))
                elif scene == 'magic-prop':
                    initial = scene_metadata['initial_prop']
                    checks['prop_purpose_warp_shown'] = any(r['warp_cause'] == 'prop' for r in records)
                    checks['real_ball_kick_succeeded'] = any(c['result'] and c['prop_id'] == initial['id']
                                                            for c in kick_calls)
                    checks['same_ball_moved'] = any(abs(p['x'] - initial['x']) > 5 or abs(p['y'] - initial['y']) > 5
                                                    for r in records for p in r['props'] if p['id'] == initial['id'])
                    scene_metadata['kick_calls'] = kick_calls
                else:
                    checks['requested_daily_shown'] = any(r['clip'] == scene for r in records)
                    checks['all_four_frames_shown'] = {r['frame'] for r in records if r['clip'] == scene} == {0, 1, 2, 3}
            elif scene == 'flight-interrupt':
                interrupted = scene_metadata.get('interrupt_at', args.seconds + 1)
                returning = [r for r in records if r['t'] >= interrupted and r['mode'] == 'fly']
                checks['real_attention_interrupted_airborne'] = (
                    scene_metadata.get('before_interrupt', {}).get('mode') == 'fly'
                    and scene_metadata.get('after_interrupt', {}).get('attention') is True)
                checks['return_uses_flap_then_approach'] = (bool(returning)
                    and all(r['clip_family'] in ('flap', 'approach') for r in returning)
                    and returning[-1]['clip'] == 'motion_fly_approach')
                checks['return_wings_animate'] = len({r['frame'] for r in returning}) >= 2
                checks['landing_shown'] = any(r['t'] >= interrupted and r['clip_family'] == 'landing' for r in records)
            else:
                clip = inspect_scenes[scene]
                checks['requested_clip_shown'] = any(r['clip'] == clip for r in records)
                checks['all_four_frames_shown'] = {r['frame'] for r in records if r['clip'] == clip} == {0, 1, 2, 3}
        if (scene in {'air-flight', 'air-fire', 'air-catch', 'air-miss', 'flight-interrupt'}
                and 'motion_fly_approach' in adopted):
            approaches = [r for r in records if r['clip'] == 'motion_fly_approach']
            settles = [r for r in records if r['clip'] == 'motion_fly_settle']
            checks['approach_before_contact'] = bool(approaches) and all(
                r['mode'] == 'fly' and r['frame'] == 0 for r in approaches)
            checks['grounded_settle_uses_only_123'] = bool(settles) and all(
                r['mode'] == 'ground' for r in settles) and {r['frame'] for r in settles} == {1, 2, 3}
            checks['approach_precedes_settle'] = bool(approaches and settles) and approaches[-1]['t'] < settles[0]['t']
        durations = [max(1, round((records[i + 1]['t'] - row['t']) * 1000))
                     for i, row in enumerate(records[:-1])] + [83]
        frames[0].save(output / 'continuous.webp', save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, quality=88, method=4)
        picks = [0]
        for i in range(1, len(records)):
            previous, row = records[i - 1], records[i]
            if (row['clip'], row['frame'], row['mode']) != (previous['clip'], previous['frame'], previous['mode']):
                picks.append(i)
        picks = sorted(set(picks + [len(frames) - 1]))
        if len(picks) > 24:
            picks = [picks[round(i * (len(picks) - 1) / 23)] for i in range(24)]
        tile_w, tile_h = 300, round(crop[3] * 300 / crop[2])
        contact = Image.new('RGB', (tile_w * 4, tile_h * ((len(picks) + 3) // 4)), '#e2e7eb')
        for k, i in enumerate(picks):
            contact.paste(frames[i].resize((tile_w, tile_h)), (k % 4 * tile_w, k // 4 * tile_h))
        contact.save(output / 'keyframes.jpg', quality=94)
        (output / 'frames.json').write_text(json.dumps({
            'source': str(args.source.resolve()), 'scene': scene, 'platform': app.platformName(),
            'requested_seconds': args.seconds, 'edge_micro': args.edge_micro,
            'seed': args.seed,
            'activity_main_seconds': args.activity_seconds if scene in activity_scenes else None,
            'screen_available': list(area.getRect()), 'crop': crop,
            'captured_windows': 'only QA-owned Qt widgets; no desktop or other app capture',
            'blocked_requests': submitted, 'events': sorted(events), 'programme': programmes,
            'scene_metadata': scene_metadata, 'verification': checks,
            'frames': records}, ensure_ascii=False, indent=2))
        print(json.dumps({'scene': scene, 'frames': len(frames), 'seconds': records[-1]['t'],
                          'output': str(output), 'verification': checks}, ensure_ascii=False), flush=True)
        if checks and not all(checks.values()):
            raise AssertionError('incomplete or failed scene; inspect frames.json: '
                                 + ', '.join(name for name, passed in checks.items() if not passed))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
