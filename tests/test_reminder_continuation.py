"""回答提醒澄清不必再次叫名；只有实际回应机器人时才启用提醒写入。"""
import collections
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
import config
import quotes
from regular import reminder_review
import relations
import reminders
import scheduler
import storage
import test_natural_feedback as feedback_tests
import wordfilter


NOW = time.mktime((2026, 11, 5, 16, 0, 0, 0, 0, -1))
ANSWER = "好，明天九点提醒你浇花\n〔提醒 11-06 09:00〕提醒TA给花浇水"


class ReminderContinuationTest(feedback_tests._Base):
    G, A = "GID_CONT_REMIND", "USER_CONT_REMIND"

    def setUp(self):
        super().setUp()
        self.state = storage.StateStore(str(Path(self._tmp.name) / "state.json"))
        self.sessions = storage.SessionStore()
        self.scheduler = scheduler.Scheduler()
        self.review = AsyncMock(return_value="allow")
        self.generation = AsyncMock(wraps=bot.get_ai_reply)
        self.provider = AsyncMock(return_value=ANSWER)
        self.target = "bot_response"

        async def native_decision(text, questions, **kwargs):
            wanted = {"feedback_target": self.target, "next_move": "continue_normally",
                      "request_kind": "sincere", "reminder_request": "clear"}
            answers = []
            for question in questions:
                name = question["name"]
                values = [item["value"] for item in question["choices"]]
                choice = wanted.get(name, values[0])
                if choice not in values:
                    choice = values[0]
                others = [value for value in values if value != choice]
                probabilities = [{"value": choice, "probability": 0.97}]
                probabilities += [{"value": value, "probability": 0.03 / len(others)}
                                  for value in others]
                answers.append({"name": name, "type": "choice", "choice": choice,
                                "confidence": 0.97, "probabilities": probabilities})
            return {"answers": answers}

        self.calls.side_effect = native_decision
        replacements = (
            (bot.runtime, "STATE", self.state),
            (bot.runtime, "SESSIONS", self.sessions),
            (bot.runtime, "SCHEDULER", self.scheduler),
            (bot.runtime, "QUOTES", quotes.QuoteBook(self.state)),
            (bot.runtime, "RELATIONS", relations.RelationStore()),
            (bot.runtime, "TURNS", SimpleNamespace(append=Mock(return_value=None))),
            (bot, "get_ai_reply", self.generation),
            (bot, "call_model", self.provider),
            (bot, "owner_hint_pending", Mock(return_value=False)),
            (bot, "group_buffers", collections.defaultdict(collections.deque)),
            (bot, "own_lines", collections.defaultdict(collections.deque)),
            (bot, "_reminder_revisions", collections.defaultdict(int)),
            (reminder_review, "review_reminder", self.review),
            (reminder_review, "check_response", AsyncMock(return_value=True)),
            (wordfilter, "external_words", Mock(return_value=())),
            (wordfilter, "_runtime", set()),
            (config, "BOT_NAMES", ["小王"]),
            (config, "REMINDER_ENABLED", True),
            (config, "DECISION_ENABLED", True),
            (config, "AFFINITY_ENABLED", False),
            (config, "DIGEST_ENABLED", False),
            (config, "RECEPTIVE_ENABLED", False),
            (config, "DECISION_CALLBACK_ENABLED", False),
            (config, "KNOWLEDGE_ENABLED", False),
            (config, "MEMORY_RECALL_ENABLED", False),
            (config, "SEARCH_ENABLED", False),
            (config, "BANTER_BURST_SECONDS", 0),
        )
        for target, name, value in replacements:
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)
        for target, name in ((bot.image_generation, "available"), (bot._VOICE_OUT, "ready")):
            context = patch.object(target, name, return_value=False)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(bot.time, "time", return_value=NOW)
        context.start()
        self.addCleanup(context.stop)

    def seed_invited_reply(self, question="具体要提醒什么？"):
        self.feedback.remember(self.G, self.A, question, route="reply",
                               outgoing_id="clarify-previous")

    def assert_one_triage_with_reminder_question(self):
        self.calls.assert_awaited_once()
        names = [question["name"] for question in self.calls.await_args.args[1]]
        self.assertEqual(names.count("reminder_request"), 1)
        self.assertIn("feedback_target", names)

    async def test_clear_unnamed_reply_is_saved_after_write_review(self):
        self.seed_invited_reply()
        await self._drive("明天早上九点提醒我给花浇水", is_at=False)
        self.assert_one_triage_with_reminder_question()
        self.generation.assert_awaited_once()
        self.assertFalse(self.generation.await_args.kwargs["unprompted"])
        self.review.assert_awaited_once()
        task = self.scheduler.tasks[reminders.task_id(self.G, self.A)]
        self.assertEqual(task["payload"]["what"], "提醒TA给花浇水")
        self.assertTrue(bot._reminder_review_valid(task))

    async def test_object_clarification_without_address_uses_original_question_and_saves(self):
        question = "你说的那件事具体是什么？说清楚我再记"
        self.seed_invited_reply(question)
        request = "就是给花浇水，明天九点"
        await self._drive(request, is_at=False)
        self.assert_one_triage_with_reminder_question()
        self.review.assert_awaited_once()
        self.assertEqual(self.review.await_args.args[0], request)
        self.assertIn(question, self.review.await_args.kwargs["context"])
        self.assertIn(reminders.task_id(self.G, self.A), self.scheduler.tasks)
        self.assertFalse(self.generation.await_args.kwargs["unprompted"])

    async def test_clear_request_to_another_person_keeps_unprompted_write_disabled(self):
        self.seed_invited_reply()
        self.target = "another_person"
        await self._drive("明天九点提醒我给花浇水", is_at=False)
        self.assert_one_triage_with_reminder_question()
        self.assertTrue(self.generation.await_args.kwargs["unprompted"])
        self.review.assert_not_awaited()
        self.assertEqual(self.scheduler.pending(), [])

    async def test_clear_without_a_reply_source_does_not_grant_reminder_permission(self):
        # 即使上游给了 clear，也没有实际回复来源，不能把普通群聊扩成正式写入。
        with patch.object(self.feedback, "assess", AsyncMock(return_value={
                "decision_attempted": True, "target": None,
                "reminder_request": "clear"})):
            await self._drive("明天九点提醒我给花浇水", is_at=False)
        self.calls.assert_not_awaited()
        self.assertTrue(self.generation.await_args.kwargs["unprompted"])
        self.review.assert_not_awaited()
        self.assertEqual(self.scheduler.pending(), [])
