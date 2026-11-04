"""QQ delivery receipts and bounded retry queue shared by every reply path."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import logging
import time
from typing import Callable

import aiohttp
from botpy.message import C2CMessage

logger = logging.getLogger("qqbot")
GROUP_PASSIVE_WINDOW_SECONDS = 240
C2C_PASSIVE_WINDOW_SECONDS = 3000


def passive_reply_window(message):
    return C2C_PASSIVE_WINDOW_SECONDS if isinstance(message, C2CMessage) else GROUP_PASSIVE_WINDOW_SECONDS


def message_age_seconds(message):
    raw = getattr(message, "timestamp", None)
    if not raw:
        return None
    try:
        sent = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc) if sent.tzinfo else datetime.now()
    return (now - sent).total_seconds()


def connection_error(error):
    return isinstance(error, (ConnectionError, asyncio.TimeoutError,
                              aiohttp.ClientConnectionError))



@dataclass(frozen=True)
class DeliveryResult:
    status: str
    text: str
    receipt: object = None
    reason: str = ""
    fallback: bool = False

    @property
    def delivered(self):
        return self.status == "delivered"


@dataclass
class PendingSend:
    message: object
    text: str
    created_at: float
    sequence: int = 1
    on_delivered: Callable | None = None
    is_current: Callable | None = None
    retry_lock: object = None
    send_lock: object = None
    prepare: Callable | None = None
    attempts: int = 0
    on_outcome: Callable | None = None
    fallback: bool = False
    # 引用回复：被引用消息的索引（事件 message_scene.ext 里的 msg_idx）；QQ 不收就去掉再发
    quote: str | None = None


class Sender:
    """Only a positive platform receipt commits a reply; retries keep its sequence."""

    def __init__(self, *, fallback=None, max_pending=20, clock=time.time):
        self.pending = deque()
        self.max_pending = max_pending
        self.fallback = fallback
        self.clock = clock
        self._sequences = OrderedDict()
        self._drain_lock = asyncio.Lock()
        self.last_status = None
        self.last_delivery_at = None

    def _next_sequence(self, message):
        key = (getattr(message, "group_openid", None), getattr(message, "id", id(message)))
        value = self._sequences.pop(key, 0) + 1
        self._sequences[key] = value
        if len(self._sequences) > 2000:
            self._sequences.popitem(last=False)
        return value

    def _observe(self, item, result):
        """One synchronous outcome notice per meaningful state; never breaks delivery."""
        if item is not None and item.on_outcome is not None and result is not None:
            try:
                item.on_outcome(result)
            except Exception:
                # 观察者崩溃绝不拖累已确认的发送与状态提交。
                logger.exception("投递观察者回调失败（不影响发送）")
        return result

    async def _delivered(self, item, receipt):
        result = DeliveryResult("delivered", item.text, receipt, fallback=item.fallback)
        self.last_status = result.status
        self.last_delivery_at = self.clock()
        logger.info("📤 [已送达%s] %s", "·引用" if item.quote else "",
                    item.text.replace("\n", " ")[:120])
        # 观察记录先于业务回调落账：异步/卡住的 on_delivered 不许拖延
        # 「已送达」进入索引，否则随后赶到的显式反馈会找不到刚送达的回复。
        self._observe(item, result)
        if item.on_delivered is not None:
            try:
                pending = item.on_delivered(result)
                if inspect.isawaitable(pending):
                    await pending
            except Exception:
                # A failed state callback must never resend a message already delivered.
                logger.exception("发送已确认，但提交回复状态失败")
        return result

    def _queue(self, item, reason):
        if len(self.pending) >= self.max_pending:
            evicted = self.pending.popleft()
            self._observe(evicted, DeliveryResult(
                "dropped", evicted.text, reason="queue overflow",
                fallback=evicted.fallback))
            logger.warning("补投队列已满，撤下最早的未确认回复")
        self.pending.append(item)
        self.last_status = "queued"
        return self._observe(item, DeliveryResult(
            "queued", item.text, reason=reason, fallback=item.fallback))

    async def _attempt(self, item):
        # Preparation and confirmed-state commit share the same optional scope lock.
        async with item.send_lock or nullcontext():
            if item.is_current and not item.is_current():
                self.last_status = "superseded"
                return self._observe(item, DeliveryResult(
                    "superseded", item.text, fallback=item.fallback))
            age = message_age_seconds(item.message)
            if age is not None and age > passive_reply_window(item.message):
                self.last_status = "expired"
                return self._observe(item, DeliveryResult(
                    "expired", item.text, reason="passive reply window elapsed",
                    fallback=item.fallback))
            if item.prepare is not None:
                text = item.prepare()
                if text != item.text and item.attempts:
                    item.sequence = self._next_sequence(item.message)
                item.text = text
            item.attempts += 1
            extra = ({"message_reference": {"message_id": item.quote,
                                            "ignore_get_message_error": True}}
                     if item.quote else {})
            sent = await item.message.reply(content=item.text, msg_type=0, msg_seq=item.sequence,
                                            **extra)
            return await self._delivered(item, sent) if sent else None

    def _drop_quote(self, item, error):
        """引用回复被拒：引用只是让人看清回的是谁，去掉它再发一次，话不能因此丢了。"""
        if not item.quote:
            return False
        logger.warning("QQ 不收引用回复，改发普通回复: %s", str(error)[:120])
        item.quote = None
        item.sequence = self._next_sequence(item.message)
        return True

    async def reply(self, message, text, *, on_delivered=None, is_current=None,
                    retry_lock=None, send_lock=None, prepare=None, on_outcome=None,
                    quote=None):
        item = PendingSend(message, text, self.clock(), self._next_sequence(message),
                           on_delivered, is_current, retry_lock, send_lock, prepare,
                           on_outcome=on_outcome, quote=quote)
        # 吞掉的超时只再试一次；换兜底话、去掉引用各最多一回，不占这一次
        unconfirmed = 0
        while unconfirmed < 2:
            try:
                result = await self._attempt(item)
            except Exception as error:
                if connection_error(error):
                    return self._queue(item, type(error).__name__)
                if not item.fallback and self.fallback and ("40034006" in str(error) or "违规" in str(error)):
                    item.text = self.fallback(message)
                    item.prepare = None
                    item.fallback = True
                    item.sequence = self._next_sequence(message)
                    continue
                if self._drop_quote(item, error):
                    continue
                self.last_status = "rejected"
                logger.warning("QQ拒收回复: %s", str(error)[:160])
                return self._observe(item, DeliveryResult(
                    "rejected", item.text, reason=type(error).__name__,
                    fallback=item.fallback))
            if result is not None:
                return result
            # botpy can swallow a timeout. Unchanged payload retries reuse the sequence.
            unconfirmed += 1
        return self._queue(item, "unconfirmed timeout")

    async def drain(self):
        async with self._drain_lock:
            count = len(self.pending)
            for _ in range(count):
                item = self.pending.popleft()
                async with item.retry_lock or nullcontext():
                    age = message_age_seconds(item.message)
                    if (self.clock() - item.created_at > passive_reply_window(item.message) - 15
                            or (age is not None and age > passive_reply_window(item.message))):
                        self.last_status = "expired"
                        self._observe(item, DeliveryResult(
                            "expired", item.text,
                            reason="passive reply window elapsed",
                            fallback=item.fallback))
                        continue
                    try:
                        result = await self._attempt(item)
                    except Exception as error:
                        if connection_error(error):
                            self.pending.appendleft(item)
                            break
                        if self._drop_quote(item, error):
                            self.pending.append(item)
                            continue
                        self.last_status = "rejected"
                        self._observe(item, DeliveryResult(
                            "rejected", item.text, reason=type(error).__name__,
                            fallback=item.fallback))
                        continue
                    if result is None:
                        self.pending.append(item)

    def status(self):
        return {"queued": len(self.pending), "last_status": self.last_status,
                "last_delivery_at": self.last_delivery_at}
