"""提醒写入、群主清空与异步发送的实际事务边界；只用合成消息和假回执。"""
import asyncio
import collections
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import archive as archive_mod
import bot
import config
import delivery
import quotes
import reminders
from regular import reminder_review
import scheduler
import storage
import wordfilter


NOW = time.mktime((2026, 11, 5, 16, 0, 0, 0, 0, -1))
PROPOSAL = "好，已经记好了\n〔提醒 11-06 09:00〕提醒TA处理那个节目"


class _Fixture(unittest.IsolatedAsyncioTestCase):
    G, U, OWNER = "GID_TXN", "USER_TXN", "OWNER_TXN"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = storage.StateStore(str(Path(self.tmp.name) / "state.json"))
        self.sessions = storage.SessionStore()
        self.scheduler = scheduler.Scheduler()
        self.archive = archive_mod.MessageArchive(self.tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.sender = delivery.Sender(clock=lambda: NOW)
        self.feedback = SimpleNamespace(
            quiet=Mock(return_value=False), quiet_seconds=180,
            remember=Mock(), assess=AsyncMock(side_effect=AssertionError("unexpected triage")))
        self.review = AsyncMock(return_value="allow")
        self.response_check = AsyncMock(return_value=True)
        self.generation = AsyncMock(side_effect=AssertionError("unexpected generation"))
        self.provider = AsyncMock(side_effect=AssertionError("unexpected provider call"))
        self.interactions = SimpleNamespace(append=Mock())
        replacements = (
            (bot.runtime, "STATE", self.state),
            (bot.runtime, "SESSIONS", self.sessions),
            (bot.runtime, "SCHEDULER", self.scheduler),
            (bot.runtime, "ARCHIVE", self.archive),
            (bot.runtime, "QUOTES", quotes.QuoteBook(self.state)),
            (bot.runtime, "INTERACTIONS", self.interactions),
            (bot.runtime, "TURNS", SimpleNamespace(append=Mock())),
            (bot.runtime, "FEEDBACK", self.feedback),
            (bot.runtime, "OWNER_OPENID", self.OWNER),
            (bot, "SENDER", self.sender),
            (bot, "get_ai_reply", self.generation),
            (bot, "call_model", self.provider),
            (bot, "_delivery_observer", Mock(return_value=Mock())),
            (bot, "owner_hint_pending", Mock(return_value=False)),
            (bot, "group_buffers", collections.defaultdict(collections.deque)),
            (bot, "own_lines", collections.defaultdict(collections.deque)),
            (bot, "_carried_reminders", {}),
            (bot, "_reminder_revisions", collections.defaultdict(int)),
            (bot, "processed_msg_ids", collections.deque(maxlen=100)),
            (bot, "_recent_at_ids", collections.OrderedDict()),
            (reminder_review, "review_reminder", self.review),
            (wordfilter, "external_words", Mock(return_value=())),
            (wordfilter, "_runtime", set()),
            (config, "DIGEST_ENABLED", False),
            (config, "BOT_NAMES", ["小王"]),
        )
        for target, name, value in replacements:
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(reminder_review, "check_response", self.response_check, create=True)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(bot.time, "time", return_value=NOW)
        context.start()
        self.addCleanup(context.stop)
        self.message = self.message_for()

    def message_for(self, text="九点提醒我", *, member=None, private=False):
        author = SimpleNamespace(member_openid=member or self.U,
                                 user_openid=member or self.U)
        fields = dict(id="txn-" + str(id(author)), content=text, author=author,
                      timestamp=None, mentions=[], attachments=[],
                      reply=AsyncMock(return_value={"id": "sent"}))
        if not private:
            fields["group_openid"] = self.G
        return SimpleNamespace(**fields)

    def turn(self, *, intent=None):
        return bot.MediaTurn(group=self.G, user=self.U, draw=False, animate=False,
                             voice=False, remind=True, note="",
                             reminder_intent=intent,
                             reminder_revision=bot._reminder_revisions[self.G])

    def add(self, *, group=None, member=None, daily=False, approved=True,
            what="提醒TA浇花", ready=None):
        gid, uid = group or self.G, member or self.U
        when = "每天 17:00" if daily else "11-06 09:00"
        reminders.apply(self.scheduler, gid, uid, ("set", when, what), NOW)
        task = self.scheduler.tasks[reminders.task_id(gid, uid)]
        if approved:
            p = task["payload"]
            p.update(review_policy=reminder_review.POLICY_VERSION,
                     reviewed_what=p["what"], reviewed_daily=p["daily"])
        if ready is not None:
            task["payload"]["ready"] = ready
        return task

    async def reply(self, *, text="明早九点提醒我处理那个节目", intent=None):
        return await bot.reply_in_session(
            self.message, f"{self.G}_{self.U}", text, media=self.turn(intent=intent))

    def sent(self, message=None):
        return [call.kwargs.get("content", call.kwargs.get("markdown", {}).get("content", ""))
                for call in (message or self.message).reply.await_args_list]

    async def cancel_group(self):
        message = self.message_for("停止所有定时任务", member=self.OWNER)
        await bot.handle_cancel_all_reminders(message, self.G, True)
        return message


class ReminderWriteTransactionsTest(_Fixture):
    async def test_non_allow_keeps_old_task_and_replaces_the_original_promise(self):
        old = self.add()
        for verdict in ("clarify", "decline", "unavailable"):
            with self.subTest(verdict=verdict):
                self.review.return_value = verdict
                self.generation.side_effect = [
                    (PROPOSAL, None), ("你这话留了岔路，具体提醒什么？", None)]
                self.message.reply.reset_mock()
                await self.reply()
                self.assertIs(self.scheduler.tasks[old["id"]], old)
                body = self.sent()[-1]
                self.assertNotIn("已经记好了", body)
                self.assertIn("具体提醒什么", body)
                self.assertIn("没记", body)
                self.assertIn("原来的提醒没改", body)
                self.assertNotIn("〔提醒", self.sessions._sessions[f"{self.G}_{self.U}"]["messages"][-1]["content"])

    async def test_rewrite_with_function_markers_uses_local_truth_without_another_attempt(self):
        for rewritten in ("还是记上了\n〔提醒 11-06 10:00〕节目开始",
                          "〔画〕一朵花\n来张图吧", "〔不接〕", ""):
            with self.subTest(rewritten=rewritten):
                self.review.return_value = "clarify"
                self.generation.reset_mock()
                self.generation.side_effect = [(PROPOSAL, None), (rewritten, None)]
                self.message.reply.reset_mock()
                await self.reply()
                self.assertEqual(self.generation.await_count, 2)
                self.assertEqual(self.scheduler.pending(), [])
                self.assertIn("提醒什么", self.sent()[-1])
                self.assertNotIn("〔", self.sent()[-1])

    async def test_ordinary_family_reminder_is_saved_after_review(self):
        self.generation.side_effect = None
        self.generation.return_value = ("明早提醒你\n〔提醒 11-06 09:00〕提醒TA给爸爸打电话", None)
        await self.reply(text="明早九点提醒我给爸爸打电话")
        self.review.assert_awaited_once()
        task = self.scheduler.tasks[reminders.task_id(self.G, self.U)]
        self.assertEqual(task["payload"]["what"], "提醒TA给爸爸打电话")
        self.assertTrue(bot._reminder_review_valid(task))

    async def test_plain_false_promise_in_rewrite_is_rejected_by_response_check(self):
        self.review.return_value = "clarify"
        self.response_check.return_value = False
        self.generation.side_effect = [(PROPOSAL, None), ("行，我已经记好了", None)]
        await self.reply()
        self.response_check.assert_awaited_once()
        self.assertEqual(self.scheduler.pending(), [])
        self.assertNotIn("已经记好了", self.sent()[-1])
        self.assertIn("提醒什么", self.sent()[-1])

    async def test_clarify_intent_rejects_an_initial_markerless_assumption_without_rewriting(self):
        old = self.add()
        self.generation.side_effect = None
        self.generation.return_value = ("这层意思我懂，你这个节目我就不记了", None)
        self.response_check.return_value = False
        await self.reply(intent="clarify")
        self.generation.assert_awaited_once()
        self.review.assert_not_awaited()
        self.response_check.assert_awaited_once()
        self.assertEqual(self.response_check.await_args.args[1], "clarify")
        self.assertIn("尚未保存", self.generation.await_args.kwargs["media_note"])
        self.assertIs(self.scheduler.tasks[old["id"]], old)
        self.assertNotIn("这层意思我懂", self.sent()[-1])
        self.assertIn("提醒什么？", self.sent()[-1])
        self.assertIn("原来的提醒没改", self.sent()[-1])

    async def test_clarify_intent_sends_a_valid_first_question_with_one_generation(self):
        old = self.add()
        question = "先揭一下谜底，具体要提醒什么节目？说清楚我再记"
        self.generation.side_effect = None
        self.generation.return_value = (question, None)
        await self.reply(intent="clarify")
        self.generation.assert_awaited_once()
        self.review.assert_not_awaited()
        self.response_check.assert_awaited_once()
        self.assertTrue(self.sent()[-1].startswith(question))
        self.assertIs(self.scheduler.tasks[old["id"]], old)

    async def test_decline_intent_accepts_a_playful_markerless_not_saved_reply(self):
        reply = "你想把段子塞进闹钟啊，这条没记"
        self.generation.side_effect = None
        self.generation.return_value = (reply, None)
        await self.reply(intent="decline")
        self.generation.assert_awaited_once()
        self.review.assert_not_awaited()
        self.response_check.assert_awaited_once()
        self.assertEqual(self.response_check.await_args.args[1], "decline")
        self.assertTrue(self.sent()[-1].startswith(reply))
        self.assertEqual(self.scheduler.pending(), [])

    async def test_cancel_own_reminder_does_not_call_content_review(self):
        old = self.add()
        other = self.add(member="OTHER_TXN")
        self.generation.side_effect = None
        self.generation.return_value = ("取消你那条了\n〔取消提醒〕", None)
        await self.reply(text="我自己的提醒取消")
        self.review.assert_not_awaited()
        self.assertNotIn(old["id"], self.scheduler.tasks)
        self.assertIs(self.scheduler.tasks[other["id"]], other)

    async def test_cancel_all_during_generation_prevents_a_late_write(self):
        started, resume = asyncio.Event(), asyncio.Event()
        calls = 0

        async def generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await resume.wait()
                return PROPOSAL, None
            return "本群旧提醒刚清空，这轮没记", None

        self.generation.side_effect = generate
        running = asyncio.create_task(self.reply())
        await started.wait()
        await self.cancel_group()
        resume.set()
        await running
        self.assertEqual(self.scheduler.pending(), [])
        self.review.assert_not_awaited()
        self.assertNotIn("已经记好了", self.sent()[-1])

    async def test_cancel_all_during_review_prevents_a_late_write(self):
        started, resume = asyncio.Event(), asyncio.Event()

        async def review(*args, **kwargs):
            started.set()
            await resume.wait()
            return "allow"

        self.review.side_effect = review
        self.generation.side_effect = [(PROPOSAL, None), ("旧提醒刚清空，这轮没记", None)]
        running = asyncio.create_task(self.reply())
        await started.wait()
        await self.cancel_group()
        resume.set()
        await running
        self.assertEqual(self.scheduler.pending(), [])
        self.assertNotIn("已经记好了", self.sent()[-1])

    async def test_cancel_all_suppresses_a_queued_old_success_acknowledgement(self):
        self.generation.side_effect = None
        self.generation.return_value = (
            "明早提醒你浇花\n〔提醒 11-06 09:00〕提醒TA浇花", None)
        self.message.reply.side_effect = ConnectionError("offline")
        await self.reply(text="明早九点提醒我浇花")
        self.assertEqual(len(self.sender.pending), 1)
        self.assertIn(reminders.task_id(self.G, self.U), self.scheduler.tasks)
        await self.cancel_group()
        await self.sender.drain()
        self.assertEqual(self.message.reply.await_count, 1)
        self.assertEqual(self.sender.last_status, "superseded")
        self.assertEqual(self.scheduler.pending(), [])
        self.assertEqual(self.sessions._sessions.get(f"{self.G}_{self.U}", {}).get("messages", []), [])


class ReminderControlTransactionsTest(_Fixture):
    async def test_bulk_clear_is_flushed_before_confirmation(self):
        self.state.register_collector(self.scheduler.dump_into)
        self.add(daily=True)
        self.state.save_now(force=True)
        await self.cancel_group()
        restored = storage.StateStore(self.state.path)
        restored_scheduler = scheduler.Scheduler()
        restored_scheduler.hydrate(restored.data["schedule"])
        self.assertEqual(restored_scheduler.pending(), [])

    async def test_owner_command_accepts_the_sdk_slotted_message(self):
        from botpy.message import GroupMessage
        self.add()
        message = GroupMessage(None, "synthetic-event", {
            "id": "sdk-command", "content": "小王，停止所有定时任务",
            "group_openid": self.G, "author": {"member_openid": self.OWNER},
            "timestamp": "2026-11-05T08:00:00+00:00"})
        with patch.object(bot, "safe_reply", AsyncMock()) as send:
            await bot.GroupBot.handle_group_msg(
                SimpleNamespace(robot=SimpleNamespace(id="BOT_TXN", name="小王")), message, is_at=True)
        self.assertEqual(self.scheduler.pending(), [])
        send.assert_awaited_once()
        self.assertIn("1 条", send.await_args.args[1])

    async def test_owner_command_cancels_before_carried_delivery_without_a_model(self):
        task = self.add(daily=True, ready="旧提醒正文")
        other = self.add(group="OTHER_GRP")
        self.scheduler.at("followup", self.G, NOW, {"ready": "内部查询结果"}, task_id="lookup")
        message = self.message_for("小王，停止所有定时任务！", member=self.OWNER)
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_TXN", name="regular"))
        with patch.object(bot, "deliver_carried", AsyncMock()) as carried:
            await bot.GroupBot.handle_group_msg(fake_self, message, is_at=True)
        carried.assert_not_awaited()
        self.feedback.assess.assert_not_awaited()
        self.generation.assert_not_awaited()
        self.provider.assert_not_awaited()
        self.review.assert_not_awaited()
        self.assertNotIn(task["id"], self.scheduler.tasks)
        self.assertIs(self.scheduler.tasks[other["id"]], other)
        self.assertIn("lookup", self.scheduler.tasks)
        self.assertIn("本群 1 条", self.sent(message)[-1])
        self.assertTrue(bot.is_control_command(message.content))

    async def test_non_owner_command_does_not_clear_or_deliver_reminders(self):
        task = self.add(ready="旧提醒正文")
        message = self.message_for("停止所有定时任务")
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_TXN", name="regular"))
        with patch.object(bot, "deliver_carried", AsyncMock()) as carried:
            await bot.GroupBot.handle_group_msg(fake_self, message, is_at=True)
        carried.assert_not_awaited()
        self.assertIs(self.scheduler.tasks[task["id"]], task)
        self.assertEqual(bot._reminder_revisions[self.G], 0)
        self.assertIn("群主", self.sent(message)[-1])
        self.generation.assert_not_awaited()

    async def test_private_cancel_all_points_to_the_group_and_preserves_tasks(self):
        task = self.add()
        message = self.message_for("停止所有定时任务", member=self.OWNER, private=True)
        await bot.GroupBot.on_c2c_message_create(SimpleNamespace(), message)
        self.assertIs(self.scheduler.tasks[task["id"]], task)
        self.assertIn("那个群", self.sent(message)[-1])
        self.generation.assert_not_awaited()
        self.provider.assert_not_awaited()


class ReminderSendTransactionsTest(_Fixture):
    async def test_daily_generation_crossing_grace_advances_without_permanent_pause(self):
        task = self.add(daily=True)
        task["payload"]["at"] = NOW
        clock = [NOW]

        async def generate(*args, **kwargs):
            clock[0] = NOW + reminders.GRACE_SECONDS + 1
            return "该浇花了"

        with patch.object(bot.time, "time", side_effect=lambda: clock[0]), \
                patch.object(bot, "call_model", AsyncMock(side_effect=generate)), \
                patch.object(bot, "post_to_group", AsyncMock(return_value=True)) as post:
            nxt = await bot.fire_reminder(task)
        post.assert_not_awaited()
        next_task = self.scheduler.tasks[task["id"]]
        self.assertGreater(nxt, clock[0])
        self.assertEqual(next_task["due"], nxt)
        self.assertTrue(next_task["payload"]["daily"])
        self.assertFalse(next_task["payload"].get("paused", False))

    async def test_cancel_during_daily_generation_does_not_send_or_resurrect(self):
        task = self.add(daily=True)
        task["payload"]["at"] = NOW
        started, resume = asyncio.Event(), asyncio.Event()

        async def generate(*args, **kwargs):
            started.set()
            await resume.wait()
            return "该浇花了"

        with patch.object(bot, "call_model", AsyncMock(side_effect=generate)), \
                patch.object(bot, "post_to_group", AsyncMock(return_value=True)) as post:
            running = asyncio.create_task(bot.fire_reminder(task))
            await started.wait()
            await self.cancel_group()
            resume.set()
            self.assertIsNone(await running)
        post.assert_not_awaited()
        self.assertNotIn(task["id"], self.scheduler.tasks)

    async def test_replacing_during_generation_preserves_the_new_task(self):
        old = self.add(daily=True)
        old["payload"]["at"] = NOW
        started, resume = asyncio.Event(), asyncio.Event()

        async def generate(*args, **kwargs):
            started.set()
            await resume.wait()
            return "旧任务正文"

        with patch.object(bot, "call_model", AsyncMock(side_effect=generate)), \
                patch.object(bot, "post_to_group", AsyncMock(return_value=True)) as post:
            running = asyncio.create_task(bot.fire_reminder(old))
            await started.wait()
            new = self.add(what="提醒TA给花施肥")
            resume.set()
            await running
        post.assert_not_awaited()
        self.assertIs(self.scheduler.tasks[new["id"]], new)
        self.assertIsNone(bot._reminder_done(old, NOW))
        self.assertIs(self.scheduler.tasks[new["id"]], new)

    async def test_filter_change_during_empty_generation_blocks_local_fallback(self):
        task = self.add(what="提醒TA收好合成火龙口令")
        task["payload"]["at"] = NOW

        async def generate(*args, **kwargs):
            wordfilter._runtime.add("合成火龙口令")
            return None

        with patch.object(bot, "call_model", AsyncMock(side_effect=generate)), \
                patch.object(bot, "post_to_group", AsyncMock(return_value=True)) as post:
            await bot.fire_reminder(task)
        post.assert_not_awaited()
        self.assertTrue(task["payload"]["paused"])
        self.assertNotIn("ready", task["payload"])

    async def test_filter_change_after_queue_blocks_cached_ready_on_drain(self):
        task = self.add(ready="同学，收好合成火龙口令")
        task["payload"]["at"] = NOW
        self.message.reply.side_effect = ConnectionError("offline")
        await bot.deliver_carried(self.message, self.G)
        self.assertEqual(len(self.sender.pending), 1)
        wordfilter._runtime.add("合成火龙口令")
        await self.sender.drain()
        self.assertEqual(self.message.reply.await_count, 1)
        self.assertEqual(self.sender.last_status, "superseded")
        await bot.deliver_carried(self.message, self.G)
        self.assertTrue(task["payload"]["paused"])
        self.assertNotIn("ready", task["payload"])

    async def test_legacy_task_is_paused_instead_of_guessed_or_broadcast(self):
        task = self.add(approved=False, ready="含义未核实的旧正文")
        task["payload"]["at"] = NOW
        with patch.object(bot, "post_to_group", AsyncMock(return_value=True)) as post:
            await bot.fire_reminder(task)
        self.provider.assert_not_awaited()
        post.assert_not_awaited()
        self.assertTrue(task["payload"]["paused"])
        self.assertIs(self.scheduler.tasks[task["id"]], task)
        self.assertNotIn("ready", task["payload"])
        self.assertIn("已暂停", bot._reminder_note(task, NOW))
