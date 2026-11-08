"""Curated playback keeps state coverage without making the archive a playlist."""
import random
import unittest

from regular.pet_repertoire import ACTIONS, CURATED_POOLS, Repertoire, available, clips, curated_actions


class RepertoireTest(unittest.TestCase):
    def test_each_state_pool_visits_every_variant_before_repeating(self):
        actions = curated_actions(available())
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
        actions = curated_actions(available())
        sleep = [a for a in actions.values() if a["role"] == "sleep"]
        reply = [a for a in actions.values() if a["role"] == "reply"]
        self.assertEqual(len(sleep), 3)
        self.assertGreaterEqual(len({a["family"] for a in sleep}), 3)
        self.assertEqual(sum(a["posture"] == "air" and a["family"] == "joyful" for a in reply), 1)
        self.assertTrue(any(a["posture"] == "sit" and a["family"] == "sleepy" for a in reply))
        repertoire = Repertoire(random.Random(1), actions)
        self.assertEqual(repertoire.choose("sleep", posture="air", fallback="sleep"), "sleep")

    def test_retired_variants_stay_loadable_but_never_reenter_automatic_pools(self):
        actions = available()
        repertoire = Repertoire(random.Random(7), actions)
        for role, group in (("music", "music"), ("treasure", "treasure"), ("sleep", "sleep")):
            picked = {repertoire.choose(role) for _ in range(60)}
            self.assertEqual(picked, set(CURATED_POOLS[group]))
        self.assertIn("dragon_treasure_share", clips(actions))  # scripted result
        self.assertIn("dragon_music_flute", clips(actions))    # archived art
        self.assertNotIn("dragon_music_flute", curated_actions(actions))

    def test_loop_filter_survives_repeated_draws_and_has_an_independent_bag(self):
        # Synthetic mixed family exercises the constraint independently of curation.
        actions = {
            "held": dict(role="toy", family="test", posture="stand", loop=True),
            "reveal": dict(role="toy", family="test", posture="stand", loop=False),
        }
        repertoire = Repertoire(random.Random(2), actions)
        for _ in range(20):
            self.assertEqual(repertoire.choose("toy", loop=True), "held")
            self.assertEqual(repertoire.choose("toy", loop=False), "reveal")
        self.assertEqual(repertoire.choose("toy", posture="air", loop=True, fallback="idle"), "idle")

    def test_missing_selected_art_does_not_restore_retired_prop_skins(self):
        repertoire = Repertoire(random.Random(1), {"dragon_music_flute": ACTIONS["dragon_music_flute"]})
        self.assertEqual(repertoire.choose("music", fallback="sing"), "sing")

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
