"""Local care is real, bounded and temporary, shared across display clients."""
import unittest

from regular.pet_care import CareState, CARE_GAIN, CARE_MAX


class SharedCareTest(unittest.TestCase):
    def test_first_feed_and_pat_lift_mood_but_repeated_requests_share_cooldown(self):
        care = CareState(0)
        self.assertEqual(care.apply("feed", 0)[0], "gain")
        self.assertAlmostEqual(care.snapshot(0)["warmth"], CARE_GAIN["feed"])
        self.assertEqual(care.apply("feed", 1)[0], "cooldown")
        self.assertLess(care.snapshot(1)["warmth"], CARE_GAIN["feed"])
        self.assertEqual(care.apply("pat", 1)[0], "gain")
        self.assertEqual(care.apply("feed", 90)[0], "gain")

    def test_cap_and_decay_cannot_create_a_permanent_relationship_score(self):
        care = CareState(0)
        care.apply("feed", 0)
        care.apply("pat", 0)
        care.apply("pat", 45)
        care.apply("pat", 90)
        self.assertEqual(care.snapshot(90)["warmth"], CARE_MAX)
        self.assertEqual(care.apply("feed", 90)[0], "full")
        self.assertTrue(care.facts(90)['fed_recently'])
        self.assertEqual(care.snapshot(390)["warmth"], 0)
        self.assertEqual(care.snapshot(390)["cooldown"], {"pat": 0, "feed": 0})

    def test_overfeeding_keeps_the_existing_gentle_full_reaction_without_extra_gain(self):
        care = CareState(0)
        for t in range(4):
            care.apply("feed", t)
        before = care.snapshot(4)["warmth"]
        result, text = care.apply("feed", 4)
        self.assertEqual(result, "fed_full")
        self.assertIn("吃饱", text)
        self.assertEqual(care.snapshot(4)["warmth"], before)
