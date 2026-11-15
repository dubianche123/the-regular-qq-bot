# -*- coding: utf-8 -*-
"""她的一天：作息、困意、来劲程度、心情和群里气氛融成一份 Vitals，日常一段一段地过。

守的几条（都是 2026-11-15 用真实一天的互动日志重放量出来的问题）：
  · 夜里没人在就睡整觉，睡到你回来或天亮；白天走开那一觉仍最多 OWNER_NAP；
  · 半夜被群里叫醒，回完话接着睡，不起来变龙、飞、打游戏；
  · 困了、烦了、深夜（来劲程度低）不挑玩闹跑动，动作库只给安静的；
  · 坐着的那一段连着做坐着的事，停顿跟着段落的姿势，不为歇口气站起来又坐下；
  · 动作库的姿势偏好不传时，挑选和以前一模一样。
"""
import random
from unittest.mock import patch

import pytest

from regular import pet_routines
from regular.pet_brain import (ASLEEP, CALM_PEP, CLIPS, ENGAGED_SECONDS, OWNER_AWAY, OWNER_NAP, PAUSE_CLIPS,
                               PLAY_CHOICES, SEATED_CHOICES, PetBrain, Step)


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


def run(b, start, seconds, *, idle=None, fps=4):
    t, clips = start, []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        if idle is not None:
            b.owner_idle(t, idle)
        b.tick(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
    return t, clips


# ── 一份状态 ──
@pytest.mark.parametrize("label,phase,sleepy", [
    ("凌晨被吵醒", "night", True), ("熬到半夜", "late", True), ("刚睡醒", "morning", False),
    ("午后有点困", "drowsy", False), ("", "day", False)])
def test_the_bots_own_clock_sets_her_phase(label, phase, sleepy):
    b = brain()
    b.observe(0.0, ready(circadian=label))
    v = b.vitals()
    assert (v.phase, v.sleepy) == (phase, sleepy)
    assert v.drowsy == (phase != "day")


def test_pep_follows_the_hour_her_energy_her_mood_and_the_room():
    b = brain()
    b.observe(0.0, ready())
    day = b.vitals().pep
    b.observe(1.0, ready(circadian="凌晨被吵醒"))
    assert b.vitals().pep < CALM_PEP < day
    b.observe(2.0, ready(mood=-0.8))
    assert b.vitals().pep < day and b.vitals().mood == -1
    b.observe(3.0, ready(chatter=12))
    assert b.vitals().room == "lively" and b.vitals().pep >= day
    b.observe(4.0, ready())
    b.energy = 0.2
    assert b.vitals().sleepy and b.vitals().pep < day


# ── 夜里睡整觉 ──
def test_alone_at_night_she_sleeps_until_you_come_back():
    b = brain()
    b.observe(0.0, ready(circadian="凌晨被吵醒"))
    t, clips = run(b, 0.0, 3 * OWNER_NAP, idle=2 * OWNER_AWAY)
    assert b.clip in ASLEEP and b.step.hold == "away"
    awake = [clip for clip in clips[clips.index(b.clip):] if clip not in ASLEEP]
    assert not awake                                     # no getting up every half hour
    run(b, t, 30, idle=0.0)
    assert b.clip not in ASLEEP


def test_a_daytime_away_nap_still_ends_by_itself():
    b = brain()
    b.observe(0.0, ready())
    b.owner_idle(0.0, 2 * OWNER_AWAY)
    b._queue(*b._nap_steps(0.0), now=0.0, interrupt=True)
    run(b, 0.0, OWNER_NAP - 1, idle=2 * OWNER_AWAY)
    nap = b.step
    assert nap.hold == "away"
    # She gets up once the nap is long enough (she may well nod off again later: you are still away).
    t, woke = OWNER_NAP - 1, False
    while t < OWNER_NAP + 60 and not woke:
        t, _ = run(b, t, 1, idle=2 * OWNER_AWAY)
        woke = b.step is not nap
    assert woke


def test_at_dawn_the_night_hold_gives_way():
    b = brain()
    b.observe(0.0, ready(circadian="凌晨被吵醒"))
    b.owner_idle(0.0, 2 * OWNER_AWAY)
    b._queue(*b._nap_steps(0.0), now=0.0, interrupt=True)
    run(b, 0.0, OWNER_NAP + 30, idle=2 * OWNER_AWAY)
    assert b.step.hold == "away"
    b.observe(OWNER_NAP + 31, ready(circadian="刚睡醒"))
    run(b, OWNER_NAP + 31, 10, idle=2 * OWNER_AWAY)
    assert b.step is None or b.step.hold != "away"


def test_woken_by_the_group_at_night_she_goes_back_to_sleep_without_playing():
    b = brain(adopted=True)
    b.observe(0.0, ready(circadian="凌晨被吵醒"))
    b.owner_idle(0.0, 2 * OWNER_AWAY)
    b.last_agent_event = 0.0                             # someone just called her in the group
    picks = []
    with patch.object(b, "_weighted", side_effect=lambda w: picks.append(dict(w)) or "idle"):
        b._choose(10.0)
    assert picks and all(picks[0][name] == 0 for name in PLAY_CHOICES if name in picks[0])
    steps = b._choose(ENGAGED_SECONDS + 1)               # the call is over: straight back to sleep
    assert steps[0].clip in ASLEEP and steps[0].hold == "away"


# ── 来劲程度管玩闹 ──
def test_low_pep_offers_no_play_and_only_quiet_library_routines():
    b = brain(adopted=True)
    b.observe(0.0, ready(circadian="熬到半夜"))
    seen = {}
    with patch.object(b, "_weighted", side_effect=lambda w: seen.update(w) or "library"), \
            patch("regular.pet_brain.choose_routine", return_value=None) as routine:
        b._choose(1.0)
    assert all(seen[name] == 0 for name in PLAY_CHOICES if name in seen)
    assert routine.call_args.kwargs["quiet"] is True


def test_a_good_mood_makes_play_more_likely_than_a_bad_one():
    weights = {}
    for mood in (-0.8, 0.8):
        b = brain()
        b.observe(0.0, ready(mood=mood))
        b._spell, b._spell_until = "standing", 1e9
        with patch.object(b, "_weighted", side_effect=lambda w, m=mood: weights.__setitem__(m, w) or "idle"):
            b._choose(1.0)
    play = {mood: sum(w[name] for name in PLAY_CHOICES if name in w) for mood, w in weights.items()}
    assert play[0.8] > 2 * play[-0.8]


# ── 一段一段地过 ──
def test_a_settled_spell_favours_seated_pastimes_and_pauses_seated():
    b = brain()
    b.observe(0.0, ready())
    b._spell, b._spell_until = "seated", 1e9
    seen = {}
    with patch.object(b, "_weighted", side_effect=lambda w: seen.update(w) or "headphones"):
        steps = b._choose(1.0)
    seated = sum(seen[n] for n in SEATED_CHOICES if n in seen and n != "nap")
    standing = sum(v for n, v in seen.items() if n not in SEATED_CHOICES and n != "library")
    assert seated > 2 * standing
    assert steps[-1].clip == "sit_swing"
    b._spell, b._owes_rest = "standing", False   # skip the rest after the headphones (test_pet_rest)
    with patch.object(b, "_weighted", return_value="wander"):
        steps = b._choose(2.0)
    assert steps[-1].clip in PAUSE_CLIPS[1:] and CLIPS[steps[-1].clip][4] == "stand"


def test_spells_alternate_and_last_longer_seated_when_she_is_tired():
    b = brain()
    b.observe(0.0, ready())
    v = b.vitals()
    kinds, t = [], 0.0
    for _ in range(4):
        kinds.append(b._spell_kind(t, v))
        t = b._spell_until
    assert kinds == ["standing", "seated", "standing", "seated"]
    b.observe(t, ready(circadian="熬到半夜"))
    b._spell = "standing"
    b._spell_kind(t, b.vitals())
    assert b._spell == "seated" and b._spell_until - t > 240


def test_a_working_hour_does_not_keep_standing_up_and_sitting_down():
    b = brain(adopted=True, seed=3)
    b.observe(0.0, ready())
    flips, last, t = 0, None, 0.0
    for i in range(30 * 60 * 10):
        t += 0.1
        b.owner_idle(t, 0.0)
        b.tick(t)
        level = {"lie": "sit"}.get(CLIPS[b.clip][4], CLIPS[b.clip][4])
        if level == "air":
            continue
        flips += last is not None and level != last
        last = level
    assert flips < 45          # half an hour; before 2026-11-15 this was about 55


# ── 动作库的姿势偏好 ──
def test_routine_posture_preference_is_opt_in_and_steers_the_pick():
    b = brain(adopted=True)
    actions = b.repertoire.actions
    plain = [pet_routines.choose_routine(random.Random(s), actions, mode="rest") for s in range(20)]
    again = [pet_routines.choose_routine(random.Random(s), actions, mode="rest", prefer_posture=None)
             for s in range(20)]
    assert plain == again
    def seated_share(**kwargs):
        picks = [pet_routines.choose_routine(random.Random(s), actions, mode="rest", **kwargs)[1][0][0]
                 for s in range(200)]
        return sum(actions[name]["posture"] in ("sit", "lie") for name in picks) / len(picks)
    assert seated_share(prefer_posture="sit") > seated_share() + 0.2
    assert seated_share(prefer_posture="stand") < seated_share() - 0.1
