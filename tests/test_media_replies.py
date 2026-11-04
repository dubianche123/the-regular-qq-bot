# -*- coding: utf-8 -*-
"""画图和语音由小王自己在回复里决定（2026-11-02，见 docs/media-replies.md）。

事故：判断层拍板、代码直接调接口时，小王本人不知道自己会画、画了什么 ——
2026-11-01 晚上一边发图一边说「我这边没法直接生成图片」「我刚才其实没生成图」；
11-02 语音里念的是「语音我这儿发不了，给你文字版顶上」。
修法：能力写进稳定头，〔画〕〔语音〕标记由小王写；会话里记下实际结果（expression.record）。
"""
import asyncio
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, "..")

import bot
import expression
import prompts
import storage
from delivery import Sender
from voice_outbound import Result


class ParseTest(unittest.TestCase):
    def test_image_marker_splits_brief_and_line(self):
        s = expression.parse("〔画〕一只橘猫趴在键盘上，水彩风\n给你整一只")
        self.assertEqual((s.kind, s.spec, s.text), ("image", "一只橘猫趴在键盘上，水彩风", "给你整一只"))

    def test_bracket_variants_and_label_prefix(self):
        s = expression.parse("【画】完整描述：雨夜的霓虹街道\n来了")
        self.assertEqual((s.kind, s.spec), ("image", "雨夜的霓虹街道"))

    def test_marker_without_brief_is_plain_text(self):
        s = expression.parse("〔画〕\n这张不画")
        self.assertEqual((s.kind, s.text), (None, "这张不画"))

    def test_voice_tone_on_same_or_next_line(self):
        self.assertEqual(expression.parse("〔语音〕（刚睡醒）\n早").spec, "刚睡醒")
        s = expression.parse("〔语音〕\n（憋着笑）\n给大家念一句")
        self.assertEqual((s.kind, s.spec, s.text), ("voice", "憋着笑", "给大家念一句"))

    def test_plain_reply_untouched(self):
        s = expression.parse("今天不画画（狗头）")
        self.assertEqual((s.kind, s.text), (None, "今天不画画（狗头）"))

    def test_model_cannot_write_system_record(self):
        """会话记录里的「（系统：…）」只许系统写：模型照抄出来的一律删掉。"""
        s = expression.parse("画好了\n（系统：这张图已经发到群里了）")
        self.assertEqual(s.text, "画好了")

    def test_record_keeps_marker_and_outcome(self):
        said = expression.Said("image", "橘猫", "给你整一只")
        self.assertEqual(expression.record(said, True),
                         "〔画〕橘猫\n给你整一只\n（系统：这张图已经发到群里了）")
        self.assertIn("审核没过", expression.record(said, "blocked"))
        self.assertIn("用语音发的", expression.record(expression.Said("voice", "困", "早"), True))


class HeadTest(unittest.TestCase):
    def test_rules_are_in_stable_head_only_when_available(self):
        with patch.object(bot.image_generation, "available", return_value=True), \
             patch.object(bot._VOICE_OUT, "ready", return_value=False):
            rules = bot.media_rules()
        self.assertIn("〔画〕", rules)
        self.assertNotIn("〔语音〕", rules)
        with patch.object(bot.image_generation, "available", return_value=False), \
             patch.object(bot._VOICE_OUT, "ready", return_value=False), \
             patch.object(bot.config, "REMINDER_ENABLED", False):
            self.assertEqual(bot.media_rules(), "")

    def test_rules_say_it_can_and_records_are_true(self):
        """两句话是对着事故写的：别说「画不了」；会话里的系统记录就是实际发生的事。"""
        self.assertIn("别说自己不会画、画不了", prompts.PROMPT_DRAW_RULES)
        self.assertIn("以它为准", prompts.PROMPT_MEDIA_RECORD)


