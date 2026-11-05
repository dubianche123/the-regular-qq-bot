"""没叫它的消息：交给小王自己定接不接（2026-11-02 起，见 docs/when-to-reply.md）。

以前这里测的是那枚骰子（节奏先验、插嘴判断、接纳度三个乘数）有没有接在插嘴门上。
骰子拆了，这一组证明新的接线：

  - 没 @、没叫名字的消息真的送到了小王那里，带着「接不接你自己定」和换过框的那句；
  - 它写〔不接〕就一个字都不发，没生成出来也不拿兜底话凑；
  - 代码只交代事实：「你刚开过口」「TA 不爱被搭话」、背景里它自己刚说过的话；
  - 收敛暂停窗内一律不问；跟它无关的消息不再为它发分诊调用；
  - 「下一句骂你」照旧变成跨天的证据；「居然记得」只在获批、而且它真开了口时才算数。
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
    reply_text = "（模型应答）"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = _Recorder(os.path.join(self._tmp.name, "ix"),
                             enabled=True, keep_days=0)
        self.addCleanup(self.log.close)
        self.feedback = decisions.FeedbackTracker(120.0, 180.0)
        self.receptive = receptive.Receptiveness(min_evidence=4.0)
        self.calls = AsyncMock(return_value=None)
        self.prompts_seen = []

        async def fake_model(messages, max_tokens, **kw):
            self.prompts_seen.append(messages)
            return self.reply_text

        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", self.feedback),
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
        self.addCleanup(lambda: bot.own_lines.pop(G, None))
        self.addCleanup(lambda: bot.processed_msg_ids.clear())
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{A}", None))
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{B}", None))
        self.addCleanup(runtime.SESSIONS.clear_group, G)

    async def _drive(self, text, sender=A, is_at=False, msg_id=None, scene=None):
        fake_self = SimpleNamespace(
            robot=SimpleNamespace(id="BOT_WHEN", name="regular"))
        msg = SimpleNamespace(
            id=msg_id or "msg-" + os.urandom(5).hex(), content=text,
            group_openid=G, author=SimpleNamespace(member_openid=sender),
            attachments=[], mentions=[], timestamp=None, _scene=dict(scene or {}),
            reply=AsyncMock(return_value={"id": "rcpt-" + os.urandom(3).hex()}))
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return msg

    def _seed_buffer(self, spec):
        """spec: [(距今秒数, 发言人, 文本), ...]"""
        now = time.time()
        bot.group_buffers[G].clear()
        for ago, who, text in spec:
            bot.group_buffers[G].append(
                {"sender": who, "text": text, "time": now - ago,
                 "message_id": "seed-" + os.urandom(3).hex()})

    def _session(self, who=A):
        return (runtime.SESSIONS._sessions.get(f"{G}_{who}") or {}).get("messages", [])

    def _last_prompt(self):
        return "\n".join(m["content"] for m in self.prompts_seen[-1])


class UnpromptedTurnTest(_Base):
    async def test_unnamed_message_goes_to_xiaowang(self):
        msg = await self._drive("今天好热")
        self.assertEqual(len(self.prompts_seen), 1)
        last_user = self.prompts_seen[-1][-1]["content"]
        self.assertIn(prompts.BANTER_MARK, last_user)
        self.assertIn("〔不接〕", last_user)
        self.assertTrue(last_user.endswith(prompts.PROMPT_UNPROMPTED_LINE.format(text="今天好热")))
        msg.reply.assert_awaited()
        gate = self.log.gates()[-1]
        self.assertEqual((gate["route"], gate["selected"], gate["reason"]), ("banter", True, "spoke"))
        self.assertNotIn("gate_probability", gate)
        # 会话里记的是原话，不是换过框的那句
        self.assertEqual(self._session()[-2], {"role": "user", "content": "今天好热"})

    async def test_pass_sends_nothing_and_records_nothing(self):
        self.reply_text = "〔不接〕"
        msg = await self._drive("你们晚上吃啥")
        msg.reply.assert_not_awaited()
        gate = self.log.gates()[-1]
        self.assertEqual((gate["selected"], gate["reason"]), (False, "declined"))
        self.assertEqual(self._session(), [])
        self.assertNotIn(G, bot.group_last_random_reply)

    async def test_failed_generation_sends_no_fallback(self):
        self.reply_text = ""
        msg = await self._drive("你们晚上吃啥")
        msg.reply.assert_not_awaited()

    async def test_unrelated_message_costs_no_triage_call(self):
        await self._drive("今天好热")
        self.calls.assert_not_awaited()

    async def test_just_spoke_is_told_not_enforced(self):
        await self._drive("今天好热")
        with patch.object(config, "BANTER_COOLDOWN_SECONDS", 999.0):
            msg = await self._drive("是啊热死了", sender=B, msg_id="msg-second")
        self.assertIn(prompts.PROMPT_BANTER_RECENT, self._last_prompt())
        msg.reply.assert_awaited()

    async def test_first_unprompted_turn_has_no_recent_hint(self):
        with patch.object(config, "BANTER_COOLDOWN_SECONDS", 999.0):
            await self._drive("今天好热")
        self.assertNotIn(prompts.PROMPT_BANTER_RECENT, self._last_prompt())

    async def test_quiet_window_asks_nothing(self):
        self.feedback._slot(G)["quiet_until"] = time.time() + 60
        msg = await self._drive("今天好热")
        self.assertEqual(self.prompts_seen, [])
        msg.reply.assert_not_awaited()
        self.assertEqual(self.log.gates()[-1]["reason"], "feedback_quiet")

    async def test_direct_address_is_not_an_unprompted_turn(self):
        await self._drive("在吗", is_at=True)
        self.assertNotIn(prompts.BANTER_MARK, self._last_prompt())
        gate = self.log.gates()[-1]
        self.assertEqual((gate["route"], gate["selected"]), ("direct", True))

    async def test_its_own_recent_lines_are_in_the_background(self):
        """它得看得见自己刚说了什么，才分得出眼前这句是不是在接它的话。"""
        self._seed_buffer([(30, A, "小王，你选一个股市买")])
        bot._note_own_line(G, "只选A股我偏美的集团")
        await self._drive("快把老王的钱都买进去")
        layer = self._last_prompt()
        self.assertIn("- 你：只选A股我偏美的集团", layer)
        self.assertLess(layer.index("你选一个股市买"), layer.index("- 你：只选A股"))


class UnwelcomeHintTest(_Base):
    """接纳度只在声明门槛过了之后才开口，而且只是一句交代。"""

    async def test_thin_evidence_says_nothing(self):
        for _ in range(3):      # 低于 min_evidence
            self.receptive.record(G, A, "back_off", route="banter")
        await self._drive("噢")
        self.assertNotIn(prompts.PROMPT_BANTER_UNWELCOME, self._last_prompt())

    async def test_repeated_annoyance_is_told(self):
        # 声明是跨成员的：得有爱接梗的人作对照，A 的嫌烦才算「与众不同」
        for peer in (B, "USER_WHEN_C"):
            for _ in range(8):
                self.receptive.record(G, peer, "keep_playful", route="banter")
        for _ in range(8):
            self.receptive.record(G, A, "back_off", route="banter")
        await self._drive("噢")
        self.assertIn(prompts.PROMPT_BANTER_UNWELCOME, self._last_prompt())

    async def test_disabled_says_nothing(self):
        for peer in (B, "USER_WHEN_C"):
            for _ in range(8):
                self.receptive.record(G, peer, "keep_playful", route="banter")
        for _ in range(8):
            self.receptive.record(G, A, "back_off", route="banter")
        with patch.object(config, "RECEPTIVE_ENABLED", False):
            await self._drive("噢")
        self.assertNotIn(prompts.PROMPT_BANTER_UNWELCOME, self._last_prompt())


class NaturalFeedbackBecomesEvidenceTest(_Base):
    """「下一句骂你就是反馈」—— 但只有没人请的插嘴才说明打扰的事。"""

    def _feedback_body(self, move):
        vocab = {"feedback_target": ["bot_response", "another_person",
                                     "not_feedback", "unclear"],
                 "next_move": ["back_off", "repair_briefly", "keep_playful",
                               "continue_normally", "no_adjustment"]}
        picked = {"feedback_target": "bot_response", "next_move": move}
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

    async def test_back_off_opens_the_quiet_window(self):
        self.feedback.remember(G, A, "插了一句", route="banter",
                               outgoing_id="oid-b4")
        self.calls.return_value = self._feedback_body("back_off")
        await self._drive("别老插话行不行")
        self.assertTrue(self.feedback.quiet(G))


class CallbackRecallWiredTest(_Base):
    """「居然记得」只在获批时进提示词；它写〔不接〕就不算用掉。"""

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

    async def _unprompted(self, decision_bodies, msg_id="cb-1"):
        # 跟它无关的消息不再有分诊调用：这里唯一的一次原生调用就是回忆判断
        self.calls.side_effect = list(decision_bodies)
        self._seed_buffer([(30, B, "哈哈"), (20, "USER_C", "笑死"),
                           (10, B, "再来")])
        await self._drive("今天的咖啡真香", msg_id=msg_id)

    async def test_approved_callback_reaches_the_prompt(self):
        await self._unprompted([self._callback_body("bring_it_up")])
        layer = self._last_prompt()
        self.assertIn(QUOTE, layer)
        # 必须换成开了例外的那段说明，否则材料给了也用不上
        self.assertIn("其他旧事照旧不许翻", layer)
        self.assertTrue(self.log.gates()[-1]["callback"])
        self.assertFalse(self.receptive.callback_allowed(G, A))

    async def test_declining_to_speak_does_not_use_up_the_callback(self):
        self.reply_text = "〔不接〕"
        await self._unprompted([self._callback_body("bring_it_up")])
        self.assertTrue(self.receptive.callback_allowed(G, A))

    async def test_refused_callback_stays_out_of_the_prompt(self):
        await self._unprompted([self._callback_body("hold_it")])
        layer = self._last_prompt()
        self.assertNotIn(QUOTE, layer)
        self.assertIn("不许翻", layer)
        self.assertFalse(self.log.gates()[-1]["callback"])
        # 判断说「先别」不消耗额度
        self.assertTrue(self.receptive.callback_allowed(G, A))

    async def test_unusable_judgement_brings_nothing_up(self):
        """新增的惊喜不许靠失败兜底。"""
        await self._unprompted([None])
        self.assertNotIn(QUOTE, self._last_prompt())
        self.assertTrue(self.receptive.callback_allowed(G, A))

    async def test_quota_exhausted_asks_nothing(self):
        self.receptive.mark_callback(G, A)
        await self._unprompted([])
        self.assertNotIn(QUOTE, self._last_prompt())
        self.calls.assert_not_awaited()

    async def test_disabled_callback_keeps_the_plain_note(self):
        with patch.object(config, "DECISION_CALLBACK_ENABLED", False):
            await self._unprompted([])
        self.assertNotIn(QUOTE, self._last_prompt())
        self.assertIn("不许翻", self._last_prompt())


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

    async def test_unprompted_without_approval_drops_the_quote(self):
        self.assertNotIn(QUOTE, await self._bits(unprompted=True))

    async def test_unprompted_with_approval_keeps_it(self):
        layer = await self._bits(unprompted=True, callback_approved=True)
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
                unprompted=True, callback_approved=True)
        self.assertNotIn(QUOTE, seen[0])

    async def test_normal_reply_is_unchanged(self):
        self.assertIn(QUOTE, await self._bits(unprompted=False))


class BurstTest(_Base):
    """2026-11-04 11:49「你发的啥」「截图看看」两秒内各回了一句：前一句接歪了（问的是罗老板），
    后一句看见自己刚回过，就接着歪。同一个人连着发的几句，只拿最后一句来问。"""

    async def test_same_person_burst_is_asked_once_with_both_lines_in_view(self):
        with patch.object(config, "BANTER_BURST_SECONDS", 0.3):
            first = asyncio.create_task(self._drive("你发的啥"))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(self._drive("截图看看"))
            await asyncio.gather(first, second)
        self.assertEqual(len(self.prompts_seen), 1)
        self.assertTrue(self.prompts_seen[0][-1]["content"].endswith("截图看看"))
        self.assertIn("当前说话人：你发的啥", self._last_prompt())
        self.assertIn("said_more", [g["reason"] for g in self.log.gates()])

    async def test_different_people_are_each_asked(self):
        with patch.object(config, "BANTER_BURST_SECONDS", 0.2):
            first = asyncio.create_task(self._drive("你发的啥", sender=B))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(self._drive("截图看看", sender=A))
            await asyncio.gather(first, second)
        self.assertEqual(len(self.prompts_seen), 2)


class OwnDrawingInViewTest(_Base):
    """2026-11-04 11:43「这你蛤我」：背景只剩最近几句，它 11:38 画的奶蛙被挤掉了，
    它只看见自己说过「只看到图片标记」，就说自己没看见那只蛙。它十分钟内画的图留在背景里。"""

    async def test_recent_drawing_stays_in_the_background(self):
        now = time.time()
        bot.own_lines[G].append({"time": now - 300, "text": "[图：一只水墨奶蛙蹲在荷叶上]"})
        bot.own_lines[G].append({"time": now - 900, "text": "[图：一只更早画的奶龙]"})
        self._seed_buffer([(60, B, "[图片]"), (40, B, "小王这是不是奶龙")])
        await self._drive("这你蛤我")
        prompt = self._last_prompt()
        self.assertIn("- 你：[图：一只水墨奶蛙蹲在荷叶上]", prompt)
        self.assertNotIn("更早画的奶龙", prompt)
        self.assertLess(prompt.index("水墨奶蛙"), prompt.index("小王这是不是奶龙"))

    async def test_recent_animation_keeps_its_motion_in_the_background(self):
        now = time.time()
        bot.own_lines[G].append({"time": now - 300, "text": "[动图：一只鸡原地运篮球，固定镜头循环]"})
        self._seed_buffer([(60, B, "聊点别的"), (40, B, "这球运得不错")])
        await self._drive("这鸡动作挺丝滑")
        self.assertIn("- 你：[动图：一只鸡原地运篮球，固定镜头循环]", self._last_prompt())


class MovedOnTest(_Base):
    """2026-11-04：人一多，小王一下冒出两三句（四天 39 次，约 25 次里有没叫它的插话）。
    插话写好了、发出去之前，它自己已经说过别的，就不发；只是别人说了话，就引用着回。
    欠人的那句照发。"""

    def _meanwhile(self, *, other=False, itself=False):
        """生成这一句的那几秒里：另一个人说了话 / 它自己回了别的。"""
        original = bot.call_model

        async def model(messages, max_tokens, **kw):
            if other:
                bot.group_buffers[G].append({"sender": B, "text": "我先撤了", "time": time.time(),
                                             "message_id": "later-" + os.urandom(3).hex()})
            if itself:
                bot.own_lines[G].append({"time": time.time(), "text": "刚回了别人一句"})
            return await original(messages, max_tokens, **kw)
        return patch.object(bot, "call_model", model)

    async def test_banter_is_dropped_when_it_already_spoke_meanwhile(self):
        with self._meanwhile(itself=True):
            msg = await self._drive("今天好热")
        msg.reply.assert_not_awaited()
        self.assertEqual(self.log.gates()[-1]["reason"], "stale")
        self.assertEqual(self._session(), [])

    async def test_banter_talked_over_by_others_goes_out_quoted(self):
        with self._meanwhile(other=True):
            msg = await self._drive("今天好热", scene={"msg_idx": "REFIDX_hot=="})
        self.assertEqual(msg.reply.await_args.kwargs["message_reference"]["message_id"],
                         "REFIDX_hot==")
        self.assertEqual(self.log.gates()[-1]["reason"], "spoke")

    async def test_owed_banter_still_goes_out_and_quotes(self):
        msg = SimpleNamespace(
            id="owed-1", content="？", group_openid=G, author=SimpleNamespace(member_openid=A),
            attachments=[], mentions=[], timestamp=None, _scene={"msg_idx": "REFIDX_owed=="},
            reply=AsyncMock(return_value={"id": "rcpt-owed"}))
        bot.group_buffers[G].append({"sender": A, "text": "？", "time": time.time(),
                                     "message_id": "owed-1"})
        with self._meanwhile(itself=True):
            await bot.reply_unprompted(msg, f"{G}_{A}", "？", owed=True)
        self.assertEqual(msg.reply.await_args.kwargs["message_reference"]["message_id"],
                         "REFIDX_owed==")

    async def test_addressed_reply_quotes_when_talked_over(self):
        with self._meanwhile(other=True):
            msg = await self._drive("小王你说呢", is_at=True, scene={"msg_idx": "REFIDX_q=="})
        self.assertEqual(msg.reply.await_args.kwargs["message_reference"]["message_id"], "REFIDX_q==")

    async def test_quiet_exchange_is_a_plain_reply(self):
        msg = await self._drive("小王你说呢", is_at=True, scene={"msg_idx": "REFIDX_q=="})
        self.assertNotIn("message_reference", msg.reply.await_args.kwargs)

if __name__ == "__main__":
    unittest.main()
