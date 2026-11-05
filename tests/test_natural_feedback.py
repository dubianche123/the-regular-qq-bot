"""自然反馈回路：送达缓存资格、收敛暂停、修正话术、一次调用；没叫它的消息交给它自己。"""
import asyncio
import collections
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
import prompts
import runtime
import storage
from regular import decisions
from regular.resource_quota import ResourceQuotas


def _group_message(text="你好", sender="USER_A", group="GID_FB", msg_id=None):
    return SimpleNamespace(
        id=msg_id or "msg-" + os.urandom(4).hex(), content=text,
        group_openid=group,
        author=SimpleNamespace(member_openid=sender),
        attachments=[], mentions=[], timestamp=None,
        reply=AsyncMock(side_effect=[{"id": "rcpt-1"}]))


_VOCAB = {"feedback_target": ["bot_response", "another_person",
                              "not_feedback", "unclear"],
          "next_move": ["back_off", "repair_briefly", "keep_playful",
                        "continue_normally", "no_adjustment"]}


def _body(**kv):
    """真实原生返回形态：confidence + probabilities 数组。"""
    base = {"feedback_target": "bot_response", "next_move": "keep_playful"}
    base.update(kv)
    answers = []
    for name, choice in base.items():
        others = [v for v in _VOCAB[name] if v != choice]
        probs = [{"value": choice, "probability": 0.9}]
        probs += [{"value": v, "probability": 0.1 / len(others)}
                  for v in others]
        answers.append({"name": name, "type": "choice", "choice": choice,
                        "confidence": 0.9, "probabilities": probs})
    return {"answers": answers}


