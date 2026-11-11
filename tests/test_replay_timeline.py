"""消息回放的两个时序屏障；只使用合成群聊、假模型和本地回执。"""
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

import bot
import config
import prompts
import runtime
import storage
from regular import decisions, replay_worker


FIRST = "第一问：你这句还没说清楚"
SECOND = "第二问：再补一个条件"
FIRST_REPLY = "第一稿已经写好"
SECOND_REPLY = "第二稿接住补充"
OLD_REPLY = "这是收到当前两句之前的回复"


def asked(text, message_id, **choices):
    return {"sender": "MEMBER_A", "text": text, "message_id": message_id,
            "triage_choices": {"next_move": "keep_playful", "reminder_request": "none",
                               **choices}}


def tail_is(messages, text):
    tail = messages[-1]["content"]
    return tail.endswith(text) or tail.endswith(prompts.PROMPT_UNPROMPTED_LINE.format(text=text))


class TimelineReplayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.captured = []
        self.no_native = AsyncMock(side_effect=AssertionError("录制分诊不得再请求原生接口"))

        async def model(messages, max_tokens, **kwargs):
            self.captured.append(copy.deepcopy(messages))
            return FIRST_REPLY if tail_is(messages, FIRST) else SECOND_REPLY

        self.model = model
        patches = (
            (bot, "call_model", model),
            (bot, "owner_hint_pending", lambda *args, **kwargs: False),
            (runtime, "BUDGET", storage.DailyBudget(500, {})),
            (runtime.KNOWLEDGE, "render", lambda *args, **kwargs: ""),
            (decisions.providers, "call_decision", self.no_native),
            (config, "SEARCH_ENABLED", False),
            (config, "DIGEST_ENABLED", False),
            (config, "KNOWLEDGE_ENABLED", False),
            (config, "REMINDER_ENABLED", False),
            (config, "BANTER_BURST_SECONDS", 0.0),
            (config, "BANTER_COOLDOWN_SECONDS", 0.0),
            (config, "DECISION_CALLBACK_ENABLED", False),
        )
        for target, name, value in patches:
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    async def run_case(self, case, *, n=1):
        return await asyncio.wait_for(replay_worker.replay_item(
            case, n=n, retries=0, bot=bot, runtime=runtime, config=config), timeout=3)

    def overlap(self):
        return {"id": "timeline-overlap-synthetic", "depth": "message", "messages": [
            asked(FIRST, "source-first"),
            asked(SECOND, "source-second", feedback_target="bot_response"),
        ], "timeline": {"mode": "overlap", "first": 0, "second": 1,
                        "initial_reply": {"sender": "MEMBER_B", "text": OLD_REPLY,
                                          "route": "reply", "age_seconds": 30}}}

    async def test_overlap_suppresses_old_optional_draft_and_preserves_owed_second(self):
        result = await self.run_case(self.overlap())
        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(self.captured), 2)
        self.assertIn(SECOND_REPLY, result["samples"][0])
        self.assertNotIn(FIRST_REPLY, result["samples"][0])
        trace = result["trace"][0]
        generations = trace["generations"]
        self.assertEqual([g["source_message_id"] for g in generations],
                         ["source-first", "source-second"])
        self.assertEqual([g["messages"] for g in generations], self.captured)
        delivered = [d for d in trace["deliveries"] if d.get("text") in (FIRST_REPLY, SECOND_REPLY)]
        self.assertEqual([(d["source_message_id"], d["text"]) for d in delivered],
                         [("source-second", SECOND_REPLY)])
        self.no_native.assert_not_awaited()

    async def test_late_delivery_keeps_receipt_history_and_frozen_feedback_source(self):
        initial = [{"role": "user", "content": "较早的一问"},
                   {"role": "assistant", "content": "较早的一答"},
                   {"role": "user", "content": "上一轮已答问题"},
                   {"role": "assistant", "content": "上一轮已答正文"}]
        case = {"id": "timeline-late-synthetic", "depth": "message", "messages": [
            {**asked(SECOND, "source-target", feedback_target="bot_response"), "at": True},
        ], "timeline": {"mode": "late_delivery", "target": 0,
                        "initial_sessions": {"MEMBER_A": initial},
                        "initial_context": [{"sender": "MEMBER_A", "text": FIRST,
                                             "age_seconds": 4, "message_id": "source-earlier"}],
                        "initial_reply": {"sender": "MEMBER_B", "text": OLD_REPLY,
                                          "route": "reply", "age_seconds": 30},
                        "delivery": {"sender": "MEMBER_A", "question": FIRST,
                                     "text": FIRST_REPLY}}}
        result = await self.run_case(case)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(self.captured), 1)
        messages = self.captured[0]
        self.assertEqual([m for m in messages[:-1] if m["role"] != "system"], initial)
        tail = messages[-1]["content"]
        self.assertIn("本句到达之后发生的事", tail)
        self.assertIn(FIRST_REPLY, tail)
        self.assertIn(OLD_REPLY, tail)
        self.assertIn(FIRST, tail)
        self.assertNotIn(FIRST_REPLY, tail.split("【本句到达之后发生的事", 1)[0])
        self.assertIn(FIRST_REPLY, [d["text"] for d in result["trace"][0]["deliveries"]])
        self.no_native.assert_not_awaited()

    async def test_repeated_samples_are_fresh_and_not_swallowed_by_dedup(self):
        result = await self.run_case(self.overlap(), n=2)
        self.assertEqual((result["errors"], len(result["trace"])), (0, 2))
        self.assertEqual(len(self.captured), 4)
        self.assertTrue(all(SECOND_REPLY in sample for sample in result["samples"]))

    async def test_unknown_mode_and_invalid_message_indexes_are_rejected(self):
        for timeline in ({"mode": "event-loop"},
                         {"mode": "overlap", "first": 0, "second": 99},
                         {"mode": "overlap", "first": 0, "second": 0},
                         {"mode": "late_delivery", "target": -1,
                          "delivery": {"sender": "MEMBER_A", "question": FIRST, "text": FIRST_REPLY}}):
            case = {**self.overlap(), "timeline": timeline}
            with self.subTest(timeline=timeline), self.assertRaises(ValueError):
                await self.run_case(case)
        self.assertEqual(self.captured, [])

    async def test_generation_failure_is_not_reported_as_a_normal_silent_choice(self):
        case = self.overlap()
        with patch.object(bot, "call_model", AsyncMock(return_value=None)):
            result = await self.run_case(case)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["samples"], [None])
        self.assertTrue(result["trace"][0].get("error"))

    async def test_request_timeout_is_an_error_instead_of_a_successful_skip(self):
        case = {"id": "timeline-timeout", "depth": "message", "messages": [
            {**asked(SECOND, "source-timeout", feedback_target="bot_response"), "at": True}
        ], "timeline": {"mode": "late_delivery", "target": 0,
                        "delivery": {"sender": "MEMBER_A", "question": FIRST, "text": FIRST_REPLY}}}
        with patch.object(bot, "call_model", AsyncMock(side_effect=asyncio.TimeoutError)):
            result = await self.run_case(case)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["samples"], [None])
        self.assertTrue(result["trace"][0].get("error"))

    async def test_default_message_replay_keeps_serial_last_reply_contract(self):
        case = {"id": "serial-default", "depth": "message", "messages": [
            {**asked(FIRST, "serial-one"), "at": True},
            {**asked(SECOND, "serial-two"), "at": True},
        ]}
        result = await self.run_case(case)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["samples"], [SECOND_REPLY])
        self.assertEqual(len(self.captured), 2)

    async def test_lookup_is_disabled_unless_the_case_explicitly_enables_it(self):
        case = {"id": "lookup-disabled", "depth": "message", "trace": True, "messages": [
            {**asked("最新公告是什么", "lookup-source", lookup="none"), "at": True}]}
        with patch.object(bot, "call_model", AsyncMock(return_value="〔搜索〕最新公告\n已经在查了")), \
                patch.object(bot.providers, "start_research", AsyncMock()) as start:
            result = await self.run_case(case)
        self.assertEqual(result["trace"][0]["query_mode"], "disabled")
        self.assertNotIn("已经在查", result["samples"][0])
        start.assert_not_awaited()

    async def test_fixture_lookup_is_labeled_and_finishes_without_an_external_request(self):
        case = {"id": "lookup-fixture", "depth": "message", "trace": True,
                "tools": {"lookup": "fixture", "lookup_result": {"text": "示例厂商发布蓝色款。", "sources": []}},
                "messages": [{**asked("新机什么颜色", "lookup-source", lookup="none"), "at": True}]}
        with patch.object(bot, "call_model", AsyncMock(side_effect=["〔搜索〕新机颜色", "查到的是蓝色款。"])), \
                patch.object(bot.providers, "start_research", AsyncMock()) as start:
            result = await self.run_case(case)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["trace"][0]["query_mode"], "fixture")
        self.assertIn("蓝色", result["samples"][0])
        start.assert_not_awaited()

    async def test_model_call_limit_is_an_error_not_a_successful_fallback(self):
        case = {"id": "lookup-limit", "depth": "message", "trace": True, "max_model_calls": 1,
                "tools": {"lookup": "fixture", "lookup_result": {"text": "资料", "sources": []}},
                "messages": [{**asked("新机什么颜色", "limit-source", lookup="none"), "at": True}]}
        with patch.object(bot, "call_model", AsyncMock(return_value="〔搜索〕新机颜色")):
            result = await self.run_case(case)
        self.assertEqual((result["errors"], result["samples"]), (1, [None]))
        self.assertEqual(result["trace"][0]["error"], "model_call_limit")

    async def test_serial_lookup_keeps_execution_evidence_without_an_opt_in_trace_flag(self):
        case = {"id": "serial-lookup-evidence", "depth": "message",
                "tools": {"lookup": "fixture", "lookup_result": {"text": "公告：只有蓝色款。", "sources": []}},
                "messages": [{**asked("公告里是什么颜色", "serial-query", lookup="none"), "at": True}]}
        self.assertNotIn("trace", case)
        self.assertNotIn("timeline", case)
        with patch.object(bot, "call_model", AsyncMock(side_effect=["〔搜索〕公告颜色", "公告里是蓝色款。"])), \
                patch.object(bot.providers, "start_research", AsyncMock()) as start:
            result = await self.run_case(case)
        self.assertEqual((result["errors"], result["samples"]), (0, ["公告里是蓝色款。"]))
        trace = result["trace"][0]
        self.assertEqual(trace["query_mode"], "fixture")
        self.assertEqual(len(trace["generations"]), 2)
        self.assertTrue(all(g["source_message_id"] == "serial-query" for g in trace["generations"]))
        self.assertTrue(all(g["messages"] for g in trace["generations"]))
        self.assertTrue(any(e["kind"] == "lookup" for e in trace["events"]))
        self.assertTrue(any(e["kind"] == "delivery" and e["status"] == "delivered" for e in trace["events"]))
        self.assertEqual([d["text"] for d in trace["deliveries"]], ["公告里是蓝色款。"])
        start.assert_not_awaited()

    async def test_whole_sample_deadline_cancels_a_stuck_barrier(self):
        case = {**self.overlap(), "timeout_seconds": 0.01}
        async def stuck(*args, **kwargs):
            await asyncio.Event().wait()
        with patch.object(bot, "call_model", stuck):
            result = await self.run_case(case)
        self.assertEqual((result["errors"], result["samples"]), (1, [None]))
        self.assertEqual(result["trace"][0]["error"], "timeout")
