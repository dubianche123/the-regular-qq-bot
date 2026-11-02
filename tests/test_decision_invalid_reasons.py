"""固定原因码只描述结构，不能改变分诊或记录模型正文。"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from regular import decisions


QUESTIONS = [
    {"name": "lookup", "type": "choice", "choices": [
        {"value": "search"}, {"value": "none"}]},
    {"name": "other", "type": "choice", "choices": [
        {"value": "yes"}, {"value": "no"}]},
]


def answer(name, value):
    return {"name": name, "type": "choice", "choice": value}


class Recorder:
    def __init__(self):
        self.rows = []

    def append(self, kind, group, **fields):
        self.rows.append({"kind": kind, **fields})


class InvalidReasonTest(unittest.TestCase):
    def test_per_question_reasons_leave_other_choice_usable(self):
        marker = "PRIVATE_REFUSAL_OR_MODEL_OUTPUT"
        cases = [
            ([], "missing"),
            ([{"name": "lookup", "type": "refusal", "refusal": marker}], "refusal"),
            ([{"name": "lookup", "type": marker}], "invalid_type"),
            ([answer("lookup", marker)], "invalid_choice"),
            ([{**answer("lookup", "search"), "confidence": True}], "invalid_number"),
            ([{**answer("lookup", "search"), "probabilities": {marker: 1}}], "invalid_probabilities"),
            ([{**answer("lookup", "search"), "probabilities": [
                {"value": "search", "probability": 1.5}]}], "invalid_probabilities"),
        ]
        for invalid, expected in cases:
            with self.subTest(expected=expected, invalid=invalid):
                body = {"answers": invalid + [answer("other", "yes")]}
                original_interface = decisions.parse_each(body, QUESTIONS)
                self.assertIsNone(original_interface["lookup"])
                self.assertEqual(original_interface["other"]["choice"], "yes")
                choices, scores = decisions.usable_choices(body, QUESTIONS)
                self.assertNotIn("lookup", choices)
                self.assertEqual(choices["other"], "yes")
                self.assertEqual(scores["lookup"], {
                    "invalid": True, "invalid_reason": expected})
                log = Recorder()
                decisions._log_scores(log, "group", "triage", scores)
                self.assertEqual(log.rows[0]["invalid"], ["lookup"])
                self.assertEqual(log.rows[0]["invalid_reasons"], {
                    "lookup": expected})
                self.assertNotIn(marker, json.dumps(log.rows))

    def test_whole_response_failure_and_margin_gate_are_unchanged(self):
        for body in (None, {"answers": "not a list"}, {"answers": [
            answer("other", "yes"), answer("other", "no")]}, {"answers": [
            answer("unknown", "yes")]}, {"answers": [["not a dict"]]}):
            self.assertIsNone(decisions.parse_each(body, QUESTIONS))
            self.assertEqual(decisions.usable_choices(body, QUESTIONS), (None, {}))
        body = {"answers": [{**answer("lookup", "search"),
            "probabilities": [{"value": "search", "probability": 0.51},
                              {"value": "none", "probability": 0.49}]},
            answer("other", "yes")]}
        choices, scores = decisions.usable_choices(body, QUESTIONS, min_margin=0.3)
        self.assertNotIn("lookup", choices)
        self.assertTrue(scores["lookup"]["dropped"])
        self.assertNotIn("invalid_reason", scores["lookup"])

    def test_only_fixed_codes_and_bounded_question_names_are_logged(self):
        marker = "PRIVATE_NOT_AN_ALLOWED_CODE"
        scores = {f"q{i:02}": {"invalid": True, "invalid_reason": "missing"}
                  for i in range(decisions._MAX_LOGGED_SCORES + 3)}
        scores["q00"]["invalid_reason"] = marker
        log = Recorder()
        decisions._log_scores(log, "group", "triage", scores)
        row = log.rows[0]
        self.assertEqual(len(row["invalid"]), decisions._MAX_LOGGED_SCORES)
        self.assertNotIn("q00", row["invalid_reasons"])
        self.assertEqual(len(row["invalid_reasons"]), decisions._MAX_LOGGED_SCORES - 1)
        self.assertNotIn(marker, json.dumps(row))


class SingleCallTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_answer_keeps_single_call_and_no_action(self):
        marker = "PRIVATE_MODEL_REFUSAL"
        log = Recorder()
        tracker = decisions.FeedbackTracker(120, 180, log=log)

        def response(_text, questions):
            answers = []
            for q in questions:
                if q["name"] == "lookup":
                    answers.append({"name": q["name"], "type": "refusal", "refusal": marker})
                else:
                    answers.append(answer(q["name"], q["choices"][0]["value"]))
            return {"answers": answers}

        call = AsyncMock(side_effect=response)
        with patch.object(decisions.providers, "call_decision", call):
            result = await tracker.assess("group", "user", "今天什么天气", is_direct=True,
                                          want_lookup=True, now=10.0)
        call.assert_awaited_once()
        self.assertIsNone(result["lookup"])
        self.assertEqual(log.rows[0]["invalid_reasons"], {"lookup": "refusal"})
        self.assertNotIn(marker, json.dumps(log.rows))


if __name__ == "__main__":
    unittest.main()