class _Base(unittest.IsolatedAsyncioTestCase):
    G, A = "GID_FB", "USER_FB_A"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = interactions.InteractionLog(
            os.path.join(self._tmp.name, "ix"), enabled=False, keep_days=0)
        self.feedback = decisions.FeedbackTracker(120.0, 180.0)
        self.calls = AsyncMock(return_value=None)
        self.model_calls = []
        # 没叫它的那一轮（2026-11-02 起交给它自己定）另记一本；默认它不接，
        # 这样「只进背景」的那几句不会被当成它的回话，要它接的用例自己改这个值。
        self.unprompted_calls = []
        self.unprompted_reply = "〔不接〕"

        async def fake_model(messages, max_tokens, **kw):
            if prompts.BANTER_MARK in messages[-1]["content"]:
                self.unprompted_calls.append(messages)
                return self.unprompted_reply
            self.model_calls.append(messages)
            return "（模型应答）"

        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", self.feedback),
                (runtime, "RESOURCE_QUOTAS", ResourceQuotas({}, lambda: None, lambda: None)),
                (decisions.providers, "call_decision", self.calls),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (runtime, "GROUP_BUCKET",
                 storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                     config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", fake_model)):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)
        self.addCleanup(lambda: bot.group_last_random_reply.pop(self.G, None))
        self.addCleanup(lambda: bot.group_buffers.pop(self.G, None))
        self.addCleanup(lambda: bot.processed_msg_ids.clear())

    async def _drive(self, text, sender=None, is_at=False, msg_id=None):
        fake_self = SimpleNamespace(
            robot=SimpleNamespace(id="BOT_FB", name="regular"))
        msg = _group_message(text=text, sender=sender or self.A,
                             group=self.G, msg_id=msg_id)
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return msg


class ObserverEligibilityTest(_Base):
    """只有「确认送达的模型回复」才进反馈缓存；日志关着也照样记。"""

    async def test_delivered_model_reply_is_remembered(self):
        await self._drive("今天怎么样", is_at=True)
        reply = self.feedback._groups[self.G]["reply"]
        self.assertIsNotNone(reply)
        self.assertEqual(reply["route"], "reply")
        self.assertEqual(reply["text"], "（模型应答）")
        # 日志关了也不影响缓存（它走内存，不走 interactions/）
        self.assertTrue(self.log.enabled is False)

    async def test_local_ack_and_failed_sends_never_count(self):
        # 本地应答：route=local / 无 generation → 不登记
        msg = _group_message(group=self.G)
        await bot.safe_reply(msg, "本地回一句",
                             interaction={"route": "local"})
        self.assertIsNone(self.feedback._groups.get(self.G, {}).get("reply"))
        # 平台兜底文案也不算模型回复：直接喂一个 fallback=True 的送达结果
        observer = bot._delivery_observer(
            "oid-1", self.G,
            {"route": "reply", "generation": "model",
             "source_message_id": "m-1", "user": self.A}, self.A)
        observer(delivery.DeliveryResult(
            "delivered", "兜底文案", {"id": "r"}, fallback=True))
        self.assertIsNone(self.feedback._groups.get(self.G, {}).get("reply"))


class AssessFlowTest(_Base):
    async def _seed_reply(self):
        await self._drive("来句笑话", is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()

    async def test_named_reply_gets_cross_person_context_when_feedback_is_uncertain(self):
        result = {"addressed": "answer", "target": None,
                  "bot_reply_text": "具体是谁我看不出来",
                  "bot_reply_to": "another_member"}
        with patch.object(config, "BOT_NAMES", ["小王"]), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)):
            await self._drive("小王 这个可能是老张", is_at=False)
        messages = self.model_calls[-1]
        self.assertIn("具体是谁我看不出来", messages[-1]["content"])
        self.assertIn("不要假定两者一定有关", messages[-1]["content"])
        self.assertNotIn("具体是谁我看不出来", messages[0]["content"])

    async def test_back_off_pauses_unsolicited_but_direct_still_answers(self):
        await self._seed_reply()
        self.calls.return_value = _body(next_move="back_off")
        # 点名嫌烦 → 仍然直接回复（被叫就要应），但挂上收敛话术
        await self._drive("别烦我了", is_at=True)
        self.assertTrue(self.feedback.quiet(self.G))
        self.assertEqual(self.calls.await_count, 1)
        last = self.model_calls[-1][-1]["content"]
        self.assertIn("嫌你打扰", last)   # PROMPT_FEEDBACK_BACK_OFF 已在末尾层
        # 暂停窗内非点名消息：不再评反馈，也不问小王接不接
        self.calls.reset_mock(); self.model_calls.clear()
        await self._drive("哈哈哈哈哈")
        self.calls.assert_not_awaited()
        self.assertEqual((self.model_calls, self.unprompted_calls), ([], []))

    async def test_friendly_swearing_does_not_mute_or_penalize(self):
        await self._seed_reply()
        before = (runtime.RELATIONS.get(self.G, self.A, create=False) or {}
                  ).get("score", 0)
        # 友好的「笑死」吐槽：target 判成别人/不清楚 → 不动作、不扣分
        self.calls.return_value = _body(feedback_target="another_person")
        await self._drive("笑死我了这也太蠢了")
        after = (runtime.RELATIONS.get(self.G, self.A, create=False) or {}
                 ).get("score", 0)
        self.assertGreaterEqual(after, before)
        self.assertFalse(self.feedback.quiet(self.G))
        # 词表判负但原生说是别的对象 → 不扣分（夹回 0）
        self.calls.return_value = _body(feedback_target="bot_response",
                                        next_move="keep_playful")
        await self._drive("滚啊哈哈", is_at=True)
        after2 = (runtime.RELATIONS.get(self.G, self.A, create=False) or {}
                  ).get("score", 0)
        self.assertGreaterEqual(after2, after)

    async def test_unnamed_followup_goes_to_xiaowang_with_its_last_line(self):
        """没点名的追问不再靠分诊的 reply_now 硬接，交给小王；它看得见自己刚说了什么。"""
        await self._seed_reply()
        self.calls.return_value = _body(next_move="continue_normally")
        self.unprompted_reply = "再来一个就再来一个"
        msg = await self._drive("再来一个呗")
        self.assertEqual(len(self.unprompted_calls), 1)
        self.assertIn("- 你：（模型应答）", self.unprompted_calls[-1][-1]["content"])
        msg.reply.assert_awaited()
        # 它说不接：一个字都不发
        self.unprompted_reply = "〔不接〕"
        msg = await self._drive("哈哈哈哈哈")
        msg.reply.assert_not_awaited()

    async def test_repair_note_is_final_dynamic_layer(self):
        await self._seed_reply()
        self.calls.return_value = _body(next_move="repair_briefly")
        self.model_calls.clear()
        await self._drive("你说错了那不是咖啡")
        self.assertEqual(len(self.model_calls), 1)   # 不点名也补一句
        last = self.model_calls[-1][-1]["content"]
        self.assertIn("纠正刚才", last)   # REPAIR 话术进了最后动态层

    async def test_without_a_live_reply_no_feedback_is_asked(self):
        """跟它无关、也不在反馈窗口里的消息：一次分诊都不发，直接问小王。"""
        self.feedback._groups.pop(self.G, None)       # 没登记过回复
        self.calls.reset_mock()
        await self._drive("随便聊两句")
        self.calls.assert_not_awaited()
        await self._seed_reply()                      # 登记但已过窗口
        self.feedback._groups[self.G]["reply"]["ts"] -= 9999
        await self._drive("过了几天再说")
        self.calls.assert_not_awaited()
        self.assertEqual(len(self.unprompted_calls), 2)


