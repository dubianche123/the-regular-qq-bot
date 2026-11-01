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
        self.assertEqual(self.asked(), {"addressed", "next_move", "intervention", "request_kind"})
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


class PlayfulRequestTest(_Base):
    @staticmethod
    def _answer(probabilities, choice="playful"):
        def respond(text, questions):
            body = body_for(questions)
            for answer in body["answers"]:
                if answer["name"] == "request_kind":
                    answer["choice"] = choice
                    answer["probabilities"] = [
                        {"value": name, "probability": value}
                        for name, value in probabilities.items()]
            return body
        return respond

    async def test_low_sincere_and_playful_share_existing_call(self):
        self.calls.side_effect = self._answer({"sincere": 0.02, "playful": 0.9,
                                               "not_request": 0.08})
        out = await self.tracker.assess(G, A, "给我写篇外星论文", named=True, now=10.0)
        self.assertTrue(out["playful_request"])
        self.assertIn("request_kind", self.asked())
        self.assertEqual(self.calls.await_count, 1)

    async def test_not_request_or_threshold_boundary_cannot_become_a_joke(self):
        for probs, choice in (({"sincere": 0.01, "playful": 0.09, "not_request": 0.9}, "not_request"),
                              ({"sincere": 0.1, "playful": 0.8, "not_request": 0.1}, "playful"),
                              ({"sincere": 0.01, "playful": 0.8}, "playful"),
                              ({"sincere": 0.01, "playful": 0.3, "not_request": 0.69}, "playful")):
            with self.subTest(probs=probs):
                self.calls.side_effect = self._answer(probs, choice)
                out = await self.tracker.assess(G, A, "小王看看", named=True, now=10.0)
                self.assertNotIn("playful_request", out)

    async def test_unrelated_message_does_not_add_question(self):
        await self.tracker.assess(G, A, "今天好热", every_message=True, now=10.0)
        self.assertNotIn("request_kind", self.asked())


class ImageRequestTest(_Base):
    async def test_direct_request_shares_triage_and_sanitizes_image_context(self):
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        out = await self.tracker.assess(
            G, A, f"小王给这只猫画一张图 {G} {A}", named=True, want_image=True,
            history=HIST, message_id="MSG_SECRET", now=10.0)
        self.assertEqual(self.calls.await_count, 1)
        self.assertIn("image_generation", self.asked())
        self.assertEqual(out["image_generation"], "generate")
        for secret in (G, A, B, "MSG_SECRET", "h1"):
            self.assertNotIn(secret, out["image_prompt"])

    async def test_contextual_followup_is_asked_without_another_mention(self):
        self.tracker.remember(G, A, "可以画成一张海报", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        out = await self.tracker.assess(G, A, "那就来一张吧", want_image=True, now=5.0)
        self.assertEqual(out["image_generation"], "generate")
        self.assertIn("可以画成一张海报", out["image_prompt"])
        self.assertEqual(self.calls.await_count, 1)

    async def test_no_request_or_uncertain_answer_never_generates(self):
        # 图片描述、讨论画画等由题目要求选 no_generation；代码只认积极选项。
        for sentence in ("这张图片是什么", "你觉得画画难吗"):
            out = await self.tracker.assess(G, A, sentence, is_direct=True,
                                            want_image=True, now=10.0)
            self.assertNotIn("image_generation", out)
        def uncertain(text, qs):
            body = body_for(qs, image_generation="generate")
            for answer in body["answers"]:
                if answer["name"] == "image_generation":
                    answer["probabilities"] = [{"value": "generate", "probability": 0.51},
                                               {"value": "no_generation", "probability": 0.49}]
            return body
        self.calls.side_effect = uncertain
        out = await self.tracker.assess(G, A, "来一个", is_direct=True,
                                        want_image=True, now=10.0)
        self.assertNotIn("image_generation", out)

    async def test_unrelated_chat_does_not_add_an_image_question(self):
        await self.tracker.assess(G, A, "谁想画画", want_image=True,
                                  every_message=True, now=10.0)
        self.assertNotIn("image_generation", self.asked())


if __name__ == "__main__":
    unittest.main()
