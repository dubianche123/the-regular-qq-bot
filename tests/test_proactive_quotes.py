"""隔了一阵的主动接话：选对原句、引用可见，结果不明时不重复投递。"""
import asyncio
import collections
import json
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from botpy.errors import ForbiddenError, ServerError

import bot
import interactions
import proactive
import quotes
import runtime
import storage
from test_natural_feedback import _Base, _group_message


def _rows(count=3):
    now = time.time()
    return [{"sender": f"PRIVATE_SENDER_{i}", "message_id": f"PRIVATE_MESSAGE_{i}",
             "msg_idx": f"PRIVATE_REF_{i}", "text": f"第{i + 1}句原话", "time": now - 900 + i}
            for i in range(count)]


class ProactiveProtocolTest(unittest.TestCase):
    def test_numbered_topics_expose_words_but_no_platform_identifiers(self):
        rows = _rows()
        rendered = proactive.render_topics(rows)
        for i, row in enumerate(rows, 1):
            self.assertIn(f"[{i}]", rendered)
            self.assertIn(row["text"], rendered)
            for key in ("sender", "message_id", "msg_idx"):
                self.assertNotIn(row[key], rendered)
        self.assertLess(rendered.index(rows[0]["text"]), rendered.index(rows[1]["text"]))

    def test_second_source_is_selected_instead_of_the_last_line(self):
        rows = _rows()
        text, source = proactive.parse("〔引用 2〕\n另一个角度接一句", rows)
        self.assertEqual(text, "另一个角度接一句")
        self.assertIs(source, rows[1])
        self.assertEqual(proactive.parse("〔新话题〕\n自己冒一个新点子", rows),
                         ("自己冒一个新点子", None))

    def test_missing_or_invalid_route_and_pass_never_become_group_text(self):
        rows = _rows()
        for body in ("没有协议的开场白", "〔引用 0〕\n正文", "〔引用 4〕\n正文",
                     "〔引用 -1〕\n正文", "〔引用 二〕\n正文", "〔引用 1.5〕\n正文",
                     "〔引用 2〕正文", "〔引用 2〕\n  ", "〔新话题〕\n",
                     "〔不接〕", "〔引用 2〕\n〔不接〕\n不该发出去",
                     "〔引用 2〕\n〔新话题〕\n重复路由不该漏发",
                     "〔新话题〕\n〔引用 2〕\n重复路由不该漏发",
                     "〔新话题〕\n正文中夹着〔引用 2〕也不漏发",
                     "〔引用 2〕\n正文中夹着〔新话题〕也不漏发"):
            with self.subTest(body=body):
                self.assertIsNone(proactive.parse(body, rows))

    def test_route_is_removed_before_existing_media_parser(self):
        rows = _rows()
        for tag in ("〔画〕一只撑伞的小鸭", "〔语音〕（憋着笑）"):
            with self.subTest(tag=tag):
                text, source = proactive.parse(f"〔引用 2〕\n{tag}\n就说这一句", rows)
                self.assertEqual(text, "就说这一句")
                self.assertIs(source, rows[1])
        self.assertIsNone(proactive.parse("〔新话题〕\n〔画〕只有描述没有正文", rows))


