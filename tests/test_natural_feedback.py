"""自然反馈回路：送达缓存资格、收敛暂停、修正话术、插嘴仲裁、一次调用。"""
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
                        "continue_normally", "no_adjustment"],
          "continuation": ["reply_now", "no_reply_needed", "unclear"],
          "intervention": ["join_briefly", "stay_quiet"]}


def _body(**kv):
    """真实原生返回形态：confidence + probabilities 数组。"""
    base = {"feedback_target": "bot_response", "next_move": "keep_playful",
            "continuation": "no_reply_needed", "intervention": "join_briefly"}
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

        async def fake_model(messages, max_tokens, **kw):
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

    async def test_back_off_pauses_unsolicited_but_direct_still_answers(self):
        await self._seed_reply()
        self.calls.return_value = _body(next_move="back_off",
                                        intervention="stay_quiet")
        # 点名嫌烦 → 仍然直接回复（被叫就要应），但挂上收敛话术
        await self._drive("别烦我了", is_at=True)
        self.assertTrue(self.feedback.quiet(self.G))
        self.assertEqual(self.calls.await_count, 1)
        last = self.model_calls[-1][-1]["content"]
        self.assertIn("嫌你打扰", last)   # PROMPT_FEEDBACK_BACK_OFF 已在末尾层
        # 暂停窗内非点名消息：不再评反馈、不插嘴。每条都分诊时只问 intervention（攒数据），
        # 关掉每条分诊时连这一次也不问。
        for triage_all in (True, False):
            with self.subTest(triage_all=triage_all), \
                    patch.object(config, "DECISION_TRIAGE_ALL", triage_all):
                self.calls.reset_mock(); self.model_calls.clear()
                await self._drive("哈哈哈哈哈")
                self._assert_only_intervention(triage_all)
                self.assertEqual(len(self.model_calls), 0)

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

    async def test_implicit_followup_directs_reply_but_laughter_does_not(self):
        await self._seed_reply()
        self.calls.return_value = _body(continuation="reply_now",
                                        next_move="continue_normally")
        self.model_calls.clear()
        await self._drive("再来一个呗")
        self.assertEqual(len(self.model_calls), 1)   # 没点名也照接
        # 只是笑：不许催出一条额外回复
        self.calls.return_value = _body(continuation="no_reply_needed")
        self.model_calls.clear()
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("哈哈哈哈哈")
        self.assertEqual(len(self.model_calls), 0)

    async def test_repair_note_is_final_dynamic_layer(self):
        await self._seed_reply()
        self.calls.return_value = _body(next_move="repair_briefly")
        self.model_calls.clear()
        await self._drive("你说错了那不是咖啡")
        self.assertEqual(len(self.model_calls), 1)   # 不点名也补一句
        last = self.model_calls[-1][-1]["content"]
        self.assertIn("纠正刚才", last)   # REPAIR 话术进了最后动态层

    def _assert_only_intervention(self, triage_all):
        """没有可评的反馈时：每条都分诊 → 只问 intervention；关掉 → 0 调用。"""
        if triage_all:
            self.assertEqual(self.calls.await_count, 1)
            self.assertEqual({q["name"] for q in self.calls.await_args.args[1]},
                             {"intervention"})
        else:
            self.assertEqual(self.calls.await_count, 0)

    async def test_without_a_live_reply_no_feedback_is_asked(self):
        for triage_all in (True, False):
            with self.subTest(triage_all=triage_all), \
                    patch.object(config, "DECISION_TRIAGE_ALL", triage_all):
                # 没登记过回复
                self.feedback._groups.pop(self.G, None)
                self.calls.reset_mock()
                with patch.object(bot.random, "random", return_value=0.999):
                    await self._drive("随便聊两句")
                self._assert_only_intervention(triage_all)
                # 登记但已过窗口
                await self._seed_reply()
                self.feedback._groups[self.G]["reply"]["ts"] -= 9999
                with patch.object(bot.random, "random", return_value=0.999):
                    await self._drive("过了几天再说")
                self._assert_only_intervention(triage_all)


def _intervention(p_join):
    return {"answers": [{
        "name": "intervention", "type": "choice",
        "choice": "join_briefly" if p_join >= 0.5 else "stay_quiet",
        "confidence": abs(2 * p_join - 1),
        "probabilities": [{"value": "join_briefly", "probability": p_join},
                          {"value": "stay_quiet", "probability": 1 - p_join}]}]}


