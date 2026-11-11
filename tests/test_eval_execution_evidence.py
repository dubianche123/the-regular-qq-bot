"""执行证据留本地；裁判只看匿名投影，补评沿用第一次发出的原样输入。"""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regular import cases, eval as ev


def scene():
    return {"schema": cases.SCHEMA, "id": "timeline-evidence", "depth": "message",
            "messages": [{"sender": "A", "text": "还有补充"}],
            "checks": [{"judge": "按真实时序判断是否重复回复或空头许诺"}],
            "judge_context": {"user_text": "还有补充", "context_hint": "第二句先到，第一条答复后送达。",
                              "session": [], "unprompted": True}}


def trace(status="done", mode="live", *, secret="local-private-id"):
    return {"query_mode": mode,
            "events": [{"kind": "lookup", "status": status, "ts": 2,
                        "source_message_id": secret, "endpoint": "https://secret.example"},
                       {"kind": "delivery", "status": "delivered", "medium": "text", "ts": 4,
                        "text": "不外发的观察正文", "user": secret}],
            "timeline_events": [{"kind": "generation_started", "message_index": 0, "ts": 1}],
            "generations": [{"messages": [{"content": "private model context " + secret}],
                             "completion": {"model": "private-model"}}]}


async def tied(messages):
    return '{"winner":"tie","crossed":[],"reason":"相当"}'


class ExecutionEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_projection_only_exports_whitelisted_facts_in_event_order(self):
        original = trace()
        projected = ev.execution_evidence(original)
        text = json.dumps(projected, ensure_ascii=False)
        for forbidden in ("local-private-id", "secret.example", "private-model", "不外发", "private model"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(projected["queries"], {"done": 1})
        self.assertEqual(projected["deliveries"], {"delivered": 1})
        self.assertEqual([e["kind"] for e in projected["event_order"]],
                         ["generation_started", "lookup", "delivery"])
        self.assertEqual(projected, ev.execution_evidence(projected))

    async def test_body_and_evidence_swap_together_and_raw_trace_never_reaches_judge(self):
        seen = []
        async def judge(messages):
            seen.append(messages)
            return '{"winner":"A","crossed":[],"reason":"完成查询"}'
        result = await ev.judge_pair(scene(), "原回答", "另一回答", base_evidence=trace("failed"),
                                     cand_evidence=trace("done"), judge_fn=judge, swap=True)
        self.assertEqual(result["winner"], "cand")
        a = seen[0][-1]["content"].split("【回复 A】\n")[1].split("\n\n【回复 B】")[0]
        self.assertIn("另一回答", a)
        self.assertIn('"done": 1', a)
        self.assertNotIn('"failed": 1', a)
        for forbidden in ("local-private-id", "secret.example", "private-model", "private model context"):
            self.assertNotIn(forbidden, json.dumps(seen))
        self.assertEqual(result["judge_input"], seen[0])

    async def test_streams_without_comparable_times_do_not_invent_an_order(self):
        data = trace()
        data["timeline_events"][0].pop("ts")
        projected = ev.execution_evidence(data)
        self.assertFalse(projected["event_order_recorded"])
        self.assertEqual(projected["event_order"], [])
        self.assertEqual(projected["queries"], {"done": 1})

    async def test_identical_words_with_different_execution_are_judged(self):
        calls = []
        async def judge(messages):
            calls.append(messages)
            return await tied(messages)
        await ev.judge_pair(scene(), "查到了", "查到了", base_evidence=trace("failed"),
                            cand_evidence=trace("done"), judge_fn=judge, swap=False)
        self.assertEqual(len(calls), 1)
        calls.clear()
        await ev.judge_pair(scene(), "查到了", "查到了", base_evidence=trace("done"),
                            cand_evidence=trace("done"), judge_fn=judge, swap=False)
        self.assertEqual(calls, [])

    async def test_fixture_is_explicit_and_neutral_scene_replaces_false_old_history(self):
        case = scene()
        case["turn"] = {"kwargs": {"context_hint": "旧的错误先后", "user_text": "旧问题"},
                        "session": [{"role": "assistant", "content": "其实后来才送到的答复"}]}
        seen = []
        async def judge(messages):
            seen.append(messages[-1]["content"])
            return await tied(messages)
        await ev.judge_pair(case, "甲", "乙", base_evidence=trace(mode="fixture"),
                            cand_evidence=trace(mode="live"), judge_fn=judge, swap=False)
        self.assertIn('"query_mode": "fixture"', seen[0])
        self.assertIn("fixture 表示测试资料", seen[0])
        self.assertIn("第二句先到", seen[0])
        self.assertNotIn("旧的错误先后", seen[0])
        self.assertNotIn("其实后来才送到", seen[0])

    async def test_raw_traces_and_safe_pair_inputs_are_saved_with_sample_alignment(self):
        case = scene()
        rows, _ = await ev.evaluate_cases([case],
            [{"id": case["id"], "samples": ["甲", None], "traces": [trace(), {"error": "local"}]}],
            [{"id": case["id"], "samples": ["乙", "丙"], "traces": [trace("failed"), trace()]}],
            judge_fn=tied)
        self.assertEqual(len(rows[0]["base_traces"]), 2)
        self.assertEqual(rows[0]["base_traces"][0]["events"][0]["source_message_id"], "local-private-id")
        self.assertIn("judge_input", rows[0]["pairs"][0])
        self.assertEqual(rows[0]["pairs"][1]["reason"], "generation_error")
        self.assertNotIn("local-private-id", json.dumps(rows[0]["pairs"][0]["judge_input"]))
        with self.assertRaisesRegex(ValueError, "对齐"):
            await ev.evaluate_cases([case],
                [{"id": case["id"], "samples": ["甲"], "traces": []}],
                [{"id": case["id"], "samples": ["乙"]}], judge_fn=tied)


class ExecutionRejudgeTest(unittest.TestCase):
    def test_plan_lists_timeline_and_explicit_lookup_destination(self):
        import config
        item = scene()
        item.update(timeline={"mode": "overlap"}, tools={"lookup": "live"})
        preview = "\n".join(ev.describe_traffic(config, [item], n=3))
        self.assertIn("生成中补话 1", preview)
        self.assertIn("真实查询 1", preview)
        self.assertIn("真实查询目的地", preview)
        self.assertIn("DuckDuckGo 回退在回放中禁用", preview)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = SimpleNamespace(EVAL_DIR=self.tmp.name, CASES_DIR=str(Path(self.tmp.name) / "cases"),
                                      EVAL_CONCURRENCY=1)

    def first_report(self):
        case = scene()
        async def broken(messages):
            raise ConnectionError("offline judge")
        rows, totals = asyncio.run(ev.evaluate_cases([case],
            [{"id": case["id"], "samples": ["同样的话"], "traces": [trace("failed")]}],
            [{"id": case["id"], "samples": ["同样的话"], "traces": [trace("done")]}], judge_fn=broken))
        report = {"schema": ev.SCHEMA_OUT, "stamp": "execution-test", "created_at": "-", "base": "b",
                  "cand": "c", "n": 1, "totals": totals, "cases": rows, "ablate": None}
        path = ev.write_report(report, self.config.EVAL_DIR)
        return path.with_suffix(".json"), report

    def test_rejudge_keeps_frozen_input_and_counts_same_words_with_different_actions(self):
        path, original = self.first_report()
        frozen = copy.deepcopy(original["cases"][0]["pairs"][0]["judge_input"])
        self.assertEqual(ev._rejudge_inputs(self.config, path)[-1], 1)
        changed = scene()
        changed["judge_context"]["context_hint"] = "不应进入补评的新现场"
        cases.save_case(self.config.CASES_DIR, changed)
        seen = []
        async def judge(messages):
            seen.append(messages)
            return await tied(messages)
        with patch.object(ev, "_judge_messages", side_effect=AssertionError("不得重建已保存输入")):
            report = ev.rejudge_report(self.config, path, judge_fn=judge, write=False)
        self.assertEqual(seen, [frozen])
        self.assertEqual(report["totals"]["judge_errors"], 0)
        self.assertEqual(report["cases"][0]["base_traces"], original["cases"][0]["base_traces"])
        self.assertEqual(report["cases"][0]["pairs"][0]["judge_input"], frozen)

    def test_execution_report_without_its_frozen_input_is_refused(self):
        path, report = self.first_report()
        report["cases"][0]["pairs"][0].pop("judge_input")
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "逐对评审输入"):
            ev.rejudge_report(self.config, path, judge_fn=tied, write=False)
