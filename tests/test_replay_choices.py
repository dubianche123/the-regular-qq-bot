"""预置分诊必须保留完整概率表，才能回放依赖三选概率的语气线索。"""
import unittest
from unittest.mock import AsyncMock, patch

import config
from regular import decision_prompts, decisions, replay_worker


class ReplayChoiceBodyTest(unittest.TestCase):
    def test_three_choice_preset_has_all_probabilities(self):
        questions = decision_prompts.request_kind_questions()
        body = replay_worker._choices_to_decision_body({"request_kind": "playful"}, questions)
        choices, scores = decisions.usable_choices(body, questions)
        self.assertEqual(choices, {"request_kind": "playful"})
        probabilities = scores["request_kind"]["probabilities"]
        self.assertEqual(set(probabilities), {"sincere", "playful", "not_request"})
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)
        self.assertAlmostEqual(probabilities["playful"], 0.95)
        self.assertAlmostEqual(probabilities["sincere"], 0.025)

    def test_binary_preset_retains_its_existing_margin(self):
        questions = decision_prompts.draw_questions()
        body = replay_worker._choices_to_decision_body({"draw_check": "draw"}, questions)
        choices, scores = decisions.usable_choices(body, questions)
        self.assertEqual(choices, {"draw_check": "draw"})
        self.assertEqual(scores["draw_check"]["probabilities"], {"draw": 0.95, "decline": 0.05})
        self.assertAlmostEqual(scores["draw_check"]["margin"], 0.9)

    def test_single_choice_has_complete_probability_mass(self):
        questions = [{"name": "only", "type": "choice", "choices": [{"value": "yes"}]}]
        body = replay_worker._choices_to_decision_body({"only": "yes"}, questions)
        answers = decisions.parse_answers(body, questions)
        self.assertEqual(answers["only"]["choice"], "yes")
        self.assertEqual(answers["only"]["probabilities"], {"yes": 1.0})


class ReplayPlayfulSignalTest(unittest.IsolatedAsyncioTestCase):
    async def test_preset_playful_reaches_the_real_tone_gate_but_sincere_does_not(self):
        for pick, expected in (("playful", True), ("sincere", False)):
            with self.subTest(pick=pick):
                tracker = decisions.FeedbackTracker(120.0, 180.0)
                call = AsyncMock(side_effect=lambda text, questions:
                                 replay_worker._choices_to_decision_body({"request_kind": pick}, questions))
                with patch.object(config, "DECISION_ENABLED", True), \
                     patch.object(config, "DECISION_MIN_MARGIN", 0.25), \
                     patch.object(decisions.providers, "call_decision", call):
                    result = await tracker.assess("replay-group", "replay-user", "陪我接个梗",
                                                  is_direct=True, now=10.0)
                self.assertEqual(result.get("playful_request", False), expected)
                call.assert_awaited_once()
