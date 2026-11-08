"""Prop cleanup stays continuous through both autonomous and manual scheduling."""
import random
import unittest
from unittest.mock import patch

from regular.pet_brain import CLIPS, PetBrain, Step
from regular.pet_repertoire import ACTIONS, clips
from regular.pet_routines import ACTIVITY_ENDINGS


class ActivityEndingFlowTest(unittest.TestCase):
    def setUp(self):
        # Exercise the contract even before optional artwork is adopted.
        self.clip_patch = patch.dict(CLIPS, clips(ACTIONS))
        self.clip_patch.start()
        self.addCleanup(self.clip_patch.stop)

    def brain(self):
        b = PetBrain(rng=random.Random(7), now=0)
        b.repertoire.actions = dict(ACTIONS)
        b.set_ground(0, 1440, 860, 0)
        b.place(700)
        return b

    def begin(self, b, name, now=1):
        b._set_clip(name, now, restart=True)
        b._queue(Step(name, 60 if CLIPS[name][3] else None), now=now, interrupt=True)

    def advance_for(self, b, start, seconds):
        seen = []
        for i in range(1, round(seconds * 60) + 1):
            b.tick(start + i / 60)
            if not seen or seen[-1] != b.clip:
                seen.append(b.clip)
        return seen

    def test_short_laptop_and_long_library_keep_their_duration_and_exact_prop(self):
        for source in ("life_laptop_type", "life_laptop_think"):
            with self.subTest(source=source):
                b = self.brain()
                with (patch.object(b, "_weighted", return_value="laptop"),
                      patch.object(b, "_pick", return_value=source)):
                    short = b._choose(1)
                self.assertEqual(short[0].clip, source)
                self.assertTrue(8 <= short[0].duration <= 15)
                self.assertEqual(short[-2].clip, "ending_work_put_away")
                with (patch("regular.pet_brain.choose_routine", return_value=("ambient:work", [(source, 70)])),
                      patch.object(b.repertoire, "choose", return_value=source)):
                    long = b._routine_steps(2)
                self.assertEqual((long[0].clip, long[0].duration), (source, 70))
                self.assertEqual(long[-2].clip, "ending_work_put_away")

    def test_next_during_loop_skips_unplayed_result_and_puts_same_object_away(self):
        for source, (result, _, cleanup) in ACTIVITY_ENDINGS.items():
            with self.subTest(source=source):
                b = self.brain()
                self.begin(b, source)
                b._queue(Step(result), Step(cleanup), now=1)
                with patch.object(b, "_routine_steps", return_value=[Step("idle", 60)]):
                    self.assertTrue(b.next_activity(2))
                self.assertEqual(b.clip, cleanup)
                self.assertNotIn(result, [s.clip for s in b.plan])
                self.assertEqual(sum(s.clip == cleanup for s in (b.step, *b.plan)), 1)
                seen = self.advance_for(b, 2, 6)
                self.assertEqual(seen.count(cleanup), 1)
                self.assertEqual(b.clip, "idle")

    def test_repeated_next_during_result_preserves_playhead_and_one_cleanup(self):
        for source, endings in ACTIVITY_ENDINGS.items():
            for result in endings[:-1]:
                with self.subTest(source=source, result=result):
                    b = self.brain()
                    self.begin(b, result)
                    b._queue(Step(endings[-1]), now=1)
                    step = b.step
                    for now, target in ((1.6, "peek"), (1.8, "idle")):
                        pose = b.pose(now)
                        with patch.object(b, "_routine_steps", return_value=[Step(target, 60)]):
                            self.assertTrue(b.next_activity(now))
                        self.assertIs(b.step, step)
                        self.assertEqual(b.step_started, 1)
                        self.assertEqual(b.clip_started, 1)
                        self.assertEqual(b.pose(now), pose)
                        self.assertEqual([s.clip for s in b.plan].count(endings[-1]), 1)
                    seen = self.advance_for(b, 1.8, 7)
                    self.assertEqual(seen.count(result), 1)
                    self.assertEqual(seen.count(endings[-1]), 1)
                    self.assertEqual(b.clip, "idle")

    def test_repeated_next_during_cleanup_never_replays_or_restores_prop(self):
        for endings in set(ACTIVITY_ENDINGS.values()):
            cleanup = endings[-1]
            with self.subTest(cleanup=cleanup):
                b = self.brain()
                self.begin(b, cleanup)
                step = b.step
                for now in (1.3, 1.7, 2.2):
                    pose = b.pose(now)
                    with patch.object(b, "_routine_steps", return_value=[Step("idle", 60)]):
                        self.assertTrue(b.next_activity(now))
                    self.assertIs(b.step, step)
                    self.assertEqual((b.clip_started, b.step_started), (1, 1))
                    self.assertEqual(b.pose(now), pose)
                    self.assertFalse(any(s.clip in endings for s in b.plan))
                self.advance_for(b, 2.2, 3)
                self.assertEqual(b.clip, "idle")

    def test_missing_cleanup_keeps_manual_switch_available(self):
        b = self.brain()
        self.begin(b, "life_laptop_type")
        del b.repertoire.actions["ending_work_put_away"]
        with patch.object(b, "_routine_steps", return_value=[Step("idle", 60)]):
            self.assertTrue(b.next_activity(2))
        self.assertNotIn("ending_work_put_away", [s.clip for s in (b.step, *b.plan)])
        self.advance_for(b, 2, 3)
        self.assertEqual(b.clip, "idle")

    def test_urgent_real_state_discards_unfinished_local_result(self):
        for trigger in ("state", "agent", "chat"):
            with self.subTest(trigger=trigger):
                b = self.brain()
                self.begin(b, "life_laptop_type")
                b._queue(Step("ending_work_clear"), Step("ending_work_put_away"), now=1)
                if trigger == "state":
                    b.set_state("checkup", 2, seconds=30)
                elif trigger == "agent":
                    b.observe(2, {"bot": "ready", "available": True, "qq_connected": True,
                                  "events": [{"kind": "searching", "ts": 2}]})
                else:
                    b.chat_sent(2)
                self.assertFalse(b.next_activity(2.1))
                self.assertFalse(any(s.clip.startswith("ending_") for s in (b.step, *b.plan) if s))
                self.assertFalse(any(c.startswith("ending_") for c in self.advance_for(b, 2, 5)))

    def test_holds_do_not_gain_fictional_results_in_the_activity_selector(self):
        b = self.brain()
        for hold in ("agent", "state", "chat"):
            with self.subTest(hold=hold):
                b._set_clip("life_laptop_type", 1, restart=True)
                b._queue(Step("life_laptop_type", hold=hold), now=1, interrupt=True)
                b._queue_manual([Step("idle", 60)], 2)
                self.assertFalse(any(s.clip.startswith("ending_") for s in (b.step, *b.plan) if s))


if __name__ == "__main__":
    unittest.main()
