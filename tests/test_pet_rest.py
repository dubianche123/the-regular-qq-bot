# -*- coding: utf-8 -*-
"""歇着是常态：她自己挑的一件事做完，就在原来的姿势里歇一阵。

2026-11-15 群主：「动作触发的感觉太频繁了……几乎一刻不停有动作不会是常态」。当时在白天、你坐在电脑前、
群里安静，她每小时换约 300 次动作、自己冒约 13 句话，两次明显动作之间中位数只隔 4 秒，歇着的时间约三成。
常见桌宠的做法是平时待机、眨眼，偶尔张望一下，自己起意做件事要隔几分钟。照这个改了，守这几条：
  · 一件事做完，下一拍是歇着，而且就在原来的姿势里歇，不站起来也不坐下；
  · 没精神歇得更久，群里热闹歇得短一点；
  · 群里真有动静、你点「换一个」，照样马上动，不等歇完；
  · 日常里她自己冒的小台词十分钟最多一句；
  · 白天坐在电脑前的一小时，大半时间在歇着，但也不是一动不动。
"""
import random
from unittest.mock import patch

from regular.pet_brain import AMBIENT_LINE_GAP, CLIPS, REST_SPAN, RESTING, PetBrain, Step


def brain(seed=7, *, adopted=False):
    b = PetBrain(rng=random.Random(seed), now=0.0)
    if not adopted:
        b.repertoire.actions = {}
    b.set_ground(0, 1440, 860)
    b.place(700)
    return b


def ready(mood=0.0, circadian="", chatter=1, events=()):
    return {"bot": "ready", "available": True, "qq_connected": True, "mood": mood,
            "circadian": circadian, "chatter": chatter, "events": list(events)}


