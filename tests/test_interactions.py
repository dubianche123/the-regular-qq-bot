"""交互观察日志：写入/反馈绑定/送达观察者/门控决策的离线回归。

全部是假数据：临时目录 + SimpleNamespace 消息，不打网络、不碰真实 openid。
"""
import asyncio
import glob
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import archive as archive_mod
import bot
import config
import delivery
import interactions
import providers
import runtime
import storage


def _rows(log_dir):
    out = []
    for name in sorted(glob.glob(os.path.join(log_dir, "*.jsonl"))):
        with open(name, encoding="utf-8") as fp:
            for line in fp:
                out.append(json.loads(line))
    return out


def _make_log(tmp, **kw):
    return interactions.InteractionLog(os.path.join(tmp, "ix"), **kw)



class InteractionLogTest(unittest.TestCase):
    """日志本体：追加行结构、禁用、写入失败与截断尾。"""

    G, U, U2 = "GID_TEST_OBS", "USER_OBS_A", "USER_OBS_B"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _log(self, **kw):
        log = _make_log(self._tmp.name, **kw)
        self.addCleanup(log.close)
        return log

    def _delivered(self, log, user=U, group=G, text="回你一句", **extra):
        fields = dict(status="delivered", route="reply", feedback_eligible=True,
                      source_message_id="src-1", outgoing_id="out-1", text=text)
        fields.update(extra)
        return log.append("delivery", group, user=user, actor="assistant", **fields)

    def test_append_base_schema_and_reserved_fields(self):
        log = self._log()
        # kind/group/user 是具名参数塞不进 **fields；其余保留键走私进来也不许覆盖
        row = log.append("incoming", self.G, user=self.U, actor="user",
                         message_id="m1", text="hello",
                         ts=0.0, event_id="e", schema="x")
        self.assertIsNotNone(row)
        self.assertEqual(row["schema"], interactions.SCHEMA)
        self.assertEqual(row["kind"], "incoming")
        self.assertEqual(row["user"], self.U)
        self.assertEqual(row["group"], self.G)
        self.assertEqual(row["actor"], "user")
        self.assertNotEqual(row["ts"], 0.0)
        self.assertNotEqual(row["event_id"], "e")
        self.assertEqual(row["text"], "hello")
        self.assertEqual(_rows(log.log_dir), [row])

    def test_disabled_writes_nothing(self):
        log = self._log(enabled=False)
        self.assertIsNone(log.append("incoming", self.G, user=self.U))
        self.assertFalse(os.path.exists(log.log_dir))

    def test_io_failure_returns_none_not_raises(self):
        log = self._log()
        with patch.object(log, "_ensure_fp", side_effect=OSError("disk full")):
            self.assertIsNone(log.append("incoming", self.G))

    def test_init_survives_unwritable_paths(self):
        """目录建不起来只告警，不拖垮启动，也不假装记上。"""
        file_path = os.path.join(self._tmp.name, "a-file")
        open(file_path, "w").close()
        log = interactions.InteractionLog(os.path.join(file_path, "ix"))
        self.addCleanup(log.close)
        self.assertIsNone(log.append("incoming", self.G, text="x"))

    def test_unterminated_tail_gets_separator(self):
        """崩溃留下的无换行尾行：新事件不能被粘进去，要单独成行。"""
        day = time.strftime("%Y-%m-%d")
        log_dir = os.path.join(self._tmp.name, "ix")
        os.makedirs(log_dir)
        path = os.path.join(log_dir, f"{day}.jsonl")
        good = {"schema": interactions.SCHEMA, "event_id": "ev-pre",
                "ts": time.time() - 100, "kind": "delivery", "group": self.G,
                "user": self.U, "actor": "assistant", "status": "delivered",
                "route": "reply", "feedback_eligible": True,
                "source_message_id": "s-pre"}
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(good, ensure_ascii=False) + "\n")
            fp.write('{"kind":"delivery","status":"deliv')   # 无换行截断尾
        log = self._log()
        row = self._delivered(log, text="tail 之后的新送达")
        log.close()
        # 新行自己占一行，没有被粘到截断尾里
        with open(path, encoding="utf-8") as fp:
            lines = fp.read().splitlines()
        self.assertTrue(any('"ev-pre"' in l for l in lines))
        self.assertTrue(any(row["event_id"] in l for l in lines))


