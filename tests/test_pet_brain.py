"""桌宠大脑：纯计算的行为、物理、互动与 agent 状态映射（不需要 Qt）。"""
from collections import Counter
from importlib.resources import files
import json
import random
import unittest

from regular.pet_brain import (ASLEEP, CARE_COOL_LINES, CARE_FULL_LINES, CARE_GAIN,
                               CARE_LINES, CARE_MAX, CLIPS, CUE_ACTIONS, FIGURE, LINES, PLAY,
                               PLAY_CANCEL, PLAY_GAP, PLAY_MAX_STREAK, PLAY_START, PLAY_WINDOW,
                               TAP_POOL, PetBrain, Step)

LEGAL = {("stand", "stand"), ("sit", "sit"), ("lie", "lie"), ("air", "air"),
         ("stand", "sit"), ("sit", "stand"), ("sit", "lie"), ("lie", "sit"),
         ("air", "stand"), ("stand", "air"), ("sit", "air"), ("lie", "air")}


def brain(seed=7, **kwargs):
    b = PetBrain(rng=random.Random(seed), now=0.0, **kwargs)
    b.set_ground(0, 1440, 860)
    b.place(700)
    return b


def run(b, start, seconds, fps=30, each=None):
    t = start
    clips = []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        b.tick(t)
        if each:
            each(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
    return t, clips


def ready(mood=0.0, circadian="", chatter=0, events=(), bot="ready"):
    return {"bot": bot, "available": True, "qq_connected": True, "mood": mood,
            "circadian": circadian, "chatter": chatter, "events": list(events)}


class ShippedSpritesTest(unittest.TestCase):
    def test_every_clip_uses_frames_that_ship_in_the_package(self):
        root = files("regular").joinpath("assets", "pet")
        spec = json.loads(root.joinpath("sprites.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["schema"], 2)
        width, height = spec["frame"]
        for name, action in spec["actions"].items():
            raw = root.joinpath(action["file"]).read_bytes()
            self.assertEqual((raw[:4], raw[8:12]), (b"RIFF", b"WEBP"), name)
            self.assertLess(len(raw), 200_000, name)
        for clip, (strip, sequence, fps, loop, posture) in CLIPS.items():
            with self.subTest(clip=clip):
                self.assertIn(strip, spec["actions"])
                self.assertLess(max(sequence), spec["actions"][strip]["frames"])
                self.assertGreater(fps, 0)
                self.assertIn(posture, ("stand", "sit", "lie", "air"))
        self.assertTrue(set(CUE_ACTIONS) <= set(CLIPS))
        total = sum(len(root.joinpath(a["file"]).read_bytes()) for a in spec["actions"].values())
        # 四个新增的小幅互动动作各占约 100 KB；预算随素材一起上调，仍卡住失控增长。
        self.assertLess(total, 3_500_000)
        self.assertEqual((width, height), tuple(spec["frame"]))


class AmbientLifeTest(unittest.TestCase):
    def test_she_keeps_doing_varied_things_and_never_jumps_between_postures(self):
        b = brain(seed=3)
        b.observe(0.0, ready(mood=0.3))
        xs = []
        _, clips = run(b, 0.0, 30 * 60, each=lambda t: xs.append(b.x))
        kinds = Counter(clips)
        self.assertGreaterEqual(len(kinds), 10, kinds)
        self.assertIn("walk", kinds)
        self.assertGreater(len(clips), 60)        # ~2 changes a minute or more
        margin = FIGURE * 0.35
        self.assertTrue(all(margin <= x <= 1440 - margin for x in xs))
        for before, after in zip(clips, clips[1:]):
            self.assertIn((CLIPS[before][4], CLIPS[after][4]), LEGAL, (before, after))

    def test_queued_activities_insert_sit_and_lie_transitions(self):
        b = brain()
        b._queue(Step("tea", 10), now=1.0, interrupt=True)
        self.assertEqual([b.clip] + [s.clip for s in b.plan], ["sit_down", "tea"])
        b._queue(Step("sleep", 5), now=2.0, interrupt=True)
        self.assertEqual([b.clip] + [s.clip for s in b.plan], ["doze", "lie_down", "sleep"])
        b._set_clip("sleep", 3.0)
        b._queue(Step("pout"), now=3.0, interrupt=True)
        self.assertEqual([b.clip] + [s.clip for s in b.plan], ["wake", "get_up", "pout"])

    def test_quiet_mode_stays_put_and_reduced_motion_has_no_effects(self):
        quiet = brain(quiet=True)
        run(quiet, 0.0, 600)
        self.assertEqual(quiet.x, 700)
        still = brain(motion=False)
        _, clips = run(still, 0.0, 600)
        self.assertNotIn("walk", clips)
        self.assertNotIn("hop", clips)
        self.assertEqual(still.particles, [])
        self.assertEqual(still.transform(1.0)["sy"], 1.0)

    def test_idle_breathes_and_blinks_with_the_composited_eye_frames(self):
        b = brain()
        b._queue(Step("idle", 30), now=0.0, interrupt=True)
        frames, heights = set(), set()
        t = 0.0
        for _ in range(30 * 20):
            t += 1 / 30
            b.tick(t)
            strip, frame, mirrored, transform = b.pose(t)
            self.assertEqual(strip, "idle")
            frames.add(frame)
            heights.add(round(transform["sy"], 3))
        self.assertEqual(frames, {0, 1, 2})
        self.assertGreater(len(heights), 5)


class InteractionTest(unittest.TestCase):
    def test_dragging_hangs_her_by_the_hands_swings_and_she_falls_and_lands(self):
        b = brain()
        b.press(1.0, 700, 760)
        t = 1.0
        for i in range(1, 20):
            t += 1 / 60
            b.drag(t, 700 + i * 12, 700 - i * 8)
            b.tick(t)
        self.assertEqual((b.mode, b.clip), ("drag", "dangle"))
        self.assertGreater(b.swing, 8)                     # moving right: legs trail left
        self.assertEqual(b.pose(t)[1], 1)
        self.assertAlmostEqual(b.y - (700 - 19 * 8), FIGURE * 0.98, delta=12)
        for _ in range(30):                                # hold still: the pendulum settles
            t += 1 / 60
            b.drag(t, 700 + 19 * 12, 700 - 19 * 8)
            b.tick(t)
        self.assertEqual(b.release(t, 928, 548), "drop")
        self.assertEqual((b.mode, b.clip), ("air", "fall"))
        t, clips = run(b, t, 2.0, fps=60)
        self.assertEqual(b.mode, "ground")
        self.assertEqual(b.y, 860)
        self.assertIn("land", clips)

    def test_a_throw_upwards_bonks_the_top_of_the_screen_instead_of_leaving_it(self):
        """2026-11-06：往上甩，她飞出屏幕顶端一两秒才掉回来，看着像「中途不见了」。"""
        b = brain()
        b.set_ground(0, 1440, 860, 38)
        b.press(1.0, 700, 600)
        for i in range(6):
            b.drag(1.0 + i * 0.01, 700, 600 - i * 40)
        b.release(1.06, 700, 360)
        self.assertLess(b.vy, -1500)
        highest = [b.y]
        t, clips = run(b, 1.06, 3.0, fps=60, each=lambda t: highest.append(b.y))
        self.assertGreaterEqual(min(highest) - b.figure, 38 - 1)          # the head never leaves the screen
        self.assertTrue(any(p.kind == "sparkle" for p in b.particles) or "land" in clips)
        self.assertEqual(b.mode, "ground")

    def test_a_hard_throw_lands_grumpy(self):
        b = brain()
        b.press(1.0, 700, 300)
        for i in range(6):
            b.drag(1.0 + i * 0.01, 700, 300 + i * 30)
        b.release(1.06, 700, 450)
        b.vy = 2200
        b.y = 200
        _, clips = run(b, 1.06, 2.0, fps=60)
        self.assertIn("startle", clips)                       # 摔狠了只哆嗦一下
        self.assertNotIn("pout", clips)
        self.assertIn(b.bubble.text if b.bubble else "", LINES["thrown"] + ("",))

    def test_clicks_rotate_small_reactions_and_never_escalate_to_a_sulk(self):
        b = brain()
        b.press(1.0, 700, 760)
        self.assertEqual(b.release(1.05, 700, 760), "click")
        seen = []
        for t in (1.3, 1.9, 2.5, 3.1, 3.7, 4.3):
            b.poke(t)
            seen.append(b.clip)
            self.assertIn(b.clip, TAP_POOL)
            self.assertNotIn(b.clip, ("pout", "point"))
        self.assertEqual(len(set(seen)), len(seen))           # 连点不重样
        self.assertNotIn("!", [p.kind for p in b.particles])
        self.assertNotIn("puff", [p.kind for p in b.particles])

    def test_clicking_head_body_and_feet_gives_three_different_small_reactions(self):
        def tap(y):
            b = brain()
            left, top, right, bottom = b.body_rect()
            b.poke(1.0, (left + right) / 2, top + (bottom - top) * y)
            return b.clip
        self.assertEqual(tap(0.20), "pat")                    # 摸头
        self.assertEqual(tap(0.92), "peek")                   # 戳脚边
        self.assertIn(tap(0.60), TAP_POOL)                    # 身上：轮换的小动作

    def test_play_is_a_multi_tap_rally_not_another_random_gesture(self):
        b = brain()
        b.play(1.0)
        self.assertEqual(b.clip, "highfive")           # 她把手举起来了
        self.assertTrue(b.game_active)
        self.assertGreater(b.game_until, 1.0)
        self.assertIn(b.bubble.text, PLAY_START)
        b.poke(1.4)                                    # 赶在收手前点到她
        self.assertEqual(b.game_until, 0.0)
        self.assertEqual(b.game_streak, 1)
        self.assertEqual(b.game_best, 1)
        self.assertGreater(b.game_next_at, 1.4)        # 自动抬下一手，不用再点圆盘
        self.assertIn(b.clip, ("nod", "cheer"))
        run(b, 1.4, PLAY_GAP + 0.1)
        self.assertEqual(b.clip, "highfive")
        self.assertLess(b.game_until - 2.0, PLAY_WINDOW)   # 连击之后窗口变短
        b.poke(2.3)
        self.assertEqual(b.game_streak, 2)
        lines = []
        run(b, 2.3, 4.0,                              # 停手：结算这一轮
            each=lambda t: lines.append(b.bubble.text if b.bubble else ""))
        self.assertFalse(b.game_active)
        self.assertEqual(b.game_until, 0.0)
        self.assertEqual(b.game_streak, 0)
        self.assertEqual(b.game_best, 2)               # 纪录留着
        self.assertTrue(any("连了 2 下" in line or "2 连" in line for line in lines))

    def test_chat_cancels_a_rally_without_counting_it_as_a_loss(self):
        b = brain()
        b.play(1.0)
        b.poke(1.3)
        self.assertEqual(b.game_best, 1)
        b.chat_sent(1.5)
        self.assertFalse(b.game_active)
        self.assertEqual(b.game_until, 0.0)
        self.assertEqual(b.game_next_at, 0.0)
        self.assertEqual(b.game_best, 1)               # 不算失手，纪录留着
        self.assertTrue(b.chat_pending)

    def test_cancelling_a_rally_says_so_and_keeps_the_record(self):
        b = brain()
        b.play(1.0)
        b.poke(1.2)
        b.cancel_game(1.3)
        self.assertEqual(b.bubble.text, PLAY_CANCEL)
        self.assertFalse(b.game_active)
        self.assertEqual(b.game_best, 1)

    def test_a_full_rally_wraps_up_instead_of_holding_her_hand_forever(self):
        b = brain()
        b.play(1.0)
        t = 1.0
        for _ in range(PLAY_MAX_STREAK):
            t += 0.2
            b.poke(t)
            t = run(b, t, PLAY_GAP + 0.1)[0]
        self.assertFalse(b.game_active)
        self.assertEqual(b.game_best, PLAY_MAX_STREAK)

    def test_plain_gestures_never_dissolve_into_a_ghost(self):
        b = brain()
        b._queue(Step("pat"), Step("idle", 2), now=0.0, interrupt=True)
        t = 0.0
        for _ in range(30 * 3):
            t += 1 / 30
            b.tick(t)
            self.assertIsNone(b.pose(t)[3]["fade"])

    def test_a_seated_poke_flinches_in_place(self):
        b = brain()
        b._queue(Step("tea", 30), now=0.0, interrupt=True)
        run(b, 0.0, 2)
        self.assertEqual(b.clip, "tea")
        b.poke(3.0)
        self.assertEqual(b.clip, "tea")                       # 坐着不为小动作站起来
        self.assertLess(b.transform(3.1)["dy"], 0)
        self.assertIn(b.bubble.text, LINES["poke"])

    def test_poking_a_sleeping_pet_wakes_her_with_a_grumble(self):
        b = brain()
        b._queue(Step("sleep", 100), now=0.0, interrupt=True)
        run(b, 0.0, 12)
        self.assertEqual(b.clip, "sleep")
        b.poke(13.0)
        self.assertEqual(b.clip, "wake")
        self.assertEqual([s.clip for s in b.plan][:3], ["grumble", "get_up", "idle"])
        self.assertIn(b.bubble.text, LINES["woken"])
        self.assertLess(b.transform(13.1)["dy"], 0)          # a twitch, not a stomp

    def test_poking_her_while_she_gets_up_does_not_send_her_back_to_the_floor(self):
        b = brain()
        b._queue(Step("sleep", 100), now=0.0, interrupt=True)
        t, _ = run(b, 0.0, 9)
        self.assertEqual(b.clip, "sleep")
        b.poke(t)
        t, clips = run(b, t, 1.5)
        b.poke(t)
        self.assertIn(b.clip, ("wake", "grumble"))
        t, clips = run(b, t, 8)
        self.assertNotIn("sleep", clips)
        self.assertIn("get_up", clips)
        self.assertEqual(b.posture(), "stand")

    def test_poking_her_while_she_nods_off_seated_skips_the_lying_frames(self):
        b = brain()
        b._queue(Step("sleep", 100), now=0.0, interrupt=True)
        t, _ = run(b, 0.0, 2)
        self.assertEqual(b.clip, "doze")
        b.poke(t)
        self.assertEqual([b.clip] + [s.clip for s in b.plan][:2], ["grumble", "get_up", "idle"])

    def test_waking_frames_dissolve_but_blinks_and_flinches_stay_crisp(self):
        b = brain()
        b._queue(Step("sleep", 100), now=0.0, interrupt=True)
        t, _ = run(b, 0.0, 3)
        b.poke(t)
        fades = 0
        for _ in range(30 * 6):
            t += 1 / 30
            b.tick(t)
            fades += b.pose(t)[3]["fade"] is not None
        self.assertGreater(fades, 10)
        idle = brain()
        idle._queue(Step("idle", 30), now=0.0, interrupt=True)
        t = 0.0
        for _ in range(30 * 20):
            t += 1 / 30
            idle.tick(t)
            self.assertIsNone(idle.pose(t)[3]["fade"])
        still = brain(motion=False)
        still._queue(Step("sleep", 100), now=0.0, interrupt=True)
        t, _ = run(still, 0.0, 3)
        still.poke(t)
        for _ in range(30 * 6):
            t += 1 / 30
            still.tick(t)
            self.assertIsNone(still.pose(t)[3]["fade"])

    def test_rubbing_her_head_makes_her_happy(self):
        b = brain()
        b._queue(Step("idle", 30), now=0.0, interrupt=True)
        left, top, right, bottom = b.body_rect()
        y = top + 15
        t = 1.0
        for i in range(10):
            t += 0.1
            b.hover(t, b.x + (18 if i % 2 else -18), y, near=True)
        self.assertEqual(b.clip, "pat")
        self.assertTrue(any(p.kind == "heart" for p in b.particles))

    def test_a_nearby_cursor_stops_her_walk_and_she_looks_at_it(self):
        b = brain()
        b._queue(Step("walk", target_x=200), Step("idle", 20), now=0.0, interrupt=True)
        run(b, 0.0, 1)
        self.assertEqual(b.clip, "walk")
        b.hover(1.1, b.x - 100, 800, near=True)
        t, _ = run(b, 1.1, 1.0, each=lambda t: b.hover(t, b.x - 100, 800, near=True))
        self.assertEqual(b.clip, "look_left")
        self.assertEqual(b.facing, -1)

    def test_feeding_until_full(self):
        b = brain()
        for i in range(4):
            b.feed(1.0 + i * 5)
            self.assertEqual(b.clip, "eat")
        b.feed(30.0)
        self.assertEqual(b.clip, "peek")                      # 吃不下只歪头，不跺脚
        self.assertIn(b.bubble.text, LINES["full"])


class AgentPresenceTest(unittest.TestCase):
    def test_group_events_become_visible_behaviour_once(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "mention", "ts": 10.0}]))
        self.assertEqual(b.clip, "phone")
        self.assertIn(b.bubble.text, LINES["mention"])
        self.assertTrue(any(p.kind == "!" for p in b.particles))
        b._queue(Step("idle", 30), now=2.0, interrupt=True)
        b.observe(2.0, ready(events=[{"kind": "mention", "ts": 10.0}]))
        self.assertEqual(b.clip, "idle")                    # already seen
        b.observe(3.0, ready(events=[{"kind": "replying", "ts": 11.0}]))
        self.assertEqual(b.clip, "phone")
        run(b, 3.0, 10)
        self.assertEqual(b.clip, "phone")                   # held while the bot composes
        b.observe(14.0, ready(events=[{"kind": "said", "ts": 12.0}]))
        self.assertEqual(b.clip, "talk")
        b.observe(20.0, ready(events=[{"kind": "drew", "ts": 13.0}]))
        self.assertEqual(b.clip, "draw")
        self.assertIn(b.bubble.text, LINES["drew"])

    def test_an_at_a_name_and_a_quote_say_different_things(self):
        for kind in ("mention", "named", "quoted", "called"):
            with self.subTest(kind=kind):
                b = brain()
                b.observe(1.0, ready(events=[{"kind": kind, "ts": 10.0}]))
                self.assertEqual(b.clip, "phone")
                self.assertIn(b.bubble.text, LINES[kind])
        self.assertTrue(all("@" in line for line in LINES["mention"]))
        self.assertFalse(any("@" in line for k in ("named", "quoted", "called") for line in LINES[k]))

    def test_she_draws_the_whole_time_xiaowang_is_drawing_and_shows_it_when_it_lands(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "mention", "ts": 10.0}, {"kind": "replying", "ts": 11.0}]))
        self.assertEqual(b.clip, "phone")
        b.observe(9.0, ready(events=[{"kind": "drawing", "ts": 12.0}]))
        self.assertEqual(b.clip, "drawing")
        self.assertIn(b.bubble.text, LINES["drawing"])
        b.observe(10.0, ready(events=[{"kind": "said", "ts": 13.0}]))       # "好，我画画看" goes out first
        self.assertEqual(b.clip, "drawing")
        b.observe(20.0, ready(events=[{"kind": "mention", "ts": 14.0}, {"kind": "replying", "ts": 15.0}]))
        self.assertEqual(b.clip, "drawing")                                # only glances up
        self.assertTrue(any(p.kind == "!" for p in b.particles))
        t, clips = run(b, 20.0, 60)
        self.assertEqual(clips, ["drawing"])
        b.poke(t)
        self.assertIn(b.clip, TAP_POOL)                                    # only a small glance up
        t, _ = run(b, t, 5)
        self.assertEqual(b.clip, "drawing")                                # back to the sketchbook
        b.observe(t, ready(events=[{"kind": "drew", "ts": 16.0}]))
        self.assertEqual(b.clip, "show_drawing")
        self.assertIn(b.bubble.text, LINES["drew"])
        self.assertTrue(any(p.kind == "sparkle" for p in b.particles))
        t, clips = run(b, t, 6)
        self.assertNotIn("drawing", clips)
        self.assertIsNone(b.media)

    def test_look_it_up_then_draw_reads_as_search_then_sketchbook(self):
        """2026-11-06 群里「画个介绍新款触摸 MacBook Pro 的图」：她说过「插了句嘴」就没了下文。"""
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "mention", "ts": 10.0}, {"kind": "replying", "ts": 11.0}]))
        b.observe(26.0, ready(events=[{"kind": "said", "ts": 12.0}, {"kind": "searching", "ts": 13.0}]))
        self.assertIn(b.bubble.text, LINES["searching"])                 # not "（在群里回了一句）"
        self.assertNotIn(b.bubble.text, LINES["chimed"])
        t, clips = run(b, 26.0, 20)
        self.assertEqual(b.clip, "laptop")                                 # sits down and looks it up
        b.observe(t, ready(events=[{"kind": "drawing", "ts": 14.0}, {"kind": "said", "ts": 15.0}]))
        t, clips = run(b, t, 10)
        self.assertEqual(b.clip, "drawing")
        b.observe(t, ready(events=[{"kind": "drew", "ts": 16.0}]))
        self.assertEqual(b.clip, "show_drawing")

    def test_the_answer_after_a_search_ends_it_and_only_banter_is_called_chiming_in(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "searching", "ts": 10.0}]))
        t, _ = run(b, 1.0, 10)
        self.assertEqual(b.clip, "laptop")
        b.observe(t, ready(events=[{"kind": "said", "ts": 11.0}]))
        self.assertIsNone(b.media)
        self.assertIn(b.bubble.text, LINES["said"])
        t, clips = run(b, t, 6)
        self.assertIn("talk", clips)
        self.assertTrue(all("插" not in line for line in LINES["said"]))
        b.observe(t + 80, ready(events=[{"kind": "chimed", "ts": 12.0}]))
        self.assertIn(b.bubble.text, LINES["chimed"])
        _, clips = run(b, t + 80, 3)
        self.assertIn("talk", clips)

    def test_she_sings_until_the_voice_is_sent_and_puts_things_down_when_it_fails(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "singing", "ts": 10.0}]))
        self.assertEqual(b.clip, "sing")
        self.assertIn(b.bubble.text, LINES["singing"])
        t, clips = run(b, 1.0, 30)
        self.assertEqual(clips, ["sing"])
        self.assertTrue(any(p.kind == "note" for p in b.particles))
        b.observe(t, ready(events=[{"kind": "voice", "ts": 11.0}]))
        self.assertIn(b.bubble.text, LINES["voice"])
        t, clips = run(b, t, 3)
        self.assertEqual(b.clip, "idle")
        b.observe(t, ready(events=[{"kind": "drawing", "ts": 12.0}]))
        b.observe(t + 1, ready(events=[{"kind": "media_end", "ts": 13.0}]))
        self.assertEqual(b.clip, "idle")
        b.observe(t + 2, ready(events=[{"kind": "said", "ts": 14.0}]))   # her "没画成" line in QQ
        self.assertEqual(b.clip, "talk")

    def test_a_drawing_that_starts_mid_chat_resumes_after_and_gives_up_eventually(self):
        b = brain()
        b.chat_sent(1.0)
        b.observe(2.0, ready(events=[{"kind": "drawing", "ts": 10.0}]))
        self.assertEqual(b.clip, "think")                                  # your chat comes first
        b.chat_reply(4.0, "等我一下哈")
        t, clips = run(b, 4.0, 8)
        self.assertEqual(b.clip, "drawing")
        t, clips = run(b, t, 220, fps=10)                                  # the result never came
        self.assertNotEqual(b.clip, "drawing")
        self.assertIsNone(b.media)

    def test_she_stays_up_for_a_few_minutes_after_talking_in_the_group(self):
        """2026-11-06 下午：回完一句就挑了打盹，接着画好的图送达，她睡过去了。"""
        def naps(event, seed):
            b = brain(seed=seed)
            b.observe(1.0, ready(circadian="午后有点困", events=[event] if event else []))
            _, clips = run(b, 1.0, 170, fps=10)
            return bool({"doze", "lie_down", "sleep"} & set(clips))
        quiet = sum(naps(None, seed) for seed in range(30))
        busy = sum(naps({"kind": "said", "ts": 10.0}, seed) for seed in range(30))
        self.assertGreater(quiet, 0)        # an afternoon nap does happen when the group is quiet
        self.assertEqual(busy, 0)

    def test_whatever_xiaowang_does_in_qq_wakes_a_napping_pet(self):
        b = brain()
        b.observe(1.0, ready())
        b._queue(Step("sleep", 900.0), now=1.0, interrupt=True)
        t, _ = run(b, 1.0, 12)
        self.assertEqual(b.clip, "sleep")
        b.observe(t, ready(events=[{"kind": "replying", "ts": 10.0}]))     # not @-ed: feedback repair
        t, clips = run(b, t, 6)
        self.assertEqual(clips[:3], ["wake", "get_up", "phone"])
        b._queue(Step("sleep", 900.0), now=t, interrupt=True)
        t, _ = run(b, t, 12)
        self.assertIn(b.clip, ASLEEP)
        b.observe(t, ready(events=[{"kind": "drew", "ts": 11.0}]))
        self.assertIn(b.bubble.text, LINES["drew"])
        _, clips = run(b, t, 6)
        self.assertIn("draw", clips)

    def test_events_wait_while_she_is_held_or_waiting_for_a_local_reply(self):
        b = brain()
        b.chat_sent(1.0)
        b.observe(1.5, ready(events=[{"kind": "drew", "ts": 1.0}]))
        self.assertEqual(b.clip, "think")
        self.assertEqual((b.bubble.kind, b.bubble.text), ("think", "…"))
        b.chat_reply(5.0, "回来啦，刚在想你说的事。")
        self.assertEqual(b.clip, "talk")
        self.assertEqual((b.bubble.kind, b.bubble.text), ("say", "回来啦，刚在想你说的事。"))
        b.chat_failed(9.0, "本机对话暂时不可用")
        self.assertEqual(b.bubble.kind, "status")

    def test_bot_offline_puts_her_to_sleep_and_online_wakes_her(self):
        b = brain()
        b.observe(1.0, ready())
        b.observe(2.0, ready(bot="offline"))
        self.assertIn(b.bubble.text, LINES["offline"])
        t, clips = run(b, 2.0, 20)
        self.assertEqual(b.clip, "sleep")
        b.observe(t, ready())
        self.assertEqual(b.clip, "wake")
        self.assertIn(b.bubble.text, LINES["online"])

    def test_qq_offline_does_not_interrupt_a_local_chat_or_its_reply(self):
        b = brain()
        b.observe(0.0, ready())
        b.chat_sent(1.0)
        b.observe(2.0, {**ready(bot="offline"), "state": "inspect"})
        self.assertEqual(b.clip, "think")
        self.assertEqual(b.state_source, "local")
        self.assertTrue(b.chat_pending)
        b.chat_reply(3.0, "我在这里和你聊。")
        b.observe(3.1, ready())
        b.observe(3.2, ready(bot="offline"))
        self.assertEqual(b.clip, "talk")
        self.assertNotIn("sleep", [s.clip for s in b.plan])
        b.chat_failed(4.0, "这次没接上。")
        self.assertIsNone(b.state_source)

    def test_mood_and_night_shift_what_she_chooses(self):
        def tally(**presence):
            b = brain(seed=11)
            b.observe(0.0, ready(**presence))
            _, clips = run(b, 0.0, 40 * 60)
            return Counter(clips)
        happy = tally(mood=0.9)
        grumpy_night = tally(mood=-0.9, circadian="熬到半夜")
        self.assertGreater(grumpy_night["lookaround"] + grumpy_night["sleep"],
                           happy["lookaround"] + happy["sleep"])
        self.assertGreater(happy["hop"] + happy["sing"] + happy["cheer"],
                           grumpy_night["hop"] + grumpy_night["sing"] + grumpy_night["cheer"])

    def test_cues_accept_only_known_actions_and_short_text(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "cue", "id": "a", "ts": 1.0, "action": "rm -rf", "text": ""}]))
        self.assertEqual(b.clip, "idle")
        b.observe(2.0, ready(events=[{"kind": "cue", "id": "b", "ts": 2.0, "action": "cheer",
                                      "text": "测试全过了！" + "长" * 300}]))
        self.assertEqual(b.clip, "cheer")
        self.assertTrue(b.bubble.text.startswith("测试全过了！"))
        self.assertLessEqual(len(b.bubble.text), 120)

    def test_malformed_presence_is_ignored_safely(self):
        b = brain()
        for payload in (None, [], {"mood": float("nan"), "chatter": "9", "bot": "<script>", "events": "x"},
                        {"mood": True, "events": [None, 3, {"kind": "said"}]}):
            b.observe(1.0, payload)
        self.assertEqual(b.presence.mood, 0.0)
        self.assertIn(b.presence.bot, ("unknown", "ready", "offline"))
        self.assertNotIn(b.clip, ASLEEP)


