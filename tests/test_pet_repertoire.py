"""The adopted art must remain reachable without losing state-specific variety."""
import random
import unittest

from regular.pet_repertoire import ACTIONS, Repertoire, available, clips


class RepertoireTest(unittest.TestCase):
    def test_each_state_pool_visits_every_variant_before_repeating(self):
        actions = available()
        pools = {(a["role"], a["posture"], a["family"]) for a in actions.values()}
        repertoire = Repertoire(random.Random(13), actions)
        for role, posture, family in pools:
            expected = {name for name, spec in actions.items()
                        if (spec["role"], spec["posture"], spec["family"]) == (role, posture, family)}
            with self.subTest(role=role, posture=posture, family=family):
                first = [repertoire.choose(role, posture=posture, family=family) for _ in expected]
                second = [repertoire.choose(role, posture=posture, family=family) for _ in expected]
                self.assertEqual(set(first), expected)
                self.assertEqual(set(second), expected)
                if len(expected) > 1:
                    self.assertNotEqual(first[-1], second[0])

    def test_sleep_and_reply_have_distinct_mood_and_posture_pools(self):
        actions = available()
        sleep = [a for a in actions.values() if a["role"] == "sleep"]
        reply = [a for a in actions.values() if a["role"] == "reply"]
        self.assertGreaterEqual(len(sleep), 12)
        self.assertGreaterEqual(len({a["family"] for a in sleep}), 3)
        self.assertGreaterEqual(sum(a["posture"] == "air" and a["family"] == "joyful" for a in reply), 4)
        self.assertTrue(any(a["posture"] == "sit" and a["family"] == "sleepy" for a in reply))
        repertoire = Repertoire(random.Random(1), actions)
        self.assertEqual(repertoire.choose("sleep", posture="air", fallback="sleep"), "sleep")

    def test_only_adopted_art_becomes_a_playable_clip(self):
        actions = available()
        self.assertEqual(set(actions), set(ACTIONS))
        for name, clip in clips(actions).items():
            with self.subTest(action=name):
                self.assertEqual(clip[0], name)
                self.assertTrue(all(0 <= frame < 4 for frame in clip[1]))
                self.assertGreater(clip[2], 0)


if __name__ == "__main__":
    unittest.main()
