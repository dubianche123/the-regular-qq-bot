# -*- coding: utf-8 -*-
"""活人感：歇着的时候也是活的，而且留意你。

2026-11-15 歇着成了常态之后，群主要「参考别的桌宠，增加活人感的交互」。常见做法（Desktop Mate、
Mate Engine、VPet）是视线跟着光标、待机时眨眼、睡觉有睡相；陪伴型桌面小物的经验是安静待着，
在合适的时候主动看你一眼。守这几条：
  · 坐着歇是真的静：同一张画，只眨眼，光标过来拿眼睛跟一下，不转身、不站起来；偶尔才荡几下腿；
  · 睡长觉隔一阵翻个身，换一种躺着的睡姿；每种睡姿都冒 z，z 从躺着的头顶冒；
  · 你连着干活一刻钟后停下来，她正歇着就抬头看你一眼，半小时最多一次，不说话；
  · 你在埋头干活时，她多挑看书、电脑、画画陪着你，少在旁边蹦跶。
依据仍只是「键盘鼠标多久没动」一个数，不看你在做什么。
"""
import random
from unittest.mock import patch

from regular.pet_brain import (ASLEEP, COWORK_AFTER, OWNER_AWAY, PAUSE_LOOK,
                               PAUSE_LOOK_GAP, PLAY_CHOICES, TURN_OVER, WORK_STRETCH, PetBrain, Step)


def brain(seed=7, *, adopted=False):
    b = PetBrain(rng=random.Random(seed), now=0.0)
    if not adopted:
        b.repertoire.actions = {}
    b.set_ground(0, 1440, 860)
    b.place(700)
    b.observe(0.0, {"bot": "ready", "available": True, "qq_connected": True, "mood": 0.0,
                    "circadian": "", "chatter": 1, "events": []})
    return b


def run(b, start, seconds, *, fps=10, idle=0.0):
    t, frames = start, []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        b.owner_idle(t, idle)
        b.tick(t)
        frames.append((b.clip, b._frame(t)[1]))
    return t, frames


def sit_resting(b, now=0.0, seconds=200):
    b._queue(Step("sit_rest", seconds), now=now, interrupt=True)
    t, _ = run(b, now, 2)          # sit_down, then the still seated rest
    assert b.clip == "sit_rest"
    return t


# ── 坐着歇 ──
def test_a_seated_rest_holds_still_and_only_blinks():
    b = brain()
    t = sit_resting(b)
    _, frames = run(b, t, 30)
    assert {clip for clip, _ in frames} == {"sit_rest"}
    shown = [frame for _, frame in frames]
    assert shown.count(0) > 0.9 * len(shown)                    # eyes open almost all the time
    assert {1, 2} & set(shown)                                  # but she does blink


def test_seated_eyes_follow_the_cursor_without_turning_round():
    b = brain()
    t = sit_resting(b)
    b.facing = 1
    b.hover(t, b.x + b.figure, b.y - b.figure * 0.3, near=True)
    t, _ = run(b, t, 1)
    assert b.clip == "sit_look_right" and b.facing == 1
    b.hover(t, b.x - b.figure, b.y - b.figure * 0.3, near=True)
    t, _ = run(b, t, 1)
    assert b.clip == "sit_look_left" and b.facing == 1
    b.facing = -1                     # drawn mirrored: looking at screen-left is the art's right
    t, _ = run(b, t, 1)
    assert b.clip == "sit_look_right"
    b.hover(t, b.x - b.figure * 4, b.y, near=False)
    t, _ = run(b, t, 1)
    assert b.clip == "sit_rest"


def test_a_seated_rest_only_sometimes_swings_her_legs():
    b = brain()
    b._queue(Step("tea", 5), now=0.0, interrupt=True)
    plans = []
    for _ in range(200):
        b.plan.clear()
        b.step = Step("tea", 5)
        plans.append([step.clip for step in b._rest_steps(b.vitals())])
    swinging = sum("sit_swing" in plan for plan in plans)
    assert all(plan[0] == "sit_rest" for plan in plans)
    assert 0.3 * len(plans) < swinging < 0.7 * len(plans)


