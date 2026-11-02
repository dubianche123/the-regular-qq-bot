"""定时任务：到点让机器人主动做一件事。

催债（承诺到期提醒）是第一个用户；后台查询做完再回报（followup）、群友让它到点提醒（reminder，
见 reminders.py）也挂在这里。

一张任务表随 state.json 持久化（键 schedule），每条只有 id、kind、group、due、payload、tries。
后台循环每隔一会儿看一次，到点的按 kind 交给注册的处理函数：

    处理函数返回 None   → 做完了，撤掉
    返回一个时间戳      → 改到那时再来（同一条任务接着用）

出门前的规矩（夜里不开口、刚被叫收敛、额度用完、群里一天没人说话……）由注册时给的
hold 判：挡住就返回改到什么时候，任务往后挪，不丢。规矩跟着 kind 走 —— 催债要等白天，
别人亲口要的提醒就不一定。

任务只是闹钟，不是真相：处理函数响了以后要回自己的账本看还该不该做（催债就是回承诺
账本看这条还在不在、到没到时候）。任务表和账本对不齐时，最坏是空响一次。
"""
import asyncio
import logging
import math
import time
import uuid

logger = logging.getLogger(__name__)

RETRY_SECONDS = 600.0   # 处理函数抛异常后多久再试
MAX_TRIES = 5           # 连着失败这么多次就撤掉，别每十分钟在日志里炸一次


def _valid(task):
    if not isinstance(task, dict):
        return False
    due, tries = task.get("due"), task.get("tries", 0)
    return (all(isinstance(task.get(k), str) and task.get(k) for k in ("id", "kind", "group"))
            and isinstance(due, (int, float)) and not isinstance(due, bool) and math.isfinite(due)
            and isinstance(task.get("payload", {}), dict)
            and isinstance(tries, int) and not isinstance(tries, bool) and tries >= 0)


class Scheduler:
    def __init__(self, max_tasks=512):
        self.max_tasks = int(max_tasks)
        self.tasks = {}    # id -> task
        self._kinds = {}   # kind -> (handler, hold)
        self._wake = None  # 循环在睡时的那个 Event；登记新任务就叫醒它

    # ── 持久化：与其它档案同一套 hydrate / dump_into 约定 ──
    def hydrate(self, raw):
        for task in (raw or {}).values() if isinstance(raw, dict) else ():
            if _valid(task) and len(self.tasks) < self.max_tasks:
                self.tasks[task["id"]] = {
                    "id": task["id"], "kind": task["kind"], "group": task["group"],
                    "due": float(task["due"]), "payload": dict(task.get("payload") or {}),
                    "tries": task.get("tries", 0)}
        return len(self.tasks)

    def dump_into(self, data):
        data["schedule"] = {i: dict(t, payload=dict(t["payload"])) for i, t in self.tasks.items()}

    # ── 登记 ──
    def register(self, kind, handler, *, hold=None):
        """handler(task) -> None | 时间戳（async）；hold(task, now) -> None | 时间戳。"""
        self._kinds[kind] = (handler, hold)

    def at(self, kind, group, due, payload=None, *, task_id=None):
        """登记一条任务。同一个 task_id 再登记就是改期，不会多出一条。"""
        task_id = task_id or uuid.uuid4().hex
        if task_id not in self.tasks and len(self.tasks) >= self.max_tasks:
            logger.warning("⏰ 任务表满了（%d 条），这条 %s 没登记", self.max_tasks, kind)
            return None
        self.tasks[task_id] = {"id": task_id, "kind": kind, "group": group,
                               "due": float(due), "payload": dict(payload or {}), "tries": 0}
        if self._wake is not None:
            self._wake.set()
        return task_id

    def cancel(self, task_id):
        return self.tasks.pop(task_id, None) is not None

    def pending(self, kind=None, group=None):
        return sorted((t for t in self.tasks.values()
                       if (kind is None or t["kind"] == kind)
                       and (group is None or t["group"] == group)),
                      key=lambda t: t["due"])

    # ── 执行 ──
    async def idle(self, max_wait, now=None):
        """睡到下一条到点、有新任务登记、或 max_wait，哪个先到算哪个。

        催债一天一次，慢活几秒一问：固定一分钟一圈的话，查完的东西要白等最多一分钟。
        """
        now = time.time() if now is None else now
        due = min((t["due"] for t in self.tasks.values()), default=None)
        wait = max_wait if due is None else min(max_wait, max(0.0, due - now))
        self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake = None

    async def run_due(self, now=None):
        """到点的各跑一次。返回动过几条（做完、改期、被挡住往后挪都算），调用方据此落盘。

        还没注册的 kind（功能关着，或启动顺序还没轮到）原样留着，不丢。
        """
        now = time.time() if now is None else now
        touched = 0
        for task in [t for t in self.pending() if t["due"] <= now]:
            kind = self._kinds.get(task["kind"])
            if kind is None:
                continue
            handler, hold = kind
            touched += 1
            later = hold(task, now) if hold else None
            if later is not None:
                task["due"] = max(float(later), now + 1.0)
                continue
            try:
                nxt = await handler(task)
            except Exception:
                logger.exception("⏰ 定时任务 %s 出错（%s）", task["kind"], task["group"][-6:])
                task["tries"] += 1
                if task["tries"] >= MAX_TRIES:
                    self.tasks.pop(task["id"], None)
                else:
                    task["due"] = now + RETRY_SECONDS
                continue
            if self.tasks.get(task["id"]) is not task:
                continue   # 处理期间被撤掉或改期了：以那次为准
            if nxt is None:
                self.tasks.pop(task["id"], None)
            else:
                task["due"], task["tries"] = float(nxt), 0
        return touched
