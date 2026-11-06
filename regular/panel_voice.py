"""Explicit, authenticated local tickets for the pet's bounded voice session.

The panel owns authentication. These routes add CSRF, exact Origin validation and
single-use tickets; only the consumed WebSocket ticket may reach the voice worker.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import hmac
import secrets
import time

from aiohttp import web

TOKEN_HEADER = "X-Panel-Token"
TICKET_HEADER = "X-Voice-Ticket"
STREAM_PATH = "/companion/voice/stream"
FORMAT = {"rate": 24000, "channels": 1, "sample_width": 2, "encoding": "pcm_s16le"}
LIMITS = {"session_seconds": 180, "turns": 12, "input_seconds": 20, "output_seconds": 30}


def _readiness(cfg):
    from regular.realtime_voice import readiness
    return readiness(cfg)


def _session(cfg):
    from regular.realtime_voice import RealtimeSession
    return RealtimeSession(cfg)


def _check_origin(request):
    # Native clients omit Origin and authenticate with headers. A browser must
    # match the port too: another localhost application is a different origin.
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden(text="请从本机面板打开语音。")


@dataclass
class _Active:
    session: object
    ws: web.WebSocketResponse
    task: asyncio.Task | None = None
    closing: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class VoiceRoutes:
    """One pending ticket or one active voice session per local panel."""

    def __init__(self, cfg, checked_form, token_check, *, status_provider, session_factory,
                 clock=time.monotonic, ticket_ttl=30, session_timeout=180):
        self.cfg, self.checked_form, self.token_check = cfg, checked_form, token_check
        self.status_provider, self.session_factory = status_provider, session_factory
        self.clock, self.ticket_ttl, self.session_timeout = clock, ticket_ttl, session_timeout
        self.pending = None
        self.expiry = None
        self.active = None
        self.closed = False

    def _clear_pending(self):
        if self.expiry is not None:
            self.expiry.cancel()
            self.expiry = None
        self.pending = None

    def _prune(self):
        if self.pending is not None and self.clock() >= self.pending[1]:
            self._clear_pending()

    def _status(self):
        self._prune()
        try:
            state = self.status_provider(self.cfg)
        except Exception:
            state = {"available": False, "reason": "语音服务暂时不可用，请检查设置。"}
        # Never return provider credentials or arbitrary worker fields.
        return {"available": bool(state.get("available")) and not self.closed,
                "reason": str(state.get("reason") or "")[:300],
                "busy": self.pending is not None or self.active is not None,
                "daily_limit": state.get("daily_limit", 30),
                "format": dict(FORMAT), "limits": dict(LIMITS)}

    async def status(self, request):
        _check_origin(request)
        return web.json_response(self._status())

    async def start(self, request):
        _check_origin(request)
        await self.checked_form(request)
        state = self._status()
        if state["busy"]:
            return web.json_response({"error": "已有语音会话，请先结束它。"}, status=409)
        if not state["available"]:
            return web.json_response({"error": state["reason"] or "语音服务暂时不可用。"}, status=503)
        ticket = secrets.token_urlsafe(32)
        self.pending = (ticket, self.clock() + self.ticket_ttl)
        self.expiry = asyncio.get_running_loop().call_later(self.ticket_ttl, self._clear_pending)
        return web.json_response({"ticket": ticket, "stream": STREAM_PATH,
                                  "format": dict(FORMAT), "limits": dict(LIMITS)})

    async def stream(self, request):
        _check_origin(request)
        # Cookie/query authentication alone is insufficient for this native link.
        supplied = request.headers.get(TOKEN_HEADER, "")
        try:
            authenticated = bool(supplied and self.token_check(supplied))
        except (TypeError, ValueError):
            authenticated = False
        if not authenticated:
            raise web.HTTPUnauthorized(text="语音连接需要本机会话授权。")
        self._prune()
        ticket = request.headers.get(TICKET_HEADER, "")
        if (self.closed or self.pending is None or not ticket
                or not hmac.compare_digest(ticket.encode(), self.pending[0].encode())):
            raise web.HTTPForbidden(text="语音连接已失效，请重新开始。")
        ws = web.WebSocketResponse(timeout=1, heartbeat=15, compress=False, max_msg_size=64 * 1024)
        if not ws.can_prepare(request).ok:
            raise web.HTTPBadRequest(text="请使用本机语音连接。")
        self._clear_pending()  # Consume before any await or upstream action.
        try:
            session = self.session_factory(self.cfg)
        except Exception:
            raise web.HTTPServiceUnavailable(text="语音服务暂时不可用，请检查设置。") from None
        ws.headers["X-Regular-Panel"] = "1"
        ws.headers["Cache-Control"] = "no-store"
        active = _Active(session, ws)
        self.active = active
        try:
            await ws.prepare(request)
            if self.closed or active.closing:
                return ws
            active.task = asyncio.create_task(session.run(ws))
            await asyncio.wait_for(active.task, timeout=self.session_timeout)
        except asyncio.TimeoutError:
            if not ws.closed:
                with suppress(ConnectionError, RuntimeError):
                    await ws.send_json({"type": "closed", "reason": "本次语音已到时，请重新开始。"})
        except asyncio.CancelledError:
            raise
        except web.HTTPException:
            raise
        except Exception:
            if not ws.closed:
                with suppress(ConnectionError, RuntimeError):
                    await ws.send_json({"type": "error", "message": "语音连接中断；没有自动重连。"})
        finally:
            await self._finish(active)
        return ws

    async def _finish(self, active):
        if active.closing:
            await active.finished.wait()
            return
        active.closing = True
        try:
            if active.task is not None and not active.task.done():
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
            with suppress(Exception):
                await asyncio.wait_for(active.session.close(), timeout=3)
            with suppress(Exception):
                await active.ws.close(code=1001, message="语音会话已结束。".encode())
        finally:
            if self.active is active:
                self.active = None
            active.finished.set()

    async def close(self, app=None):
        self.closed = True
        self._clear_pending()
        if self.active is not None:
            await self._finish(self.active)


def install_voice_routes(app, cfg, csrf, checked_form, *, token_check,
                         session_factory=None, status_provider=None):
    """Install on an already authenticated app; no bot, device or upstream startup."""
    routes = VoiceRoutes(cfg, checked_form, token_check,
                         status_provider=status_provider or _readiness,
                         session_factory=session_factory or _session)
    app.router.add_get("/companion/voice/status", routes.status)
    app.router.add_post("/companion/voice/start", routes.start)
    app.router.add_get(STREAM_PATH, routes.stream)
    # aiohttp shuts WebSockets down before final cleanup; both calls are idempotent.
    app.on_shutdown.append(routes.close)
    app.on_cleanup.append(routes.close)
    return routes