class ProactiveQuoteLoopTest(_Base):
    def _stage(self, reply="〔引用 2〕\n接第二句", *, real_post=False):
        self.rows = _rows(5)
        self.buffer = collections.deque(self.rows)
        gen = AsyncMock(return_value=(reply, None))
        post = AsyncMock(wraps=bot.post_to_group) if real_post else AsyncMock(return_value=True)
        for target, name, value in (
                (bot, "group_buffers", {self.G: self.buffer}),
                (bot, "_last_proactive", {}),
                (bot, "own_lines", collections.defaultdict(lambda: collections.deque(maxlen=10))),
                (runtime, "STATE", storage.StateStore(os.path.join(self._tmp.name, "proactive-state.json"))),
                (bot.activation, "proactive_ok", lambda *a, **k: (True, "ok")),
                (bot.activation, "current_mood", lambda now: 0.5),
                (bot.activation, "state_line", lambda *a, **k: ""),
                (bot, "owner_label", lambda *a, **k: ""),
                (bot, "get_ai_reply", gen),
                (bot, "post_to_group", post),
                (runtime.BUDGET, "can_consume", lambda: True)):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)
        return gen, post

    async def _tick(self):
        with patch.object(bot.STATUS, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()

    async def test_selected_number_maps_to_the_same_bounded_human_rows_sent_to_model(self):
        gen, post = self._stage()
        await self._tick()
        post.assert_awaited_once()
        kw = post.await_args.kwargs
        self.assertEqual(post.await_args.args[1], "接第二句")
        self.assertEqual(kw["quote"], self.rows[1]["msg_idx"])
        self.assertIn(self.rows[1]["text"], kw["quote_text"])
        self.assertNotIn(self.rows[-1]["text"], kw["quote_text"])
        prompt = gen.await_args.args[1] + "\n" + gen.await_args.kwargs.get("context_hint", "")
        self.assertIn("[2]", prompt)
        self.assertIn(self.rows[1]["text"], prompt)
        self.assertIn(self.rows[0]["text"], prompt)
        for row in self.rows:
            for key in ("sender", "message_id", "msg_idx"):
                self.assertNotIn(row[key], prompt)
                self.assertNotIn(row[key], kw["quote_text"])

    async def test_mutated_buffer_row_cannot_change_the_already_numbered_source(self):
        gen, post = self._stage()
        original_ref = self.rows[1]["msg_idx"]
        original_text = self.rows[1]["text"]

        async def delayed(*args, **kwargs):
            self.rows[1].update(msg_idx="REPLACED_PRIVATE_REF", text="之后才更新的内容")
            return "〔引用 2〕\n仍然接编号时的第二句", None

        gen.side_effect = delayed
        await self._tick()
        post.assert_awaited_once()
        self.assertEqual(post.await_args.kwargs["quote"], original_ref)
        self.assertIn(original_text, post.await_args.kwargs["quote_text"])
        self.assertNotIn("之后才更新的内容", post.await_args.kwargs["quote_text"])

    async def test_new_topic_has_neither_native_quote_nor_old_words_prefix(self):
        _, post = self._stage("〔新话题〕\n一个独立的新点子")
        await self._tick()
        post.assert_awaited_once()
        self.assertIsNone(post.await_args.kwargs.get("quote"))
        self.assertFalse(post.await_args.kwargs.get("quote_text"))
        self.assertEqual(post.await_args.args[1], "一个独立的新点子")

    async def test_selected_source_without_index_still_has_visible_original_words(self):
        _, post = self._stage()
        self.rows[1].pop("msg_idx")
        await self._tick()
        post.assert_awaited_once()
        self.assertIsNone(post.await_args.kwargs.get("quote"))
        self.assertIn(self.rows[1]["text"], post.await_args.kwargs["quote_text"])
        self.assertTrue(post.await_args.kwargs["quote_text"].startswith("[回复"))

    async def test_invalid_index_or_pass_does_not_send(self):
        gen, post = self._stage()
        for body in ("〔引用 6〕\n越界了", "〔引用 x〕\n无效索引", "没选来源", "〔不接〕"):
            with self.subTest(body=body):
                gen.return_value = (body, None)
                await self._tick()
        post.assert_not_awaited()

    async def test_confirmed_proactive_text_is_recorded_once_with_persisted_cooldown(self):
        self._stage("〔新话题〕\n花园今天由猫值班", real_post=True)
        api = SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "sent"}))
        with patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)}), \
             patch.object(bot, "_remember_sent"):
            await self._tick()
        self.assertEqual([r["text"] for r in bot.own_lines[self.G]], ["花园今天由猫值班"])
        slot = runtime.STATE.data["groups"][self.G]
        self.assertEqual(slot["own_lines"], list(bot.own_lines[self.G]))
        self.assertEqual(slot["last_proactive"], bot._last_proactive[self.G])

    async def test_another_bot_result_during_generation_also_cancels_the_old_candidate(self):
        gen, post = self._stage()

        async def delayed(*args, **kwargs):
            bot._note_own_line(self.G, "已经回了另一位群友")
            return "〔引用 2〕\n过时的候选", None

        gen.side_effect = delayed
        await self._tick()
        post.assert_not_awaited()

    async def test_new_human_message_during_generation_cancels_the_old_candidate(self):
        gen, _ = self._stage(real_post=True)
        api = SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "sent"}))

        async def delayed(*args, **kwargs):
            self.buffer.append({"sender": "OTHER_PRIVATE_SENDER", "message_id": "new-message",
                                "text": "群友已经接着聊了", "time": time.time()})
            return "〔引用 2〕\n已经过时的接话", None

        gen.side_effect = delayed
        with patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)}):
            await self._tick()
        api.post_group_message.assert_not_awaited()

    async def test_incoming_message_keeps_native_reference_index_in_local_buffer(self):
        state = storage.StateStore(os.path.join(self._tmp.name, "quote-state.json"))
        msg = _group_message("一句普通的现场发言", group=self.G)
        msg._scene = {"msg_idx": "INCOMING_PRIVATE_REF"}
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="TEST_BOT", name="regular"))
        with patch.object(runtime, "STATE", state), \
             patch.object(runtime, "QUOTES", quotes.QuoteBook(state)):
            await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=False)
        self.assertEqual(bot.group_buffers[self.G][-1]["msg_idx"], "INCOMING_PRIVATE_REF")


