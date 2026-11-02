"""叫到它 ≠ 欠它一句：分诊决定点名消息怎么走（2026-10-31）。

群主的原话：「@ 一定回没问题，这是显性信号；但文字带小王的不代表全部都要回 ——
有时候说小王傻，有时候说小王聪明，有时候说小王怎么样了。」之前的做法是给生成
模型一个 SKIP 出口，「说不说」由写回复的那一步顺手决定。现在：

  · 平台 @ → 一定答，分诊里连 addressed 这题都不问；
  · 正文里有名字 → 分诊 addressed：answer 答 / quip 判断说接得上才短接一句 /
    let_it_pass 当普通消息（2026-11-02 起：交给小王自己定接不接）；
  · 判断不可用 → 照答（被叫到却被无视，比多答一句难受）；
    唯一例外：判断有把握地读出「收敛」，就不回「好的我闭嘴」；
  · 每条消息最多一次判断调用；
  · 起名：本地模式只提议，判断能否决（诱饵 / 误触发），不能单独批准；
  · 翻旧账：判断说 look_up 才翻；
  · 被点名该答的那几类，沉默权在「判断」；没叫它的（含 let_it_pass）沉默权还给小王，
    写〔不接〕—— 不是旧的 SKIP 兜底：被叫到时提示词里没有这条路。
"""
import unittest
from unittest.mock import AsyncMock, patch

import bot
import config
import prompts
import runtime
from test_when_to_intervene import A, G, _Base


# 没指定的题答一个「什么都不改变」的选项。不能默认第一个：next_move 的第一个
# 选项恰好是 back_off，会让每条消息都像在叫它收敛。
NEUTRAL = {"feedback_target": "not_feedback", "next_move": "no_adjustment",
           "addressed": "answer", "recall": "no_need", "nickname": "adopt",
           "request_kind": "not_request",
           "lookup": "none"}


def answering(mapping):
    """按实际问到的题作答。mapping[题名] = 选项（有把握）/ None（拿不准，边际 0）。
    没列出的题答 NEUTRAL 里那个选项，同样有把握。"""
    async def respond(text, questions):
        answers = []
        for q in questions:
            values = [c["value"] for c in q["choices"]]
            if q["name"] in mapping and mapping[q["name"]] is None:
                even = 1.0 / len(values)
                probs = [{"value": v, "probability": even} for v in values]
                answers.append({"name": q["name"], "type": "choice", "choice": values[0],
                                "confidence": 0.0, "probabilities": probs})
                continue
            pick = mapping.get(q["name"], NEUTRAL.get(q["name"], values[0]))
            rest = [v for v in values if v != pick]
            probs = ([{"value": pick, "probability": 0.9}]
                     + [{"value": v, "probability": 0.1 / len(rest)} for v in rest])
            answers.append({"name": q["name"], "type": "choice", "choice": pick,
                            "confidence": 0.9, "probabilities": probs})
        return {"answers": answers}
    return respond


class _Named(_Base):
    def setUp(self):
        super().setUp()
        ctx = patch.object(config, "BOT_NAMES", ["小王"])
        ctx.start()
        self.addCleanup(ctx.stop)

    def asked(self, call=-1):
        return {q["name"] for q in self.calls.await_args_list[call].args[1]}

    def gate(self):
        return self.log.gates()[-1]

    def sent(self, msg):
        return msg.reply.await_count > 0


class PlatformAtTest(_Named):
    async def test_at_always_answers_and_never_asks_addressed(self):
        self.calls.side_effect = answering({})
        msg = await self._drive("随便说点", is_at=True)
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "direct_address")
        self.assertNotIn("addressed", self.asked())


