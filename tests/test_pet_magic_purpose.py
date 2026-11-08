"""Requested magic has a visible purpose and cannot invent a real-world result."""
import random
from unittest.mock import patch

import pytest

from regular.pet_actions_magic_daily import ACTIONS as MAGIC
from regular.pet_brain import CLIPS, DRAGON_COOLDOWN, PetBrain, Step
from regular.pet_repertoire import clips, curated_actions
from regular.pet_routines import DAILY_MAGIC, EPISODES, choose_routine


def brain():
    b = PetBrain(rng=random.Random(7), now=0)
    b.set_ground(0, 1440, 860, ceiling=0)
    b.place(300)
    b._queue(Step("idle", 120), now=0, interrupt=True)
    return b


@pytest.fixture
def with_magic():
    # Adoption may happen after runtime development. Simulate exactly the
    # optional adopted sheets, without making missing art globally available.
    with patch.dict(CLIPS, clips(MAGIC)):
        b = brain()
        b.repertoire.actions.update(MAGIC)
        yield b


def settled_prop(b, kind="coin", x=1100):
    prop_id = b.props.emit(kind, x, b.floor - 12, b._now)
    item = next(item for item in b.props.items if item.id == prop_id)
    item.grounded, item.vx, item.vy, item.ttl = True, 0, 0, 120
    return item


def until(b, predicate, seconds=15):
    end = b._now + seconds
    while b._now < end:
        b.tick(round(b._now + 1 / 60, 9))
        if predicate():
            return
    raise AssertionError((b._now, b.mode, b.clip, b.warp, b._chasing_prop))


def test_first_request_transforms_immediately_then_visits_every_daily_spell(with_magic):
    b = with_magic
    assert not b._can_dragon(0)
    assert b.magic(0)
    assert b.clip == "form_dragon"
    assert not b._can_dragon(DRAGON_COOLDOWN - .01)
    assert b._can_dragon(DRAGON_COOLDOWN)
    seen = []
    for now in range(1, 6):
        b._queue(Step("idle", 120), now=now, interrupt=True)
        assert b.magic(now)
        seen.append(b.clip)
    assert set(seen) == set(DAILY_MAGIC)
    assert len(set(seen)) == len(seen)
    b._queue(Step("idle", 120), now=6, interrupt=True)
    assert b.magic(6)
    assert b.clip in (*DAILY_MAGIC, "form_dragon")


def test_explicit_dragon_bypasses_only_automatic_cooldown_and_keeps_quiet_auto_off(with_magic):
    b = with_magic
    b.quiet = True
    assert b.magic(1, kind="dragon")
    assert b.clip == "form_dragon" and not b._can_dragon(1)
    b._queue(Step("idle", 120), now=2, interrupt=True)
    assert b.magic(2, kind="dragon")
    assert b._dragon_at == 2


@pytest.mark.parametrize("field,value", [("chat_pending", True), ("media", "laptop"),
    ("agent_busy_until", 100), ("game_active", True), ("motion", False), ("mode", "fly")])
def test_busy_or_reduced_motion_does_not_replace_a_real_task(with_magic, field, value):
    b = with_magic
    setattr(b, field, value)
    previous = b.step
    assert not b.magic(1, kind="magic_daily_mend")
    assert b.step is previous and not b._magic_used


def test_live_state_blocks_magic_and_manual_performance_does_not_change_mood(with_magic):
    b = with_magic
    b.state_clip, b.state_until = "think", 100
    assert not b.magic(1)
    b.state_until = 0
    before = (b.presence.mood, b.warmth)
    assert b.magic(2, kind="magic_daily_mend")
    assert (b.presence.mood, b.warmth) == before
    assert b.clip == "magic_daily_mend" and b.step.hold is None


def test_partial_adoption_only_plays_available_scenes_and_legacy_can_decline(with_magic):
    b = with_magic
    b.repertoire.actions = {"magic_daily_brush": MAGIC["magic_daily_brush"]}
    assert not b.magic(1, kind="magic_daily_mend")
    assert not b.magic(1, kind="prop")
    assert b.magic(1) and b.clip == "magic_daily_brush"
    b._queue(Step("idle", 120), now=2, interrupt=True)
    b.repertoire.actions = {}
    assert not b.magic(2)
    assert b.clip == "idle"