# ── 睡相 ──
def test_a_long_sleep_turns_over_without_waking_or_talking():
    b = brain(adopted=True)
    b.owner_away = True
    clip = b._pick("sleep", "sleep", posture="lie")
    b._queue(Step(clip, hold="away"), now=0.0, interrupt=True)
    t, frames = run(b, 0.0, TURN_OVER[1] + 60, fps=2, idle=OWNER_AWAY + 60)
    poses = []
    for name, _ in frames:
        if not poses or poses[-1] != name:
            poses.append(name)
    assert len(poses) >= 2 and all(name in ASLEEP for name in poses), poses
    assert b.bubble is None


def test_every_sleeping_pose_lets_out_zs_from_where_her_head_is():
    b = brain(adopted=True)
    clip = next(name for name, spec in b.repertoire.actions.items()
                if spec["role"] == "sleep" and spec["posture"] == "lie")
    b._queue(Step(clip, 60), now=0.0, interrupt=True)
    run(b, 0.0, 14)
    assert b.clip == clip
    zs = [particle for particle in b.particles if particle.kind == "z"]
    assert zs and all(-0.95 < particle.y < 0 for particle in zs)


# ── 留意你的节奏 ──
def working(b, start, seconds):
    t = start
    for _ in range(int(seconds / 2)):
        t += 2.0
        b.owner_idle(t, 0.5)
        b.tick(t)
    return t


def pause(b, start, seconds):
    t, looked = start, False
    for step in range(int(seconds * 2)):
        t += 0.5
        b.owner_idle(t, (step + 1) * 0.5)
        b.tick(t)
        looked |= b.clip in ("peek", "sit_look_left", "sit_look_right")
    return t, looked


def test_after_a_long_stretch_of_work_a_pause_makes_her_look_up_once():
    b = brain()
    b._queue(Step("idle", 3000), now=0.0, interrupt=True)
    t = working(b, 0.0, WORK_STRETCH + 30)
    t, looked = pause(b, t, PAUSE_LOOK + 5)
    assert looked and b.bubble is None
    t = working(b, t, 120)
    _, again = pause(b, t, PAUSE_LOOK + 5)                    # within half an hour: not again
    assert not again
    t = working(b, t, PAUSE_LOOK_GAP)
    _, later = pause(b, t, PAUSE_LOOK + 5)                    # half an hour later it may happen again
    assert later


def test_a_short_stretch_or_a_busy_moment_gets_no_look():
    short = brain()
    short._queue(Step("idle", 3000), now=0.0, interrupt=True)
    _, looked = pause(short, working(short, 0.0, WORK_STRETCH / 3), PAUSE_LOOK + 5)
    assert not looked
    busy = brain()
    busy._queue(Step("read", 3000), now=0.0, interrupt=True)        # in the middle of reading
    _, looked = pause(busy, working(busy, 0.0, WORK_STRETCH + 30), PAUSE_LOOK + 5)
    assert not looked and busy.clip == "read"


def test_while_you_work_she_keeps_you_company_rather_than_playing():
    def weights(work):
        b = brain()
        if work:
            working(b, 0.0, COWORK_AFTER + 30)
        seen = {}
        b._ambient_recent.clear()                 # compare full weights, not what she just did
        with patch.object(b, "_weighted", side_effect=lambda w: seen.update(w) or "book"):
            b._owes_rest = False
            b._choose(COWORK_AFTER + 40)
        return seen

    idle, busy = weights(False), weights(True)
    for name in ("book", "laptop", "sketch"):
        assert busy[name] > 1.5 * idle[name]
    assert all(busy[name] < idle[name] for name in PLAY_CHOICES - {"sketch"} if idle.get(name))


# 整张人物图的呼吸缩放 11-08 为统一人物大小特意去掉了（docs/pet-repertoire.md），这里不加回来。
def test_resting_does_not_scale_her_whole_figure():
    b = brain()
    b._queue(Step("idle", 60), now=0.0, interrupt=True)
    assert all((b.transform(t / 10)["sx"], b.transform(t / 10)["sy"]) == (1.0, 1.0) for t in range(60))
