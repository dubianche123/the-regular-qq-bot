"""Powered flight has a route, moving wings and a bank derived from travel."""
import json
import random
from unittest.mock import patch

import pytest

from regular.pet_brain import CLIPS, PetBrain, Step
from regular.pet_repertoire import available, curated_actions


def brain(*, x=700, scale=1):
    b = PetBrain(rng=random.Random(7), now=0, scale=scale)
    b.set_ground(0, 1440, 860, 0)
    b.place(x)
    return b


def advance_until(b, predicate, *, limit=20, each=None):
    end = b._now + limit
    while b._now < end:
        now = round(b._now + 1 / 60, 9)
        b.tick(now)
        if each:
            each(now)
        if predicate():
            return
    raise AssertionError((b.mode, b.clip, b.step, b.x, b.y))


@pytest.mark.parametrize('x,direction', [(300, 1), (1100, -1)])
def test_route_faces_its_destination_then_climbs_cruises_and_descends_diagonally(x, direction):
    b = brain(x=x)
    b.facing = -direction
    with patch.object(b.rng, 'random', return_value=1):
        steps = b._flight_steps(0)
    assert b.facing == direction
    assert b.repertoire.actions[steps[0].clip]['family'] == 'takeoff'
    assert b.repertoire.actions[steps[-1].clip]['family'] == 'landing'
    travel = steps[1:-1]
    previous = (x, b.floor)
    for step in travel:
        assert step.target_x is not None and step.target_y is not None
        assert (step.target_x - previous[0]) * direction > b.figure * .2
        assert b.repertoire.actions[step.clip]['family'] == 'flap'
        assert CLIPS[step.clip][3]
        previous = (step.target_x, step.target_y)
    assert travel[0].target_y < b.floor
    assert abs(travel[1].target_y - travel[0].target_y) < b.figure * .15
    assert travel[-1].target_y == b.floor
    assert not any(step.duration is not None for step in steps)


def test_same_wingbeat_keeps_its_frame_clock_across_all_three_route_segments():
    b = brain()
    with patch.object(b.rng, 'random', return_value=1):
        steps = b._flight_steps(0)
    b._queue(*steps, Step('idle', 60), now=0, interrupt=True)
    phase_clocks, frames, banks = {}, set(), []
    def collect(now):
        if b.mode == 'fly':
            if b.repertoire.actions[b.clip]['family'] == 'flap':
                phase_clocks[b.step_started] = b.clip_started
                frames.add(b.pose(now)[1])
            banks.append(b.transform(now)['rot'])
            assert b.left <= b.x <= b.right
            assert b.ceiling + b.figure <= b.y <= b.floor
            assert b.transform(now)['sx'] == b.transform(now)['sy'] == 1
    advance_until(b, lambda: b.clip == 'idle', each=collect)
    assert len(phase_clocks) == 3
    assert len(set(phase_clocks.values())) == 1
    assert frames == {0, 1, 2, 3}
    assert max(banks) > 5
    assert b.mode == 'ground' and b.y == b.floor
    assert b.transform(b._now)['rot'] == 0


@pytest.mark.parametrize('clip', ['motion_fly_glide', 'life_reply_air_phone'])
def test_interrupting_static_air_art_uses_a_powered_return_then_lands(clip):
    b = brain()
    b.mode, b.y = 'fly', 500
    b._set_clip(clip, 0, restart=True)
    b.step = Step(clip, 60)
    b.set_attention(True, .1)
    assert b.repertoire.actions[b.clip]['family'] == 'flap'
    assert b.step.hold == 'air_return' and abs(b.step.target_x - b.x) > b.figure * .2
    assert b.repertoire.actions[b.plan[0].clip]['family'] == 'landing'
    wing_frames = set()
    def collect(now):
        b.set_attention(True, now)
        if b.mode == 'fly':
            if b.clip == 'motion_fly_approach':
                assert 0 < b.floor - b.y <= b.figure * .18
            else:
                assert b.repertoire.actions[b.clip]['family'] == 'flap'
                wing_frames.add(b.pose(now)[1])
    advance_until(b, lambda: b.clip == 'idle', each=collect)
    assert wing_frames == {0, 1, 2, 3}
    assert b.mode == 'ground' and b.y == b.floor