def test_legacy_adopted_scene_fallback_is_a_complete_scene():
    b = brain()
    scene = b.repertoire.actions["scene_fire_controlled"]
    b.repertoire.actions = {"scene_fire_controlled": scene}
    assert b.magic(1)
    assert b.clip == "scene_fire_controlled"
    assert [step.clip for step in b.plan] == ["idle"]


def test_daily_spells_share_one_story_family_and_never_become_rest_loops():
    assert set(MAGIC) == set(DAILY_MAGIC)
    assert [family for family, branches in EPISODES.items()
            if any(name in MAGIC for branch in branches for name, _ in branch)] == ["daily_magic"]
    assert not curated_actions(MAGIC)
    assert choose_routine(random.Random(7), MAGIC, mode="rest") is None
    assert choose_routine(random.Random(7), MAGIC, mode="story", quiet=True) is None
    seen, recent = set(), []
    for seed in range(30):
        family, beats = choose_routine(random.Random(seed), MAGIC, mode="story", recent_clips=recent)
        assert family == "scene:daily_magic" and len(beats) == 1
        name, duration = beats[0]
        assert duration is None and not MAGIC[name]["loop"]
        seen.add(name)
        recent.append(name)
    assert seen == set(DAILY_MAGIC)


def test_daily_magic_uses_the_existing_story_gate(with_magic):
    b = with_magic
    b.repertoire.actions = dict(MAGIC)
    b._last_story_at, b._rests_since_story = 0, 2
    with patch.object(b.rng, "random", return_value=0):
        assert not any(step.clip in MAGIC for step in b._routine_steps(179))
        assert any(step.clip in MAGIC for step in b._routine_steps(180))
        assert not any(step.clip in MAGIC for step in b._routine_steps(360))
    assert b._rests_since_story == 0


def test_recent_daily_magic_family_yields_to_another_complete_story(with_magic):
    b = with_magic
    actions = {**MAGIC, "scene_glass_hello": b.repertoire.actions["scene_glass_hello"]}
    family, beats = choose_routine(random.Random(7), actions,
                                   recent_families=("scene:daily_magic",), mode="story")
    assert family == "scene:viewer" and beats == [("scene_glass_hello", None)]
    family, beats = choose_routine(random.Random(7), actions,
                                   recent_families=("scene:viewer",), mode="story")
    assert family == "scene:daily_magic" and len(beats) == 1


@pytest.mark.parametrize("name", DAILY_MAGIC)
def test_each_daily_story_plays_every_frame_once_then_returns_empty_handed_to_idle(with_magic, name):
    b = with_magic
    b.repertoire.actions = {name: MAGIC[name]}
    b._last_story_at, b._rests_since_story = -180, 2
    with patch.object(b.rng, "random", return_value=0):
        steps = b._routine_steps(0)
    assert [step.clip for step in steps] == [name, "idle"]
    b._queue(*steps, now=0, interrupt=True)
    seen = []
    while b.clip == name:
        frame = b.pose()[1]
        if not seen or frame != seen[-1]:
            seen.append(frame)
        b.tick(round(b._now + 1 / 60, 9))
    assert seen == [0, 1, 2, 3]
    assert b._now == pytest.approx(len(CLIPS[name][1]) / CLIPS[name][2], abs=1 / 60)
    assert b.clip == "idle" and b.posture() == "stand" and not b.plan
    assert b._last_story_at == 0 and b._rests_since_story == 0


@pytest.mark.parametrize("kind", ["ball", "coin", "gem"])
def test_distant_prop_magic_arrives_then_really_interacts_once(kind):
    b = brain()
    item = settled_prop(b, kind)
    action = "kick" if kind == "ball" else "collect"
    with patch.object(b.props, action, wraps=getattr(b.props, action)) as effect:
        assert b.magic(0, kind="prop")
        assert b.warp["cause"] == "prop" and b.warp["via"] == "floor"
        destination = b.warp["x"]
        assert effect.call_count == 0
        until(b, lambda: b.warp is None)
        assert effect.call_count == 1
    assert b.mode == "ground" and b.x == pytest.approx(destination)
    assert b.clip == ("cheer" if kind == "ball" else "nod")
    assert [step.clip for step in b.plan] == ["idle"]
    assert b._chasing_prop is None
    assert b.bubble.text == ("踢一下～" if kind == "ball" else "找到了。")
    assert b._prop_at == b._now
    if kind == "ball":
        assert item in b.props.items and item.vx > 0
    else:
        assert item not in b.props.items and not b.props.items


