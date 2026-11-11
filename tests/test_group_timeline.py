"""用真实群消息入口控制并发时序，收件前背景与后来事件不能混为一谈。"""
import asyncio
from collections import defaultdict
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
import runtime
from storage import SessionStore
from test_feedback_receipt import answer
from test_when_to_intervene import A, B, G, _Base


FIRST = "第一问：今天吃什么"
SECOND = "第二问：明天吃什么"
THIRD = "第三问：后天吃什么"
FIRST_REPLY = "第一题已经答好"
SECOND_REPLY = "第二题也答好了"


class GroupTimelineTest(_Base):
    def setUp(self):
        super().setUp()
        self.tasks = []
        self._patch(runtime, "SESSIONS", SessionStore())
        self._patch(bot, "_unprompted_locks", defaultdict(asyncio.Lock))
        self._patch(bot, "owner_hint_pending", lambda *args, **kwargs: False)
        for name, value in (("BANTER_BURST_SECONDS", 0.0),
                            ("DECISION_CALLBACK_ENABLED", False),
                            ("SEARCH_ENABLED", False)):
            self._patch(config, name, value)

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def _patch(self, target, name, value):
        context = patch.object(target, name, value)
        context.start()
        self.addCleanup(context.stop)

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def _wait(self, event):
        await asyncio.wait_for(event.wait(), timeout=2.0)

    def _model(self, first_started, release_first, *, second_started=None,
               release_second=None):
        async def generate(messages, max_tokens, **kwargs):
            self.prompts_seen.append(messages)
            current = messages[-1]["content"]
            if current.endswith(FIRST):
                first_started.set()
                await release_first.wait()
                return FIRST_REPLY
            if current.endswith(SECOND):
                if second_started is not None:
                    second_started.set()
                if release_second is not None:
                    await release_second.wait()
                return SECOND_REPLY
            return "第三题的答案"
        self._patch(bot, "call_model", generate)

    def _watch_reply(self, name, events, *, owed=None):
        original = getattr(bot, name)

        async def watched(message, session_id, user_text, **kwargs):
            if user_text in events:
                if owed is not None:
                    owed[user_text] = kwargs.get("owed")
                events[user_text].set()
            return await original(message, session_id, user_text, **kwargs)
        self._patch(bot, name, watched)

    def _prompt_for(self, text):
        return next(messages for messages in self.prompts_seen
                    if messages[-1]["content"].endswith(text))

    @staticmethod
    def _ordinary_history(messages):
        return [message["content"] for message in messages[:-1]
                if message["role"] != "system"]

    def _owe_reply(self):
        self.feedback.remember(G, A, "这句是旧的接话对象", route="banter",
                               outgoing_id="old-delivered-reply")
        self.calls.side_effect = lambda text, questions: answer(
            questions, feedback_target="bot_response", next_move="keep_playful",
            addressed="answer", reminder_request="none", recall="no_need",
            request_kind="not_request")

    async def test_same_member_keeps_receipt_history_and_future_event_order(self):
        first_started, release_first, second_waiting = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())
        self._model(first_started, release_first)
        self._watch_reply("reply_in_session", {SECOND: second_waiting})
        first = self._spawn(self._drive(FIRST, is_at=True, msg_id="timeline-a"))
        await self._wait(first_started)
        second = self._spawn(self._drive(SECOND, is_at=True, msg_id="timeline-b"))
        await self._wait(second_waiting)
        self.assertEqual(len(self.prompts_seen), 1, "同一 session 的 B 应等待 A 送达")
        release_first.set()
        first_message, second_message = await asyncio.gather(first, second)
        first_message.reply.assert_awaited_once()
        second_message.reply.assert_awaited_once()

        second_prompt = self._prompt_for(SECOND)
        self.assertNotIn(FIRST_REPLY, self._ordinary_history(second_prompt))
        dynamic = second_prompt[-1]["content"]
        self.assertIn("本句到达之后发生的事", dynamic)
        self.assertIn(FIRST_REPLY, dynamic)
        self.assertIn("对方发这句时还没看到这些回复", dynamic)
        self.assertNotIn(FIRST_REPLY, dynamic.split("【本句到达之后发生的事", 1)[0])

        await self._drive(THIRD, is_at=True, msg_id="timeline-c")
        self.assertEqual(self._ordinary_history(self._prompt_for(THIRD)),
                         [FIRST, SECOND, FIRST_REPLY, SECOND_REPLY])

    async def test_different_members_generate_concurrently_and_quote_own_sources(self):
        first_started, release_first, second_started, release_second = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event())
        self._model(first_started, release_first, second_started=second_started,
                    release_second=release_second)
        first = self._spawn(self._drive(FIRST, is_at=True, msg_id="parallel-a",
                                        scene={"msg_idx": "quote-a"}))
        await self._wait(first_started)
        second = self._spawn(self._drive(SECOND, sender=B, is_at=True, msg_id="parallel-b",
                                         scene={"msg_idx": "quote-b"}))
        await self._wait(second_started)
        self.assertFalse(first.done(), "不同成员没有被改成全群串行")
        release_first.set()
        first_message = await first
        release_second.set()
        second_message = await second
        self.assertEqual(first_message.reply.await_args.kwargs["message_reference"]["message_id"],
                         "quote-a")
        self.assertEqual(second_message.reply.await_args.kwargs["message_reference"]["message_id"],
                         "quote-b")
        self.assertEqual([message["content"] for message in self._session(A)],
                         [FIRST, FIRST_REPLY])
        self.assertEqual([message["content"] for message in self._session(B)],
                         [SECOND, SECOND_REPLY])

    async def test_optional_reply_is_cancelled_if_same_member_continues_during_generation(self):
        first_started, release_first, second_waiting = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())
        self._model(first_started, release_first)
        self._watch_reply("reply_unprompted", {SECOND: second_waiting})
        first = self._spawn(self._drive(FIRST, msg_id="optional-a"))
        await self._wait(first_started)
        second = self._spawn(self._drive(SECOND, msg_id="optional-b"))
        await self._wait(second_waiting)
        release_first.set()
        first_message, second_message = await asyncio.gather(first, second)
        first_message.reply.assert_not_awaited()
        second_message.reply.assert_awaited_once()
        self.assertEqual([message["content"] for message in self._session()],
                         [SECOND, SECOND_REPLY])
        gate = next(row for row in self.log.gates()
                    if row.get("source_message_id") == "optional-a")
        self.assertEqual((gate["selected"], gate["reason"]), (False, "stale"))

    async def test_owed_reply_survives_same_member_continuation_before_generation(self):
        self._owe_reply()
        first_waiting, second_waiting = asyncio.Event(), asyncio.Event()
        owed = {}
        self._watch_reply("reply_unprompted", {FIRST: first_waiting, SECOND: second_waiting},
                          owed=owed)
        self.reply_text = "这句必须接上"
        async with bot._unprompted_locks[G]:
            first = self._spawn(self._drive(FIRST, msg_id="owed-before-a"))
            await self._wait(first_waiting)
            second = self._spawn(self._drive(SECOND, msg_id="owed-before-b"))
            await self._wait(second_waiting)
            self.assertEqual(self.prompts_seen, [])
        first_message, second_message = await asyncio.gather(first, second)
        self.assertEqual(owed, {FIRST: True, SECOND: True})
        first_message.reply.assert_awaited_once()
        second_message.reply.assert_awaited_once()
        self.assertEqual(len(self.prompts_seen), 2)

    async def test_owed_reply_survives_same_member_continuation_during_generation(self):
        self._owe_reply()
        first_started, release_first, second_waiting = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())
        self._model(first_started, release_first)
        owed = {}
        self._watch_reply("reply_unprompted", {SECOND: second_waiting}, owed=owed)
        first = self._spawn(self._drive(FIRST, msg_id="owed-during-a"))
        await self._wait(first_started)
        second = self._spawn(self._drive(SECOND, msg_id="owed-during-b"))
        await self._wait(second_waiting)
        release_first.set()
        first_message, second_message = await asyncio.gather(first, second)
        self.assertTrue(owed[SECOND])
        first_message.reply.assert_awaited_once()
        second_message.reply.assert_awaited_once()

    async def test_late_unconfirmed_output_is_not_presented_as_delivered(self):
        first_started, release_first, second_waiting = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())
        self._model(first_started, release_first)
        self._watch_reply("reply_in_session", {SECOND: second_waiting})
        first = self._spawn(self._drive(FIRST, is_at=True, msg_id="unknown-a"))
        await self._wait(first_started)
        second = self._spawn(self._drive(SECOND, is_at=True, msg_id="unknown-b"))
        await self._wait(second_waiting)
        bot._note_own_line(G, "那条语音还没确认", medium="voice", status="unconfirmed")
        release_first.set()
        await asyncio.gather(first, second)
        second_prompt = self._prompt_for(SECOND)
        line = next(line for line in second_prompt[-1]["content"].splitlines()
                    if "那条语音还没确认" in line)
        self.assertIn("送达未确认", line)
        self.assertNotIn("已实际送达", line)
        self.assertNotIn("那条语音还没确认", self._ordinary_history(second_prompt))

    async def test_voice_wait_does_not_retarget_feedback_to_a_later_delivery(self):
        self._owe_reply()
        listening, resume = asyncio.Event(), asyncio.Event()

        async def recognize(*args, **kwargs):
            listening.set()
            await resume.wait()
            return "这句应该再补一项"

        self._patch(bot, "_recognize_voices", recognize)
        message = SimpleNamespace(
            id="voice-feedback", content="", group_openid=G,
            author=SimpleNamespace(member_openid=A), attachments=[], mentions=[],
            timestamp=None, _scene={}, _voice_attachments=[SimpleNamespace()],
            reply=AsyncMock(return_value={"id": "voice-answer"}))
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_WHEN", name="regular"))
        task = self._spawn(bot.GroupBot.handle_group_msg(fake_self, message, is_at=True))
        await self._wait(listening)
        self.feedback.remember(G, B, "等待语音时另一题已经答了", route="reply",
                               outgoing_id="new-delivered-reply")
        bot._note_own_line(G, "等待语音时另一题已经答了", user=B)
        resume.set()
        await task
        payload = json.loads(self.calls.await_args.args[0])
        self.assertEqual(payload["bot_reply"]["text"], "这句是旧的接话对象")
        feedback = next(row for row in self.log.rows
                        if row["kind"] == "natural_feedback"
                        and row.get("source_message_id") == message.id)
        self.assertEqual(feedback["source_reply_id"], "old-delivered-reply")
        final = self.prompts_seen[-1][-1]["content"]
        self.assertIn("等待语音时另一题已经答了", final)
        self.assertIn("本句到达之后发生的事", final)
        message.reply.assert_awaited_once()
