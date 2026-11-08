"""桌宠大脑：纯计算的行为、物理、互动与 agent 状态映射（不需要 Qt）。"""
from collections import Counter
from importlib.resources import files
import json
import random
import unittest
from unittest.mock import patch

from regular.pet_routines import EPISODES, choose_routine
from regular.pet_repertoire import ACTIONS, curated_actions
from regular.pet_brain import (ASLEEP, CARE_COOL_LINES, CARE_FULL_LINES, CARE_GAIN,
                               CARE_LINES, CARE_MAX, CLIPS, CUE_ACTIONS, EDGE, EDGE_PEEK, FIGURE, LINES, PLAY,
                               PLAY_CANCEL, PLAY_GAP, PLAY_MAX_STREAK, PLAY_START, PLAY_WINDOW,
                               TAP_POOL, WALK_SPEED, DRAGON_TRICKS, PetBrain, Step)

LEGAL = {("stand", "stand"), ("sit", "sit"), ("lie", "lie"), ("air", "air"),
         ("stand", "sit"), ("sit", "stand"), ("sit", "lie"), ("lie", "sit"),
         ("air", "stand"), ("stand", "air"), ("sit", "air"), ("lie", "air")}


def brain(seed=7, **kwargs):
    b = PetBrain(rng=random.Random(seed), now=0.0, **kwargs)
    # Existing deterministic scenarios exercise the legacy fallback with no optional strips.
    b.repertoire.actions = {}
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


