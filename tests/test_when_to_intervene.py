"""何时介入：节奏先验、接纳度、回忆，真的接在插嘴门上了吗。

单元测试证明三个模块各自讲理；这一组证明它们**接线了**，而且接的是对的位置：

  - 节奏先验要改变 `gate_probability`，不是只写进日志；
  - 接纳度要在证据够了之后才改变它，证据不足时一个字不动；
  - 「下一句骂你」要真的变成跨天的证据，而且只收「没人请的插嘴」那一种；
  - 回忆要只在获批时进提示词，普通接话轮的行为一个字都不变。

接线没接等于没做 —— 这类回归最容易悄悄发生（测试绿、行为旧）。
"""
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
import opening
import prompts
import receptive
import runtime
import storage
from regular import decisions

G = "GID_WHEN"
A = "USER_WHEN_A"
B = "USER_WHEN_B"
QUOTE = "我只喝手冲，速溶那种不算咖啡"


class _Recorder(interactions.InteractionLog):
    """照常落盘，另外把写出去的行留在内存里给用例读。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def append(self, kind, group, user=None, **fields):
        row = super().append(kind, group, user=user, **fields)
        if row is not None:
            self.rows.append(row)
        return row

    def gates(self):
        return [r for r in self.rows if r["kind"] == "decision"]


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = _Recorder(os.path.join(self._tmp.name, "ix"),
                             enabled=True, keep_days=0)
        self.addCleanup(self.log.close)
        self.feedback = decisions.FeedbackTracker(120.0, 180.0)
        self.opening = opening.OpeningPrior()
        self.intervention = opening.OpeningPrior(
            base=decisions.INTERVENTION_BASE, shape_min=0.0, shape_max=3.0,
            key="intervention")
        self.receptive = receptive.Receptiveness(min_evidence=4.0)
        self.calls = AsyncMock(return_value=None)
        self.prompts_seen = []

        async def fake_model(messages, max_tokens, **kw):
            self.prompts_seen.append(messages)
            return "（模型应答）"

        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", self.feedback),
                (runtime, "OPENING", self.opening),
                (runtime, "INTERVENTION", self.intervention),
                (runtime, "RECEPTIVE", self.receptive),
                (decisions.providers, "call_decision", self.calls),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (runtime, "GROUP_BUCKET",
                 storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                     config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", fake_model),
                (config, "BANTER_COOLDOWN_SECONDS", 0.0)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.addCleanup(lambda: bot.group_last_random_reply.pop(G, None))
        self.addCleanup(lambda: bot.group_buffers.pop(G, None))
        self.addCleanup(lambda: bot.processed_msg_ids.clear())
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{A}", None))
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{B}", None))

    async def _drive(self, text, sender=A, is_at=False, msg_id=None):
        fake_self = SimpleNamespace(
            robot=SimpleNamespace(id="BOT_WHEN", name="regular"))
        msg = SimpleNamespace(
            id=msg_id or "msg-" + os.urandom(5).hex(), content=text,
            group_openid=G, author=SimpleNamespace(member_openid=sender),
            attachments=[], mentions=[], timestamp=None,
            reply=AsyncMock(return_value={"id": "rcpt-" + os.urandom(3).hex()}))
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return msg

    def _seed_buffer(self, spec):
        """spec: [(距今秒数, 发言人, 文本), ...] —— 把节奏直接摆好。"""
        now = time.time()
        bot.group_buffers[G].clear()
        for ago, who, text in spec:
            bot.group_buffers[G].append(
                {"sender": who, "text": text, "time": now - ago,
                 "message_id": "seed-" + os.urandom(3).hex()})


class OpeningWiredTest(_Base):
    """节奏先验必须真的改变那枚骰子的概率。"""

    async def test_lively_floor_raises_the_gate_probability(self):
        self._seed_buffer([(40, A, "哈哈"), (30, B, "真的吗"),
                           (20, "USER_C", "我也看了"), (10, A, "笑死")])
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("再来一个")
        lively = self.log.gates()[-1]

        self.log.rows.clear()
        self._seed_buffer([(4000, A, "有人吗")])
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("？", msg_id="msg-dead")
        dead = self.log.gates()[-1]

        self.assertGreater(lively["gate_probability"],
                           dead["gate_probability"])
        self.assertGreater(lively["opening_score"], dead["opening_score"])

    async def test_trace_names_the_shape_and_the_group_mean(self):
        self._seed_buffer([(20, A, "哈"), (10, B, "嗯")])
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("噢")
        gate = self.log.gates()[-1]
        for key in ("opening_score", "opening_shape", "opening_mean",
                    "receptive_multiplier", "receptive_claimed"):
            self.assertIn(key, gate)

    async def test_disabled_prior_leaves_no_trace_and_no_effect(self):
        self._seed_buffer([(20, A, "哈"), (10, B, "嗯")])
        with patch.object(config, "OPENING_PRIOR_ENABLED", False), \
                patch.object(bot.random, "random", return_value=0.999):
            await self._drive("噢")
        gate = self.log.gates()[-1]
        self.assertNotIn("opening_score", gate)
        self.assertEqual(self.opening.records, {})

    async def test_the_group_mean_samples_every_message(self):
        """均值必须对**每条**消息取样。

        只在插嘴门里取样会漏掉冷却窗和收敛窗里的消息 —— 而被漏掉的恰好是
        「刚接过话」之后的热场消息，群均值会被系统性地压低、乘数整体偏高，
        保量就不成立了。被点名的那一轮也要取样。
        """
        self._seed_buffer([(20, A, "哈"), (10, B, "嗯")])
        await self._drive("在吗", is_at=True)      # 这一轮根本不过插嘴门
        self.assertEqual(self.opening.records[G]["count"], 1)

        bot.group_last_random_reply[G] = time.time()   # 冷却窗内
        with patch.object(config, "BANTER_COOLDOWN_SECONDS", 999.0):
            await self._drive("随便说一句", msg_id="msg-cooldown")
        self.assertEqual(self.opening.records[G]["count"], 2)
        self.assertEqual(self.log.gates()[-1]["reason"], "cooldown")

    async def test_direct_address_never_goes_through_the_gate(self):
        """被点名就该答 —— 节奏先验不许插手明确 @。"""
        self._seed_buffer([(4000, A, "有人吗")])
        await self._drive("在吗", is_at=True)
        gate = self.log.gates()[-1]
        self.assertEqual(gate["route"], "direct")
        self.assertTrue(gate["selected"])
        self.assertNotIn("gate_probability", gate)


class JudgedTimingWiredTest(_Base):
    """2026-11-01：分诊那道 intervention 题给的 p(join_briefly)，按本群均值折成乘数。"""

    def answer(self, p_join, **picks):
        async def respond(text, questions):
            answers = []
            for q in questions:
                values = [c["value"] for c in q["choices"]]
                if q["name"] == "intervention":
                    probs = {"join_briefly": p_join, "stay_quiet": 1 - p_join}
                else:
                    pick = picks.get(q["name"], values[-1])
                    probs = {v: 0.9 if v == pick else 0.1 / (len(values) - 1) for v in values}
                choice = max(probs, key=probs.get)
                answers.append({"name": q["name"], "type": "choice", "choice": choice,
                                "confidence": 0.8, "probabilities": [
                                    {"value": v, "probability": p} for v, p in probs.items()]})
            return {"answers": answers}
        return respond

    async def test_gate_records_the_judgment_multiplier(self):
        self.calls.side_effect = self.answer(0.6)
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("哈哈哈这也行")
        gate = self.log.gates()[-1]
        sharpened = 0.6 ** decisions.INTERVENTION_SHARPNESS
        self.assertAlmostEqual(gate["intervention_score"], sharpened, places=4)
        self.assertAlmostEqual(gate["intervention_shape"], sharpened / decisions.INTERVENTION_BASE, places=3)
        self.assertEqual(gate["intervention_mean"], decisions.INTERVENTION_BASE)
        self.assertEqual(self.intervention.records[G]["count"], 1)

    async def test_named_messages_do_not_move_the_mean(self):
        """冲着它说的话那道题天然偏低（题面写明另有人管），混进均值会抬高别人的乘数。"""
        self.calls.side_effect = self.answer(0.05, addressed="answer")
        with patch.object(config, "BOT_NAMES", ["小王"]):
            await self._drive("小王 在吗")
        self.assertEqual(self.intervention.records, {})

    async def test_unavailable_judgment_leaves_the_dice_alone(self):
        self.calls.return_value = None
        with patch.object(bot.random, "random", return_value=0.999):
            await self._drive("今天好热")
        self.assertNotIn("intervention_shape", self.log.gates()[-1])


class ReceptiveWiredTest(_Base):
    """接纳度只在声明门槛过了之后才允许改变行为。"""

    def _probability(self):
        return self.log.gates()[-1]["gate_probability"]

    async def _roll(self, msg_id):
        """只让接纳度变：节奏先验的群均值会在两次之间漂移，一次只比一个变量。"""
        self._seed_buffer([(20, A, "哈"), (10, B, "嗯")])
        with patch.object(config, "OPENING_PRIOR_ENABLED", False), \
                patch.object(bot.random, "random", return_value=0.999):
            await self._drive("噢", msg_id=msg_id)
        return self._probability()

    async def test_thin_evidence_changes_nothing(self):
        baseline = await self._roll("m-base")
        for _ in range(3):      # 低于 min_evidence
            self.receptive.record(G, A, "back_off", route="banter")
        self.log.rows.clear()
        self.assertAlmostEqual(await self._roll("m-thin"), baseline, places=6)
        self.assertFalse(self.log.gates()[-1]["receptive_claimed"])

    async def test_repeated_annoyance_lowers_the_probability(self):
        baseline = await self._roll("m-base2")
        # 声明是跨成员的：得有爱接梗的人作对照，A 的嫌烦才算「与众不同」
        for peer in (B, "USER_WHEN_C"):
            for _ in range(8):
                self.receptive.record(G, peer, "keep_playful", route="banter")
        for _ in range(8):
            self.receptive.record(G, A, "back_off", route="banter")
        self.log.rows.clear()
        self.assertLess(await self._roll("m-annoyed"), baseline)
        self.assertTrue(self.log.gates()[-1]["receptive_claimed"])

    async def test_disabled_leaves_no_trace(self):
        with patch.object(config, "RECEPTIVE_ENABLED", False):
            self._seed_buffer([(20, A, "哈"), (10, B, "嗯")])
            with patch.object(bot.random, "random", return_value=0.999):
                await self._drive("噢")
        self.assertNotIn("receptive_multiplier", self.log.gates()[-1])


class NaturalFeedbackBecomesEvidenceTest(_Base):
    """「下一句骂你就是反馈」—— 但只有没人请的插嘴才说明打扰的事。"""

    def _feedback_body(self, move):
        vocab = {"feedback_target": ["bot_response", "another_person",
                                     "not_feedback", "unclear"],
                 "next_move": ["back_off", "repair_briefly", "keep_playful",
                               "continue_normally", "no_adjustment"],
                 "continuation": ["reply_now", "no_reply_needed", "unclear"],
                 "intervention": ["join_briefly", "stay_quiet"]}
        picked = {"feedback_target": "bot_response", "next_move": move,
                  "continuation": "no_reply_needed",
                  "intervention": "stay_quiet"}
        answers = []
        for name, choice in picked.items():
            others = [v for v in vocab[name] if v != choice]
            answers.append({"name": name, "type": "choice", "choice": choice,
                            "confidence": 0.9,
                            "probabilities":
                                [{"value": choice, "probability": 0.9}]
                                + [{"value": v, "probability": 0.1 / len(others)}
                                   for v in others]})
        return {"answers": answers}

    async def test_annoyance_at_an_interjection_is_recorded(self):
        self.feedback.remember(G, A, "插了一句", route="banter",
                               outgoing_id="oid-b1")
        self.calls.return_value = self._feedback_body("back_off")
        await self._drive("别老插话行不行")
        self.assertEqual(self.receptive.assess(G, A)["receptive_n"], 1.0)
        self.assertEqual([label for _, label in
                          self.receptive.records[f"{G}|{A}"]["items"]], [0])

    async def test_welcome_at_an_interjection_is_recorded(self):
        self.feedback.remember(G, A, "插了一句", route="banter",
                               outgoing_id="oid-b2")
        self.calls.return_value = self._feedback_body("keep_playful")
        await self._drive("哈哈你继续")
        self.assertEqual([label for _, label in
                          self.receptive.records[f"{G}|{A}"]["items"]], [1])

    async def test_feedback_on_an_invited_reply_is_not_intrusion_evidence(self):
        """被点名的接话挨骂，说的是内容不对，不是「别来打扰我」。"""
        self.feedback.remember(G, A, "被叫来答的", route="reply",
                               outgoing_id="oid-r1")
        self.calls.return_value = self._feedback_body("back_off")
        await self._drive("你答得不对")
        self.assertEqual(self.receptive.assess(G, A)["receptive_n"], 0.0)

    async def test_repair_is_about_content_not_intrusion(self):
        self.feedback.remember(G, A, "插了一句", route="banter",
                               outgoing_id="oid-b3")
        self.calls.return_value = self._feedback_body("repair_briefly")
        await self._drive("你说错了")
        self.assertEqual(self.receptive.assess(G, A)["receptive_n"], 0.0)


class CallbackRecallWiredTest(_Base):
    """「居然记得」只在获批时进提示词，普通接话轮行为不变。"""

    def setUp(self):
        super().setUp()
        src_ts = time.time() - 3600
        runtime.DIGESTS.groups[G] = {"summary": {"brief": "", "data": {
            "people": [{"who": "阿强", "note": QUOTE, "quote": QUOTE,
                        "keywords": ["咖啡"], "ts": src_ts,
                        "evidence": {"group": G, "sender": A,
                                     "message_id": "cb-src-1", "ts": src_ts,
                                     "quote": QUOTE},
                        "decision": {"kind": "preference",
                                     "action": "retain_quote",
                                     "message_id": "cb-src-1"}}]}}}
        self.addCleanup(lambda: runtime.DIGESTS.groups.pop(G, None))
        runtime.RELATIONS.set_nick(G, A, "阿强", source="claim")
        self.addCleanup(lambda: runtime.RENAMES.groups.pop(G, None))

    def _callback_body(self, choice):
        others = [v for v in ("bring_it_up", "hold_it") if v != choice]
        return {"answers": [{"name": "callback", "type": "choice",
                             "choice": choice, "confidence": 0.9,
                             "probabilities":
                                 [{"value": choice, "probability": 0.9}]
                                 + [{"value": v, "probability": 0.1}
                                    for v in others]}]}

    def _dynamic_layer(self):
        return "\n".join(m["content"] for msgs in self.prompts_seen
                         for m in msgs)

    async def _interject(self, decision_bodies, msg_id="cb-1"):
        self.calls.side_effect = list(decision_bodies)
        self._seed_buffer([(30, B, "哈哈"), (20, "USER_C", "笑死"),
                           (10, B, "再来")])
        with patch.object(bot.random, "random", return_value=0.0):
            await self._drive("今天的咖啡真香", msg_id=msg_id)

    async def test_approved_callback_reaches_the_prompt(self):
        # 第一次调用是插嘴仲裁，第二次是回忆判断
        await self._interject([
            {"answers": [{"name": "intervention", "type": "choice",
                          "choice": "join_briefly", "confidence": 0.9,
                          "probabilities": [
                              {"value": "join_briefly", "probability": 0.9},
                              {"value": "stay_quiet", "probability": 0.1}]}]},
            self._callback_body("bring_it_up")])
        layer = self._dynamic_layer()
        self.assertIn(QUOTE, layer)
        # 必须换成开了例外的那段插嘴说明，否则材料给了也用不上
        self.assertIn("其他旧事照旧不许翻", layer)
        self.assertTrue(self.log.gates()[-1]["callback"])
        self.assertFalse(self.receptive.callback_allowed(G, A))

    async def test_refused_callback_stays_out_of_the_prompt(self):
        await self._interject([
            {"answers": [{"name": "intervention", "type": "choice",
                          "choice": "join_briefly", "confidence": 0.9,
                          "probabilities": [
                              {"value": "join_briefly", "probability": 0.9},
                              {"value": "stay_quiet", "probability": 0.1}]}]},
            self._callback_body("hold_it")])
        layer = self._dynamic_layer()
        self.assertNotIn(QUOTE, layer)
        self.assertIn("不许翻", layer)
        self.assertFalse(self.log.gates()[-1]["callback"])
        # 判断说「先别」不消耗额度
        self.assertTrue(self.receptive.callback_allowed(G, A))

    async def test_unusable_judgement_brings_nothing_up(self):
        """新增的惊喜不许靠失败兜底。"""
        await self._interject([
            {"answers": [{"name": "intervention", "type": "choice",
                          "choice": "join_briefly", "confidence": 0.9,
                          "probabilities": [
                              {"value": "join_briefly", "probability": 0.9},
                              {"value": "stay_quiet", "probability": 0.1}]}]},
            None])
        self.assertNotIn(QUOTE, self._dynamic_layer())
        self.assertTrue(self.receptive.callback_allowed(G, A))

    async def test_quota_exhausted_asks_nothing(self):
        self.receptive.mark_callback(G, A)
        await self._interject([
            {"answers": [{"name": "intervention", "type": "choice",
                          "choice": "join_briefly", "confidence": 0.9,
                          "probabilities": [
                              {"value": "join_briefly", "probability": 0.9},
                              {"value": "stay_quiet", "probability": 0.1}]}]}])
        self.assertNotIn(QUOTE, self._dynamic_layer())
        self.assertEqual(self.calls.await_count, 1)   # 只问了插嘴仲裁

    async def test_disabled_callback_keeps_the_old_behaviour(self):
        with patch.object(config, "DECISION_CALLBACK_ENABLED", False):
            await self._interject([
                {"answers": [{"name": "intervention", "type": "choice",
                              "choice": "join_briefly", "confidence": 0.9,
                              "probabilities": [
                                  {"value": "join_briefly", "probability": 0.9},
                                  {"value": "stay_quiet",
                                   "probability": 0.1}]}]}])
        self.assertNotIn(QUOTE, self._dynamic_layer())
        self.assertIn("不许翻", self._dynamic_layer())


class InjectionGateTest(unittest.IsolatedAsyncioTestCase):
    """兜底闸：直接调 get_ai_reply 的调用方也绕不开这条规则。"""

    async def _bits(self, **kw):
        seen = []

        async def fake_model(messages, max_tokens, **_kw):
            seen.append("\n".join(m["content"] for m in messages))
            return "（应答）"

        with patch.object(bot, "call_model", fake_model):
            await bot.get_ai_reply("sid-inject", "今天的咖啡真香",
                                   personal_note="【本轮个人原话】\n- " + QUOTE,
                                   **kw)
        return seen[0]

    async def test_banter_without_approval_drops_the_quote(self):
        self.assertNotIn(QUOTE, await self._bits(is_random_banter=True))

    async def test_banter_with_approval_keeps_it(self):
        layer = await self._bits(is_random_banter=True, callback_approved=True)
        self.assertIn(QUOTE, layer)
        self.assertIn("其他旧事照旧不许翻", layer)

    async def test_approval_does_not_override_a_back_off(self):
        """对方正在喊停时，获批的回忆也不许端出来。"""
        seen = []

        async def fake_model(messages, max_tokens, **_kw):
            seen.append("\n".join(m["content"] for m in messages))
            return "（应答）"

        with patch.object(bot, "call_model", fake_model):
            await bot.get_ai_reply(
                "sid-inject-2", "别这么损我",
                personal_note="【本轮个人原话】\n- " + QUOTE,
                is_random_banter=True, callback_approved=True)
        self.assertNotIn(QUOTE, seen[0])

    async def test_normal_reply_is_unchanged(self):
        self.assertIn(QUOTE, await self._bits(is_random_banter=False))


if __name__ == "__main__":
    unittest.main()