class OneCallTest(_Base):
    async def test_one_native_call_per_incoming_message(self):
        await self._drive("开场", is_at=True)     # 登记回复
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = _body(feedback_target="not_feedback")
        await self._drive("接着聊别的")
        # 反馈窗口里：分诊一次，然后问小王一次，不再有第二次原生调用
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual(len(self.unprompted_calls), 1)

    async def test_local_search_and_command_paths_bypass(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        for text in ("搜索 咖啡", "锁起名"):
            await self._drive(text, msg_id="x-" + text)
        # 本地指令（起名开关）与搜索路径都不进反馈评估
        self.assertEqual(self.calls.await_count, 0)


class PrecedenceAndSearchTest(_Base):
    async def test_back_off_means_xiaowang_is_not_even_asked(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = _body(next_move="back_off")
        await self._drive("别烦我了行不行")
        # 非点名的嫌烦：装上暂停窗，这一句也不问小王接不接
        self.assertEqual((self.model_calls, self.unprompted_calls), ([], []))
        self.assertTrue(self.feedback.quiet(self.G))
        # 点名仍然应一句
        self.calls.return_value = _body()
        await self._drive("叫你呢", is_at=True)
        self.assertGreaterEqual(len(self.model_calls), 1)

    async def test_search_command_never_runs_prospective_call(self):
        # 非点名搜索：搜索语义照旧，分诊不许插队
        self.calls.reset_mock()
        with patch.object(bot.search_mod, "search_web",
                             new=AsyncMock(return_value=[{
                                 "title": "t", "url": "u",
                                 "snippet": "s"}])):
            await self._drive("搜索 咖啡")
        self.assertEqual(self.calls.await_count, 0)

    async def test_failed_assessment_never_retries(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = None            # 评估失败也算「尝试过」
        await self._drive("这句接着说")
        self.assertEqual(self.calls.await_count, 1)       # 没有第二次
        self.assertEqual(len(self.unprompted_calls), 1)   # 评估失败不挡着问小王


class StaleAndPrivacyTest(_Base):
    async def test_stale_reply_during_assess_is_obsolete_quietly(self):
        await self._drive("开场", is_at=True)
        old_reply = self.feedback._groups[self.G]["reply"]
        self.calls.reset_mock()

        async def slow(text, questions):
            # 等待期间来了更新的一条送达 —— 这次机会作废
            self.feedback.remember(self.G, self.A, "新回复",
                                   route="reply", outgoing_id="oid-new")
            return _body(next_move="back_off")
        self.calls.side_effect = slow
        result = await self.feedback.assess(
            self.G, self.A, "刚才那句不对", is_direct=False)
        self.assertEqual(result["action"], "stay_quiet")
        self.assertFalse(self.feedback.quiet(self.G))   # 不安装暂停窗
        self.assertIs(self.feedback._groups[self.G]["reply"],
                      self.feedback._groups[self.G]["reply"])
        self.assertNotEqual(
            self.feedback._groups[self.G]["reply"]["outgoing_id"],
            old_reply["outgoing_id"])

class PromptLayerTest(_Base):
    async def test_playful_semantic_flag_uses_current_prompt_only_in_dynamic_layer(self):
        current_note = "【本轮语气线索】当版提示"
        with patch.object(prompts, "PROMPT_PLAYFUL_REQUEST", current_note):
            await bot.get_ai_reply(
                "sess-playful-semantic", "接个梗", playful_request=True,
                playful_note="旧版被冻结的提示")
        messages = self.model_calls[-1]
        self.assertIn(current_note, messages[-1]["content"])
        self.assertNotIn(current_note, "\n".join(m["content"] for m in messages[:-1]))
        self.assertNotIn("旧版被冻结的提示", "\n".join(m["content"] for m in messages))

    async def test_legacy_playful_note_remains_supported(self):
        await bot.get_ai_reply(
            "sess-playful-legacy", "接个梗", playful_note="【兼容语气线索】旧调用")
        messages = self.model_calls[-1]
        self.assertIn("【兼容语气线索】旧调用", messages[-1]["content"])
        self.assertNotIn("【兼容语气线索】", "\n".join(m["content"] for m in messages[:-1]))

    async def test_backoff_and_feedback_suppress_semantic_and_legacy_playful_cues(self):
        for cue in ({"playful_request": True}, {"playful_note": "【旧版玩笑线索】"}):
            for priority in ({"back_off": True}, {"feedback_note": prompts.PROMPT_FEEDBACK_REPAIR}):
                with self.subTest(cue=cue, priority=priority):
                    await bot.get_ai_reply("sess-playful-priority", "接着说", **cue, **priority)
                    tail = self.model_calls[-1][-1]["content"]
                    self.assertNotIn("【本轮语气线索】", tail)
                    self.assertNotIn("【旧版玩笑线索】", tail)
                    self.assertIn((prompts.PROMPT_BACK_OFF_NOTE if priority.get("back_off")
                                   else prompts.PROMPT_FEEDBACK_REPAIR).strip(), tail)

    async def test_feedback_note_suppresses_competing_material(self):
        """直接调 get_ai_reply：feedback_note 在场时知识/旧账/搜索/个人全不注。"""
        captured = {}

        async def fake_model(messages, max_tokens, **kw):
            captured["user"] = messages[-1]["content"]
            return "应一句"
        with patch.object(bot, "call_model", fake_model):
            await bot.get_ai_reply(
                "sess-feedback-note", "刚才那句说错了",
                knowledge_note="【知识库】x", recall_note="【旧账】y",
                search_note="【搜索】z", personal_note="【原话】p",
                feedback_note=prompts.PROMPT_FEEDBACK_REPAIR)
        user_msg = captured["user"]
        self.assertIn(prompts.PROMPT_FEEDBACK_REPAIR.strip()[:8], user_msg)
        for leaked in ("【知识库】x", "【旧账】y", "【搜索】z", "【原话】p"):
            self.assertNotIn(leaked, user_msg)


class TimerSpeechQuietTest(_Base):
    """收敛期按住所有主动通道；确认的群发可当被反馈的上一条。"""

    async def test_quiet_blocks_post_to_group(self):
        self.feedback._groups[self.G] = {
            "reply": {"user": None, "text": "bot 群发", "route": "proactive",
                      "outgoing_id": "o-1", "ts": time.time(), "history": []},
            "quiet_until": time.time() + 300}
        api = SimpleNamespace(
            post_group_message=AsyncMock(return_value={"id": "r"}))
        with patch.dict(bot._bot_ref,
                        {"client": SimpleNamespace(api=api)}):
            sent = await bot.post_to_group(
                self.G, "自己来一句", model_generated=True)
        self.assertFalse(sent)
        api.post_group_message.assert_not_awaited()   # quiet 按住，API 都没碰

    async def test_confirmed_model_post_cached_and_feedback_targets_it(self):
        receipt = {"id": "rcpt-9"}
        api = SimpleNamespace(post_group_message=AsyncMock(return_value=receipt))
        with patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)}):
            ok = await bot.post_to_group(
                self.G, "主动开口讲个笑话", model_generated=True,
                history=[{"sender": self.A, "text": "聊", "message_id": "m0",
                          "time": 1.0}])
        self.assertTrue(ok)
        reply = self.feedback._groups[self.G]["reply"]
        self.assertIsNone(reply["user"])
        self.assertEqual(reply["route"], "proactive")
        # 群友嫌吵：这条群发可以被认成反馈对象，判 back_off 后装暂停窗
        self.calls.return_value = _body(next_move="back_off")
        result = await self.feedback.assess(
            self.G, self.A, "别吵了", is_direct=False)
        self.assertTrue(self.feedback.quiet(self.G))
        self.assertEqual(result["bot_reply_to"], "group")

    async def test_static_and_unconfirmed_posts_are_never_cached(self):
        api = SimpleNamespace(
            post_group_message=AsyncMock(side_effect=[{"id": "r1"}, None]))
        with patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)}):
            await bot.post_to_group(self.G, "静态播报")          # 非模型生成
            self.assertIsNone(
                self.feedback._groups.get(self.G, {}).get("reply"))
            await bot.post_to_group(self.G, "模型文", model_generated=True)
            self.assertIsNone(
                self.feedback._groups.get(self.G, {}).get("reply"))

    async def test_quiet_arriving_mid_generation_still_blocks(self):
        # 生成时没有静音，发送前一刻静音落下 → post 的复查拦住这次主动发言
        calls = iter([False, True])
        with patch.object(self.feedback, "quiet",
                          side_effect=lambda *a, **k: next(calls)):
            api = SimpleNamespace(
                post_group_message=AsyncMock(return_value={"id": "r"}))
            with patch.dict(bot._bot_ref,
                            {"client": SimpleNamespace(api=api)}):
                sent = await bot.post_to_group(
                    self.G, "生成好的文", model_generated=True)
        self.assertFalse(sent)
        api.post_group_message.assert_not_awaited()


