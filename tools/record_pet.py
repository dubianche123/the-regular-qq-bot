#!/usr/bin/env python3
"""Record only this QA pet's native Cocoa windows, at real playback speed."""
from __future__ import annotations

import argparse
import ctypes
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
    parser.add_argument('--scenes', default='feed,left,right,top,bottom')
    parser.add_argument('--seconds', type=float, default=16.0,
                        help='real-time duration per scene, for example 8, 10 or 16 seconds')
    parser.add_argument('--edge-micro', action='store_true',
                        help='trigger an awake edge micro-animation at 4s instead of edge sleep')
    args = parser.parse_args()
    scenes = [scene.strip() for scene in args.scenes.split(',')]
    if not math.isfinite(args.seconds) or args.seconds <= 1:
        parser.error('--seconds must be finite and greater than 1')
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

    adopted = available()
    daily = {name for name in adopted if name.startswith('daily_')}
    air_scenes = {'air-fire', 'air-catch', 'air-miss'}
    unknown = set(scenes) - {'feed', 'left', 'right', 'top', 'bottom', 'natural', 'next'} - daily - air_scenes
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
        pet = PetWidget(BlockedClient(), quit_on_close=False, rng=random.Random(7), clock=clock,
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
        if scene in air_scenes:
            brain.place(center.x() - 150)
            pet._sync_windows(force=True)
            crop = (center.x() - 400, max(area.top(), area.bottom() - 620), 800, 640)
        air_target = (brain.x + brain.figure * 0.9, brain.floor - brain.figure * 2.1)

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

        def start_air_fire(now):
            # Force only the rare branch, preserving its real authored speed,
            # movement, preparation and landing.
            with patch.object(brain.rng, 'random', return_value=0.0):
                steps = brain._flight_steps(now)
            brain._queue(*steps, Step('idle', 60), now=now, interrupt=True)
            programmes.extend({'clip': step.clip, 'duration': step.duration}
                              for step in (brain.step, *brain.plan) if step is not None)

        def once(key, elapsed, at, action):
            if elapsed >= at and key not in events:
                events.add(key)
                action()

        def capture(now, elapsed):
            canvas = Image.new('RGBA', crop[2:], '#e2e7eb')
            portals = list(pet.portal_layer.windows.values())
            own_windows = [pair[0] for pair in portals] + [pet, pet.fx]
            own_windows += list(pet.props.windows.values()) + [pair[1] for pair in portals] + [pet.bubble]
            for widget in own_windows:
                if widget.isVisible():
                    x, y = native_position(widget)
                    canvas.alpha_composite(grab(widget), (x - crop[0], y - crop[1]))
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
                            'brain': [brain.x, brain.y], 'qt_window': [pet.x(), pet.y()],
                            'native_window': [wx, wy], 'window_anchor': [anchor.x(), anchor.y()],
                            'actual_anchor': [wx + anchor.x(), wy + anchor.y()],
                            'anchor_error': [wx + anchor.x() - brain.x, wy + anchor.y() - brain.y],
                            'pointer': [pointer[0].x(), pointer[0].y()],
                            'air_play': brain.air_play_active,
                            'air_caught': (brain._air_chase or {}).get('caught'),
                            'input_passthrough': bool(native_window(pet).ignoresMouseEvents()),
                            'bubble_passthrough': (bool(native_window(pet.bubble).ignoresMouseEvents())
                                                   if pet.bubble.isVisible() else None),
                            'scale': [transform['sx'], transform['sy']], 'lift': pet.lift})

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
                elif scene in ('natural', 'next'):
                    once('scene', elapsed, 1.0, lambda: start_routine(now))
                elif scene == 'air-fire':
                    once('scene', elapsed, 1.0, lambda: start_air_fire(now))
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
                # Keep the comparison ending idle; do not append an unrelated random programme.
                end_after = (12.0 if scene in air_scenes else 2.2 if scene == 'feed'
                             else 1.0 if scene in daily else 9.0)
                if (not finished_feed and elapsed > end_after
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
        with patch.object(QCursor, 'pos', side_effect=lambda: pointer[0]):
            timer.start()
            app.exec()
        pet.close()
        app.processEvents()
        if errors:
            raise errors[0]
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
            'screen_available': list(area.getRect()), 'crop': crop,
            'captured_windows': 'only QA-owned Qt widgets; no desktop or other app capture',
            'blocked_requests': submitted, 'events': sorted(events), 'programme': programmes,
            'frames': records}, ensure_ascii=False, indent=2))
        print(json.dumps({'scene': scene, 'frames': len(frames), 'seconds': records[-1]['t'],
                          'output': str(output)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