def test_near_prop_still_walks_and_empty_desktop_has_no_teleport_show():
    b = brain()
    assert not b.magic(0, kind="prop")
    with patch.object(b, "_weighted", return_value="idle") as choose:
        b._choose(1)
    assert choose.call_args.args[0]["warp"] == 0
    settled_prop(b, x=420)
    assert not b.magic(2, kind="prop")
    with patch.object(b.rng, "random", return_value=0):
        steps = b._prop_routine(2)
    assert len(steps) == 1 and steps[0].target_x is not None
    assert steps[0].clip != "warp_cast" and b._warp_request is None


def test_automatic_far_prop_trip_uses_existing_notice_gate_and_warp_cooldown():
    b = brain()
    settled_prop(b)
    with patch.object(b.rng, "random", return_value=0):
        b.tick(9)
    assert b.warp["cause"] == "prop" and b._chasing_prop is not None
    b._queue(Step("idle", 120), now=9.1, interrupt=True)
    with patch.object(b.rng, "random", return_value=0):
        assert b._prop_routine(10) is None  # Existing two-minute toy-interest cooldown.
        b._prop_chase_at = -120
        steps = b._prop_routine(10)
    assert steps[0].clip != "warp_cast" and steps[0].target_x is not None
    assert b._warp_request is None  # A recent warp does not become another spell.


def test_repeated_magic_during_cast_keeps_the_real_prop_destination():
    b = brain()
    settled_prop(b)
    assert b.magic(0, kind="prop")
    destination = b.warp["x"]
    b.tick(.6)
    old_portals = tuple(b.portals)
    assert b.magic(.6, kind="prop")
    assert b.warp["cause"] == "prop" and b.warp["x"] == destination
    assert b._chasing_prop is not None
    assert all(portal.closing == .6 for portal in old_portals)
    with patch.object(b.props, "collect", wraps=b.props.collect) as collect:
        until(b, lambda: b.warp is None)
    assert collect.call_count == 1


@pytest.mark.parametrize("change", ["drag", "expire", "move"])
def test_prop_lost_during_cast_closes_portals_without_a_fake_result(change):
    b = brain()
    item = settled_prop(b)
    assert b.magic(0, kind="prop")
    b.tick(.2)
    if change == "drag":
        b.props.drag(item.id, 900, 500, .21)
    elif change == "expire":
        item.ttl = .21
    else:
        item.x -= 300
    with patch.object(b.props, "collect", wraps=b.props.collect) as collect:
        b.tick(.22)
    assert collect.call_count == 0 and b.warp is None and b._chasing_prop is None
    assert b.mode == "ground" and b.x == 300
    assert all(portal.closing is not None for portal in b.portals)
    assert b.bubble is None or b.bubble.text != "找到了。"


@pytest.mark.parametrize("change", ["drag", "expire", "move", "interrupt", "busy"])
def test_committed_trip_lands_safely_but_rechecks_target_and_intent(change):
    b = brain()
    item = settled_prop(b)
    assert b.magic(0, kind="prop")
    until(b, lambda: b.mode == "warp")
    if change == "drag":
        b.props.drag(item.id, 900, 500, b._now)
    elif change == "expire":
        item.ttl = b._now - .01
    elif change == "move":
        item.x -= 300
    elif change == "busy":
        b.state_clip, b.state_until = "think", 100
    else:
        b._queue(Step("wave"), Step("idle", 120), now=b._now, interrupt=True)
    with patch.object(b.props, "collect", wraps=b.props.collect) as collect:
        until(b, lambda: b.warp is None)
    assert collect.call_count == 0 and b._chasing_prop is None
    assert b.mode == "ground" and b.y == b.floor
    assert b.bubble is None or b.bubble.text != "找到了。"
    if change == "interrupt":
        assert b.clip == "wave"


def test_cancel_queued_prop_trip_during_existing_put_away_clears_its_intent():
    b = brain()
    settled_prop(b)
    b._queue(Step("ending_work_put_away"), now=0, interrupt=True)
    until(b, lambda: b.clip == "ending_work_put_away")
    started = b.clip_started
    assert b.magic(b._now, kind="prop")
    assert b.clip == "ending_work_put_away" and b._warp_request["cause"] == "prop"
    b._queue_manual([Step("wave"), Step("idle", 120)], b._now + .1)
    assert b.clip_started == started
    assert b._warp_request is None and b._chasing_prop is None
    until(b, lambda: b.clip == "wave")
    assert b.warp is None
