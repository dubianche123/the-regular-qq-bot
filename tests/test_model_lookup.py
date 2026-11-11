"""One reply-selected lookup, with real orchestration and fake external endpoints."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
import config
import prompts
import providers
import runtime
import scheduler
import search
import slowwork
from test_slowwork import FakeJob, answer
from test_when_to_intervene import A, G, _Base


class ModelLookupTest(_Base):
    def setUp(self):
        super().setUp()
        self.sched = scheduler.Scheduler()
        self.reserve = Mock(return_value="normal")
        self.posted = []
        self.client = SimpleNamespace(api=SimpleNamespace(post_group_message=AsyncMock(
            side_effect=lambda **kw: self.posted.append(kw) or {"id": "lookup-follow"})))
        for target, name, value in (
                (config, "SEARCH_ENABLED", True), (config, "ASYNC_REPLY_SECONDS", 0.001),
                (config, "BOT_NAMES", ["小王"]), (runtime, "SCHEDULER", self.sched),
                (providers, "research_available", lambda: True),
                (bot, "_reserve_search", self.reserve), (bot, "_bot_ref", {"client": self.client})):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.calls.side_effect = answer({"addressed": "answer", "lookup": "none"})
        self.fake = FakeJob("search", [slowwork.DONE], result={
            "text": "示例厂商今天发布了蓝色新机，售价尚未确认。", "sources": [{"title": "厂商公告"}]})
        ctx = patch.dict(slowwork.JOBS, {"search": self.fake.job})
        ctx.start()
        self.addCleanup(ctx.stop)

    def script(self, *replies):
        values = iter(replies)
        async def model(messages, max_tokens, **kw):
            self.prompts_seen.append(messages)
            return next(values)
        ctx = patch.object(bot, "call_model", model)
        ctx.start()
        self.addCleanup(ctx.stop)

    async def test_model_queries_after_decision_none_and_only_final_is_public(self):
        self.script('〔搜索〕示例厂商 最新新机\n我已经查到了！\n<CMD>{"aff":1}</CMD>',
                    '厂商刚发了蓝色款，价格还没官宣。\n<CMD>{"aff":-1}</CMD>')
        with patch.object(bot, "apply_affinity_delta_capped") as affinity:
            msg = await self._drive("小王 现在新机是啥颜色", is_at=True)
        self.assertEqual(self.fake.started, ["示例厂商 最新新机"])
        self.reserve.assert_called_once()
        self.assertEqual(len(self.prompts_seen), 2)
        self.assertIn(search.LOOKUP_MARK, self._last_prompt())
        self.assertIn(prompts.PROMPT_SEARCH_USED, self.prompts_seen[-1][-1]["content"])
        self.assertNotIn("我已经查到了", msg.reply.await_args.kwargs["content"])
        self.assertEqual(msg.reply.await_count, 1)
        affinity.assert_called_once()
        self.assertEqual(affinity.call_args.args[2], 1)
        self.assertEqual([m["role"] for m in self._session()], ["user", "assistant"])
        self.assertIn("已联网查询", self._session()[-1]["content"])
        self.assertNotIn("我已经查到了", self._session()[-1]["content"])

    async def test_second_query_never_executes_or_sends_a_promise(self):
        self.script("〔搜索〕最新新机", "〔搜索〕再看另一页\n我继续去查")
        msg = await self._drive("小王 新机是什么", is_at=True)
        self.assertEqual(len(self.fake.started), 1)
        self.assertEqual(len(self.prompts_seen), 2)
        self.assertNotIn("继续去查", msg.reply.await_args.kwargs["content"])
        self.assertNotIn("〔搜索〕", msg.reply.await_args.kwargs["content"])

    async def test_decision_query_consumes_the_same_allowance(self):
        self.calls.side_effect = answer({"lookup": "search", "addressed": "answer"})
        self.script("〔搜索〕再查新机\n继续查")
        msg = await self._drive("小王 新机怎么样", is_at=True)
        self.assertEqual(len(self.fake.started), 1)
        self.assertEqual(len(self.prompts_seen), 1)
        self.reserve.assert_called_once()
        self.assertNotIn("继续查", msg.reply.await_args.kwargs["content"])

    async def test_failed_start_creates_no_pending_job(self):
        self.fake.handle = None
        self.script("〔搜索〕新机颜色\n我去查", "这次没查成，暂时不确定。")
        msg = await self._drive("小王 新机啥颜色", is_at=True)
        self.assertEqual(self.sched.tasks, {})
        self.assertIn("没有取得可用资料", self._last_prompt())
        self.assertNotIn("等我", msg.reply.await_args.kwargs["content"])

    async def test_denied_quota_starts_no_job(self):
        self.reserve.return_value = "quota"
        self.script("〔搜索〕新机颜色", "今天查得有点累，这个先不确定。")
        await self._drive("小王 新机啥颜色", is_at=True)
        self.assertEqual(self.fake.started, [])
        self.assertEqual(self.sched.tasks, {})
        self.assertIn("没有发起查询", self._last_prompt())

    async def test_invalid_query_is_not_exported_or_billed(self):
        for query in ("", G, A, "a" * 241):
            with self.subTest(query=query):
                state, _, handle = await bot._model_lookup(
                    SimpleNamespace(group_openid=G, id="source", author=SimpleNamespace(member_openid=A)),
                    query, None, seconds=1)
                self.assertEqual(state, slowwork.FAILED)
                self.assertIsNone(handle)
        self.assertEqual(self.fake.started, [])
        self.reserve.assert_not_called()

    async def test_search_disabled_cannot_run_a_proposal(self):
        self.script("〔搜索〕新机颜色\n已经开查")
        with patch.object(config, "SEARCH_ENABLED", False):
            msg = await self._drive("小王 新机啥颜色", is_at=True)
        self.assertEqual(self.fake.started, [])
        self.reserve.assert_not_called()
        self.assertNotIn("已经开查", msg.reply.await_args.kwargs["content"])

    async def test_slow_query_keeps_reference_and_followup_cannot_search_again(self):
        self.fake.states = [slowwork.RUNNING]
        self.script("〔搜索〕新机颜色", "〔搜索〕再查一遍\n我继续查")
        msg = await self._drive("小王 新机啥颜色", is_at=True, scene={"msg_idx": "source-ref"})
        self.assertIn("查查", msg.reply.await_args.kwargs["content"])
        task = next(iter(self.sched.tasks.values()))
        self.assertEqual(task["payload"]["msg_idx"], "source-ref")
        json.dumps(task)
        self.fake.states = [slowwork.DONE]
        await bot.finish_lookup(task)
        self.assertEqual(len(self.fake.started), 1)
        self.assertNotIn("继续查", self.posted[-1]["content"])
        self.assertIn(prompts.PROMPT_SEARCH_USED, self.prompts_seen[-1][-1]["content"])

    async def test_ddg_fallback_uses_one_query_and_the_same_final_path(self):
        self.script("〔搜索〕新机颜色", "查到的是蓝色款。")
        ddg = AsyncMock(return_value=[{"title": "厂商", "snippet": "蓝色", "url": "https://example.org"}])
        with patch.object(providers, "research_available", return_value=False), \
                patch.object(search, "search_web", ddg):
            await self._drive("小王 新机啥颜色", is_at=True)
        ddg.assert_awaited_once_with("新机颜色")
        self.assertIn(search.SEARCH_MARK, self._last_prompt())
        self.assertEqual(self.fake.started, [])

    async def test_poll_timeout_preserves_handle_for_later(self):
        async def slow_poll(handle):
            await asyncio.sleep(1)
        job = slowwork.Job("search", self.fake.start, slow_poll, 3, 300)
        with patch.dict(slowwork.JOBS, {"search": job}):
            state, _, handle = await bot._run_lookup("search", "颜色", seconds=0.01)
        self.assertEqual((state, handle), (slowwork.RUNNING, "resp_1"))

    async def test_query_proposal_cannot_also_save_a_reminder(self):
        self.script("〔搜索〕新机颜色\n〔提醒 11-12 09:00〕买手机\n我已经帮你定好了", "查到蓝色款。")
        apply = Mock()
        with patch.object(bot.reminders, "apply", apply):
            msg = await self._drive("小王 新机啥颜色", is_at=True)
        apply.assert_not_called()
        self.assertNotIn("定好了", msg.reply.await_args.kwargs["content"])

    async def test_lookup_result_can_draw_using_the_existing_image_path(self):
        self.script("〔搜索〕示例新机外观", "〔画〕蓝色手机产品插画\n按公告里的外观画一张。")
        draw = AsyncMock(return_value=True)
        with patch.object(bot.image_generation, "available", return_value=True), \
                patch.object(bot.image_generation, "precheck", return_value="normal"), \
                patch.object(bot.image_generation, "animation_available", return_value=False), \
                patch.object(bot, "_drawing_declined", AsyncMock(return_value=False)), \
                patch.object(bot.image_generation, "generate_and_send", draw):
            msg = await self._drive("小王 查查新机外观画一下", is_at=True)
        self.assertEqual(len(self.fake.started), 1)
        draw.assert_awaited_once()
        self.assertEqual(draw.await_args.kwargs["prompt"], "蓝色手机产品插画")
        self.assertEqual(msg.reply.await_count, 1)

    async def test_empty_final_gives_an_honest_reply_without_new_search(self):
        self.script("〔搜索〕新机外观", "")
        msg = await self._drive("小王 新机外观是什么", is_at=True)
        self.assertIn("没能整理出可靠的答复", msg.reply.await_args.kwargs["content"])
        self.assertEqual(len(self.fake.started), 1)

    async def test_drawing_rewrite_preserves_first_query_score(self):
        self.script('〔搜索〕新机\n<CMD>{"aff":1}</CMD>',
                    '〔画〕蓝色手机\n画一张\n<CMD>{"aff":-1}</CMD>',
                    '这张先不画。\n<CMD>{"aff":-1}</CMD>')
        with patch.object(bot.image_generation, "available", return_value=True), \
                patch.object(bot.image_generation, "precheck", return_value="normal"), \
                patch.object(bot, "_drawing_declined", AsyncMock(return_value=True)), \
                patch.object(bot, "apply_affinity_delta_capped") as affinity:
            await self._drive("小王 查查新机画一下", is_at=True)
        affinity.assert_called_once()
        self.assertEqual(affinity.call_args.args[2], 1)

    async def test_background_result_keeps_the_frozen_reply_and_session(self):
        self.fake.states = [slowwork.RUNNING]
        self.feedback.remember(G, A, "刚才我说新机是红色。", route="reply", outgoing_id="old")
        self.calls.side_effect = answer({"feedback_target": "bot_response", "next_move": "repair_briefly",
                                         "addressed": "answer", "lookup": "none", "reminder_request": "none"})
        self.script("〔搜索〕新机颜色", "这次确认公告是蓝色。")
        await self._drive("小王 你刚才那句话不对，再查查", is_at=True)
        task = next(iter(self.sched.tasks.values()))
        self.assertIn("红色", task["payload"]["reply_context"])
        self.assertEqual(task["payload"]["history_snapshot"], [])
        runtime.SESSIONS.record(f"{G}_{A}", "中途另一题", "中途回答")
        self.fake.states = [slowwork.DONE]
        await bot.finish_lookup(task)
        self.assertIn("红色", self.prompts_seen[-1][-1]["content"])
        self.assertNotIn("中途回答", [row["content"] for row in self.prompts_seen[-1][:-1]])
