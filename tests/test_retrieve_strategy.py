"""检索策略：冻结完整语料、独立相关性标注和无模型的版本回放。"""
import asyncio
import builtins
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

import config
import recall
from regular import cases, replay_worker


NOW = 1793600000.0


def row(mid, text, ts=NOW - 60, sender="u1"):
    return {"group": "G", "message_id": mid, "sender": sender, "text": text, "ts": ts}


def fixture(rows, relevant=None, *, complete=True):
    case = {"id": "retrieve-test", "depth": "retrieve", "checks": [],
            "retrieve": {"query": "之前说的奶茶", "rows": rows, "names": [],
                         "now": NOW, "promises": [],
                         "coverage": {"complete": complete, "reason": ""}}}
    if relevant is not None:
        case["retrieval_labels"] = {"complete": True, "relevant_ids": relevant}
    return case


class RetrievalStrategyTest(unittest.TestCase):
    def test_full_labels_count_extra_picks_and_missed_relevant_rows(self):
        corpus = [row(mid, text) for mid, text in
                  (("a", "奶茶配方"), ("b", "无关消息"), ("c", "另一个奶茶相关证据"), ("d", "也不相关"))]
        case = fixture(corpus, ["a", "c"])
        result = cases.retrieval_metrics('{"picked": ["a", "b", "a"]}', case)
        self.assertTrue(result["evidence_complete"])
        self.assertEqual([result[k] for k in ("tp", "fp", "fn", "tn")], [1, 1, 1, 1])
        for key in ("precision", "recall", "f1", "false_positive_rate", "false_negative_rate"):
            self.assertEqual(result[key], 0.5)

    def test_partial_checks_are_not_a_complete_relevance_judgment(self):
        case = fixture([row("a", "奶茶"), row("b", "无关")])
        case["checks"] = [{"retrieved": "a"}]
        sample = '{"picked": ["a", "b"]}'
        self.assertTrue(cases.check_reply(sample, case["checks"])["passed"])
        result = cases.retrieval_metrics(sample, case)
        self.assertFalse(result["evidence_complete"])
        self.assertIsNone(result["fp"])
        self.assertIsNone(result["recall"])

    def test_all_negative_and_empty_corpus_have_explicit_undefined_denominators(self):
        result = cases.retrieval_metrics({"picked": []}, fixture([row("a", "无关")], []))
        self.assertEqual(result["tn"], 1)
        self.assertEqual(result["false_positive_rate"], 0.0)
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["recall"])
        empty = cases.retrieval_metrics({"picked": []}, fixture([], []))
        self.assertTrue(empty["evidence_complete"])
        self.assertEqual(empty["tp"], 0)
        self.assertIsNone(empty["f1"])

    def test_invalid_or_incomplete_corpus_cannot_claim_full_metrics(self):
        for sample, case in (
            ({"picked": []}, fixture([row("a", "奶茶")], ["a"], complete=False)),
            ({"picked": ["outside"]}, fixture([row("a", "奶茶")], ["a"])),
            ({"picked": []}, fixture([row("a", "奶茶")], ["outside"])),
            (None, fixture([row("a", "奶茶")], ["a"])),
        ):
            with self.subTest(sample=sample, case=case):
                result = cases.retrieval_metrics(sample, case)
                self.assertFalse(result["evidence_complete"])
                self.assertIsNone(result["tp"])

    def test_snapshot_keeps_negatives_original_order_and_excludes_future(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = [row("old", "奶茶配方", NOW - 600),
                      row("negative", "完全不沾边的消息", NOW - 60),
                      row("future", "未来奶茶", NOW + 60)]
            Path(directory, "2026-11-02.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in corpus))
            rows, coverage = cases.snapshot_rows(directory, "G", "之前说的奶茶", NOW,
                                                 include_coverage=True)
            self.assertEqual([r["message_id"] for r in rows], ["old", "negative"])
            self.assertTrue(coverage["complete"])

    def test_snapshot_cap_is_reported_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "2026-11-02.jsonl").write_text(
                "".join(json.dumps(row(str(i), "奶茶内容")) + "\n" for i in range(3)))
            with patch.object(cases, "SNAPSHOT_MAX_ROWS", 2):
                rows, coverage = cases.snapshot_rows(directory, "G", "奶茶", NOW,
                                                     include_coverage=True)
            self.assertEqual(len(rows), 2)
            self.assertFalse(coverage["complete"])
            self.assertIn("截断", coverage["reason"])

    def test_trace_retains_all_promise_candidates_without_machine_identifiers(self):
        promises = [{"who": "阿强", "what": "下周请大家喝一杯", "due_text": "下周",
                     "member_id": "private-member", "id": "private-promise"},
                    {"who": "老李", "what": "去露营", "due_text": "周末"}]
        selection = recall.select("G", "上次阿强说的那件事", rows=[],
                                  promises=replay_worker._PromiseList(promises),
                                  names=("阿强", "老李"), now=NOW, policy=recall.DEFAULT_POLICY)
        trace = recall.trace(selection)
        self.assertEqual(len(trace["promises"]), 2)
        self.assertNotIn("member_id", trace["promises"][0])
        self.assertNotIn("id", trace["promises"][0])
        with tempfile.TemporaryDirectory() as directory:
            case = cases.create_retrieve_case(
                {"group": "G", "query": "上次阿强说的那件事", "names": ["阿强", "老李"],
                 "now": NOW, **trace}, "promise-replay", archive_dir=directory,
                checks=[{"promise": True}])
        replayed = json.loads(replay_worker.run_retrieve(config, case["retrieve"],
                                                       policy=recall.DEFAULT_POLICY))
        self.assertEqual(replayed["promise"], selection["promise"]["what"])
        self.assertTrue(case["retrieve"]["coverage"]["complete"])

    def test_legacy_promise_record_is_explicitly_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            case = cases.create_retrieve_case(
                {"group": "G", "query": "之前说的奶茶", "names": [], "now": NOW,
                 "picked": [], "promise": "请大家喝奶茶"},
                "legacy", archive_dir=directory, checks=[{"promise": True}])
        self.assertFalse(case["retrieve"]["coverage"]["complete"])
        self.assertIn("承诺", case["retrieve"]["coverage"]["reason"])

    def test_complete_annotation_can_declare_all_negative_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "2026-11-02.jsonl").write_text(json.dumps(row("a", "无关消息")) + "\n")
            case = cases.create_retrieve_case(
                {"group": "G", "query": "之前说的奶茶", "names": [], "now": NOW,
                 "picked": [], "promises": [], "promises_complete": True},
                "all-negative", archive_dir=directory, checks=[{"retrieved_none": True}],
                complete_relevance=True)
        self.assertEqual(case["retrieval_labels"], {"complete": True, "relevant_ids": []})
        result = cases.retrieval_metrics({"picked": []}, case)
        self.assertTrue(result["evidence_complete"])
        self.assertEqual(result["tn"], 1)

    def test_legacy_adapter_preserves_ids_when_bodies_are_identical(self):
        corpus = [row("m1", "同一句奶茶内容", sender="u1"),
                  row("m2", "同一句奶茶内容", sender="u2")]
        legacy = ModuleType("recall")
        legacy.config = config
        legacy._pick = lambda rows, *args, **kwargs: list(rows)
        def old_build_note(group, query, *, archive, **kwargs):
            chosen = legacy._pick(archive.search())
            return "\n".join("- 一位群友：" + r["text"] for r in chosen)
        legacy.build_note = old_build_note
        with patch.dict(sys.modules, {"recall": legacy}):
            result = json.loads(replay_worker.run_retrieve(config, fixture(corpus)["retrieve"]))
        self.assertEqual(result["picked"], ["m1", "m2"])

    def test_replay_keeps_recorded_defaults_but_allows_candidate_policy_overrides(self):
        spec = fixture([row("a", "奶茶一号", sender="u1"),
                        row("b", "奶茶二号", sender="u2")])["retrieve"]
        spec["defaults"] = {"max_items": 1, "lookback_days": 30}
        result = json.loads(replay_worker.run_retrieve(config, spec, policy=recall.DEFAULT_POLICY))
        self.assertEqual(len(result["picked"]), 1)
        policy = {**recall.DEFAULT_POLICY, "max_items": 2}
        result = json.loads(replay_worker.run_retrieve(config, spec, policy=policy))
        self.assertEqual(len(result["picked"]), 2)

    def test_pure_retrieval_batch_never_imports_live_bot_or_runtime(self):
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name in ("bot", "runtime"):
                raise AssertionError("local retrieval imported " + name)
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), \
                patch.object(recall, "active_policy", return_value=dict(recall.DEFAULT_POLICY)):
            results = asyncio.run(replay_worker.replay_batch([fixture([row("a", "奶茶内容")])], n=2))
        self.assertEqual(len(results[0]["samples"]), 2)
        self.assertEqual(json.loads(results[0]["samples"][0])["picked"], ["a"])

    def test_fractional_count_policy_is_rejected_before_execution(self):
        for key in ("max_items", "body_chars", "ngram_max"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                recall.validate_policy({key: 1.5 if key == "max_items" else 80.5 if key == "body_chars" else 3.5})


if __name__ == "__main__":
    unittest.main()
