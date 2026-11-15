"""桌宠自己留意到的事：照看者在不在、坐了多久、夜深了，以及心情和群里气氛的外显。

外壳只报一个数（键盘鼠标多久没动）；这里全是纯计算，不需要 Qt，也不读任何内容。
"""
import random

import pytest

from regular.pet_brain import (ASLEEP, CARE_GAIN, CHATTER_SURGE_GAP, LINES, MOOD_SHIFT_GAP, OWNER_AWAY,
                               OWNER_LATE, OWNER_LONG, OWNER_NAP, OWNER_SIT, OWNER_WHILE, PetBrain, Step)


def brain(seed=7, *, adopted=False, **kwargs):
    b = PetBrain(rng=random.Random(seed), now=0.0, **kwargs)
    if not adopted:
        b.repertoire.actions = {}        # the legacy fallback keeps clip names stable
    b.set_ground(0, 1440, 860)
    b.place(700)
    return b


def run(b, start, seconds, *, idle=None, fps=10, each=None):
    """Tick for `seconds`; idle=0 keeps the owner at the keyboard, a number above OWNER_AWAY keeps them away."""
    t, clips, said = start, [], []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        if idle is not None:
            b.owner_idle(t, idle + (t - start if idle else 0.0))
        b.tick(t)
        if each:
            each(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
        if b.bubble is not None and (not said or said[-1] != b.bubble.text):
            said.append(b.bubble.text)
    return t, clips, said


def ready(mood=0.0, circadian="", chatter=0, events=(), bot="ready"):
    return {"bot": bot, "available": True, "qq_connected": True, "mood": mood,
            "circadian": circadian, "chatter": chatter, "events": list(events)}


def resting(b, t=0.0, seconds=3600):
    """Hold a plain idle so a scenario is not cut short by an ambient routine."""
    b._queue(Step("idle", seconds), now=t, interrupt=True)


OWN_LINES = {line.format(n=n) for key in ("back", "back_while", "back_long", "back_asleep", "sat", "late",
                                         "mood_up", "mood_down", "lively")
             for line in LINES[key] for n in range(0, 400)}


# ── 照看者在不在 ──
def test_without_an_idle_report_she_knows_nothing_about_the_owner():
    b = brain()
    b.observe(0.0, ready(circadian="熬到半夜"))
    _, _, said = run(b, 0.0, 2 * OWNER_SIT, fps=4)
    assert not b.owner_away and b._owner_since is None
    assert not OWN_LINES & set(said)


def test_a_parked_cursor_is_not_someone_watching_once_the_owner_has_left():
    b = brain()
    resting(b)
    b.owner_idle(1.0, 0.0)
    b.hover(1.0, b.x + 40, 800, near=True)
    assert b.pointer.near
    t, clips, said = run(b, 1.0, 200, idle=OWNER_AWAY,
                         each=lambda t: b.hover(t, b.x + 40, 800, near=True))
    assert b.owner_away and not b.pointer.near
    assert "wave" not in clips and not set(LINES["greet"]) & set(said)   # 不对着没人的鼠标打招呼
    b.owner_idle(t + 0.1, 0.0)
    b.hover(t + 0.1, b.x + 40, 800, near=True)
    assert b.pointer.near and b.pointer.since == t + 0.1


def test_she_naps_while_you_are_away_and_wakes_up_to_greet_you():
    b = brain()
    t, clips, _ = run(b, 0.0, 900, idle=OWNER_AWAY)
    assert b.clip in ASLEEP and b.step.hold == "away"
    slept_from = b.step_started
    t, _, _ = run(b, t, 120, idle=OWNER_AWAY + 900)
    assert b.clip in ASLEEP and b.step_started == slept_from      # 比平时 12–25 秒的打盹长：一直睡到你回来
    b.owner_idle(t + 0.1, 0.0)
    assert not b.owner_away
    assert b.bubble.text in LINES["back_asleep"] and b.bubble.kind == "say"
    _, clips, _ = run(b, t + 0.1, 6, idle=0.0)
    assert clips[:2] == ["wake", "get_up"] and b.posture() == "stand"


def test_an_unattended_nap_ends_by_itself_and_group_activity_still_wakes_her():
    b = brain()
    t, _, _ = run(b, 0.0, 900, idle=OWNER_AWAY)
    assert b.step.hold == "away"
    b.observe(t, ready(events=[{"kind": "mention", "ts": 1.0}]))
    _, clips, _ = run(b, t, 5, idle=OWNER_AWAY + 900)
    assert b.clip not in ASLEEP and "get_up" in clips
    b = brain()
    t, _, _ = run(b, 0.0, 900, idle=OWNER_AWAY)
    start = b.step_started
    run(b, start, OWNER_NAP + 30, idle=2 * OWNER_AWAY, fps=4)
    assert b.step_started > start


@pytest.mark.parametrize("away, key, clip, kind, hearts", [
    (OWNER_AWAY + 60, "back", "peek", "status", False),
    (OWNER_WHILE + 60, "back_while", "wave", "say", False),
    (OWNER_LONG + 60, "back_long", "hop", "say", True),
])
def test_the_welcome_grows_with_how_long_you_were_gone(away, key, clip, kind, hearts):
    b = brain()
    resting(b)
    b.hover(0.5, 300, 800, near=False)
    b.owner_idle(1.0, away)
    b.tick(1.0)
    b.owner_idle(1.1, 0.0)
    assert b.clip == clip and b.facing == -1               # 转过来朝着鼠标那边
    assert b.bubble.text in LINES[key] and b.bubble.kind == kind
    assert any(p.kind == "heart" for p in b.particles) is hearts
    _, clips, _ = run(b, 1.1, 5, idle=0.0)
    assert ("wave" in clips) is (key != "back")
    assert b._owner_since == 1.1                           # 回来算重新坐下


def test_coming_back_never_interrupts_real_work_or_pulls_her_out_of_an_edge():
    b = brain()
    b.chat_sent(1.0)
    thinking = b.bubble.text
    b.owner_idle(2.0, OWNER_WHILE)
    b.owner_idle(2.1, 0.0)
    assert b.clip == "think" and b.bubble.text == thinking
    b = brain()
    b.observe(1.0, ready(events=[{"kind": "drawing", "ts": 1.0}]))
    drawing = b.clip
    b.owner_idle(2.0, OWNER_WHILE)
    b.owner_idle(2.1, 0.0)
    assert b.clip == drawing
    b = brain()
    b.hidden_edge, b.mode = "left", "hidden"
    b.owner_idle(2.0, OWNER_LONG)
    b.owner_idle(2.1, 0.0)
    assert b.mode == "hidden" and [p.kind for p in b.particles] == ["!"]
    b = brain()                                           # 被明确要求去睡的那一觉，不由她自己结束
    b.observe(1.0, ready(chatter=0, events=[{"kind": "cue", "action": "sleep", "ts": 1.0, "id": 1}]))
    t, _, _ = run(b, 1.0, 12, idle=OWNER_WHILE)
    assert b.clip in ASLEEP and b.step.hold == "sleep"
    b.owner_idle(t + 0.1, 0.0)
    b.observe(t + 0.2, ready(chatter=9))
    assert b.clip in ASLEEP and b.bubble is None


def test_the_welcome_is_her_greeting_so_a_lingering_cursor_does_not_get_a_second_wave():
    b = brain()
    resting(b)
    b.owner_idle(1.0, OWNER_WHILE)
    b.owner_idle(1.1, 0.0)
    _, clips, said = run(b, 1.1, 60, idle=0.0, each=lambda t: b.hover(t, b.x + 40, 800, near=True))
    assert clips.count("wave") == 1 and not set(LINES["greet"]) & set(said)


# ── 久坐与深夜 ──
def test_a_long_sitting_gets_one_reminder_with_the_real_minutes_and_her_own_stretch():
    b = brain()
    resting(b, seconds=3 * OWNER_SIT)
    t, clips, said = run(b, 0.0, OWNER_SIT - 5, idle=0.0, fps=4)
    assert clips == ["idle"] and not said
    t, clips, said = run(b, t, 12, idle=0.0, fps=4)
    assert clips[:3] == ["idle", "stretch", "wing_stretch"]
    assert said == [said[0]] and said[0] in [line.format(n=50) for line in LINES["sat"]]
    resting(b, t, 3 * OWNER_SIT)
    t, _, said = run(b, t + 6, OWNER_SIT - 60, idle=0.0, fps=4)
    assert not said                                       # 下一次要再过五十分钟
    _, _, said = run(b, t, 90, idle=0.0, fps=4)
    assert len(said) == 1 and "100 分钟" in said[0]        # 一直没歇：说的是累计时长
    adopted = brain(adopted=True)
    resting(adopted, seconds=3 * OWNER_SIT)
    _, clips, _ = run(adopted, 0.0, OWNER_SIT + 14, idle=0.0, fps=4)
    assert clips[:4] == ["idle", "life_clock_check", "stretch", "wing_stretch"]   # 有素材时先看一眼表


def test_a_break_restarts_the_sitting_clock():
    b = brain()
    resting(b, seconds=4 * OWNER_SIT)
    t, _, _ = run(b, 0.0, OWNER_SIT - 120, idle=0.0, fps=4)
    b.owner_idle(t + 1, OWNER_AWAY + 30)                 # 起身歇了五分钟
    t, _, said = run(b, t + 1, 600, idle=0.0, fps=4)
    assert not any("分钟" in line for line in said)
    resting(b, t, 4 * OWNER_SIT)
    _, _, said = run(b, t, OWNER_SIT - 500, idle=0.0, fps=4)
    assert any("50 分钟" in line for line in said)


def test_reminders_wait_for_real_work_and_can_be_switched_off():
    b = brain()
    resting(b, seconds=3 * OWNER_SIT)
    t, _, _ = run(b, 0.0, OWNER_SIT - 10, idle=0.0, fps=4)
    b.chat_sent(t)                                        # 正在等本机回复：不插话
    t, _, said = run(b, t, 60, idle=0.0, fps=4)
    assert not any("分钟" in line for line in said)
    b.chat_reply(t, "好。")
    _, _, said = run(b, t, 30, idle=0.0, fps=4)
    assert any("分钟" in line for line in said)
    b = brain()
    b.reminders = False
    b.observe(0.0, ready(circadian="熬到半夜"))
    resting(b, seconds=3 * OWNER_SIT)
    _, clips, said = run(b, 0.0, OWNER_SIT + 60, idle=0.0, fps=4)
    assert not OWN_LINES & set(said) and "stretch" not in clips


def test_late_at_night_she_yawns_says_so_and_goes_to_bed_first():
    b = brain()
    b.observe(0.0, ready(circadian="熬到半夜"))
    resting(b, seconds=3 * OWNER_LATE)
    t, clips, said = run(b, 0.0, 595, idle=0.0, fps=4)
    assert not set(LINES["late"]) & set(said)             # 刚坐下不催
    t, clips, said = run(b, t, 40, idle=0.0, fps=4)
    assert len(set(LINES["late"]) & set(said)) == 1
    assert clips.index("stretch") < clips.index("sleep") and b.clip in ASLEEP
    t, _, said = run(b, t, OWNER_LATE - 120, idle=0.0, fps=4)
    assert not set(LINES["late"]) & set(said)             # 一小时内不再说第二遍
    # 白天不说；群里刚有她的动静时只催不睡。
    day = brain()
    resting(day, seconds=3 * OWNER_LATE)
    _, _, said = run(day, 0.0, 900, idle=0.0, fps=4)
    assert not set(LINES["late"]) & set(said)
    busy = brain()
    busy.observe(0.0, ready(circadian="凌晨被吵醒"))
    resting(busy, seconds=3 * OWNER_LATE)
    t, _, _ = run(busy, 0.0, 560, idle=0.0, fps=4)
    busy.observe(t, ready(circadian="凌晨被吵醒", events=[{"kind": "said", "ts": 5.0}]))
    _, clips, said = run(busy, t, 80, idle=0.0, fps=4)
    assert set(LINES["late"]) & set(said) and "stretch" in clips and "sleep" not in clips


# ── 心情与群里气氛 ──
def test_the_first_snapshot_is_only_the_baseline_and_a_later_turn_is_acted_out_once():
    b = brain()
    resting(b)
    b.observe(0.0, ready(mood=0.8))
    _, clips, said = run(b, 0.0, 30)
    assert "hop" not in clips and not set(LINES["mood_up"]) & set(said)
    b = brain()
    resting(b)
    b.observe(0.0, ready(mood=0.0))
    b.observe(20.0, ready(mood=0.5))
    t, clips, said = run(b, 20.0, 6)
    assert "hop" in clips and len(set(LINES["mood_up"]) & set(said)) == 1
    resting(b, t)
    b.observe(t, ready(mood=0.0))
    t, _, _ = run(b, t, 5)
    b.observe(t, ready(mood=0.5))                        # 十分钟内同一个方向只演一次
    t, clips, said = run(b, t, 10)
    assert "hop" not in clips and not set(LINES["mood_up"]) & set(said)
    resting(b, t)
    b.observe(t, ready(mood=0.0))
    t, _, _ = run(b, t, MOOD_SHIFT_GAP)
    resting(b, t)
    b.observe(t, ready(mood=0.5))
    _, clips, said = run(b, t, 6)
    assert "hop" in clips and set(LINES["mood_up"]) & set(said)


def test_a_bad_turn_sends_her_to_her_tail_and_the_adopted_art_plays_when_it_ships():
    b = brain()
    resting(b)
    b.observe(0.0, ready(mood=0.0))
    b.observe(20.0, ready(mood=-0.5))
    _, clips, said = run(b, 20.0, 8)
    assert "tail_care" in clips and set(LINES["mood_down"]) & set(said)
    for mood, clip in ((0.5, "daily_joy_tail_catch"), (-0.5, "daily_low_tail_comfort")):
        b = brain(adopted=True)
        resting(b)
        b.observe(0.0, ready(mood=0.0))
        b.observe(20.0, ready(mood=mood))
        _, clips, _ = run(b, 20.0, 8)
        assert clip in clips


def test_a_mood_turn_waits_until_your_interaction_and_real_work_are_over():
    b = brain()
    resting(b)
    b.observe(0.0, ready(mood=0.0))
    b.poke(19.0, b.x, b.y - 80)                          # 你刚戳过她：先把这一下演完
    b.observe(20.0, ready(mood=0.5))
    _, _, said = run(b, 20.0, 5)
    assert not set(LINES["mood_up"]) & set(said)
    _, clips, said = run(b, 25.0, 6)
    assert "hop" in clips and set(LINES["mood_up"]) & set(said)


def test_her_mood_shows_all_the_time_and_being_cared_for_stops_the_sighing():
    b = brain()
    resting(b)
    b.observe(0.0, ready(mood=-0.4))
    seen = set()
    t, _, _ = run(b, 0.0, 70, each=lambda t: seen.update(p.kind for p in b.particles))
    assert seen == {"sigh"}                               # 烦的时候不时叹口气
    b.warmth, b._warmth_at = 2 * CARE_GAIN["feed"], t    # 被哄好了一阵：-0.4 + 0.52
    assert b.effective_mood > -0.3
    b.particles.clear()
    seen.clear()
    run(b, t, 40, each=lambda t: seen.update(p.kind for p in b.particles))
    assert not seen
    happy = brain()
    resting(happy)
    happy.observe(0.0, ready(mood=0.6))
    notes = {}
    run(happy, 0.0, 120, each=lambda t: notes.update((p.born, p.y) for p in happy.particles
                                                     if p.kind == "note" and p.born == t))
    assert 4 <= len(notes) <= 9                           # 不时哼一句，不是一直冒
    assert all(-1.0 < y < -0.9 for y in notes.values())   # 站着时从头顶飘起
    happy._queue(Step("sit_swing", 600), now=120.0, interrupt=True)
    seated = {}
    run(happy, 120.0, 60, each=lambda t: seated.update((p.born, p.y) for p in happy.particles
                                                       if p.kind == "note" and p.born == t))
    assert seated and all(-0.7 < y < -0.55 for y in seated.values())   # 坐下后跟着头的高度走
    for kwargs, mood in (({"motion": False}, 0.6), ({}, 0.0)):
        still = brain(**kwargs)
        resting(still)
        still.observe(0.0, ready(mood=mood))
        run(still, 0.0, 60)
        assert not still.particles
    state = random.Random(7).getstate()
    quiet = brain()
    resting(quiet)
    quiet.observe(0.0, ready(mood=0.6))
    run(quiet, 0.0, 1.5)                                  # 冒了第一个音符
    assert any(p.kind == "note" for p in quiet.particles)
    assert quiet.rng.getstate() == state                  # 外显不占用随机数：不改变她原本会选什么


def test_a_suddenly_lively_group_makes_her_look_up_once():
    b = brain()
    resting(b)
    b.observe(0.0, ready(chatter=1))
    b.observe(60.0, ready(chatter=9))
    assert [p.kind for p in b.particles] == ["!"]
    assert b.bubble.text in LINES["lively"] and b.bubble.kind == "status"
    t, clips, _ = run(b, 60.0, 8)
    assert clips[:1] == ["lookaround"] and b.clip == "popcorn"
    b.observe(t, ready(chatter=1))
    b.observe(t + 30, ready(chatter=12))                  # 一刻钟内不再大惊小怪
    assert b.bubble is None or b.bubble.text not in LINES["lively"]
    resting(b, t + 30)
    b.observe(t + CHATTER_SURGE_GAP, ready(chatter=0))
    b.observe(t + CHATTER_SURGE_GAP + 30, ready(chatter=10))
    assert b.bubble.text in LINES["lively"]


def test_a_group_that_was_already_lively_or_a_bot_coming_online_is_not_a_surge():
    b = brain()
    resting(b)
    b.observe(0.0, ready(chatter=12))                     # 一连上就是热闹的
    b.observe(3.0, ready(chatter=14))
    assert b.bubble is None and b.clip == "idle"
    b.observe(6.0, ready(chatter=0, bot="offline"))       # 没在运行时的 0 条不算冷清
    b.bubble = None
    b.observe(9.0, ready(chatter=12))
    assert b.bubble is None or b.bubble.text not in LINES["lively"]
    b = brain()
    b.observe(0.0, ready(chatter=0))
    b.chat_sent(1.0)                                      # 正在和你聊：不抬头
    b.observe(30.0, ready(chatter=9))
    assert b.clip == "think" and not any(p.kind == "!" for p in b.particles)


def test_a_noisy_group_wakes_a_nap_with_a_grumble():
    b = brain()
    b.observe(0.0, ready(chatter=0))
    b._queue(Step("sleep", 600), now=0.0, interrupt=True)
    t, _, _ = run(b, 0.0, 12)
    assert b.clip == "sleep"
    b.observe(t, ready(chatter=9))
    assert b.bubble.text in LINES["woken"]
    _, clips, _ = run(b, t, 10)
    assert clips[:3] == ["wake", "grumble", "get_up"] and "popcorn" in clips
