# -*- coding: utf-8 -*-
"""到点提醒（2026-11-02，见 docs/reminders.md）。

事故：11-01 02:49 群主让小王「明天早上 9 点提醒大家你回来了」，它答「9 点你们等我上线就行」，
代码里却没有这回事 —— 那一轮只有判断和聊天回复，九点什么也没发生。
修法：小王答应了就在回复里写〔提醒 11-03 09:00〕；一人一条，再定就顶掉，〔取消提醒〕撤掉；
到点主动发，发不出去捎在下一条消息上，过点一小时作罢。
"""
import sys
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from botpy.errors import ForbiddenError

sys.path.insert(0, "..")

import bot
import expression
import prompts
import reminders
import scheduler as scheduler_mod
import storage
from delivery import Sender


def at(month, day, hour, minute, year=2026):
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


NOW = at(11, 2, 21, 30)


class ResolveTest(unittest.TestCase):
    def test_dated_time(self):
        self.assertEqual(reminders.resolve("11-03 09:00", NOW), (at(11, 3, 9, 0), False, "09:00"))
        self.assertEqual(reminders.resolve("2026-11-03 09:00", NOW)[0], at(11, 3, 9, 0))

    def test_time_alone_is_the_next_one(self):
        self.assertEqual(reminders.resolve("22:00", NOW)[0], at(11, 2, 22, 0))
        self.assertEqual(reminders.resolve("08:00", NOW)[0], at(11, 3, 8, 0))

    def test_daily(self):
        self.assertEqual(reminders.resolve("每天 08:00", NOW), (at(11, 3, 8, 0), True, "08:00"))

    def test_year_end_wraps_to_next_year(self):
        due = reminders.resolve("01-02 09:00", at(12, 31, 20, 0))[0]
        self.assertEqual(due, at(1, 2, 9, 0, year=2027))

    def test_rejections_say_why(self):
        for when, why in (("11-02 09:00", "已经过了"), ("12-25 09:00", "太远"),
                          ("02-30 09:00", "不存在"), ("明早九点", "没看懂"), ("25:00", "没看懂"),
                          ("11-02 21:30", "已经过了")):
            with self.subTest(when=when), self.assertRaises(reminders.Rejected) as caught:
                reminders.resolve(when, NOW)
            self.assertIn(why, caught.exception.args[0])


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.s = scheduler_mod.Scheduler()

    def set(self, member, when, what="叫我开会"):
        return reminders.apply(self.s, "g", member, ("set", when, what), NOW)

    def test_one_per_person_new_replaces_old(self):
        self.assertEqual(self.set("a", "11-03 09:00"),
                         ("提醒记下了：11-03 09:00", "（记下了：11-03 09:00）"))
        note, line = self.set("a", "11-03 10:00", "叫我吃饭")
        self.assertIn("原来那条 11-03 09:00 不提醒了", note)
        self.assertEqual(line, "（记下了：11-03 10:00）")
        self.assertEqual(len(self.s.pending(kind=reminders.KIND)), 1)
        self.assertEqual(self.s.tasks[reminders.task_id("g", "a")]["payload"]["what"], "叫我吃饭")
        self.set("b", "11-03 09:00")                       # 别人的不受影响
        self.assertEqual(len(self.s.pending(kind=reminders.KIND)), 2)

    def test_cancel(self):
        self.set("a", "每天 08:00")
        note, _ = reminders.apply(self.s, "g", "a", ("cancel", "", ""), NOW)
        self.assertIn("取消了（原来是 每天 08:00）", note)
        self.assertEqual(self.s.pending(), [])
        note, _ = reminders.apply(self.s, "g", "a", ("cancel", "", ""), NOW)
        self.assertIn("本来就没有", note)

    def test_rejected_keeps_the_old_one_and_says_so(self):
        self.set("a", "11-03 09:00")
        note, line = self.set("a", "11-01 09:00")
        self.assertIn("没记上：这个时间已经过了", note)
        self.assertIn("没记上", line)
        self.assertEqual(self.s.tasks[reminders.task_id("g", "a")]["payload"]["at"], at(11, 3, 9, 0))
        _, line = reminders.apply(self.s, "g", "a", ("set", "11-03 09:00", "骂人的话"), NOW,
                                  blocked=lambda text: "骂人" in text)
        self.assertIn("不提醒", line)
        _, line = self.set("a", "11-03 09:00", "")
        self.assertIn("没写提醒什么", line)

    def test_note_gives_the_time_and_their_reminder(self):
        note = reminders.render_note(None, NOW)
        self.assertEqual(note, "【现在】11-02 周一 21:30")
        self.set("a", "每天 08:00", "喊我喝水")
        note = reminders.render_note(self.s.tasks[reminders.task_id("g", "a")], NOW)
        self.assertIn("【TA 让你记着的提醒】每天 08:00：喊我喝水", note)


