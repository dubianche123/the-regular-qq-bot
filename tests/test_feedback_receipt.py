"""反馈绑定收消息时的原句，不能被等待期间送达的新回复换掉。"""
import json
import unittest
from unittest.mock import AsyncMock, patch

import config
from regular import decisions


GROUP, USER, OTHER = "GROUP_RECEIPT", "MEMBER_RECEIPT", "MEMBER_OTHER"
OLD_TEXT, NEW_TEXT = "刚才那题我算漏了一项", "另一道题已经答完了"


def answer(questions, **picks):
    rows = []
    for question in questions:
        values = [choice["value"] for choice in question["choices"]]
        picked = picks.get(question["name"], values[-1])
        rows.append({
            "name": question["name"], "type": "choice", "choice": picked,
            "confidence": 1.0,
            "probabilities": [{"value": value,
                               "probability": 1.0 if value == picked else 0.0}
                              for value in values],
        })
    return {"answers": rows}


class ReceiptFeedbackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tracker = decisions.FeedbackTracker(120.0, 180.0)
        self.calls = AsyncMock(side_effect=lambda text, questions: answer(
            questions, addressed="answer", feedback_target="bot_response",
            next_move="repair_briefly"))
        for target, name, value in (
                (config, "DECISION_ENABLED", True),
                (config, "BOT_NAMES", ["小团团"]),
                (decisions.providers, "call_decision", self.calls)):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    def remember(self, text=OLD_TEXT, outgoing_id="sent-old", now=100.0):
        self.tracker.remember(GROUP, OTHER, text, route="reply",
                              outgoing_id=outgoing_id, now=now)

    def payload(self):
        return json.loads(self.calls.await_args.args[0])

    async def test_snapshot_returns_only_the_reply_effective_at_receipt(self):
        self.assertIsNone(self.tracker.reply_snapshot(GROUP, now=99.0))
        self.assertEqual(self.tracker._groups, {})
        self.remember()
        reply = self.tracker._groups[GROUP]["reply"]
        self.assertIsNone(self.tracker.reply_snapshot(GROUP, now=99.0))
        self.assertIs(self.tracker.reply_snapshot(GROUP, now=100.0), reply)
        self.assertIs(self.tracker.reply_snapshot(GROUP, now=220.0), reply)
        self.assertIsNone(self.tracker.reply_snapshot(GROUP, now=220.1))

    async def test_snapshot_stays_bound_when_new_delivery_precedes_assessment(self):
        self.remember()
        receipt = self.tracker.reply_snapshot(GROUP, now=101.0)
        self.remember(NEW_TEXT, "sent-new", now=102.0)

        result = await self.tracker.assess(
            GROUP, USER, "刚才少算了两项", named=True,
            reply_snapshot=receipt, now=101.0)

        self.assertEqual(self.payload()["bot_reply"]["text"], OLD_TEXT)
        self.assertEqual(result["source_reply_id"], "sent-old")
        self.assertEqual(result["bot_reply_text"], OLD_TEXT)
        self.assertEqual(result["action"], "repair_briefly")
        self.assertEqual(self.calls.await_count, 1)

    async def test_snapshot_keeps_feedback_semantics_during_new_delivery(self):
        self.remember()
        receipt = self.tracker.reply_snapshot(GROUP, now=101.0)

        async def delivery_during_assessment(text, questions):
            self.remember(NEW_TEXT, "sent-new", now=102.0)
            return answer(questions, addressed="answer", feedback_target="bot_response",
                          next_move="back_off")

        self.calls.side_effect = delivery_during_assessment
        result = await self.tracker.assess(
            GROUP, USER, "先别接了", named=True,
            reply_snapshot=receipt, now=101.0)

        self.assertEqual(result["source_reply_id"], "sent-old")
        self.assertEqual(result["bot_reply_text"], OLD_TEXT)
        self.assertTrue(result["back_off"])
        self.assertTrue(self.tracker.quiet(GROUP, now=102.0))
        self.assertEqual(self.tracker._groups[GROUP]["reply"]["text"], NEW_TEXT)

    async def test_explicit_none_does_not_adopt_a_later_reply(self):
        receipt = self.tracker.reply_snapshot(GROUP, now=99.0)
        self.remember()

        result = await self.tracker.assess(
            GROUP, USER, "小团团看看这道题", named=True,
            reply_snapshot=receipt, now=99.0)

        self.assertNotIn("bot_reply", self.payload())
        self.assertNotIn("feedback_target", result["asked"])
        self.assertNotIn("source_reply_id", result)
        self.assertNotIn("bot_reply_text", result)

    async def test_unnamed_explicit_none_still_costs_no_call(self):
        self.remember()
        result = await self.tracker.assess(
            GROUP, USER, "今天好热", reply_snapshot=None, now=101.0)
        self.assertEqual(result, {})
        self.calls.assert_not_awaited()

    async def test_receipt_validity_is_not_rechecked_at_processing_time(self):
        self.remember()
        receipt = self.tracker.reply_snapshot(GROUP, now=101.0)
        result = await self.tracker.assess(
            GROUP, USER, "刚才少算了两项", named=True,
            reply_snapshot=receipt, now=300.0)
        self.assertEqual(result["bot_reply_text"], OLD_TEXT)
        self.assertEqual(result["action"], "repair_briefly")

    async def test_legacy_assessment_keeps_replaced_reply_stale_protection(self):
        self.remember()

        async def delivery_during_assessment(text, questions):
            self.remember(NEW_TEXT, "sent-new", now=102.0)
            return answer(questions, addressed="answer", feedback_target="bot_response",
                          next_move="back_off")

        self.calls.side_effect = delivery_during_assessment
        result = await self.tracker.assess(
            GROUP, USER, "先别接了", named=True, now=101.0)

        self.assertEqual(result["action"], "stay_quiet")
        self.assertFalse(result["back_off"])
        self.assertNotIn("bot_reply_text", result)
        self.assertFalse(self.tracker.quiet(GROUP, now=102.0))

    async def test_cleared_receipt_cannot_restore_a_quiet_window(self):
        for recreate in (False, True):
            with self.subTest(recreate=recreate):
                self.remember()
                receipt = self.tracker.reply_snapshot(GROUP, now=101.0)

                async def clear_during_assessment(text, questions):
                    self.tracker._groups.clear()
                    if recreate:
                        self.remember(NEW_TEXT, "sent-new", now=102.0)
                    return answer(questions, addressed="answer",
                                  feedback_target="bot_response", next_move="back_off")

                self.calls.side_effect = clear_during_assessment
                result = await self.tracker.assess(
                    GROUP, USER, "先别接了", named=True,
                    reply_snapshot=receipt, now=101.0)

                self.assertEqual(result["action"], "stay_quiet")
                self.assertFalse(result["back_off"])
                self.assertFalse(self.tracker.quiet(GROUP, now=102.0))
                if not recreate:
                    self.assertNotIn(GROUP, self.tracker._groups)

    async def test_cache_reset_before_assessment_cannot_revive_old_state(self):
        self.remember()
        receipt = self.tracker.reply_snapshot(GROUP, now=101.0)
        self.tracker._groups.clear()
        self.remember(NEW_TEXT, "sent-new", now=102.0)
        self.calls.side_effect = lambda text, questions: answer(
            questions, addressed="answer", feedback_target="bot_response",
            next_move="back_off")

        result = await self.tracker.assess(
            GROUP, USER, "先别接了", named=True,
            reply_snapshot=receipt, now=101.0)

        self.assertEqual(result["action"], "stay_quiet")
        self.assertFalse(self.tracker.quiet(GROUP, now=102.0))
