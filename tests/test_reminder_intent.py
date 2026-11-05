"""提醒意图与原分诊合并；生成前的歧义结论和审核题都能原样回放。"""
import json
import unittest
from unittest.mock import AsyncMock, patch

import config
from regular import decision_prompts, decisions, reminder_review, replay_worker


class ReminderIntentTest(unittest.IsolatedAsyncioTestCase):
    async def test_directed_request_reuses_the_single_triage_call(self):
        q = reminder_review.request_questions()
        body = replay_worker._choices_to_decision_body({"reminder_request": "clarify"}, q)
        call = AsyncMock(return_value=body)
        tracker = decisions.FeedbackTracker(120, 180)
        with patch.object(config, "REMINDER_ENABLED", True), \
                patch.object(config, "DECISION_ENABLED", True), \
                patch.object(decisions.providers, "call_decision", call):
            result = await tracker.assess("GROUP", "REQUESTER", "明晚九点提醒那个节目", is_direct=True)
        self.assertEqual(result["reminder_request"], "clarify")
        call.assert_awaited_once()
        self.assertEqual(sum(q["name"] == "reminder_request" for q in call.await_args.args[1]), 1)
        self.assertIn("next_move", [q["name"] for q in call.await_args.args[1]])

    async def test_disabled_reminders_and_uninvited_chat_do_not_add_the_question(self):
        for enabled, directed in [(False, True), (True, False)]:
            with self.subTest(enabled=enabled, directed=directed):
                call = AsyncMock(return_value={"answers": []})
                tracker = decisions.FeedbackTracker(120, 180)
                with patch.object(config, "REMINDER_ENABLED", enabled), \
                        patch.object(config, "DECISION_ENABLED", True), \
                        patch.object(decisions.providers, "call_decision", call):
                    await tracker.assess("GROUP", "REQUESTER", "今天很冷", is_direct=directed)
                if call.await_count:
                    self.assertNotIn("reminder_request", [q["name"] for q in call.await_args.args[1]])

    async def test_all_three_reminder_questions_are_registered_for_triage_replay(self):
        names = ["reminder_request", "reminder_write", "reminder_response"]
        questions = replay_worker._questions_for_asked(decision_prompts, names)
        self.assertEqual([q["name"] for q in questions], names)
        choices = {"reminder_request": "clarify", "reminder_write": "clarify", "reminder_response": "valid"}
        body = replay_worker._choices_to_decision_body(choices, questions)
        with patch.object(replay_worker, "_gate", return_value=replay_worker._ReplayGate()), \
                patch.object(decisions.providers, "call_decision", AsyncMock(return_value=body)):
            out = await replay_worker._run_triage_once(None, config,
                {"asked": names, "payload": {"original_request": "提醒一个未说清的节目"}})
        self.assertEqual(json.loads(out), choices)
