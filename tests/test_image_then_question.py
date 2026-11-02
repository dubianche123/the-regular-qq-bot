"""先发图、紧接着问它：回话时背景里必须是图的描述，不是裸 [图片]。

2026-11-01 12:31–12:33 线上：家豪发图后问「小王读这两张图」「小王这张」，
小王连着三次答「没看到图片」。实测（bot.log + 归档 caption 记录）两种情形：

  - 后台读图还在路上（读一张实测 2.5–3.4 秒），问题先到；
  - 后台读图被 VISION_COOLDOWN_SECONDS 挡掉（上一张 37 秒前刚读过），
    之后被问到也没人再读——这是当天三次「没看到」的真正原因。

两种都走真实的 handle_group_msg，只把读图和回话模型换成假的。
"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import archive as archive_mod
import bot
import config
import delivery
import interactions
import media
import prompts
import runtime
import storage
from regular import decisions

G = "GID_IMAGE_Q"
A = "USER_IMAGE_A"
B = "USER_IMAGE_B"
URL = "https://image.invalid/cat.jpg"
URL2 = "https://image.invalid/calendar.jpg"
CAPTION = "一只橘猫趴在键盘上"
CAPTIONS = {URL: CAPTION, URL2: "一页写着周一的日历"}


class ImageThenQuestionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = interactions.InteractionLog(
            os.path.join(self._tmp.name, "ix"), enabled=True, keep_days=0)
        self.addCleanup(self.log.close)
        self.prompts = []
        self.unprompted = []
        self.vision_calls = []
        state = storage.StateStore(os.path.join(self._tmp.name, "state.json"))

        async def fake_model(messages, max_tokens, **kw):
            if prompts.BANTER_MARK in messages[-1]["content"]:
                self.unprompted.append(messages[-1]["content"])
                return "〔不接〕"                 # 发图那条没叫它：它不接
            self.prompts.append("\n".join(str(m.get("content")) for m in messages))
            return "（模型应答）"

        async def describe(url, context_text=""):
            self.vision_calls.append(url)
            await asyncio.sleep(0.5)          # 真实读图要几秒；问题在这之前就到了
            return CAPTIONS[url]

        self.service = media.CaptionService(
            state, self.archive, bot.group_buffers,
            storage.DailyBudget(100, state.data["usage"]),
            describe=describe, available=lambda: True)
        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", decisions.FeedbackTracker(120.0, 180.0)),
                (decisions.providers, "call_decision", AsyncMock(return_value=None)),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (runtime, "GROUP_BUCKET",
                 storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                     config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", fake_model),
                (media, "_service", self.service),
                (config, "VISION_ENABLED", True),
                (config, "VISION_COOLDOWN_SECONDS", 60.0),
                (config, "VISION_DAILY_CAP", 30)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.addCleanup(lambda: bot.group_buffers.pop(G, None))
        self.addCleanup(lambda: bot.group_last_random_reply.pop(G, None))
        self.addCleanup(bot.processed_msg_ids.clear)
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{A}", None))
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{B}", None))

    async def asyncTearDown(self):
        await asyncio.gather(*bot._media_tasks, return_exceptions=True)
        await self.service.drain()

    async def _drive(self, text, *, image=None, is_at=False, sender=A):
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_IMAGE_Q", name="regular"))
        msg = SimpleNamespace(
            id="msg-" + os.urandom(5).hex(), content=text, group_openid=G,
            author=SimpleNamespace(member_openid=sender), mentions=[], timestamp=None,
            attachments=[SimpleNamespace(content_type="image/jpeg", url=image)] if image else [],
            reply=AsyncMock(return_value={"id": "rcpt-" + os.urandom(3).hex()}))
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return [c.kwargs.get("content") for c in msg.reply.await_args_list]

    def _background(self):
        """回话提示词里的「群聊最近背景」那一段。"""
        self.assertEqual(len(self.prompts), 1)
        return self.prompts[0].split("【群聊最近背景】", 1)[1]

    async def test_question_within_a_second_sees_the_description(self):
        await self._drive("", image=URL)          # 后台读图开始，0.5 秒后才有描述
        await self._drive("读一下这张", is_at=True)  # 立刻就问
        background = self._background()
        self.assertIn(f"[图片：{CAPTION}]", background)
        self.assertNotIn("[图片]", background)
        self.assertEqual(self.vision_calls, [URL])  # 等的是同一次读图，没有重读

    async def test_image_the_cooldown_skipped_is_read_when_asked(self):
        self.service.usage.update(day=time.strftime("%Y-%m-%d"), count=1, last=time.time())
        await self._drive("", image=URL)
        await asyncio.gather(*bot._media_tasks)
        self.assertEqual(self.vision_calls, [])     # 后台：刚读过一张，冷却挡掉
        await self._drive("读一下这张", is_at=True)
        self.assertIn(f"[图片：{CAPTION}]", self._background())
        self.assertEqual(self.vision_calls, [URL])  # 被问到了，前台补读一次

    async def test_two_images_in_a_row_both_reach_the_question(self):
        await self._drive("", image=URL)
        await self._drive("", image=URL2)          # 同一人连发：两条此刻都是「[图片]」
        await self._drive("读这两张图", is_at=True)
        background = self._background()
        for caption in CAPTIONS.values():
            self.assertIn(f"[图片：{caption}]", background)


    def _cooled_down(self):
        """上一张图刚读过：后台读图被冷却挡掉。"""
        self.service.usage.update(day=time.strftime("%Y-%m-%d"), count=1, last=time.time())

    async def test_unread_image_nobody_asked_about_is_not_put_to_it(self):
        """2026-11-02 21:04：没叫它的纯图，冷却挡掉没读，它看着裸 [图片] 答「我这边看不到」，
        群友连发几张就连答几遍。没读成的纯图没东西可接，不问它。"""
        self._cooled_down()
        await self._drive("", image=URL)
        self.assertEqual(self.unprompted, [])
        self.log.close()
        rows = [json.loads(line) for name in sorted(os.listdir(self.log.log_dir))
                for line in open(os.path.join(self.log.log_dir, name), encoding="utf-8")]
        gate = [r for r in rows if r["kind"] == "decision"][-1]
        self.assertEqual((gate["route"], gate["selected"], gate["reason"]),
                         ("banter", False, "image_unread"))

    async def test_read_image_nobody_asked_about_is_still_its_call(self):
        await self._drive("", image=URL)
        self.assertEqual(len(self.unprompted), 1)
        self.assertIn(f"[图片：{CAPTION}]", self.unprompted[0])

    async def test_unread_image_beside_words_is_left_out_of_the_reply(self):
        self._cooled_down()
        await self._drive("哈哈哈这个", image=URL)
        self.assertEqual(len(self.unprompted), 1)
        self.assertIn("这张图你没看；没人问你，别提它", self.unprompted[0])

    async def test_own_image_stays_in_background_past_the_turn_limit(self):
        """2026-11-02 21:05：生人发图，中间别人插了两句，再问「这张怎么样」——亲密度只给
        它看最后几条，图被挤出背景，它答「没看到画面」。"""
        await self._drive("", image=URL)
        turns = bot.activation.context_turns(
            bot.relations.level_key(0))
        for i in range(turns):
            await self._drive(f"别人插的第 {i} 句", sender=B)
        await self._drive("这张怎么样", is_at=True)
        self.assertIn(f"[图片：{CAPTION}]", self._background())

    async def test_quick_read_is_one_reply(self):
        self._cooled_down()
        await self._drive("", image=URL)
        sent = await self._drive("读一下这张", is_at=True)       # 读图 0.5 秒，线上等得到
        self.assertEqual(sent, ["（模型应答）"])
        self.assertIn(f"[图片：{CAPTION}]", self._background())

    async def test_own_slow_image_gets_a_holding_line_then_the_answer(self):
        """群主 2026-11-02：自己发了图紧接着问，等不到就先说一句在读图，读出来再答 ——
        跟联网查询先回「我去查查」一个节奏。以前是闷着等，等不到答「看不到」，可图还在路上。"""
        self._cooled_down()
        await self._drive("", image=URL)
        with patch.object(config, "ASYNC_REPLY_SECONDS", 0.05):
            sent = await self._drive("读一下这张", is_at=True)
        self.assertEqual(sent, [bot.IMAGE_HOLD_LINE, "（模型应答）"])
        self.assertIn(f"[图片：{CAPTION}]", self._background())

    async def test_image_in_the_question_itself_gets_the_holding_line(self):
        with patch.object(config, "ASYNC_REPLY_SECONDS", 0.05):
            sent = await self._drive("这是啥", image=URL, is_at=True)
        self.assertEqual(sent, [bot.IMAGE_HOLD_LINE, "（模型应答）"])
        self.assertIn(f"[图片：{CAPTION}]", self.prompts[0])

    async def test_someone_elses_slow_image_is_waited_for_quietly(self):
        """别人的图不为它先开口：这句多半不是在问那张。闷等最多 VISION_SETTLE_SECONDS。"""
        self._cooled_down()
        await self._drive("", image=URL, sender=B)
        with patch.object(config, "ASYNC_REPLY_SECONDS", 0.05), \
                patch.object(config, "VISION_SETTLE_SECONDS", 2.0):
            sent = await self._drive("读一下这张", is_at=True)
        self.assertEqual(sent, ["（模型应答）"])
        self.assertIn(f"[图片：{CAPTION}]", self._background())

if __name__ == "__main__":
    unittest.main()