class ProactiveQuoteDeliveryTest(_Base):
    TEXT = "这句才是主动接的话"
    PREFIX = "刚才一位群友说「原句在这里」：\n"
    REF = "PRIVATE_NATIVE_REFERENCE"

    def setUp(self):
        super().setUp()
        self.quote_log = interactions.InteractionLog(os.path.join(self._tmp.name, "quote-ix"),
                                                      enabled=True, keep_days=0)
        self.addCleanup(self.quote_log.close)
        ctx = patch.object(runtime, "INTERACTIONS", self.quote_log)
        ctx.start(); self.addCleanup(ctx.stop)

    def _deliveries(self):
        return [json.loads(line) for path in Path(self.quote_log.log_dir).glob("*.jsonl")
                for line in path.read_text().splitlines() if json.loads(line)["kind"] == "delivery"]

    async def _send(self, api_call, *, quote=REF):
        api = SimpleNamespace(post_group_message=api_call)
        with patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)}), \
             patch.object(bot, "_remember_sent") as remembered, \
             patch.object(self.feedback, "remember", wraps=self.feedback.remember) as feedback:
            ok = await bot.post_to_group(self.G, self.TEXT, quote=quote, quote_text=self.PREFIX,
                                         model_generated=True, history=[{"text": "原句在这里"}])
        return ok, remembered, feedback

    def _assert_one_delivered_record(self, remembered, feedback, wire_text):
        remembered.assert_called_once()
        self.assertEqual(remembered.call_args.args[-1], wire_text)
        feedback.assert_called_once()
        self.assertEqual(feedback.call_args.args[2], wire_text)
        self.assertEqual(self.feedback._groups[self.G]["reply"]["text"], wire_text)
        self.assertEqual([(x["status"], x.get("text")) for x in self._deliveries()],
                         [("delivered", wire_text)])

    async def test_native_quote_is_active_message_reference_without_old_passive_message_id(self):
        api = AsyncMock(return_value={"id": "confirmed"})
        ok, remembered, feedback = await self._send(api)
        self.assertTrue(ok)
        api.assert_awaited_once()
        payload = api.await_args.kwargs
        self.assertEqual(payload["content"], self.TEXT)
        self.assertEqual(payload["message_reference"],
                         {"message_id": self.REF, "ignore_get_message_error": False})
        self.assertNotIn("msg_id", payload)
        self.assertNotIn(self.REF, payload["content"])
        self._assert_one_delivered_record(remembered, feedback, self.TEXT)
        self.assertEqual(self._deliveries()[0]["reference_mode"], "native")
        self.assertEqual(self._deliveries()[0]["ref_msg_idx"], self.REF)

    async def test_no_native_index_sends_original_words_prefix_and_records_exact_wire(self):
        api = AsyncMock(return_value={"id": "confirmed"})
        ok, remembered, feedback = await self._send(api, quote=None)
        self.assertTrue(ok)
        api.assert_awaited_once()
        payload = api.await_args.kwargs
        self.assertNotIn("message_reference", payload)
        self.assertNotIn("msg_id", payload)
        self.assertEqual(payload["content"], self.PREFIX + self.TEXT)
        self._assert_one_delivered_record(remembered, feedback, payload["content"])
        self.assertEqual(self._deliveries()[0]["reference_mode"], "text")

    async def test_explicit_rejection_falls_back_once_and_commits_actual_text_once(self):
        for error in (ForbiddenError("quote refused"),
                      aiohttp.ClientResponseError(SimpleNamespace(real_url="https://qq.test/messages"), (),
                                                  status=400, message="quote refused"),
                      aiohttp.ClientResponseError(SimpleNamespace(real_url="https://qq.test/messages"), (),
                                                  status=403, message="quote refused")):
            with self.subTest(error=type(error).__name__, status=getattr(error, "status", None)):
                before = len(self._deliveries())
                api = AsyncMock(side_effect=[error, {"id": "fallback-confirmed"}])
                ok, remembered, feedback = await self._send(api)
                self.assertTrue(ok)
                self.assertEqual(api.await_count, 2)
                self.assertIn("message_reference", api.await_args_list[0].kwargs)
                self.assertNotIn("message_reference", api.await_args_list[1].kwargs)
                self.assertEqual(api.await_args_list[1].kwargs["content"], self.PREFIX + self.TEXT)
                self.assertTrue(all("msg_id" not in c.kwargs for c in api.await_args_list))
                remembered.assert_called_once()
                feedback.assert_called_once()
                self.assertEqual(remembered.call_args.args[-1], self.PREFIX + self.TEXT)
                self.assertEqual(feedback.call_args.args[2], self.PREFIX + self.TEXT)
                new_rows = self._deliveries()[before:]
                self.assertEqual([(r["status"], r.get("text")) for r in new_rows],
                                 [("delivered", self.PREFIX + self.TEXT)])
                self.assertEqual(new_rows[0]["reference_mode"], "text")
                self.assertEqual(new_rows[0]["ref_msg_idx"], self.REF)

    async def test_second_explicit_rejection_does_not_try_a_third_send(self):
        api = AsyncMock(side_effect=[ForbiddenError("quote refused"), ForbiddenError("plain refused")])
        ok, remembered, feedback = await self._send(api)
        self.assertFalse(ok)
        self.assertEqual(api.await_count, 2)
        remembered.assert_not_called()
        feedback.assert_not_called()
        rows = self._deliveries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unconfirmed")
        self.assertNotIn("text", rows[0])

    async def test_unknown_delivery_result_never_retries_or_commits_as_delivered(self):
        for error in (asyncio.TimeoutError(), ServerError("ambiguous server response"),
                      aiohttp.ClientResponseError(SimpleNamespace(real_url="https://qq.test/messages"), (),
                                                  status=500, message="uncertain"), None):
            with self.subTest(error=type(error).__name__):
                before = len(self._deliveries())
                api = AsyncMock(return_value=None) if error is None else AsyncMock(side_effect=error)
                ok, remembered, feedback = await self._send(api)
                self.assertFalse(ok)
                api.assert_awaited_once()
                remembered.assert_not_called()
                feedback.assert_not_called()
                new_rows = self._deliveries()[before:]
                self.assertEqual(len(new_rows), 1)
                self.assertEqual(new_rows[0]["status"], "unconfirmed")
                self.assertNotIn("text", new_rows[0])