class ReplyContextTest(_Base):
    async def test_cross_person_correction_gets_actual_bot_text(self):
        """A 收到的回复，B 来纠正：B 的会话里补进真实原文 + 另一位标签 + 修话术。"""
        await self._drive("说点什么", sender=self.A, is_at=True)
        reply = self.feedback._groups[self.G]["reply"]
        self.assertEqual(reply["text"], "（模型应答）")
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = _body(next_move="repair_briefly")
        await self._drive("刚才那句说错了", sender="USER_FB_B")
        last = self.model_calls[-1][-1]["content"]
        self.assertIn("（模型应答）", last)          # 真实原文，不是模型复述
        self.assertIn("另一位群友", last)
        self.assertIn("纠正刚才", last)              # repair 话术也在
        self.assertIn("即时提示", last)              # 只在最末动态层

    async def test_same_person_followup_gets_context_without_repair(self):
        await self._drive("说点什么", sender=self.A, is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = _body(next_move="continue_normally")
        self.unprompted_reply = "接着说就接着说"
        await self._drive("接着说", sender=self.A)
        last = self.unprompted_calls[-1][-1]["content"]
        self.assertIn("（模型应答）", last)
        self.assertIn("本轮说话人", last)
        self.assertNotIn("纠正刚才", last)           # 有语境不等于修话术

    async def test_reply_context_alone_keeps_other_notes(self):
        captured = {}

        async def fake_model(messages, max_tokens, **kw):
            captured["user"] = messages[-1]["content"]
            return "应"
        ctx_text = prompts.PROMPT_PREVIOUS_BOT_REPLY.format(
            recipient="由另一位群友触发的接话",
            quoted_text=json.dumps("上一条话", ensure_ascii=False))
        with patch.object(bot, "call_model", fake_model):
            await bot.get_ai_reply(
                "sess-reply-ctx", "接着聊",
                knowledge_note="【知识库】x", reply_context=ctx_text)
        self.assertIn("上一条话", captured["user"])
        self.assertIn("【知识库】x", captured["user"])   # 不压制其他材料


class ProactiveLoopTickTest(_Base):
    """主动巡检一整圈：安静群全段跳过；其余由小王自己定说不说。"""

    def _one_tick(self):
        """STATUS.sleep 第一轮放行、第二轮抛 CancelledError 收住循环。"""
        return patch.object(bot.STATUS, "sleep",
                            AsyncMock(side_effect=[None,
                                                   asyncio.CancelledError()]))

    def _stage(self):
        """把 proactive_loop 的外部闸门全打开放行，只剩小王自己那一道。"""
        bot.group_buffers.clear()
        bot.group_buffers[self.G] = collections.deque(
            [{"sender": self.A, "text": f"第{i}句", "time": time.time()}
             for i in range(3)])
        gen = AsyncMock(return_value=("开场白", None))
        post = AsyncMock(return_value=True)
        for target, name, value in (
                (bot.activation, "proactive_ok",
                 lambda *a, **k: (True, "ok")),
                (bot.activation, "current_mood", lambda now: 0.5),
                (bot.activation, "state_line", lambda *a, **k: ""),
                (bot, "get_ai_reply", gen),
                (bot, "post_to_group", post),
                (runtime.BUDGET, "can_consume", lambda: True)):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)
        return gen, post

    async def test_pass_skips_the_post(self):
        """主动开口说不说由小王自己定：它写〔不接〕就不发，不再先问一次插嘴判断。"""
        gen, post = self._stage()
        gen.return_value = ("〔不接〕", None)
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.calls.assert_not_awaited()
        self.assertEqual(gen.await_count, 1)
        post.assert_not_awaited()

    async def test_it_speaks_once_when_it_has_something(self):
        gen, post = self._stage()
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.calls.assert_not_awaited()
        self.assertEqual(post.await_count, 1)
        kw = post.await_args.kwargs
        self.assertTrue(kw["model_generated"])
        self.assertIn("history", kw)
        self.assertIn("〔不接〕", gen.await_args.args[1])   # 提示里给了它不说的路

    async def test_quiet_group_skips_the_whole_branch(self):
        gen, post = self._stage()
        self.feedback._groups[self.G] = {
            "reply": None, "quiet_until": time.time() + 300}
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.assertEqual(self.calls.await_count, 0)
        gen.assert_not_awaited()
        post.assert_not_awaited()