def _group_message(text="你好", sender="USER_A", group="GID_OBS", msg_id=None,
                   script=None, timestamp=None):
    return SimpleNamespace(
        id=msg_id or "msg-" + os.urandom(4).hex(), content=text,
        group_openid=group,
        author=SimpleNamespace(member_openid=sender),
        attachments=[], mentions=[], timestamp=timestamp,
        reply=AsyncMock(side_effect=script or [{"id": "rcpt-1"}]))


class DeliveryObserveTest(unittest.IsolatedAsyncioTestCase):
    """投递观察者：只有正面回执才算「说过」，状态与正文严格对得上。"""

    G, U = "GID_OBS_DELIV", "USER_OBS_A"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = _make_log(self._tmp.name)
        self.addCleanup(self.log.close)
        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply))):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)

    def deliveries(self):
        return [r for r in _rows(self.log.log_dir) if r["kind"] == "delivery"]

    async def test_delivered_records_exact_body_and_receipt_id(self):
        text = "多行\n长回复 " + "字" * 150
        msg = _group_message(group=self.G, sender=self.U)
        meta = {"route": "reply", "feedback_eligible": True,
                "source_message_id": msg.id, "generation": "model",
                "provider": "gemini", "model": "m-x"}
        result = await bot.safe_reply(msg, text, interaction=meta)
        self.assertTrue(result.delivered)
        rows = self.deliveries()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["text"], text)          # 超长+换行原样保留
        self.assertEqual(row["platform_message_id"], "rcpt-1")
        self.assertEqual(row["status"], "delivered")
        self.assertEqual(row["route"], "reply")
        self.assertTrue(row["feedback_eligible"])
        self.assertEqual(row["user"], self.U)
        self.assertEqual(row["group"], self.G)
        self.assertEqual(row["source_message_id"], msg.id)
        self.assertEqual(row["actor"], "assistant")
        self.assertEqual(row["policy_id"], config.INTERACTION_POLICY_ID)
        self.assertFalse(row["fallback"])
        self.assertEqual(row["provider"], "gemini")
        self.assertEqual(row["model"], "m-x")
        self.assertNotIn("receipt", row)             # 绝不序列化平台回执对象

    async def test_platform_fallback_marks_and_replaces_body(self):
        msg = _group_message(group=self.G, sender=self.U,
                             script=[RuntimeError("40034006"), {"id": "rcpt-2"}])
        original = "这句话被平台拒收"
        result = await bot.safe_reply(msg, original, interaction={"route": "reply"})
        self.assertTrue(result.delivered)
        rows = self.deliveries()
        self.assertEqual(len(rows), 1)               # 只有一条 delivered
        self.assertTrue(rows[0]["fallback"])
        self.assertNotEqual(rows[0]["text"], original)
        self.assertNotIn(original, json.dumps(rows, ensure_ascii=False))

    async def test_non_success_statuses_carry_no_text(self):
        async def drive(script=None, **kw):
            return await bot.safe_reply(
                _group_message(group=self.G, sender=self.U, script=script,
                               **{k: v for k, v in kw.items()
                                  if k in ("timestamp", "msg_id")}),
                "正文", interaction={"route": "reply"},
                **{k: v for k, v in kw.items() if k == "is_current"})
        # None 回执 → queued（不记正文）
        await drive(script=[None, None])
        # 普通拒收 → rejected
        await drive(script=[RuntimeError("denied")])
        # 过期：时间戳远超被动窗口
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        await drive(timestamp=old)
        # 被新轮次顶掉 → superseded
        await drive(is_current=lambda: False)
        rows = self.deliveries()
        self.assertEqual([r["status"] for r in rows],
                         ["queued", "rejected", "expired", "superseded"])
        for row in rows:
            self.assertNotIn("text", row, row["status"])
            self.assertNotIn("platform_message_id", row)

    async def test_queued_then_drained_records_once_with_same_id(self):
        # 两次内联尝试都没拿到回执 → 进队列；补投窗口内第三次成功只记一次 delivered
        msg = _group_message(group=self.G, sender=self.U,
                             script=[None, None, {"id": "late-1"}])
        outcome = {"saw": []}
        sender = delivery.Sender()
        def observe(result):
            outcome["saw"].append(result.status)
        first = await sender.reply(msg, "先排队", on_outcome=observe)
        self.assertEqual(first.status, "queued")
        await sender.drain()
        await sender.drain()
        self.assertEqual(outcome["saw"], ["queued", "delivered"])
        self.assertEqual(msg.reply.await_count, 3)   # 不重复投

    async def test_queue_eviction_reports_dropped(self):
        sender = delivery.Sender(max_pending=1)
        seen = []
        m1 = _group_message(group=self.G, sender=self.U, script=[None, None])
        m2 = _group_message(group=self.G, sender=self.U, script=[None, None])
        await sender.reply(m1, "一", on_outcome=lambda r: seen.append((1, r.status)))
        await sender.reply(m2, "二", on_outcome=lambda r: seen.append((2, r.status)))
        self.assertIn((1, "dropped"), seen)
        self.assertEqual(seen.count((2, "queued")), 1)

    async def test_observer_exception_never_breaks_delivery(self):
        sender = delivery.Sender()
        committed = []
        msg = _group_message(group=self.G, sender=self.U)
        def bad_observer(_result):
            raise RuntimeError("observer bug")
        result = await sender.reply(msg, "照常送达",
                                    on_delivered=lambda r: committed.append(1),
                                    on_outcome=bad_observer)
        self.assertTrue(result.delivered)
        self.assertEqual(committed, [1])

    async def test_c2c_writes_nothing(self):
        c2c = SimpleNamespace(id="c2c-1", content="hi",
                              author=SimpleNamespace(user_openid="U_C2C"),
                              reply=AsyncMock(return_value={"id": "r"}))
        await bot.safe_reply(c2c, "私聊回复")
        self.assertEqual(_rows(self.log.log_dir), [])

    async def test_post_to_group_statuses(self):
        api = SimpleNamespace(post_group_message=AsyncMock())
        ctx = patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)})
        ctx.start(); self.addCleanup(ctx.stop)
        # 确认 → delivered + 全文
        api.post_group_message.side_effect = [{"id": "p-1"}]
        self.assertTrue(await bot.post_to_group(self.G, "播报内容一"))
        # 空回执 → unconfirmed（SDK 可能吞超时，不能当拒收），无正文
        api.post_group_message.side_effect = [None]
        self.assertFalse(await bot.post_to_group(self.G, "播报内容二"))
        # 异常 → unconfirmed，无正文
        api.post_group_message.side_effect = RuntimeError("boom")
        self.assertFalse(await bot.post_to_group(self.G, "播报内容三"))
        rows = self.deliveries()
        self.assertEqual([r["status"] for r in rows],
                         ["delivered", "unconfirmed", "unconfirmed"])
        self.assertEqual(rows[0]["text"], "播报内容一")
        self.assertEqual(rows[0]["platform_message_id"], "p-1")
        self.assertNotIn("text", rows[1])
        self.assertNotIn("text", rows[2])
        for row in rows:
            self.assertEqual(row["route"], "proactive")
            self.assertFalse(row["feedback_eligible"])
            self.assertIsNone(row["user"])

    async def test_post_journal_failure_keeps_confirmed_result(self):
        api = SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "p-9"}))
        ctx = patch.dict(bot._bot_ref, {"client": SimpleNamespace(api=api)})
        ctx.start(); self.addCleanup(ctx.stop)
        with patch.object(runtime.INTERACTIONS, "append",
                          side_effect=RuntimeError("journal bug")):
            self.assertTrue(await bot.post_to_group(self.G, "照常播报"))
        self.assertEqual(api.post_group_message.await_count, 1)

    async def test_local_ack_gets_source_linkage_without_eligibility(self):
        msg = _group_message(group=self.G, sender=self.U)
        result = await bot.safe_reply(msg, "本地应答")
        self.assertTrue(result.delivered)
        row = self.deliveries()[-1]
        self.assertEqual(row["route"], "local")
        self.assertFalse(row["feedback_eligible"])
        # 本地应答也绑实际来源消息与发言人 —— 不留 None
        self.assertEqual(row["source_message_id"], msg.id)
        self.assertEqual(row["user"], self.U)

    async def test_delivery_logged_before_async_commit_finishes(self):
        gate = asyncio.Event()
        committed = []
        async def slow_commit(_result):
            await gate.wait()
            committed.append(1)
        msg = _group_message(group=self.G, sender=self.U)
        task = asyncio.create_task(bot.safe_reply(
            msg, "先记后提交", on_delivered=slow_commit,
            interaction={"route": "reply", "feedback_eligible": True}))
        await asyncio.sleep(0.05)
        # on_delivered 还挂着，delivered 行必须已经落账 —— 反馈窗口才能立即找到它
        rows = self.deliveries()
        self.assertEqual([r["status"] for r in rows], ["delivered"])
        self.assertEqual(committed, [])
        gate.set()
        result = await task
        self.assertTrue(result.delivered)
        self.assertEqual(committed, [1])
        # 全链路只有一条 delivered 记录
        self.assertEqual(len(self.deliveries()), 1)

    async def test_queued_drain_keeps_original_meta_snapshot(self):
        """排队补投：delivered 沿用发起时的元数据快照，后改 config/meta 不生效。"""
        msg = _group_message(group=self.G, sender=self.U,
                             script=[None, None, {"id": "d-1"}])
        meta = {"route": "reply", "feedback_eligible": True,
                "generation": "model", "provider": "gemini", "model": "m-1"}
        result = await bot.safe_reply(msg, "排个队", interaction=meta)
        self.assertEqual(result.status, "queued")
        meta["policy_id"] = "tampered"
        meta["provider"] = "tampered-provider"
        with patch.object(config, "INTERACTION_POLICY_ID", "mutated-v9"):
            await bot.SENDER.drain()
        rows = self.deliveries()
        self.assertEqual([r["status"] for r in rows], ["queued", "delivered"])
        self.assertEqual(rows[0]["outgoing_id"], rows[1]["outgoing_id"])
        for row in rows:
            self.assertEqual(row["policy_id"], config.INTERACTION_POLICY_ID)
            self.assertEqual(row["provider"], "gemini")