class ParseTest(unittest.TestCase):
    def test_tag_line_is_taken_out_of_what_is_said(self):
        s = expression.parse("好，明早九点叫大家\n〔提醒 11-03 09:00〕提醒大家小王回来了")
        self.assertEqual(s.text, "好，明早九点叫大家")
        self.assertEqual(s.remind, ("set", "11-03 09:00", "提醒大家小王回来了"))
        self.assertEqual(expression.parse("〔取消提醒〕\n行").remind, ("cancel", "", ""))

    def test_heading_without_time_is_just_text(self):
        s = expression.parse("【提醒】明天别忘了带伞")
        self.assertIsNone(s.remind)
        self.assertEqual(s.text, "【提醒】明天别忘了带伞")

    def test_rules_name_the_failure_mode(self):
        """规则是对着「嘴上答应、实际没记」写的。"""
        self.assertIn("不写这一行，就是没记上", prompts.PROMPT_REMIND_RULES)
        self.assertIn("一个人只记一条", prompts.PROMPT_REMIND_RULES)
        self.assertIn("别用「你」「我」", prompts.PROMPT_REMIND_RULES)   # 到点时「你」是谁就说不清了


class _Reply(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sessions = storage.SessionStore()
        self.scheduler = scheduler_mod.Scheduler()
        for p in (patch.object(bot.runtime, "SESSIONS", self.sessions),
                  patch.object(bot.runtime, "SCHEDULER", self.scheduler),
                  patch.object(bot.runtime, "STATE", SimpleNamespace(data={}, mark_dirty=Mock())),
                  patch.object(bot.runtime, "FEEDBACK", SimpleNamespace(
                      remember=Mock(), quiet=Mock(return_value=False), quiet_seconds=180.0)),
                  patch.object(bot.runtime, "INTERACTIONS", SimpleNamespace(append=Mock())),
                  patch.object(bot, "SENDER", Sender()),
                  patch.object(bot, "owner_hint_pending", return_value=False),
                  patch.object(bot, "_delivery_observer", return_value=Mock()),
                  patch.object(bot.time, "time", return_value=NOW)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(bot._carried_reminders.clear)
        self.message = SimpleNamespace(
            group_openid="group", id="incoming", author=SimpleNamespace(member_openid="member"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            reply=AsyncMock(return_value={"id": "sent"}))

    def turn(self, remind=True):
        return bot.MediaTurn(group="group", user="member", score=0, draw=False, voice=False,
                             remind=remind, note="")

    async def reply(self, body, **kw):
        with patch.object(bot, "get_ai_reply", AsyncMock(return_value=(body, None))):
            return await bot.reply_in_session(self.message, "group_member", "九点提醒大家",
                                              media=self.turn(**kw))

    def sent(self):
        return [c.kwargs.get("content") for c in self.message.reply.await_args_list]

    def record(self):
        return self.sessions._sessions["group_member"]["messages"][-1]["content"]


class SetWhileReplyingTest(_Reply):
    async def test_tag_sets_it_and_the_session_remembers(self):
        await self.reply("行，明早九点我叫大家\n〔提醒 11-03 09:00〕提醒大家小王回来了")
        self.assertEqual(self.sent(), ["行，明早九点我叫大家\n（记下了：11-03 09:00）"])
        task = self.scheduler.tasks[reminders.task_id("group", "member")]
        self.assertEqual((task["due"], task["payload"]["what"]), (at(11, 3, 9, 0), "提醒大家小王回来了"))
        self.assertEqual(self.record(), "〔提醒 11-03 09:00〕提醒大家小王回来了\n"
                                        "行，明早九点我叫大家\n（记下了：11-03 09:00）\n"
                                        "（系统：提醒记下了：11-03 09:00）")

    async def test_promise_without_tag_sets_nothing(self):
        await self.reply("好，九点你们等我上线就行")
        self.assertEqual(self.scheduler.pending(), [])

    async def test_bad_time_is_admitted_in_the_same_reply(self):
        await self.reply("好嘞\n〔提醒 11-01 09:00〕叫我开会")
        self.assertEqual(self.sent(), ["好嘞\n（这个提醒没记上：这个时间已经过了）"])
        self.assertEqual(self.scheduler.pending(), [])

    async def test_not_honoured_when_nobody_asked(self):
        await self.reply("顺手记一个\n〔提醒 11-03 09:00〕叫我开会", remind=False)
        self.assertEqual(self.sent(), ["顺手记一个"])
        self.assertEqual(self.scheduler.pending(), [])

    async def test_tag_alone_still_confirms(self):
        await self.reply("〔取消提醒〕")
        self.assertEqual(self.sent(), ["TA 本来就没有记着的提醒"])


class FireTest(_Reply):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.posts = []
        self.post_kwargs = []
        self.post_ok = True

        async def post(group_id, text, **kw):
            self.posts.append(text)
            self.post_kwargs.append(kw)
            return self.post_ok
        for p in (patch.object(bot, "post_to_group", post),
                  patch.object(bot, "call_model", AsyncMock(return_value="老王，九点了，开会去")),
                  patch.object(bot, "_who_label", return_value="老王")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: bot.own_lines.pop("group", None))

    def set(self, when):
        reminders.apply(self.scheduler, "group", "member", ("set", when, "叫我开会"), NOW)
        return self.scheduler.tasks[reminders.task_id("group", "member")]

    async def run_at(self, now):
        with patch.object(bot.time, "time", return_value=now):
            await self.scheduler.run_due(now=now)

    def register(self):
        self.scheduler.register(reminders.KIND, bot.fire_reminder, hold=bot._reminder_hold)

    async def test_one_off_is_said_once_and_gone(self):
        self.register()
        self.set("11-03 09:00")
        await self.run_at(at(11, 3, 9, 0))
        self.assertEqual(self.posts, ["老王，九点了，开会去"])
        self.assertEqual(self.post_kwargs[0]["mention_member"], "member")
        self.assertEqual(self.scheduler.pending(), [])
        self.assertEqual(bot.own_lines["group"][-1]["text"], "老王，九点了，开会去")
        await self.run_at(at(11, 3, 9, 5))
        self.assertEqual(len(self.posts), 1)

    async def test_daily_moves_to_tomorrow(self):
        self.register()
        self.set("每天 08:00")
        await self.run_at(at(11, 3, 8, 0))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.scheduler.pending()[0]["due"], at(11, 4, 8, 0))

    async def test_rejected_post_rides_on_the_next_message(self):
        self.register()
        self.set("11-03 09:00")
        self.post_ok = False
        await self.run_at(at(11, 3, 9, 0))
        self.assertEqual(len(self.scheduler.pending()), 1)          # 留着等捎带
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
        payload = self.message.reply.await_args.kwargs
        self.assertEqual(payload["msg_type"], 2)
        self.assertEqual(payload["markdown"]["content"],
                         '<qqbot-at-user id="member" /> 老王，九点了，开会去')
        self.assertEqual(self.scheduler.pending(), [])
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 20)):
            await bot.deliver_carried(self.message, "group")         # 不重复
        self.assertEqual(len(self.sent()), 1)

    async def test_carried_reminder_mentions_requester_not_the_next_speaker(self):
        task = self.set("11-03 09:00")
        task["payload"]["ready"] = "老王，到点了"
        self.message.author.member_openid = "another-speaker"
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
        wire = self.message.reply.await_args.kwargs["markdown"]["content"]
        self.assertTrue(wire.startswith('<qqbot-at-user id="member" />'))
        self.assertNotIn("another-speaker", wire)
        self.assertEqual(self.scheduler.pending(), [])

    async def test_queued_reminder_waits_for_receipt_and_is_not_queued_twice(self):
        task = self.set("11-03 09:00")
        task["payload"]["ready"] = "老王，到点了"
        self.message.reply.side_effect = [ConnectionError("offline"), {"id": "sent"}]
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
            self.assertIn(task["id"], self.scheduler.tasks)
            self.assertEqual(task["payload"]["ready"], "老王，到点了")
            await bot.deliver_carried(self.message, "group")
            self.assertEqual(self.message.reply.await_count, 1)
            await bot.SENDER.drain()
        self.assertEqual(self.message.reply.await_count, 2)
        self.assertEqual(self.scheduler.pending(), [])
        self.assertNotIn(task["id"], bot._carried_reminders)

    async def test_rejected_carried_reminder_is_kept_for_a_later_message(self):
        task = self.set("11-03 09:00")
        task["payload"]["ready"] = "老王，到点了"
        self.message.reply.side_effect = [RuntimeError("rejected"), {"id": "sent"}]
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
            self.assertIn(task["id"], self.scheduler.tasks)
            self.assertNotIn(task["id"], bot._carried_reminders)
            await bot.deliver_carried(self.message, "group")
        self.assertEqual(self.scheduler.pending(), [])

    async def test_carried_fallback_is_not_counted_as_the_requested_reminder(self):
        task = self.set("11-03 09:00")
        task["payload"]["ready"] = "原提醒正文"
        sender = Sender(fallback=lambda message: "这次没能发出来")
        self.message.reply.side_effect = [RuntimeError("40034006"),
                                          {"id": "fallback"}, {"id": "reminder"}]
        with patch.object(bot, "SENDER", sender), \
                patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
            self.assertIn(task["id"], self.scheduler.tasks)
            self.assertEqual(task["payload"]["ready"], "原提醒正文")
            self.assertNotIn(task["id"], bot._carried_reminders)
            self.assertEqual(list(bot.own_lines.get("group", ())), [])
            await bot.deliver_carried(self.message, "group")
        self.assertEqual(self.scheduler.pending(), [])
        self.assertEqual(bot.own_lines["group"][-1]["text"], "原提醒正文")

    async def test_replacing_a_queued_reminder_does_not_cancel_the_new_one(self):
        old = self.set("11-03 09:00")
        old["payload"]["ready"] = "旧提醒"
        self.message.reply.side_effect = ConnectionError("offline")
        with patch.object(bot.time, "time", return_value=at(11, 3, 9, 10)):
            await bot.deliver_carried(self.message, "group")
            new = self.set("11-03 10:00")
            await bot.SENDER.drain()
        self.assertIs(self.scheduler.tasks[new["id"]], new)
        self.assertEqual(bot.SENDER.last_status, "superseded")
        self.assertNotIn(old["id"], bot._carried_reminders)

    async def test_unsent_for_an_hour_is_dropped(self):
        self.register()
        self.set("11-03 09:00")
        self.post_ok = False
        await self.run_at(at(11, 3, 9, 0))
        due = self.scheduler.pending()[0]["due"]
        self.assertEqual(due, at(11, 3, 10, 0))
        await self.run_at(due)                                        # 正好一小时：作罢，不空转
        self.assertEqual(self.scheduler.pending(), [])
        self.assertEqual(len(self.posts), 1)

    async def test_missed_while_down_is_not_said_hours_late(self):
        """「九点提醒大家我回来了」下午才发出来，比不发还糟。"""
        self.register()
        self.set("每天 08:00")
        await self.run_at(at(11, 3, 15, 0))
        self.assertEqual(self.posts, [])
        self.assertEqual(self.scheduler.pending()[0]["due"], at(11, 4, 8, 0))

    async def test_ta_is_spelled_out_before_the_line_is_written(self):
        """规则让小王把提醒的人写成「TA」；原样交给到点那次生成，它会把「TA」当名字念出来。"""
        self.register()
        reminders.apply(self.scheduler, "group", "member", ("set", "11-03 09:00", "提醒TA收衣服"), NOW)
        await self.run_at(at(11, 3, 9, 0))
        prompt = bot.call_model.await_args.args[0][-1]["content"]
        self.assertIn("要提醒的事：提醒老王收衣服", prompt)
        self.assertNotIn("TA", prompt)
        self.assertEqual(reminders.spell_out("看TAXI", "老王"), "看TAXI")

    async def test_local_line_when_the_model_is_out(self):
        self.register()
        self.set("11-03 09:00")
        with patch.object(bot, "call_model", AsyncMock(return_value=None)):
            await self.run_at(at(11, 3, 9, 0))
        self.assertEqual(self.posts, ["老王，到点了：叫我开会"])

    async def test_held_while_the_group_said_stop(self):
        self.register()
        self.set("11-03 09:00")
        bot.runtime.FEEDBACK.quiet.return_value = True
        await self.run_at(at(11, 3, 9, 0))
        self.assertEqual(self.posts, [])
        self.assertEqual(self.scheduler.pending()[0]["due"], at(11, 3, 9, 3))


