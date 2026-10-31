"""定时任务（scheduler.py）和跑在它上面的催债。

2026-11-01 群主：「把之前欠的事项的提醒，标准化成定时任务的能力，提醒也是调用这个。」
原来催债是一个每小时一圈的专用循环，什么时候能开口的规矩全写在循环里。
现在：任务表只管「几点响」，响了交给按 kind 注册的处理函数；能不能开口由 hold 判，
挡住往后挪、不丢；真相在各自的账本里。
"""
import time
import unittest
from unittest.mock import patch

import bot
import config
import runtime
import scheduler
from regular import decisions

GID = "GID_SCHED"


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = scheduler.Scheduler(max_tasks=3)
        self.seen = []

    def kind(self, result=None, raises=False):
        async def handler(task):
            self.seen.append(task["id"])
            if raises:
                raise RuntimeError("boom")
            return result(task) if callable(result) else result
        return handler

    async def test_done_reschedule_and_not_yet_due(self):
        self.s.register("once", self.kind(None))
        self.s.register("again", self.kind(lambda t: t["due"] + 60))
        self.s.at("once", GID, 10, task_id="a")
        self.s.at("again", GID, 10, task_id="b")
        self.s.at("once", GID, 99, task_id="later")
        self.assertEqual(await self.s.run_due(now=20), 2)
        self.assertEqual(sorted(self.seen), ["a", "b"])
        self.assertNotIn("a", self.s.tasks)
        self.assertEqual(self.s.tasks["b"]["due"], 70)
        self.assertIn("later", self.s.tasks)

    async def test_hold_postpones_without_running(self):
        self.s.register("k", self.kind(None), hold=lambda task, now: now + 300)
        self.s.at("k", GID, 0, task_id="a")
        await self.s.run_due(now=100)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.s.tasks["a"]["due"], 400)

    async def test_unregistered_kind_is_kept(self):
        """功能关着、或启动时还没注册：留着，不许当成做完了删掉。"""
        self.s.at("off", GID, 0, task_id="a")
        self.assertEqual(await self.s.run_due(now=10), 0)
        self.assertIn("a", self.s.tasks)

    async def test_failures_retry_then_give_up(self):
        self.s.register("k", self.kind(raises=True))
        self.s.at("k", GID, 0, task_id="a")
        now = 0.0
        for attempt in range(1, scheduler.MAX_TRIES):
            await self.s.run_due(now=now)
            self.assertEqual(self.s.tasks["a"]["tries"], attempt)
            now = self.s.tasks["a"]["due"]
        await self.s.run_due(now=now)
        self.assertNotIn("a", self.s.tasks)

    def test_same_id_reschedules_and_table_is_bounded(self):
        self.s.at("k", GID, 10, task_id="a")
        self.s.at("k", GID, 20, task_id="a")
        self.assertEqual(len(self.s.tasks), 1)
        self.assertEqual(self.s.tasks["a"]["due"], 20)
        self.s.at("k", GID, 1, task_id="b")
        self.s.at("k", GID, 1, task_id="c")
        self.assertIsNone(self.s.at("k", GID, 1, task_id="d"))
        self.assertEqual(self.s.at("k", GID, 5, task_id="c"), "c")   # 改期不受上限影响

    def test_round_trip_rejects_forged_rows(self):
        self.s.at("k", GID, 10, {"id": "p1"}, task_id="a")
        data = {}
        self.s.dump_into(data)
        data["schedule"]["bad1"] = {"id": "bad1", "kind": "k", "group": GID, "due": "10"}
        data["schedule"]["bad2"] = {"id": "bad2", "kind": "k", "group": GID, "due": float("nan")}
        data["schedule"]["bad3"] = {"id": "bad3", "kind": "", "group": GID, "due": 1}
        data["schedule"]["bad4"] = {"id": "bad4", "kind": "k", "group": GID, "due": 1, "payload": []}
        fresh = scheduler.Scheduler()
        self.assertEqual(fresh.hydrate(data["schedule"]), 1)
        self.assertEqual(fresh.tasks["a"]["payload"], {"id": "p1"})
        self.assertEqual(fresh.hydrate("garbage"), 1)