class _HandlerBase:
    """handle_group_msg 共用垫层：临时归档 + 真实观察日志 + 短路模型。"""

    G = "GID_OBS_FLOW"
    A, B = "USER_OBS_A", "USER_OBS_B"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = _make_log(self._tmp.name)
        self.addCleanup(self.log.close)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.model_calls = []

        async def fake_model(messages, max_tokens, **kw):
            self.model_calls.append(messages)
            return "（模型应答）"

        for target, name, value in (
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (runtime, "GROUP_BUCKET",
                 storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                     config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", fake_model)):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)

        self.addCleanup(lambda: bot.group_last_random_reply.pop(self.G, None))
        self.addCleanup(lambda: bot.group_buffers.pop(self.G, None))
        self.addCleanup(lambda: bot.processed_msg_ids.clear())

    def _msg(self, text, sender=None, msg_id=None):
        return _group_message(text=text, sender=sender or self.A,
                              group=self.G, msg_id=msg_id)

    async def _drive(self, text, sender=None, is_at=False, msg_id=None):
        fake_self = SimpleNamespace(
            robot=SimpleNamespace(id="BOT_OBS", name="regular"))
        msg = self._msg(text, sender, msg_id)
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return msg

    def decisions(self):
        return [r for r in _rows(self.log.log_dir) if r["kind"] == "decision"]

    def incomings(self):
        return [r for r in _rows(self.log.log_dir) if r["kind"] == "incoming"]


class GateAndIncomingTest(_HandlerBase, unittest.IsolatedAsyncioTestCase):
    """门控决策可区分、不重复调用；入口输入完整落日志。"""

    async def test_incoming_says_how_it_was_called(self):
        with patch.object(bot.naming, "bot_names", return_value=["小王"]):
            await self._drive("小王 在吗", msg_id="by-name")
            await self._drive("今天天气不错", msg_id="nobody")
        calls = {r["message_id"]: (r["at"], r.get("call")) for r in self.incomings()}
        self.assertEqual(calls["by-name"], (True, "name"))
        self.assertEqual(calls["nobody"], (False, None))

    async def test_incoming_records_full_text_and_dedup(self):
        long_text = "长消息 " + "字" * 600
        await self._drive(long_text, is_at=True)
        rows = self.incomings()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["text"].startswith("长消息"))
        self.assertGreater(len(rows[0]["text"]), 500)
        self.assertTrue(rows[0]["at"])
        self.assertFalse(rows[0]["cmd"])
        self.assertEqual(rows[0]["user"], self.A)
        self.assertEqual(rows[0]["actor"], "user")
        self.assertEqual(rows[0]["call"], "at")       # 桌宠据此说「有人@我」而不是「有人喊我名字」
        # 同一 message.id 重复投递：入口去重，不再写第二条 incoming
        msg = self._msg("重复投递", msg_id="dup-1")
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_OBS", name="regular"))
        await bot.GroupBot.handle_group_msg(fake_self, msg)
        await bot.GroupBot.handle_group_msg(fake_self, msg)
        self.assertEqual(len([r for r in self.incomings()
                              if r["message_id"] == "dup-1"]), 1)
        # 观察日志不许进原始消息归档：归档行还是纯「人说了什么」的结构
        pending_rows, _ = self.archive.pending(self.G)
        for r in pending_rows:
            self.assertIn("sender", r)
            self.assertNotIn("event_id", r)
            self.assertNotIn("kind", r)

    async def test_direct_route(self):
        with patch.object(bot, "reply_in_session",
                          AsyncMock(return_value=("ok", None, None))):
            await self._drive("你好", is_at=True)
        routes = [(d["route"], d["selected"], d["reason"]) for d in self.decisions()]
        self.assertIn(("direct", True, "direct_address"), routes)

    async def test_unprompted_turns_record_what_xiaowang_chose(self):
        """没叫它的消息不掷骰子：每条都问它，记下它接了还是没接；刚接过也照问不误。"""
        spoke = ("ok", None, delivery.DeliveryResult("delivered", "ok"))
        with patch.object(bot, "reply_in_session", AsyncMock(return_value=spoke)) as reply:
            await self._drive("今天天气不错哦")
            await self._drive("又来一句日常")
        self.assertEqual(reply.await_count, 2)
        self.assertEqual([(d["route"], d["selected"], d["reason"]) for d in self.decisions()[-2:]],
                         [("banter", True, "spoke")] * 2)
        self.assertNotIn("gate_probability", self.decisions()[-1])

        with patch.object(bot, "reply_in_session", AsyncMock(return_value=("", None, None))):
            await self._drive("再一句闲话")
        declined = self.decisions()[-1]
        self.assertEqual((declined["route"], declined["selected"], declined["reason"]),
                         ("banter", False, "declined"))

    async def test_budget_blocked_is_recorded(self):
        with patch.object(bot.BudgetGate, "check", AsyncMock(return_value=False)):
            await self._drive("你好", is_at=True)
        blocked = self.decisions()[-1]
        self.assertEqual((blocked["route"], blocked["selected"], blocked["reason"]),
                         ("reply", False, "budget_blocked"))



class ProviderMetaTest(unittest.IsolatedAsyncioTestCase):
    """出处元数据：记的是真正答上来的候选；失败清空；任务间隔离。"""

    def _result(self, text="答", finish="stop"):
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish,
                                     message=SimpleNamespace(content=text,
                                                             reasoning_content=None))],
            usage=None)

    def setUp(self):
        self.addCleanup(providers.reset_completion_meta)
        clients = {name: [SimpleNamespace()] for name in providers.PROVIDER_CHAIN}
        ctx = patch.dict(providers.AI_CLIENTS, clients)
        ctx.start(); self.addCleanup(ctx.stop)

    async def test_meta_records_actual_successful_candidate(self):
        calls = []
        empty = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",
                                     message=SimpleNamespace(content="",
                                                             reasoning_content=None))],
            usage=None)
        async def fake_request(client, **kw):
            calls.append(kw["model"])
            return empty if len(calls) == 1 else self._result()
        with patch.object(providers, "_request_completion", fake_request), \
                patch.object(providers, "_bound_budget",
                             lambda: SimpleNamespace(try_consume=lambda: True)):
            text = await providers.call_model([{"role": "user", "content": "hi"}], 50)
        self.assertEqual(text, "答")
        meta = providers.completion_meta()
        self.assertEqual(meta["provider"], providers.PROVIDER_CHAIN[0])
        self.assertEqual(meta["model"], calls[-1])   # 第二个候选，不是第一个
        self.assertNotEqual(meta["model"], calls[0])
        self.assertEqual(meta["tier"], "chat")
        self.assertEqual(meta["finish_reason"], "stop")

    async def test_failure_clears_seeded_meta(self):
        # 先埋一条上一轮的出处：失败调用必须把残留清掉，不许带着旧出处冒充
        providers._completion_meta.set({"provider": "stale", "model": "m-old",
                                        "tier": "chat", "finish_reason": "stop"})
        async def boom(client, **kw):
            raise RuntimeError("stop action")
        with patch.object(providers, "_request_completion", boom), \
                patch.object(providers, "_bound_budget",
                             lambda: SimpleNamespace(try_consume=lambda: True)):
            self.assertIsNone(await providers.call_model(
                [{"role": "user", "content": "hi"}], 50))
        self.assertIsNone(providers.completion_meta())

    async def test_concurrent_tasks_have_isolated_meta(self):
        async def dispatch(client, **kw):
            tag = kw["messages"][-1]["content"]
            await asyncio.sleep(0.01)
            if tag == "B":
                raise RuntimeError("fail")
            return self._result(text="A-ok")
        budget = SimpleNamespace(try_consume=lambda: True)
        # 补丁只打在 gather 外面一次：并发任务里各自 patch 会按退出顺序互相
        # 覆盖还原值，反而把 mock 漏给后面的测试（实测踩过）。
        with patch.object(providers, "_request_completion", dispatch), \
                patch.object(providers, "_bound_budget", lambda: budget):
            async def run(tag):
                await providers.call_model([{"role": "user", "content": tag}], 50)
                return providers.completion_meta()
            meta_a, meta_b = await asyncio.gather(run("A"), run("B"))
        self.assertIsNotNone(meta_a)
        self.assertIsNone(meta_b)

    async def test_request_kwargs_unchanged(self):
        seen = {}
        async def spy(client, **kw):
            seen.update(kw)
            return self._result()
        with patch.object(providers, "_request_completion", spy), \
                patch.object(providers, "_bound_budget",
                             lambda: SimpleNamespace(try_consume=lambda: True)):
            await providers.call_model([{"role": "user", "content": "hi"}], 50)
        self.assertEqual(set(seen), {"model", "messages", "max_tokens",
                                     "temperature", "extra_body"})


