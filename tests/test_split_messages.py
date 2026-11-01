"""群友拆成数条发送时，接话模型既能看到内容也能区分说话人。"""

import time
from unittest.mock import patch

import bot
import config
import runtime
from test_natural_feedback import _Base


class SplitMessageRoutingTest(_Base):
    def setUp(self):
        super().setUp()
        self.names = patch.object(config, "BOT_NAMES", ["小王"])
        self.names.start()
        self.addCleanup(self.names.stop)
        for user in (self.A, "USER_FB_B"):
            self.addCleanup(runtime.SESSIONS.drop_session, f"{self.G}_{user}")

    async def _quietly_receive(self, text, sender=None):
        # 无人点名的第一句仅进群聊背景，避免随机插嘴影响场景。
        with patch.object(bot.random, "random", return_value=0.999):
            return await self._drive(text, sender=sender)

    async def test_same_speaker_request_before_name_is_attributed_to_them(self):
        first = await self._quietly_receive("说说海虎的故事？")
        self.assertFalse(first.reply.await_count)
        second = await self._drive("小王")
        self.assertTrue(second.reply.await_count)
        turn = self.model_calls[-1][-1]["content"]
        question = next(line for line in turn.splitlines() if "说说海虎的故事" in line)
        self.assertIn("当前说话人", question)
        self.assertIn("小王", turn)

    async def test_another_speakers_request_is_not_attributed_to_current_speaker(self):
        await self._quietly_receive("说说海虎的故事？", sender="USER_FB_B")
        await self._drive("小王", sender=self.A)
        turn = self.model_calls[-1][-1]["content"]
        question = next(line for line in turn.splitlines() if "说说海虎的故事" in line)
        self.assertNotIn("当前说话人", question)
        self.assertNotIn("USER_FB_B", turn)

    async def test_interleaved_speakers_preserve_their_ownership(self):
        await self._quietly_receive("我准备去看电影", sender="USER_FB_B")
        await self._quietly_receive("说说海虎的故事？", sender=self.A)
        await self._drive("小王", sender=self.A)
        turn = self.model_calls[-1][-1]["content"]
        other = next(line for line in turn.splitlines() if "我准备去看电影" in line)
        current = next(line for line in turn.splitlines() if "说说海虎的故事" in line)
        self.assertNotIn("当前说话人", other)
        self.assertIn("当前说话人", current)

    async def test_expired_message_is_not_presented_as_recent_context(self):
        await self._quietly_receive("说说海虎的故事？")
        bot.group_buffers[self.G][-1]["time"] = time.time() - config.GROUP_BUFFER_TTL_SECONDS - 10
        await self._drive("小王")
        turn = self.model_calls[-1][-1]["content"]
        self.assertNotIn("说说海虎的故事", turn)

    async def test_normal_one_message_request_remains_intact(self):
        await self._drive("小王，说说海虎的故事？")
        turn = self.model_calls[-1][-1]["content"]
        self.assertIn("说说海虎的故事", turn)

    async def test_short_at_can_reuse_previous_message(self):
        await self._quietly_receive("说说海虎的故事？")
        second = await self._drive("@小王", is_at=True)
        self.assertTrue(second.reply.await_count)
        self.assertTrue(self.model_calls, "不应在有上一句话时只回‘找我啥事’")
        turn = self.model_calls[-1][-1]["content"]
        self.assertIn("说说海虎的故事", turn)

    async def test_lookup_context_preserves_speaker_without_internal_ids(self):
        rows = [{"sender": "other-secret", "text": "他问的是哪部作品", "message_id": "a"},
                {"sender": "self-secret", "text": "查查这部作品的作者", "message_id": "b"}]
        query = bot._lookup_query("小王", rows, sender_openid="self-secret")
        self.assertIn("其他群友：他问的是哪部作品", query)
        self.assertIn("当前说话人：查查这部作品的作者", query)
        self.assertNotIn("self-secret", query)
        self.assertNotIn("other-secret", query)

    async def test_already_answered_request_stays_in_conversation_history(self):
        await self._drive("小王 说说海虎的故事？")
        self.model_calls.clear()
        await self._drive("小王")
        messages = self.model_calls[-1]
        self.assertTrue(any(message["role"] == "assistant" and "模型应答" in message["content"]
                            for message in messages))
        self.assertIn("已经答过", messages[0]["content"])
