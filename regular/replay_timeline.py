"""Two bounded message-replay barriers, independent of model/network latency.

This module belongs to the evaluation driver, not the bot under test. It can
therefore exercise both old and new source trees without changing either bot.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import time
from contextlib import ExitStack
from unittest.mock import patch


def validate_timeline(spec, messages):
    if not isinstance(spec, dict):
        raise ValueError("timeline must be an object")
    mode = spec.get("mode")
    if mode not in ("overlap", "late_delivery"):
        raise ValueError("timeline mode must be overlap or late_delivery")
    def index(name):
        value = spec.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(messages):
            raise ValueError(f"timeline {name} must be a message index")
        if messages[value].get("bot"):
            raise ValueError(f"timeline {name} must refer to an incoming message")
        return value
    if mode == "overlap":
        first, second = index("first"), index("second")
        if second != first + 1:
            raise ValueError("timeline overlap requires consecutive first/second messages")
    else:
        index("target")
        delivery = spec.get("delivery")
        if not isinstance(delivery, dict) or not all(
                isinstance(delivery.get(k), str) and delivery[k].strip()
                for k in ("sender", "question", "text")):
            raise ValueError("timeline late_delivery needs sender/question/text")
    wait = spec.get("max_wait_seconds", 30)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or not 0 < wait <= 120:
        raise ValueError("timeline max_wait_seconds must be in (0, 120]")
    sessions = spec.get("initial_sessions", {})
    if not isinstance(sessions, dict):
        raise ValueError("timeline initial_sessions must be an object")
    for sender, history in sessions.items():
        if not isinstance(sender, str) or not isinstance(history, list) or any(
                not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                or not isinstance(m.get("content"), str) for m in history):
            raise ValueError("timeline initial_sessions must contain role/content histories")
    if not isinstance(spec.get("initial_context", []), list):
        raise ValueError("timeline initial_context must be a list")
    for row in spec.get("initial_context", []):
        if (not isinstance(row, dict) or not isinstance(row.get("sender"), str)
                or not isinstance(row.get("text"), str)):
            raise ValueError("timeline initial_context needs sender/text")
    reply = spec.get("initial_reply")
    if reply is not None and (not isinstance(reply, dict)
                             or not isinstance(reply.get("text"), str)):
        raise ValueError("timeline initial_reply needs text")
    return spec


class Timeline:
    def __init__(self, spec, messages, bot, runtime, group, trace, row_context):
        self.spec = validate_timeline(spec, messages)
        self.messages, self.bot, self.runtime, self.group = messages, bot, runtime, group
        self.trace, self.row_context = trace, row_context
        self.started, self.waiting, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.tasks = []
        self.stack = ExitStack()
        self.wait_seconds = float(spec.get("max_wait_seconds", 30))
        self.trace.setdefault("timeline_events", [])
        self.trace.setdefault("deliveries", [])

    def event(self, kind, index=None, **fields):
        self.trace["timeline_events"].append({"kind": kind, "message_index": index,
                                               "ts": time.time(), **fields})

    def seed(self):
        now = time.time()
        for sender, history in self.spec.get("initial_sessions", {}).items():
            self.runtime.SESSIONS._sessions[f"{self.group}_{sender}"] = {
                "messages": [dict(m) for m in history], "last_time": now}
        for i, row in enumerate(self.spec.get("initial_context", [])):
            self.bot.group_buffers[self.group].append({
                "sender": row["sender"], "text": row["text"],
                "time": now - max(0, float(row.get("age_seconds", 1))),
                "message_id": row.get("message_id") or f"timeline-initial-{i}",
                **({"msg_idx": row["msg_idx"]} if row.get("msg_idx") else {})})
        reply = self.spec.get("initial_reply")
        if reply:
            self.remember(reply, now=now - max(0, float(reply.get("age_seconds", 1))))

    def remember(self, reply, *, now=None):
        now = time.time() if now is None else now
        sender, text = reply.get("sender"), reply["text"]
        self.runtime.FEEDBACK.remember(self.group, sender, text,
            route=reply.get("route", "reply"), outgoing_id=reply.get("outgoing_id", "timeline-delivery"), now=now)
        # Initial records must retain their actual relative age. _note_own_line()
        # always stamps the current clock, so append its existing bounded format.
        self.bot.own_lines[self.group].append({"time": now, "text": text, "medium": "text",
                                               "status": "delivered", "user": sender})

    def install(self):
        self.seed()
        real_reply = self.bot.reply_in_session
        async def reply(message, sid, text, **kw):
            row = self.row_context.get() or {}
            index = row.get("index")
            if self.spec["mode"] == "late_delivery" and index == self.spec["target"]:
                self.event("reply_waiting", index)
                self.waiting.set()
            return await real_reply(message, sid, text, **kw)
        self.stack.enter_context(patch.object(self.bot, "reply_in_session", reply))
        if self.spec["mode"] == "overlap":
            real_unprompted = self.bot.reply_unprompted
            async def unprompted(message, sid, text, **kw):
                row = self.row_context.get() or {}
                index = row.get("index")
                if index == self.spec["second"]:
                    self.event("reply_waiting", index, owed=bool(kw.get("owed")))
                    self.waiting.set()
                return await real_unprompted(message, sid, text, **kw)
            self.stack.enter_context(patch.object(self.bot, "reply_unprompted", unprompted))
            # Direct second questions go straight to reply_in_session.
            old_reply = self.bot.reply_in_session
            async def direct_reply(message, sid, text, **kw):
                row = self.row_context.get() or {}
                index = row.get("index")
                if index == self.spec["second"]:
                    self.event("reply_waiting", index)
                    self.waiting.set()
                return await old_reply(message, sid, text, **kw)
            self.stack.enter_context(patch.object(self.bot, "reply_in_session", direct_reply))

    def model_started(self, index):
        self.event("generation_started", index)
        if self.spec["mode"] == "overlap" and index == self.spec["first"]:
            self.started.set()

    async def model_finished(self, index):
        if self.spec["mode"] == "overlap" and index == self.spec["first"]:
            await asyncio.wait_for(self.release.wait(), self.wait_seconds)

    async def run(self, drive):
        self.install()
        replies = {}
        try:
            if self.spec["mode"] == "overlap":
                first, second = self.spec["first"], self.spec["second"]
                for i in range(first):replies[i] = await drive(i)
                first_task = asyncio.create_task(drive(first));self.tasks.append(first_task)
                await asyncio.wait_for(self.started.wait(), self.wait_seconds)
                second_task = asyncio.create_task(drive(second));self.tasks.append(second_task)
                await asyncio.wait_for(self.waiting.wait(), self.wait_seconds)
                self.event("first_released", first);self.release.set()
                replies[first], replies[second] = await asyncio.gather(first_task, second_task)
                for i in range(second + 1, len(self.messages)):replies[i] = await drive(i)
                return "\n".join(f"【回应第{i + 1}句】" + ("\n".join(replies[i]) or "〔未发送〕")
                                 for i in sorted(replies) if not self.messages[i].get("bot"))
            target = self.spec["target"]
            for i in range(target):replies[i] = await drive(i)
            sender = self.messages[target].get("sender") or "REPLAY_USER"
            sid = f"{self.group}_{sender}"
            async with self.runtime.SESSIONS.lock(sid):
                target_task = asyncio.create_task(drive(target));self.tasks.append(target_task)
                await asyncio.wait_for(self.waiting.wait(), self.wait_seconds)
                delivery = self.spec["delivery"]
                source_sid = f"{self.group}_{delivery['sender']}"
                kw = {}
                if "received_at" in inspect.signature(self.runtime.SESSIONS.record).parameters:
                    kw["received_at"] = time.time() - max(0.001, float(delivery.get("question_age_seconds", 1)))
                self.runtime.SESSIONS.record(source_sid, delivery["question"], delivery["text"], **kw)
                self.remember(delivery)
                evidence = {"kind": "delivery", "source_message_id": delivery.get("source_message_id"),
                            "user": delivery["sender"], "text": delivery["text"], "status": "delivered",
                            "ts": time.time(), "injected": True}
                self.trace.setdefault("events", []).append(evidence)
                self.trace["deliveries"].append(dict(evidence))
                self.event("fixed_delivery", target)
            replies[target] = await target_task
            for i in range(target + 1, len(self.messages)):replies[i] = await drive(i)
            return "\n".join(replies[target]) or "〔不接〕"
        finally:
            self.release.set()
            for task in self.tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.stack.close()