class PersonalInjectionFlagTest(_HandlerBase, unittest.IsolatedAsyncioTestCase):
    """personal_memory_used 记的是「真的注进去了」，不是开关状态。"""

    async def _last_delivery(self):
        rows = [r for r in _rows(self.log.log_dir)
                if r["kind"] == "delivery" and r["status"] == "delivered"]
        self.assertTrue(rows)
        return rows[-1]

    async def test_flag_matches_actual_append(self):
        with patch.object(config, "PERSONAL_MEMORY_ENABLED", True), \
                patch.object(bot, "get_ai_reply",
                             AsyncMock(side_effect=lambda *a, **k: ("答", None))):
            # 旁路 mock 掉 get_ai_reply 时 trace 保持 False —— 不许自称进过
            await bot.reply_in_session(
                self._msg("hi"), f"{self.G}_{self.A}", "hi",
                is_owner=False, mode="normal", personal_note="（个人原话）")
            row = await self._last_delivery()
            self.assertFalse(row["personal_memory_used"])
            self.assertTrue(row["personal_memory_enabled"])
            self.assertEqual(row["route"], "reply")
            self.assertEqual(row["generation"], "model")

    async def test_flag_true_only_when_guard_appends(self):
        real = bot.get_ai_reply
        with patch.object(config, "PERSONAL_MEMORY_ENABLED", True):
            await bot.reply_in_session(
                self._msg("hi"), f"{self.G}_{self.A}", "hi",
                personal_note="（本轮的个人原话块）")
            row = await self._last_delivery()
            self.assertTrue(row["personal_memory_used"])
            # 被压制的路径（随机插嘴）即使带了材料也不算注入
            await bot.reply_in_session(
                self._msg("hi2"), f"{self.G}_{self.A}", "hi2",
                unprompted=True, personal_note="（本轮的个人原话块）")
            row2 = await self._last_delivery()
            self.assertFalse(row2["personal_memory_used"])
            self.assertEqual(row2["route"], "banter")
        # 元数据字段绝不渗进提示词：模型看到的最后一层里没有 policy/event 字样
        last_messages = self.model_calls[-1]
        envelope = json.dumps(last_messages, ensure_ascii=False)
        self.assertNotIn(config.INTERACTION_POLICY_ID, envelope)
        self.assertNotIn("personal_memory_used", envelope)


if __name__ == "__main__":
    unittest.main()
