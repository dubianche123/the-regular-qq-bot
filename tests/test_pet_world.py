# -*- coding: utf-8 -*-
"""她和屏幕外的世界（2026-11-15）：起飞、光标、屏幕边、天气、魔法的可读性。

群主的几条反馈落成这里的约束：
  · 起飞频率异常高：平常用鼠标（路过、手抖、微调）不会让她扑过去或追着飞起来，只有故意来回晃才会；
  · 起飞僵硬：先屈膝展翼、蹬地离地，爬升时快拍，之后每段接着上一段的速度飞，不在拐点停住；
  · 光标路过不只会扑：停在头顶她踮脚去够，停在脸前她戳一戳，在尾巴边逗留她甩尾巴，绕着她转会晕；
    都只在她歇着时发生、隔得很开，反应完接着歇；
  · 屏幕边偶尔有新花样：背靠屏幕边、敲敲屏幕边、倒挂在屏幕顶上，二十五分钟最多一回；
  · 天气联动：下雪接雪花，太热瘫一会儿再冰可乐，太冷呵火暖手再烤棉花糖或烘围巾；天气刚变时先演这个；
  · 魔法看得懂：开场说出想做什么，吹气、火苗、金线由引擎在那一拍发出来，帧与帧之间轻轻过渡。
"""
import math
import random
from unittest.mock import patch

import pytest

from regular.pet_brain import (CLIPS, CURSOR_LINGER, CURSOR_PLAY_REPEAT, EDGE_MOMENT_GAP, RESTING,
                               WEATHER_GAP, PetBrain, Step, _flight_ease)


def brain(seed=7, *, x=700):
    b = PetBrain(rng=random.Random(seed), now=0.0)
    b.set_ground(0, 1440, 860, 25)
    b.place(x)
    b.observe(0.0, {"bot": "ready", "available": True, "qq_connected": True, "mood": 0.0,
                    "circadian": "", "chatter": 1, "events": []})
    return b


def resting(b, now=0.0):
    b._queue(Step("idle", 600), now=now, interrupt=True)
    b.tick(now + 0.05)
    return now + 0.05


def hover_for(b, start, seconds, x, y, *, hz=30, near=True):
    t = start
    for _ in range(int(seconds * hz)):
        t += 1 / hz
        b.hover(t, x, y, near=near)
        b.tick(t)
    return t


# ── 起飞频率：平常用鼠标不算逗她 ──
def test_ordinary_mouse_use_near_her_never_starts_a_pounce_or_a_chase():
    b = brain()
    rng = random.Random(3)
    t = resting(b)
    f = b.figure
    # Tremor while the cursor rests in front of her and above her head, then plain passes across.
    for cx, cy in ((b.x + f * 1.2, b.floor - f * 0.6), (b.x, b.floor - f * 2.0), (b.x - f * 1.5, b.floor - f * 1.0)):
        for _ in range(300):
            t += 1 / 30
            b.hover(t, cx + rng.gauss(0, 1.2), cy + rng.gauss(0, 1.2), near=False)
            b.tick(t)
    for sweep in range(6):
        for i in range(60):
            t += 1 / 30
            b.hover(t, 100 + i * 21 if sweep % 2 else 1340 - i * 21, b.floor - f * (0.4 + 0.3 * sweep), near=False)
            b.tick(t)
    assert b._pounce_at < 0 and not b.air_play_active
    assert not b.clip.startswith(("pounce", "air_play"))


