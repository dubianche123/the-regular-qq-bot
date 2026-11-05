"""群主批量取消只改本群提醒；权限和指令路由由 bot 负责。"""
import asyncio
import unittest

import reminders
from scheduler import Scheduler


class CancelAllTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()

    def add(self, kind, group, member, *, daily=False, ready=None):
        payload = {"member": member, "what": "提醒喝水", "at": 100.0,
                   "daily": daily, "hm": "09:00"}
        if ready:
            payload["ready"] = ready
        tid = (reminders.task_id(group, member) if kind == reminders.KIND
               else f"{kind}:{group}:{member}")
        self.scheduler.at(kind, group, 100.0, payload, task_id=tid)
        return self.scheduler.tasks[tid]

    def test_only_group_reminders_are_removed_including_daily_and_ready(self):
        removed = [self.add(reminders.KIND, "g1", "alice"),
                   self.add(reminders.KIND, "g1", "bob", daily=True, ready="喝水啦")]
        kept = [self.add(reminders.KIND, "g2", "alice"),
                self.add("followup", "g1", "alice"),
                self.add("nag", "g1", "alice"),
                self.add("digest", "g1", "alice")]

        self.assertEqual(reminders.cancel_all(self.scheduler, "g1"), 2)
        for task in removed:
            self.assertNotIn(task["id"], self.scheduler.tasks)
        for task in kept:
            self.assertIs(self.scheduler.tasks[task["id"]], task)

    def test_empty_and_repeated_cancellation_return_actual_count(self):
        self.assertEqual(reminders.cancel_all(self.scheduler, "g1"), 0)
        for i in range(40):
            self.add(reminders.KIND, "g1", str(i))
        self.assertEqual(reminders.cancel_all(self.scheduler, "g1"), 40)
        self.assertEqual(reminders.cancel_all(self.scheduler, "g1"), 0)
        self.assertEqual(self.scheduler.pending(), [])

    def test_old_queued_task_identity_is_invalid_even_after_new_task_created(self):
        task = self.add(reminders.KIND, "g1", "alice", ready="喝水啦")

        def is_current():
            return self.scheduler.tasks.get(task["id"]) is task

        self.assertTrue(is_current())
        reminders.cancel_all(self.scheduler, "g1")
        self.assertFalse(is_current())
        replacement = self.add(reminders.KIND, "g1", "alice")
        self.assertIsNot(replacement, task)
        self.assertFalse(is_current())

    def test_cancelled_tasks_stay_absent_after_state_restore(self):
        self.add(reminders.KIND, "g1", "alice", daily=True)
        kept = self.add(reminders.KIND, "g2", "alice")
        reminders.cancel_all(self.scheduler, "g1")
        data = {}
        self.scheduler.dump_into(data)
        restored = Scheduler()
        restored.hydrate(data["schedule"])
        self.assertEqual(restored.pending(kind=reminders.KIND, group="g1"), [])
        self.assertEqual([task["id"] for task in restored.pending()], [kept["id"]])


class InFlightCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_return_cannot_restore_a_cancelled_task(self):
        scheduler = Scheduler()
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(task):
            started.set()
            await finish.wait()
            return 200.0

        scheduler.register(reminders.KIND, handler)
        tid = reminders.task_id("g1", "alice")
        scheduler.at(reminders.KIND, "g1", 100.0, {"daily": True}, task_id=tid)
        running = asyncio.create_task(scheduler.run_due(now=100.0))
        await started.wait()
        self.assertEqual(reminders.cancel_all(scheduler, "g1"), 1)
        finish.set()
        self.assertEqual(await running, 1)
        self.assertNotIn(tid, scheduler.tasks)