@pytest.mark.parametrize('clip', ['motion_fly_flap_slow', 'motion_fly_glide'])
@pytest.mark.parametrize('direction', [-1, 1])
def test_banking_follows_actual_horizontal_motion_for_wingbeats_and_legacy_glide(clip, direction):
    b = brain()
    b.mode, b.y = 'fly', 500
    b._queue(Step(clip, target_x=b.x + direction * 150, target_y=450),
             Step('motion_fly_flap_slow', 60), now=0)
    b._advance(0)
    assert b.transform(0)['rot'] == 0
    advance_until(b, lambda: b._now > .5)
    assert 0 < b.transform(b._now)['rot'] * direction <= 12
    advance_until(b, lambda: b.step.duration == 60)
    assert b.transform(b._now)['rot'] == 0
    advance_until(b, lambda: b._now - b.step_started > .5)
    assert b.transform(b._now)['rot'] == 0


def test_cursor_chase_banks_with_movement_and_straightens_when_pointer_is_reached():
    b = brain()
    b.mode, b.y = 'fly', 500
    b.pointer.seen = True
    b.pointer.x, b.pointer.y = 900, 420
    b._air_chase = {'origin': (b.x, b.y), 'caught': None}
    b._queue(Step('motion_fly_flap_slow', 3, hold='air_chase'), now=0)
    b._advance(0)
    b.tick(.1)
    assert b.transform(.1)['rot'] > 0
    b.pointer.x, b.pointer.y = 500, 420
    b.tick(.2)
    assert b.transform(.2)['rot'] < 0
    b.pointer.x, b.pointer.y = b._air_hands()
    b.tick(.3)
    assert b.transform(.3)['rot'] == pytest.approx(0)


def test_air_reply_hovers_but_uses_wingbeats_for_both_moving_legs():
    b = brain()
    b.presence.mood, b.presence.known = .8, True
    b.state_until = 30
    steps = b._state_steps('reply', 'reply', 0)
    assert any(s.hold == 'state' and CLIPS[s.clip][4] == 'air' for s in steps)
    assert all(b.repertoire.actions[s.clip]['family'] == 'flap' for s in steps if s.target_y is not None)
    b._queue(*steps, now=0, interrupt=True)
    advance_until(b, lambda: b.step.hold == 'state')
    position, started = (b.x, b.y), b._now
    advance_until(b, lambda: b._now >= started + .5)
    assert (b.x, b.y) == position and b.transform(b._now)['rot'] == 0
    b.state_until = b._now
    advance_until(b, lambda: b.clip == 'idle')
    assert b.mode == 'ground' and b.y == b.floor


@pytest.mark.parametrize('width,height,scale', [(160, 860, 1), (1440, 180, 1), (300, 200, 1.5)])
def test_too_little_room_keeps_the_pet_on_the_ground(width, height, scale):
    b = brain(scale=scale)
    b.set_ground(0, width, height, 0)
    b.place(width / 2)
    assert b._flight_steps(0) == [Step('idle', 2)]


def test_return_uses_current_screen_and_does_not_reuse_an_old_floor():
    b = brain()
    b.mode, b.y = 'fly', 400
    b._set_clip('motion_fly_glide', 0, restart=True)
    b._queue(Step('idle', 60), now=0, interrupt=True)
    b.set_ground(0, 900, 700, 30)
    b.scale = 1.5
    advance_until(b, lambda: b.clip == 'idle')
    assert b.mode == 'ground' and b.y == 700
    assert b._clamp_x(b.x) == b.x


def test_thrown_motion_keeps_its_inertia_gravity_and_fixed_scale():
    b = brain()
    b.mode, b.y, b.vx, b.vy = 'air', 500, 150, -180
    b._set_clip('fall', 0, restart=True)
    x, vy = b.x, b.vy
    b.tick(.05)
    assert b.x > x and b.vy > vy
    assert b.mode == 'air'
    assert b.transform(.05)['sx'] == b.transform(.05)['sy'] == 1


