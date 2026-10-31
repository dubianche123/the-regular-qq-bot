"""分诊本身：一条消息该问哪几题、一次调用、逐题容错（decisions.FeedbackTracker.assess）。

按输入计费、输出不要钱：所以每条消息只建一份 payload，按本机已知的事实挂上用得着的题，
最多一次调用。这里钉住「挂哪几题」的规矩，以及一题坏了不连累别的题。
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

import config
import wordfilter
from regular import decisions

G, A, B = "GID_TRIAGE", "USER_TRIAGE_A", "USER_TRIAGE_B"
HIST = [{"sender": B, "text": "今天加班到十点", "message_id": "h1"}]


def body_for(questions, **picks):
    answers = []
    for q in questions:
        values = [c["value"] for c in q["choices"]]
        pick = picks.get(q["name"], values[-1])
        rest = [v for v in values if v != pick]
        answers.append({"name": q["name"], "type": "choice", "choice": pick,
                        "confidence": 0.9,
                        "probabilities": [{"value": pick, "probability": 0.9}]
                        + [{"value": v, "probability": 0.1 / len(rest)} for v in rest]})
    return {"answers": answers}


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tracker = decisions.FeedbackTracker(120.0, 180.0)
        self.calls = AsyncMock(side_effect=lambda text, qs: body_for(qs))
        for target, name, value in ((decisions.providers, "call_decision", self.calls),
                                    (config, "BOT_NAMES", ["小王"])):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    def asked(self):
        return {q["name"] for q in self.calls.await_args.args[1]}

    def payload(self):
        return json.loads(self.calls.await_args.args[0])


class WhichQuestionsTest(_Base):
    async def test_nothing_to_ask_costs_nothing(self):
        self.assertEqual(await self.tracker.assess(G, A, "今天好热", history=HIST, now=10.0), {})
        self.calls.assert_not_awaited()

    async def test_every_message_asks_only_intervention(self):
        """群主 2026-10-31：每条群消息都分诊。跟机器人无关的只问「此刻插一句合不合适」。"""
        await self.tracker.assess(G, A, "今天好热", history=HIST, every_message=True, now=10.0)
        self.assertEqual(self.asked(), {"intervention"})

    async def test_named_asks_addressed_tone_and_intervention(self):
        out = await self.tracker.assess(G, A, "小王傻", named=True, history=HIST, now=10.0)
        self.assertEqual(self.asked(), {"addressed", "next_move", "intervention"})
        self.assertEqual(out["asked"], sorted(self.asked()))
        self.assertIn(out["addressed"], ("answer", "quip", "let_it_pass"))

    async def test_platform_at_never_asks_addressed(self):
        await self.tracker.assess(G, A, "在吗", is_direct=True, history=HIST, now=10.0)
        self.assertNotIn("addressed", self.asked())
        self.assertIn("next_move", self.asked())

    async def test_recall_only_when_wanted(self):
        await self.tracker.assess(G, A, "小王 上次那家店", named=True, history=HIST,
                                  want_recall=True, now=10.0)
        self.assertIn("recall", self.asked())

    async def test_reply_in_window_asks_the_feedback_set(self):
        self.tracker.remember(G, A, "加班到十点？效率堪忧", route="banter",
                              outgoing_id="o1", history=HIST, now=0.0)
        await self.tracker.assess(G, A, "小王你闭嘴", named=True, now=5.0)
        self.assertTrue({"feedback_target", "next_move", "continuation",
                         "intervention", "addressed"} <= self.asked())
        self.assertIn("bot_reply", self.payload())

    async def test_quiet_window_still_triages_a_named_message(self):
        self.tracker.remember(G, A, "插了一句", route="banter", outgoing_id="o1",
                              history=HIST, now=0.0)
        self.tracker._slot(G)["quiet_until"] = 1000.0
        self.assertEqual(await self.tracker.assess(G, B, "哈哈", now=5.0), {})
        self.calls.assert_not_awaited()
        await self.tracker.assess(G, B, "小王你怎么不说话了", named=True, now=6.0)
        self.assertIn("addressed", self.asked())
        self.assertNotIn("feedback_target", self.asked())

    async def test_nickname_candidate_goes_with_the_same_call(self):
        out = await self.tracker.assess(G, A, "小王叫我老王", named=True, history=HIST,
                                        nick_candidate="老王", now=10.0)
        self.assertIn("nickname", self.asked())
        self.assertEqual(self.payload()["nickname_candidate"], "老王")
        self.assertEqual(self.calls.await_count, 1)
        self.assertIn(out["nickname"], ("adopt", "refuse", "not_a_request"))

    async def test_listed_word_never_leaves_the_machine(self):
        """候选命中本地词表：底线已经拦了，不问、也不把它送出去。"""
        with patch.object(wordfilter, "has_hit", side_effect=lambda t: "坏名" in (t or "")):
            await self.tracker.assess(G, A, "叫我好名字", nick_candidate="坏名", now=10.0)
        self.calls.assert_not_awaited()

    async def test_interjection_probability_is_exposed(self):
        """插嘴用 p(join_briefly) 本身，不是结论（body_for 默认选最后一个 = stay_quiet 0.9）。"""
        out = await self.tracker.assess(G, A, "今天好热", history=HIST, every_message=True, now=10.0)
        self.assertEqual(out["intervention"], "stay_quiet")
        self.assertAlmostEqual(out["intervention_p"], 0.1)

    async def test_ids_stay_local(self):
        await self.tracker.assess(G, A, "小王在吗", named=True, history=HIST,
                                  message_id="MSG_SECRET", now=10.0)
        raw = self.calls.await_args.args[0]
        for ident in (G, A, B, "MSG_SECRET", "h1"):
            self.assertNotIn(ident, raw)


class OneBadAnswerTest(_Base):
    async def test_a_refused_question_does_not_sink_the_others(self):
        def refuse_nickname(text, qs):
            body = body_for(qs, addressed="quip")
            for a in body["answers"]:
                if a["name"] == "nickname":
                    a["type"] = "refusal"
            return body
        self.calls.side_effect = refuse_nickname
        out = await self.tracker.assess(G, A, "小王叫我某某", named=True, history=HIST,
                                        nick_candidate="某某", now=10.0)
        self.assertIsNone(out["nickname"])
        self.assertEqual(out["addressed"], "quip")

    def test_broken_structure_voids_everything(self):
        qs = [{"name": "x", "choices": [{"value": "a"}, {"value": "b"}]}]
        self.assertIsNone(decisions.parse_each({"answers": "nope"}, qs))
        self.assertIsNone(decisions.parse_each(
            {"answers": [{"name": "x", "type": "choice", "choice": "a"}] * 2}, qs))

    def test_parse_answers_stays_all_or_nothing(self):
        qs = [{"name": n, "choices": [{"value": "a"}, {"value": "b"}]} for n in ("x", "y")]
        body = {"answers": [{"name": "x", "type": "choice", "choice": "a"},
                            {"name": "y", "type": "refusal"}]}
        self.assertIsNone(decisions.parse_answers(body, qs))
        self.assertEqual(decisions.parse_each(body, qs)["x"]["choice"], "a")
        self.assertIsNone(decisions.parse_each(body, qs)["y"])


if __name__ == "__main__":
    unittest.main()