class AssessExceptionTest(_Base):
    async def test_assess_exception_counts_as_attempt(self):
        await self._drive("开场", is_at=True)     # 登记回复
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.side_effect = RuntimeError("native down")
        await self._drive("接着说")
        # 异常算「已尝试」：不补发第二次原生，照常问小王
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual(len(self.unprompted_calls), 1)


class PlayfulRequestRoutingTest(_Base):
    async def test_playful_hint_only_enters_this_turn(self):
        # 群里的单招/大专玩笑即使出现，也不该默认当成一次正经求助。
        result = {"playful_request": True}
        with patch.object(self.feedback, "assess", AsyncMock(return_value=result)):
            await self._drive("单招大专申请火箭科学家，帮我写个获奖感言", is_at=True)
        self.assertEqual(len(self.model_calls), 1)
        messages = self.model_calls[-1]
        marker = "【本轮语气线索】"
        self.assertIn(marker, messages[-1]["content"])
        self.assertIn(prompts.PROMPT_PLAYFUL_REQUEST, messages[-1]["content"])
        self.assertNotIn(marker, "\n".join(m["content"] for m in messages[:-1]))

    async def test_no_playful_signal_leaves_no_extra_hint(self):
        with patch.object(self.feedback, "assess", AsyncMock(return_value={})):
            await self._drive("单招考不上怎么办", is_at=True)
        self.assertNotIn("【本轮语气线索】", self.model_calls[-1][-1]["content"])

    async def test_feedback_backoff_and_repair_override_playful_signal(self):
        for action in ("back_off", "repair_briefly"):
            with self.subTest(action=action):
                result = {"playful_request": True, "target": "bot_response",
                          "action": action, "back_off": action == "back_off"}
                with patch.object(self.feedback, "assess", AsyncMock(return_value=result)):
                    await self._drive("别拿这个笑我", is_at=True)
                tail = self.model_calls[-1][-1]["content"]
                self.assertNotIn("【本轮语气线索】", tail)
                self.assertIn("嫌你打扰" if action == "back_off" else "纠正刚才", tail)

    async def test_actual_lookup_suppresses_playful_hint(self):
        result = {"playful_request": True, "lookup": "search"}
        with patch.object(config, "SEARCH_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.providers, "research_available", return_value=True), \
             patch.object(bot, "_reserve_search", return_value="normal"), \
             patch.object(bot, "answer_with_lookup", AsyncMock(return_value=None)) as lookup:
            msg = await self._drive("帮我查一下单招报名时间", is_at=True)
        lookup.assert_awaited_once()
        self.assertIs(lookup.await_args.kwargs["reply_kwargs"]["playful_request"], False)
        self.assertIn("没查成", msg.reply.await_args.kwargs["content"])


if __name__ == "__main__":
    unittest.main()
