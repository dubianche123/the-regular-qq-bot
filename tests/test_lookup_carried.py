"""A borrowed QQ transport never changes who requested a lookup result."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import runtime
import scheduler
from storage import SessionStore
from test_when_to_intervene import A, B, G, _Base


class CarriedLookupTest(_Base):
    def setUp(self):
        super().setUp()
        self.sched = scheduler.Scheduler()
        self.sessions = SessionStore()
        self.source_time = time.time() - 600
        for target, name, value in (
                (runtime, "SCHEDULER", self.sched),
                (runtime, "SESSIONS", self.sessions),
                (bot, "_carried_followups", {})):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    def task(self, *, generated=True):
        self.sched.at("followup", G, time.time() + 3600, {
            "session": f"{G}_{A}", "question": "查查这件事",
            "member": A, "msg_id": "original-lookup-request",
            "msg_ts": "2020-01-01T00:00:00+08:00", "msg_idx": "original-reference",
            "started": time.time() - 300, "received_at": self.source_time,
            "ready": "查完了，实际结论在这里。", "ready_generated": generated,
            "ready_history": [{"sender": A, "text": "原来的问题", "time": self.source_time}]})
        return next(iter(self.sched.tasks.values()))

    def carrier(self, replies=None, *, msg_id="new-carrier"):
        return SimpleNamespace(group_openid=G, id=msg_id, timestamp=None,
                               author=SimpleNamespace(member_openid=B),
                               reply=AsyncMock(side_effect=replies or [{"id": "carried-receipt"}]))

    def deliveries(self):
        return [row for row in self.log.rows if row["kind"] == "delivery"]

    async def test_confirmed_borrowed_delivery_records_and_attributes_original_request(self):
        task = self.task()
        carrier = self.carrier()
        with patch.object(bot, "apply_affinity_delta_capped") as affinity:
            await bot.deliver_carried(carrier, G)
        carrier.reply.assert_awaited_once()
        self.assertEqual(carrier.reply.await_args.kwargs["msg_seq"], 1)
        self.assertEqual(carrier.reply.await_args.kwargs["message_reference"]["message_id"],
                         "original-reference")
        self.assertEqual(self.sched.tasks, {})
        self.assertEqual([row["content"] for row in self._session(A)],
                         ["查查这件事", task["payload"]["ready"]])
        self.assertEqual(self._session(B), [])
        session = self.sessions._sessions[f"{G}_{A}"]
        self.assertEqual(session["message_times"][0], self.source_time)
        row = self.deliveries()[-1]
        self.assertEqual((row["user"], row["source_message_id"], row["carrier_message_id"]),
                         (A, "original-lookup-request", "new-carrier"))
        self.assertEqual((row["route"], row["generation"]), ("followup", "model"))
        self.assertEqual(bot.own_lines[G][-1]["user"], A)
        reply = self.feedback.reply_snapshot(G)
        self.assertEqual((reply["user"], reply["text"], reply["route"]),
                         (A, task["payload"]["ready"], "reply"))
        affinity.assert_not_called()

    async def test_queued_result_waits_for_receipt_and_is_not_enqueued_again(self):
        task = self.task()
        carrier = self.carrier([None, None, {"id": "late-confirmation"}])
        await bot.deliver_carried(carrier, G)
        self.assertIn(task["id"], self.sched.tasks)
        self.assertEqual(self._session(A), [])
        self.assertIsNone(self.feedback.reply_snapshot(G))
        self.assertEqual(len(bot.SENDER.pending), 1)
        next_carrier = self.carrier(msg_id="another-carrier")
        await bot.deliver_carried(next_carrier, G)
        next_carrier.reply.assert_not_awaited()
        self.assertEqual(len(bot.SENDER.pending), 1)
        await bot.SENDER.drain()
        self.assertEqual(self.sched.tasks, {})
        self.assertEqual(len(self._session(A)), 2)
        self.assertEqual(bot._carried_followups, {})
        self.assertEqual({call.kwargs["msg_seq"] for call in carrier.reply.await_args_list}, {1})

    async def test_rejection_keeps_result_for_a_later_carrier_without_recording(self):
        task = self.task()
        rejected = self.carrier([RuntimeError("denied")])
        await bot.deliver_carried(rejected, G)
        self.assertIn(task["id"], self.sched.tasks)
        self.assertEqual(self._session(A), [])
        self.assertIsNone(self.feedback.reply_snapshot(G))
        self.assertEqual(bot._carried_followups, {})
        accepted = self.carrier(msg_id="accepted-carrier")
        await bot.deliver_carried(accepted, G)
        accepted.reply.assert_awaited_once()
        self.assertEqual(len(self._session(A)), 2)
        self.assertEqual(self.sched.tasks, {})

    async def test_cancelled_queued_task_cannot_deliver_or_commit(self):
        task = self.task()
        carrier = self.carrier([None, None, {"id": "should-not-send"}])
        await bot.deliver_carried(carrier, G)
        self.sched.cancel(task["id"])
        await bot.SENDER.drain()
        self.assertEqual(carrier.reply.await_count, 2)
        self.assertEqual(self._session(A), [])
        self.assertIsNone(self.feedback.reply_snapshot(G))
        self.assertEqual(bot._carried_followups, {})

    async def test_local_failure_is_not_registered_as_model_feedback(self):
        self.task(generated=False)
        await bot.deliver_carried(self.carrier(), G)
        self.assertEqual(len(self._session(A)), 2)
        self.assertIsNone(self.feedback.reply_snapshot(G))
        self.assertEqual(self.deliveries()[-1]["generation"], "local_fallback")

    async def test_ordinary_metadata_cannot_spoof_a_semantic_source(self):
        carrier = self.carrier()
        await bot.safe_reply(carrier, "普通回复", interaction={
            "user": A, "source_message_id": "spoofed", "route": "reply"})
        row = self.deliveries()[-1]
        self.assertEqual((row["user"], row["source_message_id"]), (B, carrier.id))

    async def test_two_concurrent_carriers_cannot_send_the_same_result(self):
        self.task()
        started, finish = asyncio.Event(), asyncio.Event()
        async def pending(**kwargs):
            started.set()
            await finish.wait()
            return {"id": "single-result"}
        first = self.carrier()
        first.reply.side_effect = pending
        first_run = asyncio.create_task(bot.deliver_carried(first, G))
        await asyncio.wait_for(started.wait(), 1)
        second = self.carrier(msg_id="parallel-carrier")
        try:
            await bot.deliver_carried(second, G)
            second.reply.assert_not_awaited()
        finally:
            finish.set()
            await first_run
        self.assertEqual(len(self._session(A)), 2)

    async def test_proactive_lookup_fallback_has_original_source_while_regular_post_stays_group(self):
        client = SimpleNamespace(api=SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "post"})))
        source = SimpleNamespace(id="original-lookup-request", author=SimpleNamespace(member_openid=A))
        with patch.object(bot, "_bot_ref", {"client": client}):
            self.assertTrue(await bot.post_to_group(G, "主动投递查询结论", model_generated=True,
                                                    semantic_source=source))
            row = self.deliveries()[-1]
            self.assertEqual((row["user"], row["source_message_id"], row["route"]),
                             (A, source.id, "followup"))
            self.assertEqual(bot.own_lines[G][-1]["user"], A)
            self.assertEqual(self.feedback.reply_snapshot(G)["user"], A)
            self.assertTrue(await bot.post_to_group(G, "普通群发", model_generated=True))
        row = self.deliveries()[-1]
        self.assertEqual((row["user"], row["source_message_id"], row["route"]),
                         (None, None, "proactive"))
        self.assertIsNone(bot.own_lines[G][-1]["user"])
        self.assertIsNone(self.feedback.reply_snapshot(G)["user"])
