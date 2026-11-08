"""Air play follows real pointer positions and finite, authored performances."""
import math
import random
from unittest.mock import patch

import pytest

from regular.pet_brain import (AIR_CHASE_COOLDOWN, AIR_CHASE_RANGE, AIR_CHASE_SECONDS,
                               AIR_FIRE_COOLDOWN, AIR_HAND, CLIPS, PetBrain, Step)


def brain(**kwargs):
    b = PetBrain(rng=random.Random(7), now=0, **kwargs)
    b.set_ground(0, 1440, 860, ceiling=0)
    b.place(700)
    b._queue(Step("idle", 120), now=0, interrupt=True)
    return b


def until(b, condition, *, seconds=25, each=None):
    end = b._now + seconds
    while b._now < end:
        now = round(b._now + 1 / 60, 9)
        if each:
            each(now)
        b.tick(now)
        if condition():
            return b._now
    raise AssertionError((b._now, b.mode, b.clip, b.step, b._air_chase))


def start_chase(b):
    b.pointer.seen = True
    b.pointer.x, b.pointer.y = b.x + b.figure * .1, b.floor - b.figure * 2.4
    assert b._start_air_chase(b._now)


def wiggle(b, x, y):
    for index, offset in enumerate((-14, 14, -14, 14, -14, 14)):
        b.hover(.1 + index * .1, x + offset, y, near=False)


def test_adopted_clips_have_authored_durations_and_airborne_success_alias():
    b = brain()
    for name, seconds in (("air_play_fire", 2.5), ("air_play_reach", 2),
                          ("air_play_miss", 2), ("air_play_caught", 1)):
        assert b._has(name)
        assert b.repertoire.actions[name]["starts_from"] == "air"
        _, sequence, fps, loop, posture = CLIPS[name]
        assert len(sequence) / fps == seconds
        assert posture == "air" and not loop
    assert CLIPS["air_play_caught"][0:2] == ("air_play_fire", [3, 3])


def test_natural_flight_adds_only_one_rare_fire_with_its_own_cooldown():
    b = brain()
    with patch.object(b.rng, "random", return_value=0):
        first = b._flight_steps(0)
        assert sum(s.clip == "air_play_fire" for s in first) == 1
        assert not any(s.clip == "air_play_fire" for s in b._flight_steps(AIR_FIRE_COOLDOWN - .1))
        assert any(s.clip == "air_play_fire" for s in b._flight_steps(AIR_FIRE_COOLDOWN))
    fire = next(s for s in first if s.clip == "air_play_fire")
    assert 0 < abs(fire.target_x - b.x) <= b.figure
    assert fire.target_y < b.floor and first[-2].hold == "air_return"
    with patch.object(b.rng, "random", return_value=.21):
        assert not any(s.clip == "air_play_fire" for s in b._flight_steps(2 * AIR_FIRE_COOLDOWN))


@pytest.mark.parametrize("distance", [2, 160])
def test_fire_moves_for_all_frames_even_when_destination_is_close(distance):
    b = brain()
    b.mode, b.y, b.step = "fly", 500, None
    b._queue(Step("air_play_fire", target_x=b.x + distance, target_y=b.y),
             Step("motion_fly_flap_slow", 60), now=0)
    b._advance(0)
    seen, positions = set(), []
    while b.clip == "air_play_fire":
        seen.add(b.pose()[1])
        positions.append(b.x)
        b.tick(round(b._now + 1 / 60, 9))
    assert seen == {0, 1, 2, 3}
    assert b._now == pytest.approx(2.5, abs=1 / 60)
    assert max(positions) - min(positions) > distance * .99
    assert b.x == pytest.approx(700 + distance)


def test_high_wiggle_invites_flight_and_low_wiggle_keeps_ground_pounce():
    high = brain()
    wiggle(high, high.x, high.floor - high.figure * 2.2)
    assert high.air_play_active and high._air_chase is not None
    low = brain()
    wiggle(low, low.x + low.figure, low.floor - low.figure * .7)
    assert not low.air_play_active and low.clip == "pounce_crouch"


def test_invitation_during_existing_flight_does_not_take_off_again():
    b = brain()
    b.mode, b.y, b.step = "fly", 500, None
    b._queue(Step("motion_fly_flap_slow", 60), now=0)
    b._advance(0)
    wiggle(b, b.x + b.figure * .2, b.y - b.figure * .5)
    assert b.air_play_active and b.step.hold == "air_chase"
    assert not any(b.repertoire.actions.get(s.clip, {}).get("family") == "takeoff" for s in b.plan)


@pytest.mark.parametrize("field,value", [("quiet", True), ("motion", False), ("attention", True),
    ("chat_pending", True), ("game_active", True), ("media", "laptop"), ("agent_busy_until", 100)])