class CareMoodTest(unittest.TestCase):
    """摸头 / 喂饼干真的会改心情，但刷不出满心情、也不能一直刷。"""

    @staticmethod
    def head(b):
        left, top, right, bottom = b.body_rect()
        return (left + right) / 2, top + (bottom - top) * 0.2

    def test_petting_and_feeding_really_lift_mood_without_touching_read_only_presence(self):
        b = brain()
        b.observe(0.0, ready(mood=-0.4))
        x, y = self.head(b)
        self.assertEqual(b.effective_mood, -0.4)
        b.poke(1.0, x, y)
        self.assertAlmostEqual(b.warmth, CARE_GAIN["pat"])
        self.assertGreater(b.effective_mood, -0.4)
        self.assertIn(b.bubble.text, CARE_LINES["pat"])
        b.feed(2.0)
        self.assertGreater(b.warmth, CARE_GAIN["pat"])
        self.assertIn(b.bubble.text, CARE_LINES["feed"])
        self.assertEqual(b.presence.mood, -0.4)      # 群聊统计始终是只读的

    def test_repeating_the_same_care_within_cooldown_does_not_stack(self):
        b = brain()
        x, y = self.head(b)
        b.poke(1.0, x, y)
        first = b.warmth
        b.poke(3.0, x, y)
        self.assertLessEqual(b.warmth, first)     # 只会自然衰减，不会叠加
        self.assertGreater(b.warmth, 0.0)
        self.assertIn(b.bubble.text, (*CARE_COOL_LINES["pat"], *PLAY["pat"][1]))
        b.poke(46.0, x, y)                            # 45 秒冷却过了
        self.assertGreater(b.warmth, first)

    def test_hover_and_click_head_pats_share_the_same_gain_and_cooldown(self):
        b = brain()
        x, y = self.head(b)
        for i in range(10):
            b.hover(1.0 + i * .1, x + (18 if i % 2 else -18), y, near=True)
        self.assertEqual(b.clip, "pat")
        self.assertAlmostEqual(b.warmth, CARE_GAIN["pat"])
        self.assertIn(b.bubble.text, CARE_LINES["pat"])
        first = b.warmth
        b.poke(3.0, x, y)
        self.assertLessEqual(b.warmth, first)
        self.assertGreater(b.warmth, 0.0)
        b._queue(Step("idle", 30), now=4.0, interrupt=True)
        for i in range(10):
            b.hover(4.0 + i * .1, x + (18 if i % 2 else -18), y, near=True)
        self.assertLessEqual(b.warmth, first)

    def test_warmth_has_a_cap_and_says_so(self):
        b = brain()
        x, y = self.head(b)
        for i in range(12):
            b.poke(1.0 + i * 60.0, x, y)             # 每次都跳过冷却
            self.assertLessEqual(b.warmth, CARE_MAX + 1e-6)
        self.assertEqual(b.warmth, CARE_MAX)
        self.assertIn(b.bubble.text, CARE_FULL_LINES)

    def test_warmth_fades_back_and_never_rewrites_the_group_mood(self):
        b = brain()
        b.observe(0.0, ready(mood=-0.4))
        x, y = self.head(b)
        b.poke(1.0, x, y)
        b.tick(301.0)
        self.assertEqual(b.warmth, 0.0)
        self.assertEqual(b.effective_mood, -0.4)
        self.assertEqual(b.presence.mood, -0.4)

    def test_overfeeding_does_not_lift_mood(self):
        b = brain()
        for i in range(5):
            b.feed(1.0 + i * 0.5)
        self.assertLessEqual(b.warmth, CARE_GAIN["feed"] + 1e-6)
        self.assertIn(b.bubble.text, LINES["full"])

    def test_a_lifted_mood_really_changes_what_she_chooses_to_do(self):
        def tally(warmth):
            b = brain(seed=11)
            b.observe(0.0, ready(mood=0.0))
            b.warmth = warmth
            counts = Counter()
            for _ in range(400):
                counts[b._choose(0.0)[0].clip] += 1
            return counts
        calm, happy = tally(0.0), tally(CARE_MAX)
        lively = ("hop", "sing", "cheer")
        self.assertGreater(sum(happy[k] for k in lively), sum(calm[k] for k in lively))


if __name__ == "__main__":
    unittest.main()
