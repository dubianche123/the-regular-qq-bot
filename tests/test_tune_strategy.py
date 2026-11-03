"""本地策略比较：独立分母、留出隔离、时间冻结和原子策略发布。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from regular import checkup, tune


EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/checkup-benchmark.example.json"


class CheckupBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.config = SimpleNamespace(CHECKUP_POLICY_FILE=str(self.base / "checkup.json"),
                                      EVAL_DIR=str(self.base / "evals"))

    def run_benchmark(self, document=None):
        source = self.base / "benchmark.json"
        source.write_text(json.dumps(document or self.document), encoding="utf-8")
        return tune.tune_checkup(self.config, benchmark=source, write=False)

    def test_unreported_positive_is_a_false_negative_and_totals_have_denominators(self):
        result = self.run_benchmark()
        self.assertEqual(result["scope"], "independent_benchmark")
        for split in ("train", "validation"):
            base = result["benchmark"]["splits"][split]["base"]
            cand = result["benchmark"]["splits"][split]["cand"]
            self.assertEqual([base[k] for k in ("tp", "fp", "fn", "tn")], [1, 1, 1, 1])
            self.assertEqual([cand[k] for k in ("tp", "fp", "fn", "tn")], [1, 0, 1, 2])
            self.assertEqual(cand["recall"], .5)
            self.assertEqual(cand["fnr"], .5)
            self.assertEqual(cand["fpr"], 0)
        self.assertIsNotNone(result["proposal"])
        self.assertIn("FN", tune.render_checkup(result))

    def test_validation_does_not_train_the_candidate_and_can_reject_it(self):
        doc = copy.deepcopy(self.document)
        held = doc["samples"][1]
        next(x for x in held["labels"] if x["anchor"] == "held-negative")["positive"] = True
        result = self.run_benchmark(doc)
        row = result["signals"][0]
        self.assertIn("confused_markers", row["changes"])
        self.assertEqual(row["cand"]["train"]["fp"], 0)
        self.assertEqual(row["base"]["validation"]["fn"], 1)
        self.assertEqual(row["cand"]["validation"]["fn"], 2)
        self.assertFalse(row["recommended"])
        self.assertIsNone(result["proposal"])

    def test_unannotated_inputs_are_not_implicitly_negative(self):
        doc = copy.deepcopy(self.document)
        doc["samples"][0]["labels"].pop()
        result = self.run_benchmark(doc)
        train = result["benchmark"]["splits"]["train"]["base"]
        self.assertFalse(train["evidence_complete"])
        self.assertIsNone(train["tn"])
        self.assertIsNone(train["fnr"])
        self.assertTrue(train["unlabeled_units"])
        self.assertIsNone(result["proposal"])

    def test_unknown_prediction_invalidates_complete_coverage(self):
        original = checkup.detect
        def detect(*args, **kwargs):
            rows = original(*args, **kwargs)
            return rows + [{"anchor": "not-annotated", "group": "G"}]
        with patch.object(checkup, "detect", side_effect=detect):
            result = self.run_benchmark()
        m = result["benchmark"]["splits"]["validation"]["base"]
        self.assertFalse(m["evidence_complete"])
        self.assertTrue(m["unknown_predictions"])
        self.assertIsNone(m["fp"])
        self.assertIsNone(result["proposal"])

    def test_missing_positive_or_negative_denominator_is_not_verified(self):
        doc = copy.deepcopy(self.document)
        for label in doc["samples"][1]["labels"]:
            label["positive"] = False
        result = self.run_benchmark(doc)
        held = result["benchmark"]["splits"]["validation"]["base"]
        self.assertTrue(held["evidence_complete"])
        self.assertIsNone(held["recall"])
        self.assertIsNone(held["fnr"])
        self.assertIsNone(result["proposal"])

    def test_same_day_cannot_cross_train_validation(self):
        doc = copy.deepcopy(self.document)
        doc["samples"][1]["day"] = doc["samples"][0]["day"]
        with self.assertRaisesRegex(ValueError, "crosses train and validation"):
            self.run_benchmark(doc)

    def test_future_input_cannot_enter_frozen_benchmark(self):
        doc = copy.deepcopy(self.document)
        doc["samples"][0]["data"]["incoming"][0]["ts"] = doc["samples"][0]["now"] + 1
        with self.assertRaisesRegex(ValueError, "future"):
            self.run_benchmark(doc)

    def test_candidate_labels_have_no_recall_claim_and_now_is_frozen(self):
        source = self.base / "fixture.json"
        source.write_text(json.dumps(self.document), encoding="utf-8")
        sample = tune.load_checkup_benchmark(source)[0]
        policy = checkup.validate_policy({})
        found = checkup.detect(sample["ctx"], policy, signals=[sample["signal"]], now=sample["now"])
        labels = {f["id"]: {"verdict": "yes", "signal": sample["signal"]} for f in found}
        bench = tune._CheckupBench({"day": sample["ctx"]}, labels, now=1234)
        original = checkup.detect
        with patch.object(checkup, "detect", wraps=original) as detector:
            result = bench.metrics(policy, sample["signal"], ["day"])
            bench.found({**policy, "reaction_seconds": 45}, sample["signal"], ["day"])
        self.assertEqual(result["scope"], "candidate_only")
        for name in ("fn", "tn", "recall", "fpr", "fnr"):
            self.assertIsNone(result[name])
        self.assertEqual([c.kwargs["now"] for c in detector.call_args_list], [1234, 1234])


class PolicyPublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.active = self.base / "checkup.json"
        self.active.write_text(json.dumps({"policy": {"reaction_seconds": 60}}), encoding="utf-8")
        self.config = SimpleNamespace(CHECKUP_POLICY_FILE=str(self.active),
                                      RECALL_POLICY_FILE=str(self.base / "recall.json"))

    def proposal(self, seconds):
        path = self.base / f"report-{seconds}.json"
        path.write_text(json.dumps({"proposal": {"name": "checkup", "policy": {
            "reaction_seconds": seconds}}}), encoding="utf-8")
        return path

    def test_failed_replace_keeps_previous_active_policy(self):
        previous = self.active.read_text(encoding="utf-8")
        with patch.object(tune.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                tune.apply_proposal(self.config, self.proposal(45))
        self.assertEqual(self.active.read_text(encoding="utf-8"), previous)
        self.assertEqual(list(self.base.glob("*.tmp")), [])

    def test_repeated_apply_preserves_each_history_even_in_same_second(self):
        with patch.object(tune, "_now_stamp", return_value="same-second"):
            tune.apply_proposal(self.config, self.proposal(45))
            tune.apply_proposal(self.config, self.proposal(30))
        history = list((self.base / "history").glob("*.json"))
        self.assertEqual(len(history), 2)
        self.assertEqual({json.loads(p.read_text())["policy"]["reaction_seconds"] for p in history}, {60, 45})
        restored = next(p for p in history if json.loads(p.read_text())["policy"]["reaction_seconds"] == 45)
        tune.apply_proposal(self.config, restored)
        self.assertEqual(checkup.load_policy(self.active)["reaction_seconds"], 45)

    def test_malformed_report_is_a_readable_value_error(self):
        source = self.base / "bad.json"
        source.write_text('{"proposal":{"policy":{}}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            tune.apply_proposal(self.config, source)
        self.assertEqual(checkup.load_policy(self.active)["reaction_seconds"], 60)


class RetrievalStrategyMetricTest(unittest.TestCase):
    def test_micro_totals_do_not_average_case_rates(self):
        rows = {
            "small": {"metrics": {"evidence_complete": True, "tp": 1, "fp": 0, "fn": 1, "tn": 2}},
            "large": {"metrics": {"evidence_complete": True, "tp": 9, "fp": 1, "fn": 0, "tn": 0}},
        }
        result = tune._retrieve_totals(rows)
        self.assertEqual(result["tp"], 10)
        self.assertAlmostEqual(result["precision"], 10 / 11)
        self.assertAlmostEqual(result["recall"], 10 / 11)
        self.assertAlmostEqual(result["fpr"], 1 / 3)

    def test_partial_cases_do_not_become_full_error_rates(self):
        result = tune._retrieve_totals({"old": {"metrics": {
            "evidence_complete": False, "reason": "candidate-only checks"}}})
        self.assertFalse(result["evidence_complete"])
        self.assertIsNone(result["fn"])
        self.assertIsNone(result["fpr"])
        self.assertEqual(result["coverage_gaps"][0]["id"], "old")

    def test_search_cannot_trade_hidden_false_positives_for_check_passes(self):
        import recall
        def evaluate(_config, _cases, policy):
            return {"case": {"passed": True, "picked": ["relevant"], "metrics": {
                "evidence_complete": True, "tp": 1, "fp": int(policy["min_score"] == 2),
                "fn": 0, "tn": 1}}}
        with patch.object(tune, "_retrieve_eval", side_effect=evaluate), \
                patch.object(recall, "POLICY_SPACE", {"min_score": (1, 2)}):
            cand, _ = tune._retrieve_search(None, [{"id": "case"}], {"min_score": 1})
        self.assertEqual(cand, {"min_score": 1})


if __name__ == "__main__":
    unittest.main()