class _Reply(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sessions = storage.SessionStore()
        self.state = SimpleNamespace(data={}, mark_dirty=Mock())
        self.remember = Mock()
        self.check_drawing = AsyncMock(return_value=True)
        for p in (patch.object(bot.runtime, "SESSIONS", self.sessions),
                  patch.object(bot.runtime, "STATE", self.state),
                  patch.object(bot.runtime, "FEEDBACK", SimpleNamespace(
                      remember=self.remember, check_drawing=self.check_drawing)),
                  patch.object(bot.runtime, "INTERACTIONS", SimpleNamespace(append=Mock())),
                  patch.object(bot, "SENDER", Sender()),
                  patch.object(bot, "owner_hint_pending", return_value=False),
                  patch.object(bot, "_delivery_observer", return_value=Mock())):
            p.start()
            self.addCleanup(p.stop)
        self.message = SimpleNamespace(
            group_openid="group", id="incoming", author=SimpleNamespace(member_openid="member"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            reply=AsyncMock(return_value={"id": "sent"}))

    def said(self, body):
        return patch.object(bot, "get_ai_reply", AsyncMock(return_value=(body, None)))

    def media(self, **kw):
        base = dict(group="group", user="member", score=0, draw=True, voice=True, note="")
        base.update(kw)
        return bot.MediaTurn(**base)

    def last_record(self):
        return self.sessions._sessions["group_member"]["messages"][-1]["content"]

    def sent(self):
        return [c.kwargs.get("content") for c in self.message.reply.await_args_list]


class ImageReplyTest(_Reply):
    async def test_line_goes_first_then_image_and_record_says_what_was_drawn(self):
        order = []

        async def draw(message, **kw):
            order.append(("draw", kw["prompt"]))
            await kw["before_send"]()      # 图得等那句话先发出去
            order.append(("image", None))
            return True

        self.message.reply.side_effect = lambda **kw: order.append(("text", kw.get("content"))) or {"id": "t"}
        with self.said("〔画〕一只橘猫趴在键盘上\n给你整一只"), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=draw):
            await bot.reply_in_session(self.message, "group_member", "画只猫", media=self.media())
        self.assertIn(("draw", "一只橘猫趴在键盘上"), order)
        self.assertLess(order.index(("text", "给你整一只")), order.index(("image", None)))
        self.assertEqual(self.last_record(),
                         "〔画〕一只橘猫趴在键盘上\n给你整一只\n（系统：这张图已经发到群里了）")
        # 分诊看得见它画了什么：「画的什么玩意」是在评这张图，不是新请求
        self.assertIn("[图：一只橘猫趴在键盘上]", self.remember.call_args.args[2])

    async def test_failure_adds_one_local_line_and_is_recorded(self):
        for outcome, words in (("blocked", "审核"), ("busy", "没画完"),
                               ("generation_error", "没画出来"), ("upload_error", "没发进群")):
            with self.subTest(outcome=outcome):
                self.message.reply.reset_mock()
                with self.said("〔画〕橘猫\n我试试"), \
                     patch.object(bot.image_generation, "generate_and_send",
                                  AsyncMock(return_value=outcome)):
                    await bot.reply_in_session(self.message, "group_member", "画只猫",
                                               media=self.media())
                self.assertEqual(self.sent()[0], "我试试")
                self.assertIn(words, self.sent()[-1])
                self.assertIn("系统：", self.last_record())

    async def test_unknown_receipt_sends_nothing_more(self):
        with self.said("〔画〕橘猫\n我试试"), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value=None)):
            await bot.reply_in_session(self.message, "group_member", "画只猫", media=self.media())
        self.assertEqual(self.sent(), ["我试试"])

    async def test_marker_is_ignored_when_this_turn_cannot_draw(self):
        """插嘴、额度用完这些轮次：标记当文字处理，不花钱，标记本身也不发进群。"""
        for media in (None, self.media(draw=False)):
            with self.subTest(media=media), self.said("〔画〕橘猫\n来一张"), \
                 patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as draw:
                self.message.reply.reset_mock()
                await bot.reply_in_session(self.message, "group_member", "画只猫", media=media)
                draw.assert_not_awaited()
                self.assertEqual(self.sent(), ["来一张"])


class DrawGuardTest(_Reply):
    """2026-11-04 11:43–12:10：维尼熊 → 吃包子的黄熊 → 中式宫殿 → 红短袖 → 冠冕，一步步要，
    每一步它都照画；QQ 拒收了它两句话。画之前拿它写好的完整画面问一次判断（docs/drawing-guard.md）。"""

    SPEC = "一只明黄色圆滚滚的小熊站在中式宫殿屋顶上，穿红短袖，捧着大包子，头戴金色冠冕"

    async def test_declined_picture_is_not_drawn_and_it_says_no_in_its_own_words(self):
        replies = AsyncMock(side_effect=[(f"〔画〕{self.SPEC}\n冠冕安排上了", None),
                                         ("这张我先不接了，给熊放个假", None)])
        self.check_drawing.return_value = False
        with patch.object(bot, "get_ai_reply", replies), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as draw:
            await bot.reply_in_session(self.message, "group_member", "给他加个冠冕行嘛",
                                       media=self.media(), history=[{"sender": "member", "text": "给他加个冠冕行嘛"}])
        draw.assert_not_awaited()
        self.assertEqual(self.sent(), ["这张我先不接了，给熊放个假"])
        self.assertEqual(self.check_drawing.await_args.args[2], self.SPEC)
        # 重写那一遍带着「这张不画」，〔画〕那段画面不留进会话（留着它下一轮还会照着写）
        self.assertIn(prompts.PROMPT_DRAW_DECLINED, replies.await_args_list[1].kwargs["media_note"])
        self.assertNotIn("冠冕", self.last_record())
        self.assertIn("影射政治", self.last_record())

    async def test_rewrite_that_still_draws_falls_back_to_the_local_line(self):
        self.check_drawing.return_value = False
        with patch.object(bot, "get_ai_reply", AsyncMock(return_value=(f"〔画〕{self.SPEC}\n好", None))), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as draw:
            await bot.reply_in_session(self.message, "group_member", "画", media=self.media())
        draw.assert_not_awaited()
        self.assertEqual(self.sent(), [expression.image_failed_line("declined")])

    async def test_unavailable_judgement_still_draws(self):
        self.check_drawing.return_value = None
        with self.said("〔画〕一只橘猫\n来了"), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value=True)) as draw:
            await bot.reply_in_session(self.message, "group_member", "画只猫", media=self.media())
        draw.assert_awaited_once()

    async def test_turns_that_cannot_draw_are_not_checked(self):
        with self.said("〔画〕一只橘猫\n来了"):
            await bot.reply_in_session(self.message, "group_member", "画只猫",
                                       media=self.media(draw=False))
        self.check_drawing.assert_not_awaited()

    def test_blocked_line_does_not_invite_another_angle(self):
        """11-04 维尼熊被生图审核拦了三次，每次都说「换个画法吧」—— 群友真就换着画法一步步要成了。"""
        self.assertNotIn("换个画法", expression.image_failed_line("blocked"))