def run(b, start, seconds, *, fps=4):
    t, clips = start, []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        b.owner_idle(t, 0.0)
        b.tick(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
    return t, clips


def test_each_pastime_is_followed_by_a_rest():
    b = brain()
    b.observe(0.0, ready())
    b._spell, b._spell_until = "standing", 1e9
    with patch.object(b, "_weighted", return_value="observe") as weighted:
        plans = [[step.clip for step in b._choose(float(i))] for i in range(4)]
    assert weighted.call_count == 2
    assert "lookaround" in plans[0] and "lookaround" in plans[2]
    for rest in plans[1], plans[3]:
        assert rest[0] == rest[-1] == "idle" and rest[1] in ("look_left", "look_right")


def test_she_rests_in_the_posture_she_is_already_in():
    standing = brain()
    standing.observe(0.0, ready())
    standing._owes_rest = True
    rest = standing._rest_steps(standing.vitals())
    assert {CLIPS[step.clip][4] for step in rest} == {"stand"}
    assert REST_SPAN[0] <= sum(step.duration for step in rest)

    seated = brain()
    seated.observe(0.0, ready())
    seated._queue(Step("tea", 5), now=0.0, interrupt=True)       # sit_down, then tea
    seated._queue(*seated._rest_steps(seated.vitals()), now=0.0)
    assert {CLIPS[step.clip][4] for step in seated.plan} == {"sit"}   # tea, then a seated rest: no stand_up


def test_low_pep_rests_longer_and_a_lively_room_shorter():
    def average(snapshot, n=300):
        b = brain()
        b.observe(0.0, snapshot)
        return sum(sum(step.duration for step in b._rest_steps(b.vitals())) for _ in range(n)) / n

    day, late, lively = average(ready()), average(ready(circadian="熬到半夜")), average(ready(chatter=12))
    assert late > 1.3 * day
    assert lively < 0.85 * day


def test_furniture_stays_across_multiple_rests_without_reentering_or_exiting():
    for clip, name in (("scene_chair_rest", "chair"), ("scene_sofa_lounge", "sofa")):
        b = brain(adopted=True)
        b.observe(0, ready())
        b._set_clip(clip, 0, restart=True)
        b._queue(Step(clip, 1), now=0, interrupt=True)
        _, clips = run(b, 0, 180)
        assert b.current_rest()["name"] == name
        assert set(clips) == {clip}
        assert not any(step.clip.endswith("_exit") for step in b.plan)


def test_restful_non_furniture_pose_is_kept_when_taking_the_next_rest():
    b = brain(adopted=True)
    b._set_clip("daily_quiet_tail_rest", 0, restart=True)
    steps = b._rest_steps(b.vitals())
    assert [step.clip for step in steps] == ["daily_quiet_tail_rest"]
    assert b.current_rest()["exit"] is None


def test_leaving_furniture_for_real_work_exits_once_and_a_new_request_does_not_restart_cleanup():
    b = brain(adopted=True)
    b._set_clip("scene_sofa_lounge", 0, restart=True)
    b._queue(Step("talk", 10), now=1, interrupt=True)
    assert b.clip == "scene_sofa_exit"
    began = b.clip_started
    b._queue(Step("inspect", 10), now=1.2, interrupt=True)
    assert b.clip == "scene_sofa_exit" and b.clip_started == began
    assert [step.clip for step in b.plan] == ["inspect"]


def test_small_acknowledgement_keeps_rest_and_furniture_in_place():
    b = brain(adopted=True)
    b._set_clip("scene_chair_rest", 0, restart=True)
    b._queue(Step("scene_chair_rest", 120), now=0, interrupt=True)
    original = b.step
    assert b.keep_rest(10)
    assert b.step is original and b.clip == "scene_chair_rest"
    assert b.jolt_started == 10


def test_resting_furniture_can_later_leave_for_an_activity_without_a_forced_timeout():
    b = brain(adopted=True)
    b.observe(0, ready())
    b._set_clip("scene_chair_rest", 0, restart=True)
    b._rest_stays = 2
    b._owes_rest = False
    with patch.object(b.rng, "random", return_value=1), patch.object(b, "_pastime", return_value=[Step("walk", target_x=900)]):
        steps = b._choose(100)
    b._queue(*steps, now=100, interrupt=True)
    assert b.clip == "scene_chair_exit"
    assert any(step.clip == "walk" for step in b.plan)


def test_a_real_group_event_does_not_wait_for_the_rest_to_end():
    b = brain()
    b.observe(0.0, ready())
    b._queue(*b._rest_steps(b.vitals()), now=0.0, interrupt=True)
    t, _ = run(b, 0.0, 10)
    assert b.clip == "idle"
    b.observe(t, ready(events=[{"kind": "said", "ts": t}]))
    assert b.clip == "talk"


def test_asking_for_the_next_thing_skips_the_rest():
    b = brain(adopted=True)
    b.observe(0.0, ready())
    b._queue(*b._rest_steps(b.vitals()), now=0.0, interrupt=True)
    t, _ = run(b, 0.0, 5)
    assert b.next_activity(t)
    assert b.clip not in ("idle", "look_left", "look_right")


def test_resting_costs_no_energy_but_doing_things_does():
    b = brain()
    b.observe(0.0, ready())
    b.energy = 0.6
    b._queue(Step("idle", 60), now=0.0, interrupt=True)
    t, _ = run(b, 0.0, 30)
    assert b.energy == 0.6
    b._queue(Step("think", 60), now=t, interrupt=True)
    run(b, t, 30)
    assert b.energy < 0.6


def test_her_own_little_lines_come_at_most_once_in_ten_minutes():
    b = brain()
    said = [b._hush([Step("idle", 1, line="嘿"), Step("nod", line="好")], now)
            for now in (0.0, AMBIENT_LINE_GAP / 2, AMBIENT_LINE_GAP + 1)]
    assert [[step.line for step in steps] for steps in said] == [["嘿", "好"], [None, None], ["嘿", "好"]]


def test_an_hour_at_the_desk_is_mostly_resting_but_not_still():
    for seed in range(3):
        b = brain(seed, adopted=True)
        b.observe(0.0, ready())
        t, changes, resting = 0.0, 0, 0
        last = None
        for _ in range(3600 * 4):
            t += 0.25
            b.owner_idle(t, 0.0)
            b.tick(t)
            spec = b.repertoire.actions.get(b.clip, {})
            resting += b.clip in RESTING or spec.get("role") == "rest" or bool(spec.get("restful"))
            changes += b.clip != last
            last = b.clip
        assert 40 < changes < 200, (seed, changes)            # was ~300 an hour before resting was the default
        assert 0.55 < resting / (3600 * 4) < 0.97, (seed, resting / (3600 * 4))