class BanterVetoTest(_Base):
    async def test_judgment_scales_the_dice_instead_of_vetoing(self):
        """2026-11-01：判断的 p(join) 折进概率，结论不再一票否决。"""
        self.addCleanup(lambda: bot.group_last_random_reply.pop(self.G, None))
        # 有把握地说不合适（p(join)=0）：概率跟着归零，骰子再小也不中
        with patch.object(bot.random, "random", return_value=0.0):
            self.calls.return_value = _intervention(0.0)
            await self._drive("大家在认真讨论正事")
        self.assertEqual(len(self.model_calls), 0)
        # 结论是 stay_quiet 但概率不是零：照比例缩小，照样可能命中
        with patch.object(bot.random, "random", return_value=0.0):
            self.calls.return_value = _intervention(0.2)
            await self._drive("这句也许能接", msg_id="m-low")
        self.assertEqual(len(self.model_calls), 1)
        # 判断不可用（None）→ 乘数为 1，维持骰子基线
        bot.group_last_random_reply.pop(self.G, None)
        with patch.object(bot.random, "random", return_value=0.0):
            self.calls.return_value = None
            await self._drive("这句值得接", msg_id="m-baseline")
        self.assertEqual(len(self.model_calls), 2)

    async def test_one_native_call_per_incoming_message(self):
        await self._drive("开场", is_at=True)     # 登记回复
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = _body(intervention="stay_quiet")
        with patch.object(bot.random, "random", return_value=0.0):
            await self._drive("骰子也命中了")
        # assess 已跑过一次 → 判断已折进概率，掷中也不许再发第二次
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual(len(self.model_calls), 1)

    async def test_local_search_and_command_paths_bypass(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        for text in ("搜索 咖啡", "锁起名"):
            await self._drive(text, msg_id="x-" + text)
        # 本地指令（起名开关）与搜索路径都不进反馈评估
        self.assertEqual(self.calls.await_count, 0)


class PrecedenceAndSearchTest(_Base):
    async def test_back_off_overrides_reply_now_and_join_briefly(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()
        # 矛盾答案：back_off + reply_now + join_briefly —— 收敛一票否决
        self.calls.return_value = _body(
            next_move="back_off", continuation="reply_now",
            intervention="join_briefly")
        with patch.object(bot.random, "random", return_value=0.0):
            await self._drive("别烦我了行不行")
        self.assertEqual(len(self.model_calls), 0)   # 非点名：不多嘴、不掷后续
        self.assertTrue(self.feedback.quiet(self.G))
        # 点名仍然应一句
        self.calls.return_value = _body()
        await self._drive("叫你呢", is_at=True)
        self.assertGreaterEqual(len(self.model_calls), 1)

    async def test_search_command_never_runs_prospective_call(self):
        # 非点名搜索 + 强制骰子命中：搜索语义照旧，原生插嘴仲裁不许插队
        self.calls.reset_mock()
        with patch.object(bot.random, "random", return_value=0.0), \
                patch.object(bot.search_mod, "search_web",
                             new=AsyncMock(return_value=[{
                                 "title": "t", "url": "u",
                                 "snippet": "s"}])):
            await self._drive("搜索 咖啡")
        self.assertEqual(self.calls.await_count, 0)

    async def test_failed_assessment_never_retries_on_dice_hit(self):
        await self._drive("开场", is_at=True)
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.return_value = None            # 评估失败也算「尝试过」
        with patch.object(bot.random, "random", return_value=0.0):
            await self._drive("这句骰子会中")
        self.assertEqual(self.calls.await_count, 1)   # 没有第二次
        self.assertEqual(len(self.model_calls), 1)    # 评估失败不否决骰子


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
        self.assertEqual(result["intervention"], "stay_quiet")
        self.assertFalse(self.feedback.quiet(self.G))   # 不安装暂停窗
        self.assertIs(self.feedback._groups[self.G]["reply"],
                      self.feedback._groups[self.G]["reply"])
        self.assertNotEqual(
            self.feedback._groups[self.G]["reply"]["outgoing_id"],
            old_reply["outgoing_id"])

    async def test_stale_reply_during_banter_is_obsolete(self):
        self.calls.reset_mock()

        async def slow(text, questions):
            self.feedback.remember(self.G, self.A, "插队回复",
                                   route="reply", outgoing_id="oid-new")
            return {"answers": [{"name": "intervention", "type": "choice",
                                 "choice": "join_briefly",
                                 "confidence": 0.9,
                                 "probabilities": [
                                     {"value": "join_briefly",
                                      "probability": 0.9},
                                     {"value": "stay_quiet",
                                      "probability": 0.1}]}]}
        self.feedback.remember(self.G, self.A, "旧回复",
                               route="reply", outgoing_id="oid-old")
        self.calls.side_effect = slow
        verdict = await self.feedback.consider_banter(
            self.G, self.A, history=[{"sender": self.A, "text": "聊"}])
        self.assertEqual(verdict, "stay_quiet")

    async def test_banter_expired_reply_and_empty_context(self):
        # 过期回复不带进请求；没有任何人话语境 → 不调原生
        self.feedback.remember(self.G, self.A, "老黄历回复",
                               route="reply", outgoing_id="oid-old")
        self.feedback._groups[self.G]["reply"]["ts"] -= 9999
        self.calls.reset_mock()
        verdict = await self.feedback.consider_banter(
            self.G, self.A, history=[{"sender": self.A, "text": "还在聊"}])
        self.assertIsNone(verdict)  # 超时回复被排除后这次调用仍允许，
        # 但 payload 里不许出现旧回复
        if self.calls.await_count:
            payload = json.loads(self.calls.await_args.args[0])
            self.assertNotIn("老黄历回复", payload)
        self.calls.reset_mock()
        verdict = await self.feedback.consider_banter(
            self.G, self.A, history=[{"sender": self.A, "text": ""}])
        self.assertIsNone(verdict)
        self.assertEqual(self.calls.await_count, 0)


class PromptLayerTest(_Base):
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
        self.calls.return_value = _body(continuation="reply_now",
                                        next_move="continue_normally")
        await self._drive("接着说", sender=self.A)
        last = self.model_calls[-1][-1]["content"]
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
    """主动巡检一整圈：安静群全段跳过，原生否决/不可用各走各的路。"""

    def _one_tick(self):
        """STATUS.sleep 第一轮放行、第二轮抛 CancelledError 收住循环。"""
        return patch.object(bot.STATUS, "sleep",
                            AsyncMock(side_effect=[None,
                                                   asyncio.CancelledError()]))

    def _stage(self):
        """把 proactive_loop 的外部闸门全打开放行，只剩原生裁决一道。"""
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

    async def test_native_veto_skips_generation_and_post(self):
        gen, post = self._stage()
        self.calls.return_value = {"answers": [
            {"name": "intervention", "type": "choice",
             "choice": "stay_quiet", "confidence": 0.9,
             "probabilities": [{"value": "stay_quiet", "probability": 0.9},
                               {"value": "join_briefly",
                                "probability": 0.1}]}]}
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.assertEqual(self.calls.await_count, 1)
        gen.assert_not_awaited()
        post.assert_not_awaited()

    async def test_native_unavailable_stays_quiet(self):
        """主动开口是「多出来的一句」：判断不可用就不说（2026-10-31 起不再让生成用 SKIP 兜底）。"""
        gen, post = self._stage()
        self.calls.return_value = None
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.assertEqual(self.calls.await_count, 1)
        gen.assert_not_awaited()
        post.assert_not_awaited()

    async def test_native_join_speaks_once(self):
        gen, post = self._stage()
        self.calls.return_value = {"answers": [{
            "name": "intervention", "type": "choice", "choice": "join_briefly",
            "confidence": 0.8, "probabilities": [
                {"value": "join_briefly", "probability": 0.9},
                {"value": "stay_quiet", "probability": 0.1}]}]}
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual(gen.await_count, 1)
        self.assertEqual(post.await_count, 1)
        kw = post.await_args.kwargs
        self.assertTrue(kw["model_generated"])
        self.assertIn("history", kw)

    async def test_quiet_group_skips_the_whole_branch(self):
        gen, post = self._stage()
        self.feedback._groups[self.G] = {
            "reply": None, "quiet_until": time.time() + 300}
        with self._one_tick():
            with self.assertRaises(asyncio.CancelledError):
                await bot.proactive_loop()
        self.assertEqual(self.calls.await_count, 0)   # 仲裁都不必问
        gen.assert_not_awaited()
        post.assert_not_awaited()


class AssessExceptionTest(_Base):
    async def test_assess_exception_counts_as_attempt(self):
        await self._drive("开场", is_at=True)     # 登记回复
        self.calls.reset_mock()
        self.model_calls.clear()
        self.calls.side_effect = RuntimeError("native down")
        with patch.object(bot.random, "random", return_value=0.0):
            await self._drive("骰子会中")
        # 异常算「已尝试」：不补发第二次原生，骰子基线照常放行
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual(len(self.model_calls), 1)


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
        self.assertIn("不要揪着学历", messages[-1]["content"])
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
        self.assertEqual(lookup.await_args.kwargs["reply_kwargs"]["playful_note"], "")
        self.assertIn("没查成", msg.reply.await_args.kwargs["content"])


class ImageRoutingTest(_Base):
    async def test_confirmed_image_becomes_followup_context(self):
        async def send(_message, **kw):
            self.assertEqual(await kw["before_send"](), 1)
            return True
        assessor = AsyncMock(return_value={"image_generation": "generate",
                                          "image_prompt": "sanitized context"})
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(self.feedback, "assess", assessor), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send) as generated:
            await self._drive("给我画只猫", is_at=True)
        self.assertTrue(assessor.await_args.kwargs["want_image"])
        generated.assert_awaited_once()
        self.assertEqual(self.model_calls, [])
        self.assertIn("已生成并发送图片", self.feedback._groups[self.G]["reply"]["text"])

    async def test_slow_image_waits_then_sends_with_next_sequence(self):
        async def send(_message, **kw):
            await asyncio.sleep(0.01)
            self.assertEqual(await kw["before_send"](), 2)
            return True
        result = {"image_generation": "generate", "image_prompt": "cat"}
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(config, "ASYNC_REPLY_SECONDS", 0), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send):
            msg = await self._drive("画只猫", is_at=True)
        self.assertEqual(msg.reply.await_count, 1)
        self.assertEqual(msg.reply.await_args.kwargs["msg_seq"], 1)
        self.assertIn("稍等", msg.reply.await_args.kwargs["content"])

    async def test_failed_image_reports_failure_without_chat_budget(self):
        result = {"image_generation": "generate", "image_prompt": "cat"}
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value=False)), \
             patch.object(bot.BudgetGate, "check", AsyncMock(return_value=False)) as budget:
            msg = await self._drive("画只猫", is_at=True)
        self.assertIn("没画成", msg.reply.await_args.kwargs["content"])
        budget.assert_not_awaited()

    async def test_slow_failure_notifies_even_when_chat_budget_is_exhausted(self):
        async def send(_message, **kw):
            await asyncio.sleep(0.01)
            return False
        result = {"image_generation": "generate", "image_prompt": "cat"}
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(config, "ASYNC_REPLY_SECONDS", 0), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send), \
             patch.object(bot.BudgetGate, "check", AsyncMock(return_value=False)) as budget, \
             patch.object(bot, "safe_reply", AsyncMock(wraps=bot.safe_reply)) as replies:
            await self._drive("画只猫", is_at=True)
        self.assertEqual(replies.await_count, 2)
        self.assertIn("没画成", replies.await_args.args[1])
        budget.assert_not_awaited()

    async def test_unknown_send_result_never_sends_an_extra_reply(self):
        result = {"image_generation": "generate", "image_prompt": "cat"}
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value=None)):
            msg = await self._drive("画只猫", is_at=True)
        msg.reply.assert_not_awaited()
        self.assertEqual(self.model_calls, [])

    async def test_no_reply_gate_never_generates(self):
        result = {"image_generation": "generate", "image_prompt": "cat",
                  "intervention": "stay_quiet", "decision_attempted": True}
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.random, "random", return_value=1.0), \
             patch.object(bot.image_generation, "generate_and_send", AsyncMock()) as generated:
            await self._drive("画只猫")
        generated.assert_not_awaited()

    async def test_followup_without_mention_uses_existing_reply_gate(self):
        result = {"image_generation": "generate", "image_prompt": "poster",
                  "target": "bot_response", "continuation": "reply_now",
                  "action": "continue_normally"}
        async def send(_message, **kw):
            await kw["before_send"]()
            return True
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send) as generated:
            await self._drive("那就来一张吧")
        generated.assert_awaited_once()

    async def test_image_intent_takes_priority_over_automatic_lookup(self):
        result = {"image_generation": "generate", "image_prompt": "poster", "lookup": "search"}
        async def send(_message, **kw):
            await kw["before_send"]()
            return True
        with patch.object(config, "IMAGE_GENERATION_ENABLED", True), \
             patch.object(config, "SEARCH_ENABLED", True), \
             patch.object(self.feedback, "assess", AsyncMock(return_value=result)), \
             patch.object(bot.providers, "research_available", return_value=True), \
             patch.object(bot.image_generation, "generate_and_send", side_effect=send) as generated, \
             patch.object(bot, "answer_with_lookup", AsyncMock()) as lookup:
            await self._drive("给我画一幅未来城市", is_at=True)
        generated.assert_awaited_once()
        lookup.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
