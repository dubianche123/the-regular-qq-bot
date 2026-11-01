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


class ContextualContinuationTest(_Base):
    async def test_recent_messages_after_bot_reply_keep_speakers_and_chronology(self):
        before = [{"sender": B, "text": "上一个话题", "message_id": "old", "time": 99.0}]
        self.tracker.remember(G, B, "上一句回复", route="reply",
                              outgoing_id="o1", history=before, now=100.0)
        history = before + [
            {"sender": A, "text": f"请讲讲星星 {A}", "message_id": "part", "time": 101.0},
            {"sender": B, "text": "另一位群友的话", "message_id": "other", "time": 102.0},
            {"sender": A, "text": "小王", "message_id": "current", "time": 103.0},
            {"sender": A, "text": "未来消息", "message_id": "future", "time": 104.0},
        ]
        await self.tracker.assess(G, A, "小王", named=True, history=history,
                                  message_id="current", now=103.0)
        payload = self.payload()
        self.assertEqual([r["text"] for r in payload["recent_human_messages"]], ["上一个话题"])
        self.assertEqual(payload["bot_reply"]["text"], "上一句回复")
        after = payload["messages_after_bot_reply"]
        self.assertEqual(len(after), 2)
        self.assertIn("请讲讲星星", after[0]["text"])
        self.assertEqual(after[1]["text"], "另一位群友的话")
        self.assertEqual(after[0]["speaker"], payload["latest_message"]["speaker"])
        self.assertNotEqual(after[1]["speaker"], payload["latest_message"]["speaker"])
        for secret in (G, A, B, "current", "future", "part", "other"):
            self.assertNotIn(secret, self.calls.await_args.args[0])

    async def test_post_reply_context_is_bounded_fresh_and_filters_sensitive_text(self):
        self.tracker.remember(G, B, "上一句", route="reply", outgoing_id="o1", now=100.0)
        history = [{"sender": A, "text": f"第 {i} 句", "message_id": str(i), "time": 101.0 + i}
                   for i in range(6)]
        history += [{"sender": A, "text": "不可外发", "message_id": "secret", "time": 108.0}]
        with patch.object(wordfilter, "has_hit", side_effect=lambda t: "不可外发" in (t or "")):
            await self.tracker.assess(G, A, "小王", named=True, history=history, now=110.0)
        after = self.payload()["messages_after_bot_reply"]
        self.assertLessEqual(len(after), 4)
        self.assertNotIn("不可外发", str(after))
        self.assertNotIn("第 0 句", str(after))

        # bot 回复过期后不提供该历史字段，也不能将未来输入误当作已有消息。
        await self.tracker.assess(G, A, "小王", named=True,
                                  history=[{"sender": A, "text": "未来", "time": 222.0}], now=221.0)
        self.assertNotIn("messages_after_bot_reply", self.payload())

    async def test_generated_image_prompt_includes_recent_request_after_reply(self):
        self.tracker.remember(G, B, "上一句", route="reply", outgoing_id="o1", now=100.0)
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        result = await self.tracker.assess(
            G, A, "小王", named=True, want_image=True, message_id="current", now=103.0,
            history=[{"sender": A, "text": "给我画只猫", "message_id": "part", "time": 101.0}])
        self.assertIn("给我画只猫", result["image_prompt"])
        self.assertNotIn(A, result["image_prompt"])

    async def test_image_sees_only_current_speakers_pending_messages_after_bot_reply(self):
        self.tracker.remember(G, B, "之前的回答", route="reply", outgoing_id="o1", now=100.0)
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        history = [
            {"sender": A, "text": "已答复的旧请求", "message_id": "old", "time": 99.0},
            {"sender": A, "text": f"画一幅风景 {A}", "message_id": "own", "time": 101.0},
            {"sender": B, "text": "别人的请求", "message_id": "other", "time": 102.0},
            {"sender": A, "text": "当前消息", "message_id": "current", "time": 103.0},
            {"sender": A, "text": "未来消息", "message_id": "future", "time": 104.0},
        ]
        result = await self.tracker.assess(G, A, "小王", named=True, want_image=True,
                                           history=history, message_id="current", now=103.0)
        messages = self.payload()["current_speaker_messages"]
        self.assertEqual(len(messages), 2)
        self.assertIn("画一幅风景", messages[0]["text"])
        self.assertEqual(messages[-1], {"text": "小王"})
        self.assertNotIn(A, str(messages))
        self.assertIn("画一幅风景", result["image_prompt"])
        self.assertNotIn("别人的请求", str(messages))
        self.assertNotIn("已答复的旧请求", str(messages))
        self.assertNotIn("未来消息", str(messages))

    async def test_image_pending_messages_without_bot_reply_respect_window_and_identity(self):
        history = [
            {"sender": A, "text": "已过期", "message_id": "old", "time": 1.0},
            {"sender": B, "text": "别人说的话", "message_id": "other", "time": 102.0},
            {"sender": A, "text": "本人前一句", "message_id": "own", "time": 103.0},
        ]
        await self.tracker.assess(G, A, "小王", named=True, want_image=True,
                                  history=history, message_id="current", now=125.0)
        self.assertEqual(self.payload()["current_speaker_messages"], [
            {"text": "本人前一句"}, {"text": "小王"}])

    async def test_image_pending_messages_do_not_send_sensitive_text(self):
        history = [{"sender": A, "text": "不可外发", "time": 101.0}]
        with patch.object(wordfilter, "has_hit", side_effect=lambda text: "不可外发" in text):
            await self.tracker.assess(G, A, "小王", named=True, want_image=True,
                                      history=history, now=103.0)
        self.assertEqual(self.payload()["current_speaker_messages"], [{"text": "小王"}])
        self.assertNotIn("不可外发", self.calls.await_args.args[0])

    async def test_image_pending_messages_exclude_pre_restart_history_without_receipt(self):
        history = [
            {"sender": A, "text": "重启前可能已答的请求", "time": 101.0},
            {"sender": A, "text": "启动后的新请求", "time": 103.0},
        ]
        await self.tracker.assess(G, A, "小王", named=True, want_image=True,
                                  history=history, image_history_since=102.0, now=105.0)
        self.assertEqual(self.payload()["current_speaker_messages"], [
            {"text": "启动后的新请求"}, {"text": "小王"}])

    async def test_image_claim_excludes_handled_request_but_keeps_a_new_one(self):
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        first = await self.tracker.assess(
            G, A, "小王画一只猫", named=True, want_image=True, now=100.0)
        self.assertIsNone(first["image_boundary"])
        self.assertTrue(self.tracker.claim_image(
            G, A, message_time=100.0, expected_boundary=first["image_boundary"]))
        history = [
            {"sender": A, "text": "小王画一只猫", "message_id": "old", "time": 100.0},
            {"sender": A, "text": "重画一只蓝色的", "message_id": "new", "time": 101.0},
        ]
        second = await self.tracker.assess(
            G, A, "小王", named=True, want_image=True, history=history, now=102.0)
        self.assertEqual(second["image_boundary"], 100.0)
        self.assertTrue(self.payload()["previous_image_request_handled"])
        self.assertEqual(self.payload()["current_speaker_messages"], [
            {"text": "重画一只蓝色的"}, {"text": "小王"}])
        self.assertTrue(self.tracker.claim_image(
            G, A, message_time=102.0, expected_boundary=second["image_boundary"]))

    async def test_previous_image_fact_is_fresh_and_person_specific(self):
        self.assertTrue(self.tracker.claim_image(
            G, A, message_time=100.0, expected_boundary=None))
        await self.tracker.assess(G, B, "小王", named=True, want_image=True, now=101.0)
        self.assertFalse(self.payload()["previous_image_request_handled"])
        await self.tracker.assess(G, A, "小王", named=True, want_image=True, now=221.0)
        self.assertFalse(self.payload()["previous_image_request_handled"])

    async def test_in_flight_image_claim_is_atomic_and_user_scoped(self):
        self.calls.side_effect = lambda text, qs: body_for(qs, image_generation="generate")
        first = await self.tracker.assess(G, A, "画一只猫", named=True,
                                          want_image=True, now=100.0)
        second = await self.tracker.assess(G, A, "怎么还没好", named=True,
                                           want_image=True, now=101.0)
        self.assertTrue(self.tracker.claim_image(
            G, A, message_time=100.0, expected_boundary=first["image_boundary"]))
        # 两次判断都在第一条执行前取到了同一旧边界，后到的不能重复执行。
        self.assertFalse(self.tracker.claim_image(
            G, A, message_time=101.0, expected_boundary=second["image_boundary"]))
        self.assertTrue(self.tracker.claim_image(
            G, B, message_time=101.0, expected_boundary=None))
        self.assertTrue(self.tracker.claim_image(
            "another-group", A, message_time=101.0, expected_boundary=None))

    async def test_image_boundary_cannot_be_rolled_back_by_late_event(self):
        self.assertTrue(self.tracker.claim_image(
            G, A, message_time=100.0, expected_boundary=None))
        self.assertFalse(self.tracker.claim_image(
            G, A, message_time=99.0, expected_boundary=100.0))
        self.assertFalse(self.tracker.claim_image(
            G, A, message_time=float("nan"), expected_boundary=100.0))

    async def test_image_pending_messages_do_not_cross_known_reply_when_quiet(self):
        self.tracker.remember(G, A, "已经回答", route="reply", outgoing_id="o1", now=100.0)
        self.tracker._slot(G)["quiet_until"] = 200.0
        history = [
            {"sender": A, "text": "已回答的请求", "time": 99.0},
            {"sender": A, "text": "新的请求", "time": 101.0},
        ]
        await self.tracker.assess(G, A, "小王", named=True, want_image=True,
                                  history=history, now=105.0)
        self.assertNotIn("bot_reply", self.payload())
        self.assertEqual(self.payload()["current_speaker_messages"], [
            {"text": "新的请求"}, {"text": "小王"}])

    async def test_named_supplement_retains_previous_bot_reply_and_directed_fact(self):
        self.tracker.remember(G, A, "我不确定是什么", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        self.calls.side_effect = lambda text, qs: body_for(
            qs, addressed="answer", feedback_target="bot_response",
            continuation="reply_now", next_move="continue_normally")
        # 正文已经被调用方剥掉小王；named 仍保留原始叫名事实。
        result = await self.tracker.assess(G, B, "这个好像是另一种", named=True, now=5.0)
        self.assertTrue(self.payload()["latest_is_directed"])
        self.assertEqual(self.payload()["latest_message"]["text"], "这个好像是另一种")
        self.assertEqual(self.payload()["bot_reply"]["text"], "我不确定是什么")
        self.assertEqual(result["addressed"], "answer")
        self.assertEqual(result["target"], "bot_response")
        self.assertEqual(result["continuation"], "reply_now")
        self.assertEqual(self.calls.await_count, 1)

    async def test_unnamed_correction_uses_feedback_and_continuation(self):
        self.tracker.remember(G, A, "这题结果应该是十", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        self.calls.side_effect = lambda text, qs: body_for(
            qs, feedback_target="bot_response", continuation="reply_now",
            next_move="repair_briefly")
        result = await self.tracker.assess(G, B, "少算了两项", now=5.0)
        self.assertFalse(self.payload()["latest_is_directed"])
        self.assertNotIn("addressed", self.asked())
        self.assertEqual(result["target"], "bot_response")
        self.assertEqual(result["continuation"], "reply_now")
        self.assertEqual(result["action"], "repair_briefly")
        self.assertEqual(self.calls.await_count, 1)

    async def test_named_answer_with_uncertain_target_receives_sanitized_reply(self):
        self.tracker.remember(G, A, f"我不太确定 {G} {A}", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        def response(text, questions):
            body = body_for(questions, addressed="answer")
            for answer in body["answers"]:
                if answer["name"] == "feedback_target":
                    answer["type"] = "refusal"
            return body
        self.calls.side_effect = response
        result = await self.tracker.assess(G, B, "也许是另一种", named=True, now=5.0)
        self.assertIsNone(result["target"])
        self.assertEqual(result["addressed"], "answer")
        self.assertEqual(result["bot_reply_to"], "another_member")
        self.assertIn("我不太确定", result["bot_reply_text"])
        self.assertNotIn(G, result["bot_reply_text"])
        self.assertNotIn(A, result["bot_reply_text"])

    async def test_other_person_and_unrelated_remark_do_not_attach_bot_reply(self):
        self.tracker.remember(G, A, "我不太确定", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        for target, addressed in (("another_person", "answer"),
                                  ("not_feedback", "let_it_pass")):
            with self.subTest(target=target, addressed=addressed):
                self.calls.side_effect = lambda text, qs: body_for(
                    qs, feedback_target=target, addressed=addressed)
                result = await self.tracker.assess(G, B, "群里那边怎样了", named=True, now=5.0)
                self.assertNotIn("bot_reply_text", result)

    async def test_expired_and_replaced_reply_are_not_attached(self):
        self.tracker.remember(G, A, "过期的回复", route="reply",
                              outgoing_id="o1", history=HIST, now=0.0)
        self.calls.side_effect = lambda text, qs: body_for(qs, addressed="answer")
        result = await self.tracker.assess(G, B, "也许是另一种", named=True, now=121.0)
        self.assertNotIn("bot_reply_text", result)
        self.tracker.remember(G, A, "原来的回复", route="reply",
                              outgoing_id="o2", history=HIST, now=122.0)
        async def replacement(text, questions):
            self.tracker.remember(G, A, "更新的回复", route="reply",
                                  outgoing_id="o3", history=HIST, now=123.0)
            return body_for(questions, addressed="answer", feedback_target="unclear")
        self.calls.side_effect = replacement
        result = await self.tracker.assess(G, B, "也许是另一种", named=True, now=123.0)
        self.assertNotIn("bot_reply_text", result)
        self.assertEqual(result["intervention"], "stay_quiet")


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