class MotionRepertoireTest(unittest.TestCase):
    def test_restful_energy_only_for_uninterrupted_loop(self):
        for looping, working, expected in ((True, False, 0.1 / 600),
                                           (False, False, -0.1 / 2400),
                                           (True, True, -0.1 / 2400)):
            with self.subTest(looping=looping, working=working):
                b = brain()
                b.energy = 0.5
                b.repertoire.actions["test_rest"] = {"restful": True}
                b.chat_pending = working
                with patch.dict(CLIPS, {"test_rest": ("idle", [0], 1, looping, "stand")}), patch.object(b, "_ground"):
                    b._set_clip("test_rest", 0, restart=True)
                    b.tick(0.1)
                self.assertAlmostEqual(b.energy, 0.5 + expected)

    def test_home_routine_counts_as_rest_without_replacing_entry(self):
        b = brain()
        beats = [("scene_chair_entry", None), ("scene_chair_rest", 60), ("scene_chair_exit", None)]
        with patch("regular.pet_brain.choose_routine", return_value=("home:chair", beats)):
            result = b._routine_steps(200)
        self.assertEqual([step.clip for step in result[:3]], [beat[0] for beat in beats])
        self.assertEqual(b._rests_since_story, 1)
        self.assertNotEqual(b._last_story_at, 200)

    def test_furniture_shares_one_cooldown_across_chair_sofa_and_both_modes(self):
        for first_mode in ("rest", "story"):
            with self.subTest(mode=first_mode):
                b = PetBrain(rng=random.Random(1), now=0)
                b.repertoire.actions = {name: ACTIONS[name] for family in ("chair", "sofa")
                                        for branch in EPISODES[family] for name, _ in branch}
                if first_mode == "story":
                    b._last_story_at, b._rests_since_story = -180, 2
                with patch.object(b.rng, "random", return_value=0):
                    first = b._routine_steps(0)
                    self.assertIn(first[0].clip, ("scene_chair_entry", "scene_sofa_entry"))
                    self.assertEqual(b._furniture_at, 0)
                    self.assertEqual([step.clip for step in b._routine_steps(1799)], ["idle"])
                    second = b._routine_steps(1800)
                    self.assertIn(second[0].clip, ("scene_chair_entry", "scene_sofa_entry"))
                    self.assertNotEqual(first[0].clip, second[0].clip)
                    manual = b._routine_steps(1801, manual=True)
                    self.assertIn(manual[0].clip, ("scene_chair_entry", "scene_sofa_entry"))
                    self.assertEqual(b._furniture_at, 1801)

    def test_pounce_uses_miss_only_when_pointer_has_moved_away(self):
        b = brain()
        b.repertoire.actions["scene_pounce_miss"] = {"role": "scene", "family": "viewer", "posture": "stand"}
        with patch.dict(CLIPS, {"scene_pounce_miss": ("stand", [0, 1, 2, 3], 6, False, "stand")}):
            b.pointer.seen = True
            b.pointer.x, b.pointer.y = b.x + b.figure * .25, b.floor - b.figure * .45
            b._landing = "pounce_catch"
            b._land_trick(10)
            self.assertEqual(b.clip, "pounce_catch")
            b.pointer.x = b.right
            b._landing = "pounce_catch"
            b._land_trick(20)
            self.assertEqual(b.clip, "scene_pounce_miss")

    def test_scenes_have_no_separate_menu_and_play_inside_daily_routines(self):
        b = PetBrain(rng=random.Random(7), now=0)
        self.assertFalse(hasattr(b, "perform_routine"))
        if "scene_sweet_share" not in b.repertoire.actions:
            self.skipTest("personality scenes not adopted")
        families = set()
        for seed in range(80):
            routine = choose_routine(random.Random(seed), b.repertoire.actions, mode="story")
            families.add(routine[0])
        self.assertTrue(any(name.startswith("scene:") for name in families))
        self.assertTrue(all(name.startswith("scene:") for name in families))

    def test_adopted_scene_start_posture_prevents_extra_bridge(self):
        b = PetBrain(rng=random.Random(7), now=0)
        if "scene_game_win" not in b.repertoire.actions:
            self.skipTest("game result not adopted")
        b._set_clip("gamepad", 0, restart=True)
        b._queue(Step("scene_game_win"), now=1, interrupt=True)
        self.assertEqual(b.clip, "scene_game_win")
        self.assertNotIn("stand_up", [step.clip for step in b.plan])

    def test_manual_change_closes_furniture_before_new_scene(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.repertoire.actions.update({"scene_chair_rest": {"role": "scene_furniture", "family": "chair"},
                                     "scene_chair_exit": {"role": "scene_furniture", "family": "chair", "starts_from": "sit"}})
        clips = {"scene_chair_rest": ("sit", [0], 1, True, "sit"),
                 "scene_chair_exit": ("sit", [0, 1], 2, False, "stand")}
        with patch.dict(CLIPS, clips), patch.object(b, "_routine_steps", return_value=[Step("peek")]):
            b._set_clip("scene_chair_rest", 0, restart=True)
            self.assertTrue(b.next_activity(1))
            self.assertEqual(b.clip, "scene_chair_exit")
            self.assertEqual(b.plan[0].clip, "peek")

    def test_form_change_is_hidden_by_a_puff_whoever_switches_the_clip(self):
        b = PetBrain(rng=random.Random(7), now=0)
        clips = {name: ("idle", [0, 1, 2, 3], 4, False, "stand") for name in ("form_dragon", "form_recover")}
        b.repertoire.actions.update({"form_dragon": {"form": "dragon", "role": "form"},
                                     "form_recover": {"form": "human", "role": "form"}})
        with patch.dict(CLIPS, clips):
            b._set_clip("form_dragon", 1, restart=True)
            self.assertEqual(sum(p.kind == "cloud" for p in b.particles), 11)
            b.particles.clear()
            b._set_clip("form_dragon", 2, restart=True)          # same form: no puff
            self.assertFalse(b.particles)
            b._queue(Step("pat"), now=3, interrupt=True)          # a cue brings her straight back
            self.assertEqual(b.clip, "pat")
            self.assertTrue(any(p.kind == "cloud" for p in b.particles))
            b._set_clip("form_dragon", 4, restart=True)
            b.press(5, b.x, b.y - 50)
            b.drag(5.1, b.x + 30, b.y - 70)                        # picked up: a puff and a human dangle
            self.assertEqual(b._form(), "human")
            self.assertEqual(b._poof_at, 5.1)

    def test_dragon_episode_is_rare_and_always_ends_on_two_legs(self):
        b = PetBrain(rng=random.Random(3), now=0)
        if not b._has("form_dragon"):
            self.skipTest("dragon form not adopted")
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        self.assertFalse(b._can_dragon(10))                       # not in the first minutes
        self.assertTrue(b._can_dragon(1000))
        b.quiet = True
        self.assertFalse(b._can_dragon(1000))
        b.quiet = False
        for trick in DRAGON_TRICKS:
            steps = b._dragon_steps(1000, trick=trick)
            clips = [step.clip for step in steps]
            self.assertEqual(clips[0], "form_dragon")
            self.assertIn(trick, clips)
            recovery = "form_recover" if trick == "form_dragon_reach" else "form_tidy"
            self.assertEqual(clips[-2:], [recovery, "idle"])
            self.assertTrue(all(clip in CLIPS for clip in clips))
            self.assertEqual(steps[-2].line, DRAGON_TRICKS[trick][1])
        self.assertFalse(b._can_dragon(1500))                     # cooldown after an episode
        b._queue(*b._dragon_steps(1000, trick="form_dragon_flap"), now=1000, interrupt=True)
        _, clips = run(b, 1000, 18)
        self.assertIn("form_dragon_flap", clips)
        self.assertEqual(b._form(), "human")

    def test_rest_uses_repertoire_bag_and_story_records_played_beats(self):
        b = PetBrain(rng=random.Random(7), now=0)
        group = [name for name, spec in curated_actions(b.repertoire.actions).items()
                 if spec["role"] == "study" and spec["family"] == "work" and spec["posture"] == "sit"]
        if len(group) < 2:
            self.skipTest("no adopted work variants")
        with (patch.object(b, "_weighted", return_value="library"),
              patch("regular.pet_brain.choose_routine", return_value=("ambient:work", [(group[0], 30)]))):
            selected = [b._choose(i)[0].clip for i in range(len(group))]
        self.assertEqual(set(selected), set(group))
        b.repertoire.recent.clear()
        b._queue(Step(group[0], 1), Step(group[1], 1), now=3, interrupt=True)
        run(b, 3, 3)
        self.assertIn(group[0], b.repertoire.recent)
        self.assertIn(group[1], b.repertoire.recent)

    def test_long_rest_cannot_redraw_a_one_shot_from_the_same_family(self):
        b = PetBrain(rng=random.Random(1), now=0)
        b.repertoire.actions = {
            "test_count": ACTIONS["dragon_treasure_count"],
            "test_find": ACTIONS["dragon_treasure_find"],
        }
        with patch("regular.pet_brain.choose_routine",
                   return_value=("ambient:coins", [("test_count", 60)])):
            for now in range(20):
                steps = b._routine_steps(now)
                self.assertEqual((steps[0].clip, steps[0].duration), ("test_count", 60))

    def test_collection_history_precedes_the_next_main_clip_selection(self):
        b = PetBrain(rng=random.Random(1), now=0)
        b.repertoire.recent.extend(("dragon_treasure_polish", "dragon_treasure_share"))
        with patch("regular.pet_brain.choose_routine",
                   return_value=("ambient:gems", [("dragon_treasure_polish", 60)])):
            private = b._routine_steps(1)
            self.assertNotIn("dragon_treasure_share", [step.clip for step in private])
            shared = b._routine_steps(2)
            self.assertIn("dragon_treasure_share", [step.clip for step in shared])

    def test_reading_and_laptop_keep_their_objects_and_reading_gets_an_ending(self):
        b = PetBrain(rng=random.Random(1), now=0)
        b.last_agent_event = 0
        saw_comic = False
        for _ in range(12):
            with patch.object(b, "_weighted", return_value="book"):
                steps = b._choose(1)
            self.assertEqual(b.repertoire.actions[steps[0].clip]["family"], "reading")
            self.assertFalse(any(b.repertoire.actions.get(step.clip, {}).get("family") == "work"
                                 for step in steps))
            if steps[0].clip == "life_comic_turn":
                saw_comic = True
                self.assertTrue(any(step.clip in ("life_comic_amused", "life_bookmark_place")
                                    for step in steps))
        self.assertTrue(saw_comic)
        with patch.object(b, "_weighted", return_value="laptop"):
            self.assertEqual(b.repertoire.actions[b._choose(1)[0].clip]["family"], "work")

    def test_sketch_keeps_its_selected_result_without_an_extra_reveal(self):
        b = PetBrain(rng=random.Random(1), now=0)
        for result in ("scene_paint_proud", "scene_paint_cheek"):
            with (patch.object(b, "_weighted", return_value="sketch"),
                  patch.object(b, "_activity_steps", return_value=[Step("paint", 12), Step(result)])):
                clips = [step.clip for step in b._choose(1)]
            self.assertEqual(clips[-1], result)
            self.assertNotIn("paint_reveal", clips)
        with (patch.object(b, "_weighted", return_value="sketch"),
              patch.object(b, "_activity_steps", return_value=[Step("paint", 12)])):
            self.assertEqual([step.clip for step in b._choose(1)][-2:], ["paint_reveal", "nod"])

    def test_dragon_avoids_recent_tricks_and_does_not_reenter_its_nap(self):
        b = PetBrain(rng=random.Random(2), now=0)
        b._queue(Step("form_dragon_flap"), now=1000, interrupt=True)
        for _ in range(10):
            steps = b._dragon_steps(3000)
            self.assertNotIn("form_dragon_flap", [step.clip for step in steps])
        guard = b._dragon_steps(3000, trick="form_dragon_guard")
        self.assertEqual(sum(step.clip == "form_dragon_guard" for step in guard), 1)
        stay = next(step for step in guard if step.clip == "form_dragon_guard")
        self.assertGreater(stay.duration, len(CLIPS[stay.clip][1]) / CLIPS[stay.clip][2])
        self.assertEqual(guard[-2].clip, "form_tidy")
        self.assertEqual(set(CLIPS["form_tidy"][1]), {0, 1})

    def test_real_work_discards_an_unplayed_activity_result(self):
        b = PetBrain(rng=random.Random(2), now=0)
        b._queue(*b._activity_steps("gamepad", 60), now=0, interrupt=True)
        self.assertTrue(any(step.clip.startswith("scene_game_") for step in b.plan))
        b.observe(1, {**ready(), "state": "reply"})
        self.assertFalse(any(step.clip.startswith("scene_game_") for step in b.plan))
        self.assertFalse(any(name.startswith("scene_game_") for name in b.repertoire.recent))

    def test_bottom_target_preserves_clickable_head_across_assets_and_scales(self):
        for scale in (0.6, 1.0, 1.5):
            b = PetBrain(rng=random.Random(7), now=0, scale=scale)
            b.set_ground(0, 1440, 860, 0)
            names = [name for name, spec in b.repertoire.actions.items()
                     if spec["role"] == "bottom" and spec.get("geometry")]
            if not names:
                self.skipTest("no measured bottom assets")
            for name in names:
                with self.subTest(scale=scale, clip=name):
                    clearance = b._head_clearance(name)
                    target = b._bottom_target(name)
                    visible = b.floor - (target - clearance)
                    self.assertAlmostEqual(visible, min(clearance, max(60, min(80, b.figure * 0.45))))
                    b.hidden_edge, b.bottom_peek, b._hidden_clip, b.mode = "bottom", True, name, "hidden"
                    b.y = target
                    b.set_ground(0, 1440, 860, 0)
                    self.assertAlmostEqual(b.y, target)

    def test_automatic_bottom_peek_uses_same_measured_target(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        with patch.object(b, "_weighted", return_value="bottom"):
            steps = b._choose(1)
        step = steps[0]
        self.assertEqual(step.target_y, b._bottom_target(step.clip))
        b._queue(*steps, now=1, interrupt=True)
        t, _ = run(b, 1, 3, fps=10, each=lambda _: b.set_ground(0, 1440, 860, 0))
        self.assertAlmostEqual(b.y, b._bottom_target(step.clip), delta=1.5)

    def test_library_selects_long_rest_and_story_modes(self):
        b = PetBrain(rng=random.Random(7), now=0)
        def selected(*args, mode, **kwargs):
            return (("tea", [("idle", 20)]) if mode == "story" else
                    ("ambient:work", [("life_laptop_type", 60)]))
        for now, expected in ((1, "rest"), (80, "rest"), (179, "rest"), (180, "story"), (181, "rest")):
            with (patch.object(b, "_weighted", return_value="library"),
                  patch.object(b.rng, "random", return_value=0),
                  patch("regular.pet_brain.choose_routine", side_effect=selected) as choose):
                steps = b._choose(now)
            self.assertEqual(choose.call_args.kwargs["mode"], expected)
            self.assertEqual(steps[0].duration, 20 if expected == "story" else 60)

    def test_reduced_motion_does_not_emit_hidden_props(self):
        for motion in (False, True):
            with self.subTest(motion=motion):
                b = brain(motion=motion)
                b._queue(Step("dragon_magic_flower"), now=0, interrupt=True)
                self.assertFalse(b.props.items)
                b._queue(Step("dragon_toy_ball"), now=1, interrupt=True)
                self.assertEqual(len(b.props.items), int(motion))
                b.props.items.clear()
                b._queue(Step("dragon_toy_ball"), now=60, interrupt=True)
                self.assertFalse(b.props.items)
                b._queue(Step("dragon_toy_ball"), now=181, interrupt=True)
                self.assertEqual(len(b.props.items), int(motion))

    def test_next_activity_works_under_toolbar_attention_and_preserves_real_work(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.set_attention(True, 1)
        self.assertTrue(b.next_activity(1))
        self.assertFalse(b.game_active)
        self.assertTrue(any(s.clip != "idle" for s in (b.step, *b.plan) if s))
        b.set_state("reply", 2, seconds=30)
        work = (b.clip, b.step, tuple(b.plan), b.state_until)
        self.assertFalse(b.next_activity(3))
        self.assertEqual((b.clip, b.step, tuple(b.plan), b.state_until), work)

    def test_prop_feedback_and_return_do_not_change_relationship_state(self):
        b = brain()
        gem = b.props.emit("gem", b.x + 50, b.floor - 12, 0)
        b.prop_dragged(gem, 1)
        self.assertIn("宝贝", b.bubble.text)
        self.assertEqual(b.return_prop(gem, 2), "gem")
        self.assertEqual(b.bubble.text, "收好啦。")
        self.assertFalse(b.props.items)
        self.assertEqual(b.warmth, 0)

    def test_pet_notices_a_settled_toy_during_a_long_activity_before_it_expires(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        with (patch.object(b.props, "kick", wraps=b.props.kick) as kick,
              patch.object(b.rng, "random", return_value=0)):
            b._queue(Step("dragon_toy_ball"), Step("laptop", 60), now=0, interrupt=True)
            self.assertEqual(len(b.props.items), 1)
            run(b, 0, 25)
            self.assertEqual(kick.call_count, 1)
            self.assertLess(kick.call_args.args[1], 20)

    def test_settled_treasure_does_not_interrupt_a_real_reply(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.set_state("reply", 0, seconds=60)
        b.props.emit("gem", b.x + 50, b.floor - 12, 0)
        item = b.props.items[0]
        item.grounded, item.vx, item.vy = True, 0, 0
        with (patch.object(b.props, "collect", wraps=b.props.collect) as collect,
              patch.object(b.rng, "random", return_value=0)):
            run(b, 0, 25)
            self.assertFalse(collect.called)
        self.assertEqual(b.state_name, "reply")
        self.assertIsNone(b._chasing_prop)

    def test_poke_tricks_have_a_cooldown_and_do_not_drop_props(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        if not b._can_warp():
            self.skipTest("spell sheets not adopted")
        with patch.object(b.rng, "random", return_value=0):
            b.poke(1)
        self.assertEqual(b.clip, "warp_cast")              # 被戳烦了：开阵溜走
        self.assertFalse(b.props.items)
        t, clips = run(b, 1, 9)
        self.assertEqual(b.mode, "ground")
        self.assertIn("warp_land", clips)
        b._queue(Step("idle", 30), now=t, interrupt=True)
        with patch.object(b.rng, "random", return_value=0):
            b.poke(t + 1)
        self.assertNotIn(b.clip, ("warp_cast", "sneeze"))
        b._queue(Step("idle", 30), now=t + 125, interrupt=True)
        with patch.object(b.rng, "random", return_value=0):
            b.poke(t + 126)
        self.assertIn(b.clip, ("warp_cast", "walk"))       # walks clear of the edge first if she landed there

    def test_fall_and_landing_keep_fixed_character_scale(self):
        b = brain()
        b.enter(0, 200)
        transforms = []
        _, clips = run(b, 0, 3, each=lambda t: transforms.append(b.transform(t)))
        self.assertIn("land", clips)
        self.assertTrue(all(t["sx"] == t["sy"] == 1 for t in transforms))

    def test_bottom_motion_accelerates_and_slows_before_arrival(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        clip = "motion_bottom_peek"
        b._queue(Step(clip, target_y=b._bottom_target(clip)), Step(clip, 5), now=0, interrupt=True)
        duration = b._move_duration
        positions = []
        for fraction in (0, .1, .4, .5, .8, .9, 1):
            b._fly(duration * fraction, 0)
            positions.append(b.y)
        self.assertLess(positions[1] - positions[0], positions[3] - positions[2])
        self.assertLess(positions[6] - positions[5], positions[3] - positions[2])
        self.assertAlmostEqual(positions[-1], b._bottom_target(clip), delta=1.5)

    def test_repeated_reply_heartbeat_rotates_variants_and_stops(self):
        b = PetBrain(rng=random.Random(42), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        if sum(s["role"] == "reply" and s["posture"] != "air" for s in b.repertoire.actions.values()) < 2:
            self.skipTest("no adopted reply variants")
        seen = set()
        for i in range(600):
            now = i * 0.1
            if i % 50 == 0:
                b.set_state("reply", now)
            b.tick(now)
            if b._role(b.clip) == "reply":
                seen.add(b.clip)
        self.assertGreater(len(seen), 1)
        b.clear_state(60)
        run(b, 60, 5, fps=10)
        self.assertIsNone(b.state_name)
        self.assertNotEqual(b._role(b.clip), "reply")

    def test_sleep_pool_varies_without_increasing_frequency(self):
        b = PetBrain(rng=random.Random(7), now=0)
        if sum(s["role"] == "sleep" for s in b.repertoire.actions.values()) < 2:
            self.skipTest("no adopted sleep variations")
        b.observe(0, ready(circadian="午后有点困"))
        chosen = set()
        for now in (1, 482, 963, 1444):
            with patch.object(b, "_weighted", return_value="nap"):
                step = b._choose(now)[0]
            chosen.add(step.clip)
            self.assertEqual(b._role(step.clip), "sleep")
            self.assertLessEqual(step.duration, 25)
            with patch.object(b, "_weighted", return_value="idle") as choose:
                b._choose(now + 1)
            self.assertEqual(choose.call_args.args[0].get("nap", 0), 0)
        self.assertGreater(len(chosen), 1)

    def test_local_text_reply_uses_reply_pool_but_voice_does_not(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.observe(0, ready(mood=0.8))
        b.set_attention(True, 0)
        b.chat_sent(1)
        b.chat_reply(2, "收到啦")
        self.assertEqual(b.state_name, "reply")
        self.assertNotEqual(b.mode, "fly")
        self.assertTrue(b.state_clip == "reply" or b._role(b.state_clip) == "reply")
        old_state = b.state_name
        b.chat_reply(3, "语音文本", animate=False)
        self.assertEqual(b.state_name, old_state)

    def test_quiet_and_reduced_motion_keep_a_stationary_catalog(self):
        for settings in ({"quiet": True}, {"motion": False}):
            with self.subTest(**settings):
                b = PetBrain(rng=random.Random(42), now=0, **settings)
                b.set_ground(0, 1440, 860, 0)
                b.place(700)
                if not b.repertoire.actions:
                    self.skipTest("no adopted catalog")
                visited, positions, modes = set(), [], set()
                run(b, 0, 900, fps=10, each=lambda _: (visited.add(b.clip), positions.append(b.x), modes.add(b.mode)))
                self.assertTrue(visited & b.repertoire.actions.keys())
                self.assertTrue(all(x == 700 for x in positions))
                self.assertEqual(modes, {"ground"})
                self.assertTrue(all(CLIPS[name][4] != "air" for name in visited))

    def test_changing_state_during_flying_reply_lands_and_shows_new_state(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.observe(0, ready(mood=0.9))
        if not any(s["role"] == "reply" and s["posture"] == "air" for s in b.repertoire.actions.values()):
            self.skipTest("no adopted airborne reply")
        b.set_state("reply", 1, seconds=30)
        t, _ = run(b, 1, 1)
        self.assertEqual(b.mode, "fly")
        before = b.y
        b.set_state("listen", t, seconds=20)
        self.assertEqual(b.y, before)
        t, clips = run(b, t, 6)
        self.assertEqual(b.mode, "ground")
        self.assertEqual(b.y, b.floor)
        self.assertTrue(any(b._role(clip) == "listen" for clip in clips))

    def test_adopted_catalog_drives_daytime_life(self):
        b = PetBrain(rng=random.Random(42), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        if len(b.repertoire.actions) < 30:
            self.skipTest("full adopted catalog not present")
        b.observe(0, ready(chatter=2))
        counts = Counter()
        for i in range(36000):
            b.tick((i + 1) * 0.1)
            counts[b.clip] += 1
        # The prop library is still a large part of the day, but no longer the whole of it:
        # whole-body spells (circles, sneezes) and walks take a real share.
        self.assertGreater(sum(n for clip, n in counts.items() if clip in b.repertoire.actions), 12000)
        self.assertTrue(any(clip.startswith("warp_") for clip in counts))
        self.assertGreaterEqual(sum(clip in b.repertoire.actions for clip in counts), 30)
        self.assertLess(sum(n for clip, n in counts.items() if clip in ASLEEP), 1800)

    def test_adopted_drawing_remains_semantically_busy(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860)
        b.place(700)
        if not any(spec["role"] == "drawing" for spec in b.repertoire.actions.values()):
            self.skipTest("no adopted drawing assets")
        b.observe(1, ready(events=[{"kind": "drawing", "ts": 10}]))
        t, _ = run(b, 1, 4)
        expected = "paint" if b._has("spell_paint") else None
        self.assertEqual(b.clip if expected else b._role(b.clip), expected or "drawing")
        self.assertEqual(b.media, "drawing")
        t, _ = run(b, t, 25)
        self.assertEqual(b.clip if expected else b._role(b.clip), expected or "drawing")
        b.observe(t, ready(events=[{"kind": "media_end", "ts": 11}]))
        self.assertIsNone(b.media)

    def test_controlled_flight_moves_and_lands_without_teleporting(self):
        b = brain()
        b._queue(Step("fall", target_y=600), Step("fall", target_x=850, target_y=600),
                 Step("fall", target_y=b.floor), now=0, interrupt=True)
        self.assertEqual(b.mode, "fly")
        before = (b.x, b.y)
        b.tick(0.1)
        self.assertLess(b.y, before[1])
        self.assertLessEqual(before[1] - b.y, 11)
        run(b, 0.1, 9)
        self.assertEqual(b.mode, "ground")
        self.assertAlmostEqual(b.y, b.floor)

    def test_prop_touch_is_local_and_bounded(self):
        b = brain()
        item = b.props.emit("coin", b.x, b.y - 20, 0)
        self.assertEqual(b.touch_prop(item, 1), "coin")
        self.assertFalse(any(prop.id == item for prop in b.props.items))
        self.assertIsNone(b.touch_prop(item, 2))

    def test_bottom_peek_survives_geometry_refresh_and_returns(self):
        b = brain()
        b.bottom_peek = True
        b._queue(Step("dangle", target_y=b._bottom_target("dangle")), Step("dangle", 0.5),
                 Step("dangle", target_y=b.floor), now=0, interrupt=True)
        t, _ = run(b, 0, 1)
        before = b.y
        self.assertGreater(before, b.floor)
        b.set_ground(0, 1440, 860, 0)
        self.assertEqual(b.y, before)
        run(b, t, 4)
        self.assertFalse(b.bottom_peek)
        self.assertEqual(b.mode, "ground")
        self.assertEqual(b.y, b.floor)

    def test_happy_reply_uses_real_flight_and_returns_after_state(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.observe(0, ready(mood=0.8))
        with patch.object(b, "_weighted", return_value="idle"):
            b.set_state("reply", 1, seconds=6)
            self.assertEqual(b.mode, "ground")
            self.assertEqual(b.repertoire.actions[b.clip]["family"], "takeoff")
            self.assertEqual(b.y, b.floor)
            now, clips = run(b, 1, 2)
            self.assertEqual(b.mode, "fly")
            self.assertLess(b.y, b.floor)
            now, after = run(b, now, 10)
            self.assertTrue(any(name.startswith("life_reply_air_") for name in clips + after))
            self.assertTrue(any("flap" in name for name in clips + after))
            self.assertEqual(b.mode, "ground")
            self.assertEqual(b.y, b.floor)


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
        # Bound the average packaged strip size as the catalog grows.
        self.assertLess(total, 120_000 * len(spec["actions"]))
        self.assertEqual((width, height), tuple(spec["frame"]))


class AmbientLifeTest(unittest.TestCase):
    def test_stretching_unfolds_her_wings_then_settles_back_down(self):
        b = brain()
        with patch.object(b, "_weighted", return_value="stretch"):
            steps = b._choose(0.0)
        self.assertEqual([step.clip for step in steps], ["stretch", "wing_stretch", "sit_swing"])
        self.assertIn("wing_stretch", CUE_ACTIONS)
        self.assertFalse(CLIPS["wing_stretch"][3])

    def test_routines_continue_past_one_gesture_without_an_idle_tail(self):
        b = brain()
        b.observe(0.0, ready())
        for name in ("tea_time", "book", "sketch", "observe", "patrol"):
            with self.subTest(routine=name), patch.object(b, "_weighted", return_value=name):
                steps = b._choose(1.0)
                self.assertGreater(len(steps), 1)
                self.assertNotEqual(steps[-1].clip, "idle")
                self.assertNotIn("pout", [step.clip for step in steps])
                self.assertNotIn("point", [step.clip for step in steps])

    def test_recent_routines_do_not_repeat_back_to_back(self):
        b = brain(seed=12)
        b.observe(0.0, ready(chatter=20))
        recent = []
        for _ in range(120):
            b._choose(1.0)
            choice = b._ambient_recent[-1]
            self.assertNotIn(choice, recent[-3:])
            recent.append(choice)

    def test_self_served_snack_is_not_a_feed_or_care_action(self):
        b = brain()
        b.external_care = True
        with patch.object(b, "_weighted", return_value="tea_time"):
            b._queue(*b._choose(0.0), now=0.0, interrupt=True)
        self.assertEqual(b.clip, "eat")
        run(b, 0.0, 5)
        self.assertEqual(b.warmth, 0.0)
        self.assertEqual(list(b.feeds), [])
        self.assertEqual(list(b.care_requests), [])

    def test_quiet_activities_last_longer_than_a_quick_gesture(self):
        b = brain()
        b.observe(0.0, ready(chatter=20))
        for name in ("popcorn", "headphones", "gamepad", "tail_care", "lie_chin"):
            with self.subTest(activity=name), patch.object(b, "_weighted", return_value=name):
                step = next(step for step in b._choose(0.0) if step.clip == name)
                self.assertGreaterEqual(step.duration, 24.0)
                self.assertLessEqual(CLIPS[name][2], 1.1)

    def test_popcorn_follows_lively_group_chatter(self):
        def count(chatter):
            b = brain(seed=16)
            b.observe(0.0, ready(chatter=chatter))
            return sum(any(step.clip == "popcorn" for step in b._choose(1.0)) for _ in range(200))
        self.assertEqual(count(0), 0)
        self.assertGreater(count(30), 10)

    def test_lying_awake_is_not_a_nap_and_uses_a_short_sit_up_transition(self):
        b = brain()
        b.energy = 0.5
        b._queue(Step("lie_chin", 30), now=1.0, interrupt=True)
        self.assertEqual([b.clip] + [s.clip for s in b.plan], ["sit_down", "lie_chin"])
        t, clips = run(b, 1.0, 10)
        self.assertEqual(b.clip, "lie_chin")
        self.assertFalse(set(clips) & ASLEEP)
        self.assertLess(b.energy, 0.5)
        b.poke(t)
        self.assertEqual(b.clip, "lie_chin")
        b.chat_sent(t + 1)
        self.assertEqual([b.clip] + [s.clip for s in b.plan], ["sit_up", "stand_up", "think"])

    def test_she_keeps_doing_varied_things_and_never_jumps_between_postures(self):
        b = brain(seed=3)
        b.observe(0.0, ready(mood=0.3))
        xs = []
        _, clips = run(b, 0.0, 30 * 60, each=lambda t: xs.append((b.x, b.edge_side)))
        kinds = Counter(clips)
        self.assertGreaterEqual(len(kinds), 10, kinds)
        self.assertIn("walk", kinds)
        self.assertGreater(len(clips), 60)        # ~2 changes a minute or more
        self.assertTrue(all(FIGURE * (EDGE_PEEK if side == -1 else EDGE) <= x
                            <= 1440 - FIGURE * (EDGE_PEEK if side == 1 else EDGE) for x, side in xs))
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

    def test_idle_blinks_without_scaling_the_character(self):
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
        self.assertEqual(heights, {1.0})


class ScreenEdgeTest(unittest.TestCase):
    @staticmethod
    def edge(side, *, left=0, right=1440, scale=1):
        b = brain(scale=scale)
        b.set_ground(left, right, 860, 20)
        b.place(left + b.figure if side == -1 else right - b.figure)
        with patch.object(b, "_weighted", return_value="edge"):
            b._queue(*b._choose(0.0), now=0.0, interrupt=True)
        return b

    def test_both_edges_approach_peek_and_return_without_position_jumps(self):
        for left, right, scale in ((0, 1440, 1), (-1440, 0, 0.6), (320, 1280, 1.5)):
            for side in (-1, 1):
                with self.subTest(left=left, side=side, scale=scale):
                    b = self.edge(side, left=left, right=right, scale=scale)
                    positions, peeks = [b.x], []
                    for index in range(30 * 60):
                        now = (index + 1) / 60
                        b.set_ground(left, right, 860, 20)    # native shell refreshes bounds every frame
                        b.tick(now)
                        positions.append(b.x)
                        if b.clip == "edge_peek":
                            peeks.append(b.x)
                            self.assertEqual(b.facing, -side)
                            visible_left, _, visible_right, _ = b.body_rect()
                            self.assertGreater(visible_right - visible_left, b.figure * 0.25)
                    self.assertTrue(peeks)
                    expected = left + b.figure * EDGE_PEEK if side == -1 else right - b.figure * EDGE_PEEK
                    self.assertTrue(all(abs(x - expected) < 0.001 for x in peeks))
                    self.assertLessEqual(max(abs(x2 - x1) for x1, x2 in zip(positions, positions[1:])),
                                         WALK_SPEED * scale / 60 + 0.001)
                    self.assertEqual(b.edge_side, 0)
                    self.assertGreaterEqual(b.x, left + b.figure * EDGE)
                    self.assertLessEqual(b.x, right - b.figure * EDGE)

    def test_a_chat_retreats_first_and_hover_cannot_cancel_the_return(self):
        for side in (-1, 1):
            with self.subTest(side=side):
                b = self.edge(side)
                t, _ = run(b, 0.0, 3.2, fps=60)
                self.assertEqual(b.clip, "edge_peek")
                x = b.x
                b.chat_sent(t)
                self.assertEqual(b.x, x)
                self.assertEqual((b.clip, b.edge_side), ("walk", side))
                b.pointer.near = True
                b.set_attention(True, t)
                _, clips = run(b, t, 1.2, fps=60)
                self.assertEqual(b.clip, "think")
                self.assertNotIn("idle", clips)
                self.assertEqual(b.edge_side, 0)
                self.assertTrue(b.chat_pending)

    def test_clicks_and_dragging_interrupt_a_peek(self):
        b = self.edge(-1)
        t, _ = run(b, 0.0, 3.2, fps=60)
        x = b.x
        b.poke(t)
        self.assertEqual(b.x, x)
        self.assertEqual((b.clip, b.edge_side), ("walk", -1))
        self.assertTrue(any(step.clip in TAP_POOL for step in b.plan))
        b.press(t + 0.1, b.x, b.y - b.figure * 0.5)
        b.drag(t + 0.2, b.x + 30, b.y - 100)
        self.assertEqual((b.mode, b.clip, b.edge_side), ("drag", "dangle", 0))
        b.release(t + 0.3, b.x + 30, b.y - 100)
        t, _ = run(b, t + 0.3, 3)
        self.assertEqual(b.mode, "hidden")
        b.poke(t)
        run(b, t, 3)
        self.assertEqual(b.mode, "ground")
        self.assertGreaterEqual(b.x, b.left + b.figure * EDGE)

    def test_arriving_at_an_edge_is_a_direct_environment_reaction(self):
        for side in (-1, 1):
            b = brain()
            b.place(b.left + b.figure * EDGE if side == -1 else b.right - b.figure * EDGE)
            steps = b._choose(0.0)
            self.assertIn("edge_peek", [step.clip for step in steps])
            self.assertEqual(steps[0].edge_side, side)
            self.assertNotIn("edge_peek", CUE_ACTIONS)

    def test_staying_put_or_a_nearby_user_does_not_start_an_edge_trip(self):
        for quiet, near, motion in ((True, False, True), (False, True, True), (False, False, False)):
            b = brain(quiet=quiet, motion=motion)
            b.place(b.left + b.figure * EDGE)
            b.pointer.near = near
            for _ in range(15):
                self.assertNotIn("edge_peek", [step.clip for step in b._choose(0.0)])

    def test_edge_visits_have_a_cooldown_even_after_other_routines(self):
        b = brain()
        b.place(b.left + b.figure * EDGE)
        self.assertIn("edge_peek", [step.clip for step in b._choose(0.0)])
        for name in ("book", "tea_time", "headphones"):
            with patch.object(b, "_weighted", return_value=name):
                b._choose(10.0)
        self.assertNotIn("edge_peek", [step.clip for step in b._choose(120.0)])
        self.assertIn("edge_peek", [step.clip for step in b._choose(241.0)])


class InteractionTest(unittest.TestCase):
    def test_top_exit_sinks_into_near_portal_and_rises_from_floor(self):
        b = PetBrain(rng=random.Random(7), now=0)
        if not b._can_warp():
            self.skipTest("portal assets not adopted")
        b.set_ground(0, 1440, 860, 38)
        b.place(700)
        b.hidden_edge, b.mode, b.top_peek = "top", "hidden", True
        b._hidden_clip = b._hidden_awake_clip = "boundary_top_peek"
        b._set_clip(b._hidden_clip, 0, restart=True)
        b.x, b.y = b._hidden_target()
        b.poke(1)
        seen = []
        def collect(_):
            if b.warp:
                seen.append((b.warp["phase"], b.y, b.portal_clip, b.warp["src"], b.warp["dst"]))
        run(b, 1, 7, fps=60, each=collect)
        phases = [entry[0] for entry in seen]
        self.assertLess(phases.index("top_sink"), phases.index("gone"))
        self.assertLess(phases.index("gone"), phases.index("rise"))
        source = next(entry for entry in seen if entry[0] == "top_sink")
        self.assertEqual(source[2][1], "below")
        self.assertLess(source[1], b.ceiling + b.figure * 2)
        self.assertEqual(source[3].side, "floor")
        self.assertEqual(source[4].y, b.floor)
        self.assertTrue(any(entry[0] == "rise" and entry[2] == (b.floor, "below") for entry in seen))
        self.assertEqual((b.mode, b.y, b.portal_clip), ("ground", b.floor, None))

    def test_horizontal_glide_leans_in_direction_without_scaling(self):
        for direction in (-1, 1):
            b = PetBrain(rng=random.Random(7), now=0)
            b.set_ground(0, 1440, 860, 0)
            b.place(700)
            b.mode, b.y = "fly", 500
            b.step = Step("motion_fly_glide", target_x=700 + direction * 200, target_y=500)
            b._move_origin, b._move_duration, b.step_started = (700, 500), 2, 0
            b._set_clip("motion_fly_glide", 0, restart=True)
            transform = b.transform(1)
            self.assertAlmostEqual(transform["rot"], direction * 5)
            self.assertEqual((transform["sx"], transform["sy"]), (1, 1))
            self.assertAlmostEqual(b.transform(0)["rot"], 0)
            self.assertAlmostEqual(b.transform(2)["rot"], 0)

    def test_edge_sleep_entry_runs_once_on_a_fixed_support(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.hidden_edge, b.mode, b.bottom_peek = "bottom", "hidden", True
        b._hidden_clip = b._hidden_awake_clip = "motion_bottom_look"
        b._set_clip(b._hidden_clip, 0, restart=True)
        b.x, b.y = b._hidden_target()
        b._hidden_sleep_at = 1
        spec = {"role": "edge_sleep", "family": "bottom", "posture": "air",
                "entry_sequence": [0, 1, 2], "entry_fps": 2.5,
                "geometry": {"rest_y": 350, "bounds": [-100, -300, 100, 0]}}
        with patch.dict(CLIPS, {"test_edge_sleep": ("test_edge_sleep", [2, 3], 0.5, True, "air")}), \
             patch.dict(b.repertoire.actions, {"test_edge_sleep": spec}, clear=True):
            b.tick(1)
            target = 860 - 350 * b.figure / 320
            self.assertEqual(b.pose(1)[1], 0)
            self.assertEqual(b.y, target)
            b.set_ground(0, 1440, 860, 38)
            self.assertEqual(b.y, target)
            b.tick(1.325)
            self.assertEqual(b.y, target)
            b.tick(1.41)
            self.assertEqual(b.pose(1.41)[1], 1)
            b.tick(1.8)
            self.assertAlmostEqual(b.y, target)
            self.assertEqual(b.pose(1.8)[1], 2)
            b.tick(2.3)
            self.assertEqual(b.pose(2.3)[1], 2)
            b.tick(4.3)
            self.assertEqual(b.pose(4.3)[1], 3)
            b.tick(6.3)
            self.assertEqual(b.pose(6.3)[1], 2)

    def test_side_hide_preserves_drop_height(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.place(700)
        b.press(1, 700, 760)
        b.drag(1.1, 700, 500)
        b.tick(1.1)
        b.drag(1.2, -50, 360)
        b.tick(1.2)
        dropped_y = b.y
        b.release(1.3, -50, 360)
        run(b, 1.3, 4)
        self.assertEqual((b.mode, b.hidden_edge), ("hidden", "left"))
        self.assertAlmostEqual(b.y, dropped_y)

    def test_side_sleep_slip_is_rare_bounded_and_interruptible(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.hidden_edge, b.mode, b.hidden_asleep = "left", "hidden", True
        b._hidden_side_y = b.y = 400
        b._hidden_clip = b._hidden_awake_clip = "motion_edge_look"
        b._set_clip(b._hidden_clip, 0, restart=True)
        b.x, b.y = b._hidden_target()
        b._hidden_sleep_started, b._hidden_slip_check_at = 1, 91
        with patch.dict(CLIPS, {"boundary_side_slip": ("motion_edge_look", [0, 1, 2, 3], 6, False, "stand")}), \
             patch.object(b.rng, "random", return_value=0):
            b.tick(90)
            self.assertIsNone(b._hidden_slip_started)
            b.tick(91)
            self.assertEqual(b.clip, "boundary_side_slip")
            b.tick(91.3)
            self.assertGreater(b.y, 400)
            b.tick(91.8)
            self.assertAlmostEqual(b.y, 400)
            self.assertEqual(b.clip, "motion_edge_look")
            b._hidden_slip_check_at = 92
            b.tick(92)
            self.assertIsNone(b._hidden_slip_started)
            self.assertTrue(b.hidden_asleep)

    def test_hidden_bottom_stays_at_fixed_height_and_only_blinks_when_idle(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.hidden_edge, b.mode, b.bottom_peek = "bottom", "hidden", True
        b._hidden_clip = b._hidden_awake_clip = "motion_bottom_look"
        b._set_clip(b._hidden_clip, 0, restart=True)
        b.x, b.y = b._hidden_target()
        b._hidden_sleep_at = float("inf")
        positions, frames = set(), set()
        for i in range(1800):
            now = (i + 1) / 30
            b.tick(now)
            positions.add(b.y)
            frames.add(b.pose(now)[1])
        self.assertEqual(len(positions), 1)
        self.assertEqual(frames, {1, 3})
        self.assertEqual((b.mode, b.hidden_edge), ("hidden", "bottom"))

    def test_awake_hidden_gaze_tracks_cursor_without_moving_or_unhiding(self):
        cases = (("left", "motion_edge_look"), ("right", "motion_edge_look"),
                 ("bottom", "motion_bottom_look"), ("top", "boundary_top_look"))
        for edge, clip in cases:
            if clip not in CLIPS:
                continue
            with self.subTest(edge=edge):
                b = PetBrain(rng=random.Random(7), now=0)
                b.set_ground(0, 1440, 860, 38)
                b.hidden_edge, b.mode = edge, "hidden"
                b.bottom_peek, b.top_peek = edge == "bottom", edge == "top"
                b.facing = -1 if edge == "right" else 1
                b._hidden_clip = b._hidden_awake_clip = clip
                b._set_clip(clip, 0, restart=True)
                b.x, b.y = b._hidden_target()
                b._hidden_sleep_at = b._hidden_blink_at = float("inf")
                position = (b.x, b.y)
                b.hover(0.1, 0, 400, near=False)
                b.tick(0.1)
                b.tick(0.4)
                first = b.pose(0.4)[1]
                b.hover(0.5, 1440, 400, near=False)
                b.tick(0.5)
                b.tick(0.6)
                self.assertEqual(b.pose(0.6)[1], first)  # debounce jitter
                b.tick(0.8)
                second = b.pose(0.8)[1]
                self.assertNotEqual(first, second)
                self.assertEqual((b.x, b.y), position)
                self.assertEqual((b.mode, b.hidden_edge), ("hidden", edge))
                self.assertEqual(b.transform(0.8)["rot"], 0)

    def test_hidden_sleep_particles_point_into_screen(self):
        b = brain()
        b.mode, b.hidden_asleep, b.hidden_edge = "hidden", True, "right"
        b._emit("z", 0)
        self.assertLess(b.particles[-1].x, 0)
        self.assertLess(b.particles[-1].vx, 0)
        b.hidden_edge = "top"
        b._emit("z", 1)
        self.assertEqual(b.particles[-1].y, 0.15)

    def test_hidden_sleep_loops_then_click_wakes_and_exits(self):
        for edge, sleep in (("left", "boundary_side_sleep"), ("right", "boundary_side_sleep"),
                            ("bottom", "boundary_bottom_sleep"), ("top", "boundary_top_sleep")):
            with self.subTest(edge=edge):
                b = PetBrain(rng=random.Random(7), now=0)
                b.set_ground(0, 1440, 860, 38)
                b.place(700)
                b.hidden_edge, b.mode = edge, "hidden"
                b.bottom_peek, b.top_peek = edge == "bottom", edge == "top"
                awake = ("motion_bottom_peek" if edge == "bottom" else
                         "boundary_top_peek" if edge == "top" else "motion_edge_peek")
                b._hidden_clip = b._hidden_awake_clip = awake
                b._set_clip(awake, 0, restart=True)
                b.x, b.y = b._hidden_target()
                b._hidden_sleep_at = 1
                b.tick(1)
                self.assertTrue(b.hidden_asleep)
                if sleep in CLIPS:
                    self.assertEqual(b.clip, sleep)
                    frames = set()
                    t, _ = run(b, 1, 2, each=lambda now: frames.add(b.pose(now)[1]))
                    self.assertGreater(len(frames), 1)
                else:
                    t = 1
                b.hover(t, b.x, b.y, near=True)
                b.observe(t, ready(events=[{"kind": "mention", "ts": 100}]))
                self.assertEqual((b.mode, b.hidden_edge), ("hidden", edge))
                self.assertTrue(b.hidden_asleep)
                b.poke(t + 0.1)
                self.assertEqual(b._edge_exit["phase"], "wake")
                # Wake on the same support before changing to the launch pose.
                self.assertEqual(b.clip, sleep if sleep in CLIPS else awake)
                self.assertFalse(b.hidden_asleep)
                t, clips = run(b, t + 0.1, 7 if edge == "top" else 5)
                self.assertEqual((b.mode, b.y), ("ground", b.floor))
                self.assertIn("warp_land" if edge == "top" and b._can_warp() else
                              "boundary_land" if "boundary_land" in CLIPS else "land", clips)

    def test_edge_exit_has_real_arc_and_launch_frames(self):
        for edge in ("left", "right", "bottom", "top"):
            with self.subTest(edge=edge):
                b = PetBrain(rng=random.Random(9), now=0)
                b.set_ground(0, 1440, 860, 38)
                b.place(700)
                b.hidden_edge, b.mode = edge, "hidden"
                b.bottom_peek, b.top_peek = edge == "bottom", edge == "top"
                b._hidden_clip = b._hidden_awake_clip = ("motion_bottom_peek" if edge == "bottom" else
                    "boundary_top_peek" if edge == "top" else "motion_edge_peek")
                b._set_clip(b._hidden_clip, 0, restart=True)
                b.x, b.y = b._hidden_target()
                start = (b.x, b.y)
                b.poke(1)
                positions, frames, clips = [], set(), set()
                def collect(now):
                    positions.append((b.x, b.y, b.mode))
                    clips.add(b.clip)
                    if b._role(b.clip) == "edge_launch":
                        frames.add(b.pose(now)[1])
                    self.assertEqual((b.transform(now)["sx"], b.transform(now)["sy"]), (1, 1))
                run(b, 1, 7 if edge == "top" else 5, fps=60, each=collect)
                self.assertEqual((b.mode, b.y), ("ground", b.floor))
                if edge == "top" and b._can_warp():
                    self.assertIn("warp_sink", clips)
                    self.assertIn("warp_rise", clips)
                else:
                    self.assertTrue(any(b._role(clip) == "fly" and b.repertoire.actions[clip]["family"] == "flap" for clip in clips))
                    self.assertTrue(any(abs(x - start[0]) > 10 for x, _, mode in positions if mode == "edge_exit"))
                if edge in ("left", "right", "bottom"):
                    self.assertLess(min(y for _, y, mode in positions if mode == "edge_exit"), b.floor - 10)
                    self.assertEqual(frames, {0, 1, 2, 3})

    def test_body_poke_can_sneeze_but_head_keeps_pat(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        if not b._has("spell_sneeze"):
            self.skipTest("spell sheets not adopted")
        with patch.object(b.rng, "random", side_effect=[0.0, 0.9, 0.9]):
            b.poke(1)
        self.assertEqual(b.clip, "sneeze")
        t, _ = run(b, 1, 4)
        left, top, right, bottom = b.body_rect()
        b.poke(t + 1, (left + right) / 2, top + (bottom - top) * 0.2)
        self.assertEqual(b.clip, "pat")

    def test_head_clearance_uses_optional_normalized_geometry(self):
        b = brain(scale=1.2)
        b._set_clip("fall", 0)
        b.repertoire.actions["fall"] = {"geometry": None}
        self.assertAlmostEqual(b._head_clearance(), b.figure * 1.05)
        b.repertoire.actions["fall"]["geometry"] = {"bounds": [-100, -400, 100, 0]}
        self.assertAlmostEqual(b._head_clearance(), 240)

    def test_ambient_prop_chase_rechecks_and_kicks_once(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        prop_id = b.props.emit("ball", 800, 848, 0)
        prop = b.props.items[0]
        prop.grounded, prop.vx, prop.vy = True, 0, 0
        with patch.object(b.rng, "random", return_value=0):
            steps = b._prop_routine(1)
        self.assertIsNotNone(steps)
        b._queue(*steps, now=1)
        b._advance(1)
        with patch.object(b.props, "kick", wraps=b.props.kick) as kick:
            run(b, 1, 4)
        self.assertEqual(kick.call_count, 1)
        self.assertEqual(b._prop_chase_at, 1)
        self.assertIsNone(b._chasing_prop)
        self.assertEqual(prop.id, prop_id)
        self.assertIsNone(b._prop_routine(10))

    def test_ambient_prop_chase_ignores_stale_or_ineligible_items(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        prop_id = b.props.emit("coin", 800, 848, 0)
        prop = b.props.items[0]
        prop.grounded, prop.vx, prop.vy = True, 0, 0
        b.pointer.near = True
        self.assertIsNone(b._prop_routine(1))
        b.pointer.near = False
        with patch.object(b.rng, "random", return_value=0):
            steps = b._prop_routine(1)
        b._queue(*steps, now=1)
        b._advance(1)
        b.props.drag(prop_id, 900, 500, 1.1)
        b.tick(1.1)
        self.assertIsNone(b._chasing_prop)
        self.assertIn(prop, b.props.items)

    def test_explicit_sleep_cue_rotates_and_wakes_on_poke(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        b.observe(1, ready(events=[{"kind": "cue", "action": "sleep", "ts": 1}]))
        seen = set()
        t, _ = run(b, 1, 90, fps=10, each=lambda _: seen.add(b.clip) if b._role(b.clip) == "sleep" else None)
        self.assertGreaterEqual(len(seen), 2)
        self.assertGreater(b._sleep_cue_until, t)
        b.poke(t)
        self.assertEqual(b._sleep_cue_until, 0)

    def test_feeding_uses_one_cookie_sheet_in_order_then_responds(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.feed(1)
        self.assertEqual(b.clip, "feed_expect")
        frames = []
        source = CLIPS["feed_accept"][0]
        feed_clips = ("feed_accept", "feed_bite", "feed_content")
        if CLIPS["feed_expect"][0] == source:
            feed_clips = ("feed_expect", *feed_clips)
        def remember(t):
            if b.clip in feed_clips:
                strip, frame, _, transform = b.pose(t)
                self.assertEqual(strip, source)
                self.assertEqual((transform["sx"], transform["sy"]), (1.0, 1.0))
                if not frames or frames[-1] != frame:
                    frames.append(frame)
        _, played = run(b, 1, 4.1, each=remember)
        self.assertEqual(frames, [0, 1, 2, 3])
        self.assertEqual([name for name in played if name.startswith("feed_")],
                         ["feed_expect", "feed_accept", "feed_bite", "feed_content"])
        self.assertNotIn("dragon_snack_cookie", played)
        self.assertNotIn("scene_sweet_share", played)
        self.assertIn(b.bubble.text, CARE_LINES["feed"])

    def test_panel_confirmation_continues_one_feeding_even_when_delayed(self):
        for delay in (0.1, 2.0):
            with self.subTest(delay=delay):
                b = PetBrain(rng=random.Random(7), now=0)
                b.external_care = True
                b.feed(1)
                started = b.clip_started
                now, before = run(b, 1, delay)
                self.assertFalse(any(name in ("feed_accept", "feed_bite", "feed_content") for name in before))
                self.assertEqual(b.warmth, 0)
                event = {"kind": "care", "action": "feed", "result": "gain", "id": "cookie-1",
                         "text": "吃饱了，心情也好了。"}
                b.receive_care(now, event)
                self.assertFalse(b._feed_waiting)
                if delay < 1:
                    self.assertEqual((b.clip, b.clip_started), ("feed_expect", started))
                else:
                    self.assertEqual(b.clip, "feed_accept")
                _, played = run(b, now, 4)
                self.assertEqual(played.count("feed_accept"), 1)
                self.assertEqual(played.count("feed_bite"), 1)
                self.assertEqual(played.count("feed_content"), 1)
                self.assertEqual(b.bubble.text, event["text"])
                before_duplicate = (b.clip, b.clip_started, tuple(b.plan))
                b.receive_care(now + 4.1, event)
                self.assertEqual((b.clip, b.clip_started, tuple(b.plan)), before_duplicate)

    def test_full_cookie_receipt_does_not_start_eating_and_care_is_not_reapplied(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.external_care = True
        b.feed(1)
        b.receive_care(1.2, {"kind": "care", "action": "feed", "result": "fed_full", "id": "full",
                             "text": "吃饱啦，留着下回再吃。"})
        self.assertEqual(b.clip, "peek")
        self.assertFalse(any(step.clip.startswith("feed_") for step in b.plan))
        self.assertFalse(b._feed_waiting)
        self.assertEqual(b.warmth, 0)

    def test_released_at_edge_hides_until_click_or_drag(self):
        for edge, cursor in (("left", (-50, 400)), ("right", (1500, 400)),
                             ("bottom", (700, 1000)), ("top", (700, -300))):
            with self.subTest(edge=edge):
                b = PetBrain(rng=random.Random(7), now=0)
                b.set_ground(0, 1440, 860, 38)
                b.place(700)
                b.press(1, 700, 760)
                b.drag(1.1, 700, 500)
                b.tick(1.1)
                b.drag(1.2, *cursor)
                b.tick(1.2)
                b.release(1.3, *cursor)
                if edge in ("left", "right"):
                    self.assertEqual((b.mode, b.clip), ("hiding", "walk"))
                observed = []
                t, _ = run(b, 1.3, 5, each=lambda _: observed.append((b.mode, b.clip)))
                self.assertEqual((b.mode, b.hidden_edge), ("hidden", edge))
                if edge in ("left", "right"):
                    self.assertTrue(all(clip == "walk" for mode, clip in observed if mode == "hiding"))
                    self.assertEqual(b.clip, b._hidden_clip)
                    self.assertEqual(b._role(b.clip), "edge")
                self.assertEqual(b.top_peek, edge == "top")
                if edge == "top" and "boundary_top_peek" in CLIPS:
                    self.assertEqual(b.clip, "boundary_top_look" if "boundary_top_look" in CLIPS else "boundary_top_peek")
                b.hover(t, b.x, b.y, near=True)
                b.set_attention(True, t)
                b.observe(t, ready(events=[{"kind": "mention", "ts": 12}]))
                t, _ = run(b, t, 3)
                self.assertEqual((b.mode, b.hidden_edge), ("hidden", edge))
                b.press(t, b.x, b.y - 20)
                self.assertEqual(b.release(t + 0.1, b.x, b.y - 20), "click")
                b.poke(t + 0.1)
                self.assertIsNone(b.hidden_edge)
                if edge == "top" and b._can_warp():
                    self.assertIn(b.clip, ("edge_daily_top_cast", "boundary_top_look", "boundary_top_peek"))
                elif edge == "top" and "boundary_top_return" in CLIPS:
                    self.assertEqual(b.clip, "boundary_top_return")
                t, clips = run(b, t + 0.1, 7 if edge == "top" else 5)
                if edge == "top" and b._can_warp():
                    self.assertIn("warp_land", clips)
                elif "boundary_land" in CLIPS:
                    self.assertIn("boundary_land", clips)
                if edge != "top" or not b._can_warp():
                    self.assertTrue(any(b._role(clip) == "fly" for clip in clips))
                self.assertEqual(b.mode, "ground")
                self.assertEqual(b.y, b.floor)
                self.assertFalse(b.top_peek)

    def test_hidden_reanchors_after_scale_and_screen_changes(self):
        for edge in ("left", "right", "bottom", "top"):
            with self.subTest(edge=edge):
                b = PetBrain(rng=random.Random(7), now=0)
                b.set_ground(0, 1440, 860, 38)
                b.place(700)
                b.hidden_edge, b.mode = edge, "hidden"
                b.bottom_peek, b.top_peek = edge == "bottom", edge == "top"
                b._hidden_clip = "motion_bottom_peek" if edge == "bottom" else "edge_peek"
                old_clip = b.clip
                for scale, bounds in ((0.6, (100, 1200, 800, 30)), (1.5, (-200, 1800, 1000, 50))):
                    b.scale = scale
                    b.set_ground(*bounds)
                    self.assertEqual((b.x, b.y), b._hidden_target())
                    self.assertEqual((b.mode, b.hidden_edge, b.clip), ("hidden", edge, old_clip))

    def test_hidden_can_be_dragged_away(self):
        b = brain()
        b.hidden_edge, b.mode, b.x = "left", "hidden", b.left + b.figure * EDGE_PEEK
        b.press(1, b.x, b.y - 50)
        b.drag(1.1, b.x + 100, b.y - 80)
        self.assertEqual((b.mode, b.hidden_edge), ("drag", None))

    def test_ceiling_impact_does_not_suspend_gravity(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.place(700)
        b.mode, b.y, b.vy = "air", b.ceiling + b.figure + 5, -1000
        b._set_clip("fall", 0)
        b.tick(0.1)
        self.assertEqual(b._role(b.clip), "ceiling")
        y = b.y
        b.tick(0.2)
        self.assertGreater(b.y, y)

    def test_drag_reacts_once_to_top_and_bottom_and_recovers_between(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.set_ground(0, 1440, 860, 38)
        b.place(700)
        if not {"drag", "ceiling", "floor"} <= {s["role"] for s in b.repertoire.actions.values()}:
            self.skipTest("no adopted boundary assets")
        b.press(1, 700, 760)
        b.drag(1.1, 700, 500)
        b.tick(1.1)
        b.drag(1.2, -100, -300)
        b.tick(1.2)
        self.assertEqual(b._role(b.clip), "ceiling")
        self.assertGreaterEqual(b.y - b._head_clearance(), b.ceiling - 0.01)
        self.assertGreaterEqual(b.x, b.left + b.figure * EDGE)
        clip, started = b.clip, b.clip_started
        for n in range(1, 6):
            b.tick(1.2 + n * 0.1)
        self.assertEqual((b.clip, b.clip_started), (clip, started))
        b.drag(1.8, 700, 400)
        b.tick(1.8)
        self.assertEqual(b._role(b.clip), "drag")
        b.drag(1.9, 700, 1000)
        b.tick(1.9)
        self.assertEqual(b._role(b.clip), "floor")
        self.assertEqual(b.y, b.floor)
        clip, started = b.clip, b.clip_started
        b.tick(2.0)
        self.assertEqual((b.clip, b.clip_started), (clip, started))
        b.drag(2.1, 700, 400)
        b.tick(2.1)
        self.assertEqual(b._role(b.clip), "drag")
        self.assertEqual(b.release(2.2, 700, 400), "drop")
        self.assertEqual(b.mode, "air")
        run(b, 2.2, 3)
        self.assertEqual((b.mode, b.y), ("ground", b.floor))

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
            self.assertEqual(b.clip, "feed_expect")
        b.feed(30.0)
        self.assertEqual(b.clip, "peek")                      # 吃不下只歪头，不跺脚
        self.assertIn(b.bubble.text, LINES["full"])


class AgentPresenceTest(unittest.TestCase):
    def test_perception_heartbeat_ends_without_clearing_other_busy_work(self):
        b = brain()
        b.observe(1.0, {**ready(), "state": "inspect"})
        self.assertEqual(b.clip, "inspect")
        b.observe(2.0, {**ready(), "state": None})
        self.assertIsNone(b.state_clip)
        self.assertEqual(b.clip, "idle")
        b.chat_sent(3.0)
        b.observe(4.0, {**ready(), "state": None,
                        "events": [{"kind": "listening", "ts": 10.0}]})
        self.assertEqual(b.state_source, "local")
        self.assertTrue(b.chat_pending)
        self.assertEqual(b.clip, "think")

    def test_short_perception_events_play_once_without_stealing_a_drawing(self):
        b = brain()
        b.observe(1.0, {**ready(), "state": None,
                        "events": [{"kind": "inspecting", "ts": 10.0}]})
        self.assertEqual(b.clip, "inspect")
        b.observe(2.0, {**ready(), "state": None})
        self.assertEqual(b.clip, "inspect")             # 一次性事件不被空心跳立即擦掉
        b.observe(3.0, ready(events=[{"kind": "drawing", "ts": 11.0}]))
        self.assertEqual(b.media, "drawing")
        b.clear_state(4.0)
        run(b, 4.0, 2)
        self.assertEqual(b.clip, "drawing")
        b.observe(6.1, {**ready(), "state": None,
                        "events": [{"kind": "listening", "ts": 12.0}]})
        self.assertEqual(b.media, "drawing")
        self.assertEqual(b.clip, "drawing")

    def test_one_shot_panel_actions_survive_the_next_empty_heartbeat(self):
        b = brain()
        b.observe(1.0, {**ready(), "state": None,
                        "events": [{"kind": "state", "state": "handoff", "ts": 10.0}]})
        self.assertEqual(b.clip, "handoff")
        b.observe(2.0, {**ready(), "state": None})
        self.assertEqual(b.clip, "handoff")

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
        self.assertEqual(b.state_name, "reply")
        self.assertTrue(any(s.clip == "reply" for s in b.plan))
        self.assertEqual((b.bubble.kind, b.bubble.text), ("say", "回来啦，刚在想你说的事。"))
        b.chat_failed(9.0, "本机对话暂时不可用")
        self.assertEqual(b.bubble.kind, "status")

    def test_bot_offline_keeps_her_awake_and_online_reports_status(self):
        b = brain()
        b.observe(1.0, ready())
        b.observe(2.0, ready(bot="offline"))
        self.assertIn(b.bubble.text, LINES["offline"])
        t, clips = run(b, 2.0, 20)
        self.assertNotIn("sleep", clips)
        b.observe(t, ready())
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
        self.assertEqual(b.state_name, "reply")
        self.assertTrue(any(s.clip == "reply" for s in b.plan))
        self.assertNotIn("sleep", [s.clip for s in b.plan])
        b.chat_failed(4.0, "这次没接上。")
        self.assertIsNone(b.state_source)

    def test_mood_and_night_shift_what_she_chooses(self):
        def weights(**presence):
            b = brain(seed=11)
            b.observe(0.0, ready(**presence))
            with patch.object(b, "_weighted", return_value="idle") as choose:
                b._choose(1)
            return choose.call_args.args[0]
        happy = weights(mood=0.9)
        grumpy_night = weights(mood=-0.9, circadian="熬到半夜")
        self.assertGreater(grumpy_night["nap"], happy["nap"])
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
        run(b, 2.0, 3.4)
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


def spelled(seed=7):
    """A brain with the adopted repertoire (spell sheets included) on a wide screen."""
    b = PetBrain(rng=random.Random(seed), now=0.0)
    b.set_ground(0, 1440, 860, 30)
    b.place(300)
    return b


class SpellTest(unittest.TestCase):
    def setUp(self):
        if not spelled()._can_warp():
            self.skipTest("spell sheets not adopted")

    def warp(self, via, x=1100):
        b = spelled()
        b._warp_request = {"x": x, "via": via, "cause": "spell"}
        b._queue(Step("warp_cast"), Step("idle", 2), now=0.0, interrupt=True)
        self.assertEqual((b.mode, b.warp["phase"]), ("ground", "cast"))
        seen = {"clips": [], "clip_sides": set(), "below": False, "xs": set(), "portals": 0}

        def each(t):
            seen["portals"] = max(seen["portals"], len(b.portals))
            if b.portal_clip:
                seen["clip_sides"].add(b.portal_clip[1])
            seen["below"] |= b.y - b._head_clearance("spell_dive") > b.floor     # even her raised hands
            seen["xs"].add(round(b.x))
        t, clips = run(b, 0.0, 9, fps=60, each=each)
        return b, t, clips, seen

    def test_a_floor_circle_swallows_her_and_another_one_lets_her_out_far_away(self):
        b, t, clips, seen = self.warp("floor")
        for beat in ("warp_cast", "warp_crouch", "warp_leap", "warp_sink", "warp_rise", "warp_tada", "warp_land"):
            self.assertIn(beat, clips)
        self.assertEqual(seen["portals"], 2)
        self.assertEqual(seen["clip_sides"], {"below"})
        self.assertTrue(seen["below"])                       # fully through the plane, hidden
        self.assertEqual(seen["xs"], {300, 1100})            # no walking in between: a real jump cut
        self.assertEqual((b.mode, b.x, b.y), ("ground", 1100, b.floor))
        self.assertIsNone(b.portal_clip)
        run(b, t, 1)
        self.assertEqual(b.portals, [])                      # both circles closed and released

    def test_a_ceiling_circle_drops_her_from_just_under_the_menu_bar(self):
        b, _, clips, seen = self.warp("ceiling")
        self.assertEqual(seen["clip_sides"], {"below", "above"})
        self.assertIn("warp_tada", clips)
        self.assertEqual((b.mode, b.y), ("ground", b.floor))

    def test_the_ceiling_is_only_used_when_the_screen_is_tall_enough(self):
        b = spelled()
        b.set_ground(0, 1440, 860, 500)
        b._warp_request = {"x": 1100, "via": "ceiling", "cause": "spell"}
        b._queue(Step("warp_cast"), now=0.0, interrupt=True)
        self.assertEqual(b.warp["via"], "floor")

    def test_she_cannot_be_grabbed_mid_spell_and_new_steps_wait_for_the_landing(self):
        b = spelled()
        b._warp_request = {"x": 1100, "via": "floor", "cause": "spell"}
        b._queue(Step("warp_cast"), now=0.0, interrupt=True)
        run(b, 0.0, 2.6, fps=60)
        b.press(2.7, b.x, b.y - 40)
        b.drag(2.8, b.x + 200, b.y - 200)
        self.assertEqual(b.mode, "warp")
        b._queue(Step("wave"), now=2.9, interrupt=True)       # e.g. a cue from the panel
        self.assertEqual(b.mode, "warp")
        _, clips = run(b, 2.9, 7, fps=60)
        self.assertLess(clips.index("warp_land"), clips.index("wave"))

    def test_a_cue_while_she_is_still_casting_closes_the_circles(self):
        b = spelled()
        b._warp_request = {"x": 1100, "via": "floor", "cause": "spell"}
        b._queue(Step("warp_cast"), now=0.0, interrupt=True)
        run(b, 0.0, 1.0, fps=60)
        self.assertEqual(len(b.portals), 2)
        b.observe(1.0, ready(events=[{"kind": "cue", "id": "c", "ts": 5.0, "action": "cheer"}]))
        self.assertEqual((b.clip, b.x), ("cheer", 300))
        self.assertIsNone(b.warp)
        run(b, 1.0, 1.0, fps=60)
        self.assertEqual(b.portals, [])

    def test_a_narrow_screen_turns_the_spell_into_a_flourish(self):
        b = spelled()
        b.set_ground(0, 500, 860, 30)
        b._warp_request = {"x": None, "via": None, "cause": "spell"}
        b._queue(Step("warp_cast"), now=0.0, interrupt=True)
        self.assertEqual((b.mode, b.clip), ("ground", "conjure"))

    def test_an_at_brings_her_to_the_cursor_only_when_it_is_known_and_far(self):
        b = spelled()
        b.observe(1.0, ready(events=[{"kind": "mention", "ts": 10.0}]))
        self.assertIsNone(b.warp)                            # the cursor was never reported
        b = spelled()
        b.hover(0.5, 1200, 500, near=False)
        with patch.object(b.rng, "random", return_value=0.0):
            b.observe(1.0, ready(events=[{"kind": "mention", "ts": 10.0}]))
        self.assertEqual(b.clip, "warp_cast")
        self.assertIn(b.bubble.text, LINES["warp_called"])
        _, clips = run(b, 1.0, 10, fps=60)
        self.assertAlmostEqual(b.x, 1200, delta=1)
        self.assertTrue(any(b._role(clip) == "greet" or clip == "phone" for clip in clips))

    def test_drawing_conjures_a_canvas_paints_and_turns_it_round_when_it_lands(self):
        b = spelled()
        b.observe(1.0, ready(events=[{"kind": "drawing", "ts": 10.0}]))
        self.assertEqual(b.clip, "conjure")
        t, clips = run(b, 1.0, 20)
        self.assertEqual(b.clip, "paint")
        self.assertTrue(any(p.kind == "paint" for p in b.particles))
        b.observe(t, ready(events=[{"kind": "drew", "ts": 11.0}]))
        self.assertEqual(b.clip, "paint_reveal")
        self.assertIn(b.bubble.text, LINES["drew"])

    def test_a_search_opens_the_tome_even_when_the_heartbeat_said_inspect_first(self):
        b = spelled()
        b.observe(1.0, {**ready(events=[{"kind": "searching", "ts": 10.0}]), "state": "inspect"})
        t, _ = run(b, 1.0, 6)
        self.assertEqual(b.clip, "tome")
        self.assertTrue(any(p.kind == "glyph" for p in b.particles))
        b.observe(t, {**ready(events=[{"kind": "said", "ts": 11.0}]), "state": None})
        self.assertEqual(b.clip, "tome_found")
        self.assertIn(b.bubble.text, LINES["found"])

    def wiggle(self, b, start, x, y, steps=24):
        t = start
        for i in range(steps):
            t += 1 / 30
            b.hover(t, x + (12 if (i // 3) % 2 else -12), y, near=False)
            b.tick(t)
        return t

    def test_wiggling_the_cursor_in_front_of_her_ends_in_a_pounce(self):
        b = spelled()
        target = b.x + b.figure * 1.4
        t = self.wiggle(b, 5.0, target, b.floor - b.figure * 0.6)
        self.assertEqual(b.clip, "pounce_crouch")
        self.assertEqual(b.facing, 1)
        said = []

        def each(now):
            b.hover(now, target, b.floor - b.figure * 0.6, near=False)
            said.append(b.bubble.text if b.bubble else None)
        t, clips = run(b, t, 3, fps=60, each=each)
        self.assertIn("pounce_leap", clips)
        self.assertIn("pounce_catch", clips)
        self.assertGreater(b.x, 300 + b.figure * 0.6)          # she actually leapt forward
        self.assertEqual(b.y, b.floor)
        self.assertTrue(set(said) & set(LINES["pounce_caught"]))
        t = self.wiggle(b, t + 1, b.x + b.figure * 1.4, b.floor - b.figure * 0.6)
        self.assertNotEqual(b.clip, "pounce_crouch")         # cooldown

    def test_a_still_or_distant_cursor_is_not_prey(self):
        b = spelled()
        t = 5.0
        for i in range(40):
            t += 1 / 30
            b.hover(t, b.x + b.figure * 1.4, b.floor - b.figure * 0.6, near=False)
            b.tick(t)
        self.assertNotIn("pounce", b.clip)
        self.wiggle(b, t, b.x + b.figure * 4, b.floor - b.figure * 0.6)
        self.assertNotIn("pounce", b.clip)

    def test_a_sneeze_breathes_a_little_fire_and_sometimes_teleports_her(self):
        b = spelled()
        b._sneeze_warp = True
        b._queue(Step("sneeze"), Step("idle", 2), now=0.0, interrupt=True)
        kinds = set()
        t, clips = run(b, 0.0, 1.2, fps=60, each=lambda now: kinds.update(p.kind for p in b.particles))
        self.assertIn("ember", kinds)
        self.assertEqual(b.clip, "sneeze")                    # the CHOO frame plays before anything else
        said = set()
        t, clips = run(b, t, 9, fps=60, each=lambda now: said.add(b.bubble.text if b.bubble else None))
        self.assertEqual(b.mode, "ground")
        self.assertNotEqual(round(b.x), 300)
        self.assertIn("startle", clips)
        self.assertTrue(said & set(LINES["sneeze_warp"]))

    def test_reduced_motion_and_the_legacy_repertoire_never_cast(self):
        b = brain()
        self.assertFalse(b._can_warp())
        b.magic(1.0)
        self.assertEqual(b.clip, "wave")
        still = PetBrain(rng=random.Random(3), now=0.0, motion=False)
        still.set_ground(0, 1440, 860, 30)
        still.place(300)
        self.assertFalse(still.magic(1.0))
        with patch.object(still, "_weighted", side_effect=lambda w: max(w, key=w.get)):
            self.assertEqual(still._choose(10.0)[0].clip != "warp_cast", True)