def test_landing_aliases_require_their_mother_strip_and_share_its_geometry(tmp_path, monkeypatch):
    import regular.pet_repertoire as repertoire
    folder = tmp_path / 'assets' / 'pet'
    folder.mkdir(parents=True)
    mother = 'motion_fly_land'
    geometry = {'bounds': [-130, -340, 130, 0]}
    manifest = {'actions': {mother: {'file': mother + '.webp', 'frames': 4}},
                'geometry': {mother: geometry}}
    (folder / 'sprites.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(repertoire, 'files', lambda package: tmp_path)
    actions = available()
    for name, sequence, posture in (('motion_fly_approach', [0], 'air'),
                                    ('motion_fly_settle', [1, 2, 3], 'stand')):
        assert actions[name]['strip'] == mother and actions[name]['geometry'] == geometry
        assert actions[name]['sequence'] == sequence and actions[name]['posture'] == posture
        assert name not in curated_actions(actions)
    (folder / 'sprites.json').write_text(json.dumps({'actions': {}}))
    assert available() == {}


@pytest.mark.parametrize('entry', ['natural', 'interrupt', 'air_play'])
def test_powered_routes_reach_before_contact_and_settle_after_without_restarting_motion(entry):
    b = brain()
    if entry == 'natural':
        with patch.object(b.rng, 'random', return_value=1):
            steps = b._flight_steps(0)
        b._queue(*steps, Step('idle', 60), now=0, interrupt=True)
    elif entry == 'interrupt':
        b.mode, b.y = 'fly', 500
        b._set_clip('motion_fly_glide', 0, restart=True)
        b.step = Step(b.clip, 60)
        b._queue(Step('idle', 60), now=0, interrupt=True)
    else:
        b.pointer.seen = True
        b.pointer.x, b.pointer.y = b.x + b.figure * .8, b.floor - b.figure * 2
        assert b._start_air_chase(0)
    advance_until(b, lambda: b.step.hold == 'air_return')
    original_step = b.step
    original_motion = (b.step_started, b._move_origin, b._move_duration,
                       b.step.target_x, b.step.target_y)
    advance_until(b, lambda: b.clip == 'motion_fly_approach')
    assert b.step is original_step and b.mode == 'fly'
    assert (b.step_started, b._move_origin, b._move_duration,
            b.step.target_x, b.step.target_y) == original_motion
    assert 0 < b.floor - b.y <= b.figure * .18
    approach_y, frames = [], []
    while b.clip != 'idle':
        strip, frame, _, transform = b.pose()
        if b.clip == 'motion_fly_approach':
            assert b.mode == 'fly' and b.y < b.floor
            assert strip == 'motion_fly_land' and frame == 0
            approach_y.append(b.y)
        elif b.clip == 'motion_fly_settle':
            assert b.mode == 'ground' and b.y == b.floor
            assert strip == 'motion_fly_land' and frame in (1, 2, 3)
            if not frames or frame != frames[-1]:
                frames.append(frame)
        else:
            raise AssertionError(b.clip)
        assert transform['sx'] == transform['sy'] == 1
        b.tick(round(b._now + 1 / 60, 9))
    assert len(approach_y) > 1 and all(a < z for a, z in zip(approach_y, approach_y[1:]))
    assert frames == [1, 2, 3]


def test_missing_corrected_landing_keeps_original_fallback():
    b = brain()
    for name in ('motion_fly_land', 'motion_fly_approach', 'motion_fly_settle'):
        b.repertoire.actions.pop(name, None)
    b.mode, b.y = 'fly', 500
    b._queue(*b._air_return_steps(), Step('idle', 60), now=0)
    b._advance(0)
    seen = set()
    advance_until(b, lambda: b.clip == 'idle', each=lambda now: seen.add(b.clip))
    assert 'motion_fly_land_soft' in seen
    assert not {'motion_fly_approach', 'motion_fly_settle'} & seen
    assert b.mode == 'ground' and b.y == b.floor


def test_physical_drop_does_not_use_the_powered_approach_or_settle():
    b = brain()
    b.mode, b.y, b.vx, b.vy = 'air', 500, 0, 0
    b._set_clip('fall', 0, restart=True)
    seen = set()
    advance_until(b, lambda: b.clip == 'idle', each=lambda now: seen.add(b.clip))
    assert any(b._role(name) == 'land' or name == 'land' for name in seen)
    assert not {'motion_fly_approach', 'motion_fly_settle'} & seen
