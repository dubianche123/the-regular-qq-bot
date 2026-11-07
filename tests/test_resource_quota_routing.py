"""实际群聊路由对个人额度、群级上限和破例提示的处理。"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import bot
import config
import prompts
import relations
import runtime
import search
import storage
from regular.resource_quota import ResourceQuotas
from test_natural_feedback import _Base


class SearchQuotaRoutingTest(_Base):
    def setUp(self):
        super().setUp()
        self.quota_state = {}
        self.save_quota = Mock()
        self.quotas = ResourceQuotas(self.quota_state, Mock(), self.save_quota)
        self.relations = relations.RelationStore()
        self.search_limiter = search.SearchLimiter(0, 60)
        self.deep_limiter = search.SearchLimiter(0, 60)
        for target, name, value in (
                (runtime, "RESOURCE_QUOTAS", self.quotas),
                (runtime, "RELATIONS", self.relations),
                (search, "LIMITER", self.search_limiter),
                (search, "DEEP_LIMITER", self.deep_limiter),
                (config, "SEARCH_ENABLED", True),
                (config, "SEARCH_USER_DAILY_CAP", 1),
                (config, "SEARCH_USER_EXTRA_FRIEND", 1),
                (config, "DEEP_RESEARCH_ENABLED", True)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    async def test_exhaustion_blocks_explicit_automatic_and_deep_search(self):
        self.assertEqual(self.quotas.consume("search", self.G, self.A, 0), "normal")
        for text, result in (("搜索 天气", {}),
                             ("今天什么天气", {"lookup": "search"}),
                             ("认真调查天气", {"lookup": "research"})):
            with self.subTest(text=text), \
                 patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
                 patch.object(bot.providers, "research_available", return_value=True), \
                 patch.object(bot, "answer_with_lookup", AsyncMock()) as lookup, \
                 patch.object(search, "search_web", AsyncMock()) as ddg:
                msg = await self._drive(text, is_at=True)
                lookup.assert_not_awaited()
                ddg.assert_not_awaited()
                self.assertIn("累", msg.reply.await_args.kwargs["content"])

    async def test_exhausted_member_does_not_consume_another_members_quota(self):
        self.assertEqual(self.quotas.consume("search", self.G, self.A, 0), "normal")
        fetch = AsyncMock(return_value=[{"title": "结果", "url": "https://example.com", "snippet": "摘要"}])
        with patch.object(bot.providers, "research_available", return_value=False), \
             patch.object(search, "search_web", fetch):
            await self._drive("搜索 天气", is_at=True)
            fetch.assert_not_awaited()
            await self._drive("搜索 天气", sender="USER_B", is_at=True)
        fetch.assert_awaited_once()
        self.assertEqual(self.quotas.check("search", self.G, "USER_B", 0), "exhausted")

    async def test_exception_note_only_after_extra_quota_reservation(self):
        self.relations.get(self.G, self.A, create=True)["score"] = 30
        self.assertEqual(self.quotas.consume("search", self.G, self.A, 30), "normal")
        lookup = AsyncMock(return_value=None)
        with patch.object(self.feedback, "assess", AsyncMock(return_value={"lookup": "search"})), \
             patch.object(bot.providers, "research_available", return_value=True), \
             patch.object(bot, "answer_with_lookup", lookup):
            await self._drive("今天什么天气", is_at=True)
        lookup.assert_awaited_once()
        self.assertEqual(self.quotas.check("search", self.G, self.A, 30), "exhausted")
        self.assertIn("这次实际用了额外次数", lookup.await_args.kwargs["reply_kwargs"]["resource_note"])

    async def test_group_cap_and_save_failure_do_not_start_lookup(self):
        for failure in ("group_cap", "save_failure"):
            with self.subTest(failure=failure):
                if failure == "group_cap":
                    self.search_limiter.daily_cap = 0
                else:
                    self.search_limiter.daily_cap = 60
                    self.save_quota.side_effect = OSError()
                with patch.object(self.feedback, "assess", AsyncMock(return_value={"lookup": "search"})), \
                     patch.object(bot.providers, "research_available", return_value=True), \
                     patch.object(bot, "answer_with_lookup", AsyncMock()) as lookup:
                    await self._drive("今天什么天气", is_at=True)
                    lookup.assert_not_awaited()

    async def test_deep_lookup_respects_shared_group_cap(self):
        self.search_limiter.daily_cap = 0
        with patch.object(self.feedback, "assess", AsyncMock(return_value={"lookup": "research"})), \
             patch.object(bot.providers, "research_available", return_value=True), \
             patch.object(bot, "answer_with_lookup", AsyncMock()) as lookup:
            msg = await self._drive("认真调查天气", is_at=True)
        lookup.assert_not_awaited()
        self.assertIn("累", msg.reply.await_args.kwargs["content"])
        self.assertEqual(self.quotas.check("search", self.G, self.A, 0), "normal")

    async def test_deep_cap_falls_back_to_ordinary_search(self):
        self.deep_limiter.daily_cap = 0
        with patch.object(self.feedback, "assess", AsyncMock(return_value={"lookup": "research"})), \
             patch.object(bot.providers, "research_available", return_value=True), \
             patch.object(bot, "answer_with_lookup", AsyncMock(return_value=None)) as lookup:
            await self._drive("认真调查天气", is_at=True)
        lookup.assert_awaited_once()
        self.assertEqual(lookup.await_args.args[3], "search")
        self.assertEqual(self.quotas.check("search", self.G, self.A, 0), "exhausted")


class ImageQuotaRoutingTest(_Base):
    """额度在小王写回复之前就告诉它：画累了、破例都由它自己说，不再是代码里的固定话术。"""

    def setUp(self):
        super().setUp()
        self.quotas = ResourceQuotas({}, Mock(), Mock())
        self.relations = relations.RelationStore()
        for target, name, value in (
                (runtime, "RESOURCE_QUOTAS", self.quotas),
                (runtime, "RELATIONS", self.relations),
                (runtime, "IMAGE_GENERATION_BUDGET", storage.DailyBudget(40, {})),
                (config, "IMAGE_USER_DAILY_CAP", 1),
                (config, "IMAGE_USER_EXTRA_FRIEND", 1),
                (bot.image_generation, "available", lambda: True)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    def tail(self):
        return self.model_calls[-1][-1]["content"]

    async def test_tired_member_is_told_and_marker_is_not_honoured(self):
        self.assertEqual(self.quotas.consume("image", self.G, self.A, 0), "normal")

        async def model(messages, max_tokens, **kw):
            self.model_calls.append(messages)
            return "〔画〕一只猫\n今天画累了，不过我这就给你画"
        with patch.object(bot, "call_model", model), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as draw:
            msg = await self._drive("画只猫", is_at=True)
        self.assertIn(prompts.PROMPT_DRAW_TIRED, self.tail())
        draw.assert_not_awaited()
        self.assertEqual(msg.reply.await_args.kwargs["content"], "今天画不动了，明天再画")

    async def test_busy_drawing_explains_the_inflight_picture_without_promising_another(self):
        async def model(messages, max_tokens, **kw):
            self.model_calls.append(messages)
            return "〔画〕一只猫\n手上那张我画着呢，这张也马上画"

        with patch.object(bot, "call_model", model), \
             patch.object(bot.image_generation, "precheck", return_value="busy"), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as draw:
            msg = await self._drive("画只猫", is_at=True)
        self.assertIn(prompts.PROMPT_DRAW_BUSY, self.tail())
        draw.assert_not_awaited()
        self.assertEqual(msg.reply.await_args.kwargs["content"], "手上那张还没画完，等会儿再叫我")

    async def test_first_extra_share_tells_it_this_one_is_an_exception(self):
        self.relations.get(self.G, self.A, create=True)["score"] = 30
        self.assertEqual(self.quotas.consume("image", self.G, self.A, 30), "normal")
        await self._drive("画只猫", is_at=True)
        self.assertIn(prompts.PROMPT_DRAW_EXCEPTION, self.tail())

    async def test_marker_draws_through_the_real_message_path(self):
        async def model(messages, max_tokens, **kw):
            self.model_calls.append(messages)
            return "〔画〕一只橘猫趴在键盘上\n给你整一只"

        async def draw(_message, **kw):
            await kw["before_send"]()
            return True
        with patch.object(bot, "call_model", model), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=draw) as gen:
            msg = await self._drive("画只猫", is_at=True)
        self.assertEqual(gen.await_args.kwargs["prompt"], "一只橘猫趴在键盘上")
        self.assertEqual(msg.reply.await_args_list[0].kwargs["content"], "给你整一只")
        session = runtime.SESSIONS._sessions[f"{self.G}_{self.A}"]["messages"]
        self.assertIn("（系统：这张图已经发到群里了）", session[-1]["content"])

    async def test_normal_turn_adds_nothing_to_the_tail(self):
        await self._drive("画只猫", is_at=True)
        self.assertNotIn("【画图】", self.tail())