class ProactiveMentionTest(_Reply):
    async def test_wire_mentions_but_history_and_journal_keep_plain_text(self):
        post = AsyncMock(return_value={"id": "sent"})
        client = SimpleNamespace(api=SimpleNamespace(post_group_message=post))
        with patch.dict(bot._bot_ref, {"client": client}), patch.object(bot, "_remember_sent") as remember:
            ok = await bot.post_to_group("group", "到点了", mention_member="member", model_generated=True)
        self.assertTrue(ok)
        self.assertEqual(post.await_args.kwargs["markdown"]["content"],
                         '<qqbot-at-user id="member" /> 到点了')
        self.assertEqual(remember.call_args.args[-1], "到点了")
        self.assertEqual(bot.runtime.FEEDBACK.remember.call_args.args[2], "到点了")
        self.assertEqual(bot.runtime.INTERACTIONS.append.call_args.kwargs["text"], "到点了")

    async def test_explicit_rejection_downgrades_once(self):
        post = AsyncMock(side_effect=[ForbiddenError("markdown refused"), {"id": "sent"}])
        client = SimpleNamespace(api=SimpleNamespace(post_group_message=post))
        with patch.dict(bot._bot_ref, {"client": client}), patch.object(bot, "_remember_sent"):
            self.assertTrue(await bot.post_to_group("group", "到点了", mention_member="member"))
        self.assertEqual([c.kwargs["msg_type"] for c in post.await_args_list], [2, 0])
        self.assertEqual(post.await_args.kwargs["content"], "到点了")

    async def test_uncertain_result_does_not_cross_formats(self):
        for failure in (ConnectionError("offline"), None):
            with self.subTest(failure=type(failure).__name__):
                post = AsyncMock(side_effect=failure) if failure else AsyncMock(return_value=None)
                client = SimpleNamespace(api=SimpleNamespace(post_group_message=post))
                with patch.dict(bot._bot_ref, {"client": client}):
                    self.assertFalse(await bot.post_to_group("group", "到点了", mention_member="member"))
                self.assertEqual(post.await_count, 1)


if __name__ == "__main__":
    unittest.main()