class NamedRoutingTest(_Named):
    async def test_answer_replies(self):
        self.calls.side_effect = answering({"addressed": "answer"})
        msg = await self._drive("小王 今天星期几")
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_answer")
        self.assertIn("addressed", self.asked())

    async def test_let_it_pass_is_an_ordinary_message(self):
        """「这下小王惨了」：名字出现了，但没人在跟它说话 —— 跟别的没叫它的消息一样，接不接它自己定。"""
        self.calls.side_effect = answering({"addressed": "let_it_pass"})
        self.reply_text = "〔不接〕"
        msg = await self._drive("这下小王惨了")
        self.assertFalse(self.sent(msg))
        self.assertIn(prompts.BANTER_MARK, self.prompts_seen[-1][-1]["content"])
        self.assertEqual((self.gate()["route"], self.gate()["reason"]), ("banter", "declined"))

    async def test_quip_replies_short_whatever_the_interjection_question_says(self):
        """「小王我真的好喜欢你啊」（2026-11-01 被吞）：addressed 已判过短接一句接得上，
        就短接，不再问小王接不接。"""
        self.calls.side_effect = answering({"addressed": "quip"})
        msg = await self._drive("小王我真的好喜欢你啊")
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_quip")
        turn = self.prompts_seen[-1][-1]["content"]
        self.assertIn("有人在逗你", turn)
        self.assertNotIn(prompts.BANTER_MARK, turn)

    async def test_quip_stays_silent_in_the_quiet_window(self):
        """刚被叫收敛：暂停窗内被逗也不接。"""
        self.calls.side_effect = answering({"addressed": "quip"})
        with patch.object(self.feedback, "quiet", return_value=True):
            msg = await self._drive("小王你是傻逼")
        self.assertFalse(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_quip_quiet")

    async def test_unavailable_judge_falls_back_to_answering(self):
        self.calls.return_value = None
        msg = await self._drive("小王 你在吗")
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_fallback")

    async def test_uncertain_judge_falls_back_to_answering(self):
        self.calls.side_effect = answering({"addressed": None})
        msg = await self._drive("小王 你在吗")
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_fallback")

    async def test_confident_back_off_beats_the_fallback(self):
        """「小王闭嘴」：判断拿不准该不该答，却有把握是在叫它收敛 —— 不回「好的我闭嘴」。"""
        self.calls.side_effect = answering({"addressed": None, "next_move": "back_off"})
        msg = await self._drive("小王闭嘴")
        self.assertFalse(self.sent(msg))
        self.assertEqual(self.prompts_seen, [])        # 连问都不问它
        self.assertEqual(self.gate()["reason"], "back_off")

    async def test_one_call_per_message_even_when_it_chimes_in(self):
        self.calls.side_effect = answering({"addressed": "let_it_pass"})
        msg = await self._drive("小王这人有意思")
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.calls.await_count, 1)
        self.assertNotIn("intervention", self.asked())


class NicknameVetoTest(_Named):
    """本地模式只提议候选；判断能否决，不能单独批准。"""

    def setUp(self):
        super().setUp()
        self.judge = AsyncMock(return_value=True)
        ctx = patch.object(bot, "judge_nick", self.judge)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.addCleanup(lambda: runtime.RELATIONS.records.pop(f"{G}|{A}", None))

    def nick(self):
        return (runtime.RELATIONS.get(G, A, create=False) or {}).get("nick")

    async def test_misfire_falls_through_to_conversation(self):
        self.calls.side_effect = answering({"nickname": "not_a_request",
                                            "addressed": "answer"})
        msg = await self._drive("小王叫外卖")
        self.assertIsNone(self.nick())
        self.assertTrue(self.sent(msg))
        self.assertEqual(self.gate()["reason"], "named_answer")

    async def test_confident_refusal_skips_the_audit_call_and_writes_nothing(self):
        self.calls.side_effect = answering({"nickname": "refuse"})
        msg = await self._drive("小王叫我习远平")
        self.assertIsNone(self.nick())
        self.judge.assert_not_awaited()
        self.assertIn("不敢往上写", str(msg.reply.await_args))

    async def test_uncertain_verdict_keeps_the_old_floor(self):
        """拿不准不等于否决：照旧走词表 + 审核梯队那几道。"""
        self.calls.side_effect = answering({"nickname": None})
        await self._drive("小王叫我老王")
        self.judge.assert_awaited()
        self.assertEqual(self.nick(), "老王")

    async def test_judge_cannot_approve_past_the_audit(self):
        self.calls.side_effect = answering({"nickname": "adopt"})
        self.judge.return_value = False
        await self._drive("小王叫我姚元文")
        self.assertIsNone(self.nick())


class RecallGateTest(_Named):
    async def test_recall_only_when_triage_says_look_up(self):
        for verdict, expected in (("look_up", 1), ("no_need", 0)):
            with self.subTest(verdict=verdict), \
                    patch.object(bot.recall_mod, "build_note",
                                 side_effect=lambda *a, **k: "") as build:
                self.calls.side_effect = answering({"addressed": "answer",
                                                    "recall": verdict})
                await self._drive("小王 上次阿强说的那家店叫啥")
                self.assertEqual(build.call_count, expected)


class SilenceIsAJudgmentTest(unittest.TestCase):
    """沉默权（2026-10-22 群主给的）还在：从「生成」挪到了「判断」。"""

    def test_no_prompt_offers_a_skip_exit(self):
        for name in ("PROMPT_NORMAL", "PROMPT_PROACTIVE"):
            self.assertNotIn("SKIP", getattr(prompts, name), name)

    def test_generation_has_no_skip_sentinel(self):
        self.assertFalse(hasattr(bot, "MODEL_SKIP"))


if __name__ == "__main__":
    unittest.main()
