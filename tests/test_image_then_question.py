"""先发图、紧接着问它：回话时背景里必须是图的描述，不是裸 [图片]。

2026-11-01 12:31–12:33 线上：家豪发图后问「小王读这两张图」「小王这张」，
小王连着三次答「没看到图片」。实测（bot.log + 归档 caption 记录）两种情形：

  - 后台读图还在路上（读一张实测 2.5–3.4 秒），问题先到；
  - 后台读图被 VISION_COOLDOWN_SECONDS 挡掉（上一张 37 秒前刚读过），
    之后被问到也没人再读——这是当天三次「没看到」的真正原因。

两种都走真实的 handle_group_msg，只把读图和回话模型换成假的。
"""
import asyncio
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
import runtime
import storage
from regular import decisions

G = "GID_IMAGE_Q"
A = "USER_IMAGE_A"
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
        self.vision_calls = []
        state = storage.StateStore(os.path.join(self._tmp.name, "state.json"))

        async def fake_model(messages, max_tokens, **kw):
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
                (bot.random, "random", lambda: 0.999),   # 发图那条不插嘴
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

    async def asyncTearDown(self):
        await asyncio.gather(*bot._media_tasks, return_exceptions=True)
        await self.service.drain()

    async def _drive(self, text, *, image=None, is_at=False):
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_IMAGE_Q", name="regular"))
        msg = SimpleNamespace(
            id="msg-" + os.urandom(5).hex(), content=text, group_openid=G,
            author=SimpleNamespace(member_openid=A), mentions=[], timestamp=None,
            attachments=[SimpleNamespace(content_type="image/jpeg", url=image)] if image else [],
            reply=AsyncMock(return_value={"id": "rcpt-" + os.urandom(3).hex()}))
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)

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


if __name__ == "__main__":
    unittest.main()