# ── 起飞与飞行 ──
def test_takeoff_crouches_pushes_off_then_climbs_on_quick_beats():
    b = brain()
    with patch.object(b.rng, "random", return_value=1):
        steps = b._flight_steps(0)
    assert [s.clip for s in steps[:2]] == ["motion_fly_launch_crouch", "motion_fly_launch_lift"]
    b._queue(*steps, Step("idle", 30), now=0, interrupt=True)
    t, clips, start_y = 0.0, [], b.y
    while t < 30 and b.mode != "ground" or t < 1:
        t += 1 / 60
        b.tick(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
        if b.step is steps[1] and b.clip == "motion_fly_flap_quick":
            break
    assert clips[:3] == ["motion_fly_launch_crouch", "motion_fly_launch_lift", "motion_fly_flap_quick"]
    assert b.y < start_y                      # already off the ground when the quick beats take over


def test_the_climb_rises_fast_while_forward_speed_builds():
    x, y = _flight_ease("launch", 0.25)
    assert y > 0.4 and x < 0.1
    assert _flight_ease("land", 1.0) == (1.0, 1.0) and _flight_ease("cruise", 0.5)[0] == 0.5


def test_flight_keeps_its_forward_speed_through_the_waypoints():
    b = brain(x=300)
    with patch.object(b.rng, "random", return_value=1):
        steps = b._flight_steps(0)
    b._queue(*steps, Step("idle", 30), now=0, interrupt=True)
    t, samples = 0.0, []
    while t < 60 and not (b.mode == "ground" and b.clip == "idle" and t > 2):
        t += 1 / 60
        x = b.x
        b.tick(t)
        if b.mode == "fly":
            samples.append((b.step, (b.x - x) * 60))
    legs = [s for s in steps[1:4]]
    speeds = [abs(v) for step, v in samples if step is legs[1]]
    cruise = sum(speeds) / len(speeds)
    for before, after in ((legs[0], legs[1]), (legs[1], legs[2])):
        end = [abs(v) for step, v in samples if step is before][-3:]
        begin = [abs(v) for step, v in samples if step is after][1:4]
        assert min(end + begin) > 0.6 * cruise    # no stop-and-go at the waypoint


# ── 光标的其他玩法 ──
@pytest.mark.parametrize("kind,dx,up,seconds", [
    ("reach_up", 0.0, 1.2, CURSOR_LINGER + 0.3),
    ("poke", 0.3, 0.75, CURSOR_LINGER + 0.3),
    ("tail_swat", -0.4, 0.25, 0.9),
])
def test_a_cursor_that_stays_at_her_head_face_or_tail_gets_its_own_reaction(kind, dx, up, seconds):
    b = brain()
    b.facing = 1
    t = resting(b)
    f = b.figure
    with patch.object(b.rng, "random", return_value=0.0):
        hover_for(b, t, seconds, b.x + dx * f, b.floor - up * f)
    assert b.clip == "cursor_" + kind
    assert b._owes_rest                     # afterwards she goes back to resting


def test_circling_her_twice_makes_her_dizzy():
    b = brain()
    t = resting(b)
    f = b.figure
    cx, cy = b.x, b.y - f * 0.5
    for i in range(90):                      # 2.25 turns in 3 s
        t += 1 / 30
        a = i / 90 * 2.25 * 2 * math.pi
        b.hover(t, cx + math.cos(a) * f, cy + math.sin(a) * f, near=False)
        b.tick(t)
        if b.clip == "cursor_dizzy":
            break
    assert b.clip == "cursor_dizzy"


def test_cursor_reactions_are_spaced_out_and_only_while_resting():
    b = brain()
    t = resting(b)
    f = b.figure
    with patch.object(b.rng, "random", return_value=0.0):
        t = hover_for(b, t, CURSOR_LINGER + 0.3, b.x, b.floor - f * 1.2)
        assert b.clip == "cursor_reach_up"
        t = resting(b, t + 5)
        t = hover_for(b, t, CURSOR_LINGER + 0.3, b.x, b.floor - f * 1.2)
        assert b.clip != "cursor_reach_up"             # cooldown
    busy = brain()
    busy._queue(Step("read", 300), now=0.0, interrupt=True)
    with patch.object(busy.rng, "random", return_value=0.0):
        hover_for(busy, 0.1, CURSOR_LINGER + 0.5, busy.x, busy.floor - busy.figure * 1.2)
    assert busy.clip == "read"
    assert CURSOR_PLAY_REPEAT >= 600


# ── 屏幕边的小时刻 ──
def run_until(b, start, done, limit=60.0):
    t = start
    while t < start + limit and not done(b):
        t += 1 / 30
        b.tick(t)
    return t


@pytest.mark.parametrize("name,x", [("edge_wall_lean", 120), ("edge_knock", 1320)])
def test_she_leans_on_or_knocks_at_the_real_screen_border(name, x):
    b = brain(x=x)
    with patch.object(b.rng, "choice", side_effect=lambda seq: name if name in seq else seq[0]):
        steps = b._edge_moment_steps(0.0)
    b._queue(*steps, now=0.0, interrupt=True)
    run_until(b, 0.0, lambda b: b.clip == name)
    assert b.clip == name
    bounds = b.repertoire.actions[name]["geometry"]["bounds"]
    k = b.figure / 320
    left, right = (b.x + bounds[0] * k, b.x + bounds[2] * k) if b.facing > 0 else (b.x - bounds[2] * k, b.x - bounds[0] * k)
    if name == "edge_wall_lean":
        assert b.facing == 1 and left == pytest.approx(b.left, abs=2)      # her back against the left border
    else:
        assert b.facing == 1 and right == pytest.approx(b.right, abs=2)    # knuckles on the right border


def test_she_hangs_from_the_top_like_a_bat_then_flies_back_down():
    b = brain()
    with patch.object(b.rng, "choice", side_effect=lambda seq: "edge_bat_hang" if "edge_bat_hang" in seq else seq[0]):
        steps = b._edge_moment_steps(0.0)
    b._queue(*steps, now=0.0, interrupt=True)
    t = run_until(b, 0.0, lambda b: b.clip == "edge_bat_hang" and b.step.clip == "edge_bat_hang"
                  and abs(b.y - b.step.target_y) < 1.5)
    top = b.y + b.repertoire.actions["edge_bat_hang"]["geometry"]["bounds"][1] * b.figure / 320
    assert top == pytest.approx(b.ceiling, abs=b.figure * 0.08)          # shoes at the top of the screen
    run_until(b, t, lambda b: b.mode == "ground" and b.clip == "idle")
    assert b.mode == "ground" and b.y == b.floor


def test_edge_moments_are_rare():
    b = brain()
    b._edge_moment_at = 0.0
    seen = {}
    with patch.object(b, "_weighted", side_effect=lambda w: seen.update(w) or "idle"):
        b._spell = "standing"
        b._pastime(EDGE_MOMENT_GAP - 1, b.vitals(), "standing", engaged=False, offline=False, mood=0.0)
    assert seen.get("edge_moment", 0) == 0


# ── 天气 ──
def weather(scene, **extra):
    return {"weather": {"sky": "clear", "temp": 20, "feels": 20, "is_day": True, "scene": scene, **extra}}


def test_weather_change_brings_its_vignette_into_the_next_pastime():
    b = brain()
    b.observe(1.0, weather(None))
    b._weather_at = -WEATHER_GAP
    b.observe(2.0, weather("snow"))
    assert b.weather_scene == "snow" and b._weather_due is not None
    b._owes_rest = False
    steps = b._choose(b._weather_due + 1)
    clips = [s.clip for s in steps]
    assert "weather_snow_catch" in clips
    assert any(s.line == "下雪啦！" for s in steps)


@pytest.mark.parametrize("scene,first,then", [
    ("hot", "weather_heat_melt", ("magic_daily_cola",)),
    ("cold", "weather_warm_hands", ("magic_daily_roast", "magic_daily_scarf")),
    ("sun", "weather_sunbask", ()),
    ("rain", "weather_wing_umbrella", ()),
])
def test_hot_cold_and_sunny_days_have_their_own_scenes(scene, first, then):
    b = brain()
    b.observe(1.0, weather(scene))
    steps = b._weather_steps(10.0)
    assert steps[0].clip == first
    if then:
        assert steps[1].clip in then


def test_magic_follows_the_weather_and_says_what_she_wants():
    b = brain()
    b._magic_dragon_shown = True
    b.observe(1.0, weather("hot"))
    assert b.magic(2.0)
    first = b.step if b.step.clip == "magic_daily_cola" else next(s for s in b.plan if s.clip.startswith("magic_daily"))
    assert first.clip == "magic_daily_cola" and first.line == "好热……来点冰的！"


def test_no_city_means_no_weather_scenes():
    b = brain()
    b.observe(1.0, {"weather": None})
    assert b.weather_scene is None and not b._weather_ready(5000.0)


# ── 魔法看得懂 ──
def test_magic_breath_and_frost_come_from_the_engine_on_their_beat():
    b = brain()
    b.facing = -1
    b._queue(Step("magic_daily_cola"), now=0.0, interrupt=True)
    t, kinds = 0.0, set()
    fades = 0
    while b.clip == "magic_daily_cola" and t < 10:
        t += 1 / 30
        b.tick(t)
        kinds |= {p.kind for p in b.particles}
        fades += b._fade is not None
        if b._frame(t)[1] == 3:
            frost = [p for p in b.particles if p.kind == "frost"]
            assert frost and all(p.x < 0 for p in frost)     # mirrored with her
    assert {"frost", "sweat"} <= kinds
    assert fades > 0                                         # the six poses flow into each other


def test_snow_falls_around_her_while_she_catches_flakes():
    b = brain()
    b._queue(Step("weather_snow_catch", 10), now=0.0, interrupt=True)
    t = 0.0
    for _ in range(90):
        t += 1 / 30
        b.tick(t)
    snow = [p for p in b.particles if p.kind == "snow"]
    assert snow and all(p.vy > 0 for p in snow)


def test_cursor_reactions_happen_from_rest_only():
    assert {"idle", "look_left", "look_right"} <= RESTING
    assert all(CLIPS["cursor_" + kind] for kind in ("reach_up", "poke", "tail_swat", "dizzy"))