class PromiseRemindersTest(unittest.IsolatedAsyncioTestCase):
    """催债是定时任务的第一个用户。账本是真相，任务只是闹钟。"""

    def setUp(self):
        self.now = time.time()
        self.row = {"id": "p1", "who": "阿龙", "what": "请大家喝奶茶", "since": self.now - 3600,
                    "due_ts": None, "due_text": "周五", "nag": 0, "last_nag": 0.0}
        runtime.PROMISES.items[GID] = [self.row]
        self.addCleanup(lambda: runtime.PROMISES.items.pop(GID, None))
        self.addCleanup(lambda: runtime.PROMISES.finished.pop(GID, None))
        self.posted = []

        async def fake_model(messages, *a, **k):
            return "阿龙，奶茶呢"

        async def fake_post(group_id, text, **kw):
            self.posted.append(text)
            return True

        groups = runtime.STATE.data.setdefault("groups", {})
        self.addCleanup(lambda: groups.pop(GID, None))
        groups[GID] = {"last_seen": self.now}
        for target, name, value in (
                (runtime, "SCHEDULER", scheduler.Scheduler()),
                (runtime, "FEEDBACK", decisions.FeedbackTracker(120.0, 180.0)),
                (bot, "call_model", fake_model), (bot, "post_to_group", fake_post),
                (bot, "_last_dun", {}), (config, "PROMISE_QUIET_HOURS", set())):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    def task(self):
        return runtime.SCHEDULER.tasks["promise:p1"]

    async def test_alarm_is_set_from_the_ledger(self):
        bot.schedule_promises(GID)
        self.assertEqual(self.task()["due"], runtime.PROMISES.next_nag(self.row))
        self.assertEqual(self.task()["kind"], "promise")

    async def test_not_yet_due_just_moves_the_alarm(self):
        due = runtime.PROMISES.next_nag(self.row)
        self.assertEqual(await bot.remind_promise({"group": GID, "payload": {"id": "p1"}}), due)
        self.assertEqual(self.posted, [])

    async def test_due_reminder_posts_then_backs_off_until_the_cap(self):
        self.row["since"] = self.now - 3 * 86400
        nxt = await bot.remind_promise({"group": GID, "payload": {"id": "p1"}})
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.row["nag"], 1)
        self.assertAlmostEqual(nxt, self.row["last_nag"] + runtime.PROMISES.nag_interval_hours * 3600)
        self.row["last_nag"] -= 3 * 86400          # 第二次也到点了
        self.assertIsNone(await bot.remind_promise({"group": GID, "payload": {"id": "p1"}}))
        self.assertEqual(len(self.posted), 2)      # 催满上限：撤下，不再响

    async def test_settled_promise_silences_its_alarm(self):
        runtime.PROMISES.items[GID] = []
        self.assertIsNone(await bot.remind_promise({"group": GID, "payload": {"id": "p1"}}))
        self.assertEqual(self.posted, [])

    async def test_runs_through_the_scheduler(self):
        self.row["since"] = self.now - 3 * 86400
        runtime.SCHEDULER.register("promise", bot.remind_promise, hold=bot._promise_hold)
        bot.schedule_promises(GID)
        await runtime.SCHEDULER.run_due(now=self.now)
        self.assertEqual(len(self.posted), 1)
        self.assertGreater(self.task()["due"], self.now)

    def test_hold_keeps_the_old_rules(self):
        t, now = {"group": GID}, self.now
        self.assertIsNone(bot._promise_hold(t, now))
        with patch.object(config, "PROMISE_QUIET_HOURS", {time.localtime(now).tm_hour}):
            later = bot._promise_hold(t, now)
        self.assertGreater(later, now)
        self.assertEqual(time.localtime(later).tm_min, 0)              # 挪到下一个能开口的整点
        runtime.STATE.data["groups"][GID]["last_seen"] = now - 2 * 86400
        self.assertEqual(bot._promise_hold(t, now), now + 3600)        # 群里一天没人说话
        runtime.STATE.data["groups"][GID]["last_seen"] = now
        runtime.FEEDBACK._slot(GID)["quiet_until"] = now + 100
        self.assertIsNotNone(bot._promise_hold(t, now))                # 刚被叫收敛
        runtime.FEEDBACK._slot(GID)["quiet_until"] = 0
        bot._last_dun[GID] = now - 60
        self.assertEqual(bot._promise_hold(t, now),                    # 同群两次催债错开
                         now - 60 + config.PROMISE_CHECK_INTERVAL)


if __name__ == "__main__":
    unittest.main()
