"""实际群聊路由对个人额度、群级上限和破例提示的处理。"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import bot
import config
import relations
import runtime
import search
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
    async def test_exhausted_image_quota_gives_human_reply_without_chat_model(self):
        result = {"image_generation": "generate", "image_prompt": "cat"}
        with patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value="quota")):
            msg = await self._drive("画只猫", is_at=True)
        self.assertIn("累", msg.reply.await_args.kwargs["content"])
        self.assertEqual(self.model_calls, [])

    async def test_exception_wait_message_follows_real_reservation(self):
        result = {"image_generation": "generate", "image_prompt": "cat"}
        async def send(_message, **kwargs):
            kwargs["on_reserved"]("exception")
            await asyncio.sleep(0.01)
            await kwargs["before_send"]()
            return True
        with patch.object(config, "ASYNC_REPLY_SECONDS", 0), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send):
            msg = await self._drive("画只猫", is_at=True)
        self.assertIn("再帮你画", msg.reply.await_args.kwargs["content"])