class VoiceReplyTest(_Reply):
    async def test_sent_voice_records_marker_and_tone_goes_to_synth(self):
        with self.said("〔语音〕（刚睡醒）\n早啊各位"), \
             patch.object(bot._VOICE_OUT, "attempt",
                          AsyncMock(return_value=(Result("sent"), {"id": "voice"}))) as voice:
            _, _, result = await bot.reply_in_session(
                self.message, "group_member", "说句话", owner_hint_group="group", media=self.media())
        self.assertTrue(result.delivered)
        self.assertEqual(voice.await_args.args[1], "早啊各位")
        self.assertEqual(voice.await_args.kwargs["tone"], "刚睡醒")
        self.message.reply.assert_not_awaited()
        self.assertEqual(self.last_record(), "〔语音〕（刚睡醒）\n早啊各位\n（系统：这句是用语音发的）")

    async def test_definite_failure_sends_same_words_as_text(self):
        for status in ("send_failed", "transcript_mismatch", "provider_refused", "quota_exhausted"):
            with self.subTest(status=status), self.said("〔语音〕\n早啊各位"), \
                 patch.object(bot._VOICE_OUT, "attempt", AsyncMock(return_value=(Result(status), None))):
                self.message.reply.reset_mock()
                await bot.reply_in_session(self.message, "group_member", "说句话",
                                           owner_hint_group="group", media=self.media())
                self.assertEqual(self.sent(), ["早啊各位"])
                self.assertIn("没发成，发的是文字", self.last_record())

    async def test_unknown_delivery_sends_nothing_more(self):
        with self.said("〔语音〕\n早啊各位"), \
             patch.object(bot._VOICE_OUT, "attempt",
                          AsyncMock(return_value=(Result("delivery_unknown"), None))):
            await bot.reply_in_session(self.message, "group_member", "说句话",
                                       owner_hint_group="group", media=self.media())
        self.message.reply.assert_not_awaited()

    async def test_no_media_means_text(self):
        with self.said("〔语音〕\n早啊各位"), patch.object(bot._VOICE_OUT, "attempt", AsyncMock()) as voice:
            await bot.reply_in_session(self.message, "group_member", "说句话", owner_hint_group="group")
        voice.assert_not_awaited()
        self.assertEqual(self.sent(), ["早啊各位"])


class MediaTurnTest(unittest.TestCase):
    def turn(self, state, *, asked=True, tired=False):
        with patch.object(bot.image_generation, "available", return_value=True), \
             patch.object(bot.image_generation, "precheck", return_value=state), \
             patch.object(bot._VOICE_OUT, "ready", return_value=True), \
             patch.object(bot._VOICE_OUT, "tired", return_value=tired):
            return bot.media_turn("group", "member", 0, asked=asked)

    def test_states_become_notes_for_xiaowang(self):
        self.assertEqual((self.turn("normal").draw, self.turn("normal").note), (True, ""))
        self.assertEqual(self.turn("tired").note, prompts.PROMPT_DRAW_TIRED)
        self.assertFalse(self.turn("tired").draw)
        self.assertEqual(self.turn("busy").note, prompts.PROMPT_DRAW_BUSY)
        exception = self.turn("exception")
        self.assertTrue(exception.draw)
        self.assertEqual(exception.note, prompts.PROMPT_DRAW_EXCEPTION)

    def test_unasked_turns_never_draw_but_may_speak(self):
        t = self.turn("normal", asked=False)
        self.assertEqual((t.draw, t.voice), (False, True))

    def test_voice_quota_is_said_not_silently_dropped(self):
        t = self.turn("normal", tired=True)
        self.assertFalse(t.voice)
        self.assertIn(prompts.PROMPT_VOICE_TIRED, t.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