def test_quiet_busy_and_reduced_motion_block_air_play(field, value):
    b = brain()
    setattr(b, field, value)
    wiggle(b, b.x, b.floor - b.figure * 2.2)
    assert not b.air_play_active
    with patch.object(b.rng, "random", return_value=0):
        assert not any(s.clip == "air_play_fire" for s in b._flight_steps(0))


def test_partial_optional_assets_cannot_start_an_unfinishable_chase():
    b = brain()
    del b.repertoire.actions["air_play_caught"]
    b.pointer.seen = True
    assert not b._start_air_chase(0)


@pytest.mark.parametrize("escape", [False, True])
def test_pointer_at_closed_palms_selects_success_or_miss_without_skipping_contact(escape):
    b = brain()
    start_chase(b)
    until(b, lambda: b.clip == "air_play_reach")
    started = b._now
    hx, hy = b._air_hands()
    assert (hx, hy) == pytest.approx((b.x + b.facing * b.figure * 38 / 320,
                                    b.y - b.figure * 158 / 320))
    b.pointer.x, b.pointer.y = hx, hy
    until(b, lambda: b._now - started >= .8)
    if escape:
        b.pointer.x += b.figure * 2
    until(b, lambda: b._air_chase["caught"] is not None)
    assert b._air_chase["caught"] is (not escape)
    assert b.clip == "air_play_reach" and b.pose()[1] == 2
    resolved = b._now
    until(b, lambda: b._now - resolved >= .4)
    assert b.clip == "air_play_reach" and b.pose()[1] == 2
    until(b, lambda: b.clip != "air_play_reach")
    assert b.clip == ("air_play_miss" if escape else "air_play_caught")
    assert b._now - started == pytest.approx(2 if escape else 1.5, abs=1 / 30)
    assert b.air_play_active
    until(b, lambda: b.mode == "ground")
    assert not b.air_play_active and b.y == b.floor
    assert not b._start_air_chase(b._now)
    assert b._start_air_chase(AIR_CHASE_COOLDOWN)


@pytest.mark.parametrize("facing", [-1, 1])
def test_contact_coordinates_and_target_mirror_and_scale_together(facing):
    b = brain(scale=1.6)
    b.facing, b.y = facing, 500
    point = b._air_hands()
    assert b._air_target(*point, (b.x, b.y)) == pytest.approx((b.x, b.y))
    assert abs(point[0] - b.x) == pytest.approx(b.figure * AIR_HAND[0])


def test_escaping_cursor_has_bounded_chase_duration_and_range_then_lands():
    b = brain()
    start_chase(b)
    origin = b._air_chase["origin"]
    until(b, lambda: b.step.hold == "air_chase")
    started, positions = b._now, []
    b.pointer.x, b.pointer.y = b.right + 5000, b.ceiling - 5000
    while b.step.hold == "air_chase":
        b.tick(round(b._now + 1 / 60, 9))
        positions.append((b.x, b.y))
    assert b._now - started == pytest.approx(AIR_CHASE_SECONDS, abs=1 / 30)
    assert all(math.dist(origin, p) <= b.figure * AIR_CHASE_RANGE + .01 for p in positions)
    assert all(b.left <= x <= b.right and b.ceiling + b.figure <= y <= b.floor for x, y in positions)
    until(b, lambda: b.mode == "ground")
    assert b._now < 20 and not b.air_play_active


def test_repeated_attention_cancels_once_and_keeps_passthrough_until_landing():
    b = brain()
    start_chase(b)
    until(b, lambda: b.step.hold == "air_chase")
    b.set_attention(True, b._now)
    started, y = b.step_started, b.y
    assert b._air_chase is None and b.air_play_active
    until(b, lambda: b.mode == "ground", each=lambda now: b.set_attention(True, now))
    assert b.y > y and b._now - started < 8
    assert not b.air_play_active
    assert not any(s.clip.startswith("air_play_") for s in b.plan)


def test_quiet_during_takeoff_cancels_before_chasing():
    b = brain()
    start_chase(b)
    b.quiet = True
    b.tick(.1)
    assert b.mode == "ground" and not b.air_play_active
    assert not any(s.hold == "air_chase" for s in b.plan)


def test_screen_change_updates_return_floor():
    b = brain()
    start_chase(b)
    until(b, lambda: b.step.hold == "air_chase")
    b.set_attention(True, b._now)
    b.set_ground(0, 1200, 700, ceiling=20)
    until(b, lambda: b.mode == "ground")
    assert b.y == 700 and not b.air_play_active


def test_scale_change_cannot_leave_fire_at_an_unreachable_old_target():
    b = brain()
    b.mode, b.y, b.step = "fly", 160, None
    b._queue(Step("air_play_fire", target_x=b.x + 80, target_y=160),
             *b._air_return_steps(), Step("idle", 60), now=0)
    b._advance(0)
    b.tick(.1)
    b.scale = 1.6
    until(b, lambda: b.clip != "air_play_fire")
    assert b._now < 2.6
    until(b, lambda: b.mode == "ground")
    assert b.y == b.floor and not b.air_play_active
