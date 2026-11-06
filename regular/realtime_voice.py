"""Bounded desktop voice transport. No microphone, QQ, or configuration writes.

The panel owns local authentication and the single active session. Only the small
native protocol below is accepted; provider events and credentials stay here.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager, suppress
import json
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import aiohttp

import prompts
from voice_budget import Budget
from regular import capabilities, instance_settings, local_chat, presence
from regular.keys import read_api_keys

RATE = 24000
MAX_SESSION_SECONDS = 180
MAX_TURNS = 12
MAX_INPUT_SECONDS = 20
MAX_OUTPUT_SECONDS = 30
MAX_OUTPUT_TOKENS = 1024
MAX_CHUNK_BYTES = 64 * 1024
_BYTES_PER_SECOND = RATE * 2

_LOCAL_NOTE = """
【当前是桌面语音，不是 QQ 群】
正在和本机维护者语音交谈。保持原来短、口语、好玩的性格，用自然中文说话，别念动作或协议。
你是 AI 合成语音。只知道本次通话和系统提供的数字概况，不知道群里原话；不能编聊天话题、共同经历或身份。
数字为本机记录，没记录不等于群里没人说话。管理参数是配置事实，不证明服务权限正常。
用户明确要开关一项群聊能力时，调用对应能力的开关工具提出一张确认卡；问用法或引用的话不算授权。
代码或部署改动只可用 propose_handoff 提议交给编码 Agent。每轮最多一张卡，只有用户点击卡片才会执行。
你不执行配置修改，不发 QQ，不画图，不设提醒，不运行命令；不要宣称已完成。提议后等本机确认。
"""


@asynccontextmanager
async def official_connector(url, key):
    """Authenticated outbound websocket; reject redirects before following them."""
    async def reject_redirect(_session, _context, _params):
        raise aiohttp.ClientConnectionError("voice_redirect_rejected")
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(reject_redirect)
    async with aiohttp.ClientSession(trust_env=True, trace_configs=[trace]) as client:
        async with client.ws_connect(url, headers={"Authorization": "Bearer " + key},
                                     timeout=15, heartbeat=15, max_msg_size=2 * 1024 * 1024,
                                     compress=0) as ws:
            yield ws


def _settings(cfg):
    """Snapshot current effective .env settings once per new desktop session."""
    values = capabilities._values(cfg)
    value = lambda name, default="": capabilities._value(cfg, values, name, default)
    preset = getattr(cfg, "PROVIDER_PRESETS", {}).get("openai", {})
    source = str(value("OPENAI_BASE_URL", preset.get("base_url", "")) or "")
    default_key = str(value("OPENAI_API_KEY", preset.get("api_key", "")) or "")
    keys = read_api_keys(instance_settings.instance_dir(cfg) / "api_keys_openai.txt", default_key)
    audio = SimpleNamespace(VOICE_API_KEY=str(value("VOICE_API_KEY", "") or ""),
                            VOICE_BASE_URL=str(value("VOICE_BASE_URL", "https://api.openai.com/v1") or ""),
                            PROVIDER_PRESETS={"openai": {"base_url": source}})
    raw_limit = value("DESKTOP_VOICE_DAILY_LIMIT", 30)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = -1
    return SimpleNamespace(key=capabilities.audio_key(audio, keys[0] if keys else ""),
                           base_url=audio.VOICE_BASE_URL,
                           model=str(value("VOICE_OUTPUT_MODEL", "gpt-realtime-2.1-mini") or ""),
                           voice=str(value("VOICE_OUTPUT_VOICE", "marin") or ""),
                           verify=capabilities._bool(value("SSL_VERIFY", True)), limit=limit)


def _reason(settings):
    if not settings.verify or not capabilities.official(settings.base_url):
        return "桌面语音需要验证证书的官方 OpenAI 语音地址。"
    if not settings.key:
        return "请先在语音服务中配置 API Key。"
    if any(not v.strip() or len(v) > 200 or any(c in v for c in "\r\n\x00")
           for v in (settings.model, settings.voice)):
        return "请先配置有效的实时语音模型和音色。"
    if not 0 < settings.limit <= 100:
        return "桌面语音每日轮数为 0 或配置无效，请在语音服务中调整。"
    return ""


def readiness(cfg):
    """Read-only configuration facts; no provider access or local audio required."""
    try:
        s = _settings(cfg)
        reason = _reason(s)
        if not reason and Budget(Path(cfg.DATA_DIR) / "voice-outbound-budget.sqlite3",
                                 "desktop_realtime", s.limit).exhausted():
            reason = "今天的桌面语音轮数用完了。"
        return {"available": not reason, "reason": reason, "model": s.model,
                "voice": s.voice, "daily_limit": max(0, s.limit)}
    except (OSError, ValueError, TypeError):
        return {"available": False, "reason": "无法读取桌面语音配置。", "model": "", "voice": "", "daily_limit": 0}


def _instructions(cfg):
    names = getattr(cfg, "BOT_NAMES", ())
    name = names[0] if isinstance(names, (tuple, list)) and names else "老群友"
    return ((prompts.PROMPT_NORMAL + prompts.PROMPT_SHARED_RULES)
            .replace("{bot}", str(name)).replace("{owner}", "群主") + _LOCAL_NOTE)


def _context(cfg):
    """Only locally selected booleans and a numeric summary leave this machine."""
    flags = "、".join(f"{label}{'开' if capabilities.enabled(cfg, flag) else '关'}"
                     for label, flag in local_chat.MANAGED.items())
    try:
        summary = presence.render_day_summary(presence.day_summary(cfg))
    except (OSError, ValueError, TypeError, AttributeError):
        summary = "今天的本机数字概况暂不可用。"
    return "【本轮本机状态】群聊能力开关：" + flags + "。\n" + summary


def _tools():
    # Bind the capability in the declared tool name. Realtime tools do not expose
    # strict JSON schema mode; asking for a second semantic flag can drift to an
    # undeclared synonym even when its parameter advertises an enum.
    tools = [{"type": "function", "name": "propose_" + flag,
              "description": f"仅提出『{label}』开关确认卡。enabled=true 表示开启，false 表示关闭；不执行修改。",
              "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}},
                             "required": ["enabled"], "additionalProperties": False}}
             for label, flag in local_chat.MANAGED.items()]
    return tools + [
        {"type": "function", "name": "propose_handoff", "description": "仅提出交给编码 Agent 的确认卡。",
         "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]


class _ProtocolError(Exception):
    pass


class RealtimeSession:
    """One isolated conversation, using explicit PTT or code-controlled server VAD."""

    def __init__(self, cfg, *, connector=official_connector, budget=None, mode="ptt"):
        if mode not in ("ptt", "continuous"):
            raise ValueError("invalid voice mode")
        self.cfg = cfg
        self.mode = mode
        self._settings = _settings(cfg)
        self._connector = connector
        self._budget = budget if budget is not None else Budget(
            Path(cfg.DATA_DIR) / "voice-outbound-budget.sqlite3", "desktop_realtime", self._settings.limit)
        self._native = self._upstream = None
        self._tasks = []
        self._closed = False
        self._ready = False
        self._ticket = None
        self._capture = False
        self._input_bytes = 0
        self._capture_at = None
        self._turn = 0
        self._response_turn = 0
        self._response_id = None
        self._response_finished = asyncio.Event()
        self._response_finished.set()
        self._response_at = None
        self._discard = False
        self._cancel_sent = False
        self._last_item = None
        self._last_content_index = 0
        self._audio_bytes = 0
        self._audio_open = False
        self._truncated = False
        self._transcript = ""
        self._proposal_sent = False
        self._started_at = None
        self._total_input_bytes = 0
        self._paused = False
        self._speech = None
        self._speech_id = 0
        self._utterances = asyncio.Queue(maxsize=1)
        self._interrupt_ack = asyncio.Event()
        self._interrupt_ack.set()
        self._ack_pending = False
        self._control_lock = asyncio.Lock()

    async def run(self, native_ws):
        self._native = native_ws
        reason = _reason(self._settings)
        if reason:
            await self._emit("error", message=reason)
            return
        endpoint = urlsplit(self._settings.base_url)
        url = "wss://api.openai.com" + endpoint.path.rstrip("/") + "/realtime?" + urlencode({"model": self._settings.model})
        try:
            self._started_at = time.monotonic()
            await asyncio.wait_for(self._conversation(url), MAX_SESSION_SECONDS)
        except asyncio.TimeoutError:
            await self._emit("error", message="这次语音已到 3 分钟，请结束后重新开启。", code="session_timeout")
        except _ProtocolError as error:
            await self._emit("error", message=str(error), code="protocol")
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit("error", message="实时语音连接中断，没有自动重试。", code="connection")
        finally:
            await self.close()

    async def _conversation(self, url):
        async with self._connector(url, self._settings.key) as upstream:
            self._upstream = upstream
            detection = ({"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
                          "silence_duration_ms": 500, "create_response": False, "interrupt_response": False}
                         if self.mode == "continuous" else None)
            await upstream.send_json({"type": "session.update", "session": {
                "type": "realtime", "instructions": _instructions(self.cfg),
                "output_modalities": ["audio"], "max_output_tokens": MAX_OUTPUT_TOKENS,
                "audio": {"input": {"format": {"type": "audio/pcm", "rate": RATE}, "turn_detection": detection},
                          "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self._settings.voice}},
                "tools": _tools(), "tool_choice": "auto"}})
            self._tasks = [asyncio.create_task(self._client()), asyncio.create_task(self._provider()),
                           asyncio.create_task(self._watchdog())]
            if self.mode == "continuous":
                self._tasks.append(asyncio.create_task(self._continuous_responses()))
            done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            if not task.done() and task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(t for t in self._tasks if t is not asyncio.current_task()), return_exceptions=True)
        if self._ticket:
            self._ticket.release()
            self._ticket = None
        if self._upstream and not self._response_finished.is_set():
            with suppress(Exception):
                await self._upstream.send_json({"type": "response.cancel"})
        await self._emit("closed", reason="ended")

    async def _emit(self, kind, **fields):
        if self._native is not None:
            with suppress(ConnectionError, RuntimeError, aiohttp.ClientError):
                await self._native.send_json({"type": kind, **fields})

    async def _interrupt(self, played_ms=0):
        await self._cancel_response()
        if self._last_item and not self._truncated:
            duration = self._audio_bytes * 1000 // _BYTES_PER_SECOND
            await self._upstream.send_json({"type": "conversation.item.truncate", "item_id": self._last_item,
                                           "content_index": self._last_content_index,
                                           "audio_end_ms": min(played_ms, duration)})
            self._truncated = True
        if self._audio_open:
            self._audio_open = False
            await self._emit("audio.end", turn_id=self._response_turn)

    async def _cancel_response(self):
        if not self._response_finished.is_set() and not self._cancel_sent:
            self._discard = self._cancel_sent = True
            await self._upstream.send_json({"type": "response.cancel"})

    async def _client(self):
        while True:
            msg = await self._native.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                data = msg.data
                if self.mode == "continuous":
                    if not self._ready or not data or len(data) % 2 or len(data) > MAX_CHUNK_BYTES:
                        raise _ProtocolError("收到无效的录音片段。")
                    self._total_input_bytes += len(data)
                    if self._total_input_bytes > _BYTES_PER_SECOND * MAX_SESSION_SECONDS:
                        raise _ProtocolError("这次语音已到音频上限，请重新开启。")
                    if not self._paused:
                        await self._upstream.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode("ascii")})
                    continue
                if not self._capture or not data or len(data) % 2 or len(data) > MAX_CHUNK_BYTES:
                    raise _ProtocolError("收到无效的录音片段。")
                self._input_bytes += len(data)
                if self._input_bytes > _BYTES_PER_SECOND * MAX_INPUT_SECONDS:
                    raise _ProtocolError("一次最多说 20 秒，请分开说。")
                if self._ticket:
                    self._ticket.commit()  # Account before any possibly billable input leaves.
                    self._ticket = None
                await self._upstream.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode("ascii")})
                continue
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = json.loads(msg.data)
            except (ValueError, TypeError):
                raise _ProtocolError("语音控制消息格式不正确。") from None
            if not isinstance(event, dict) or len(msg.data) > 2048:
                raise _ProtocolError("语音控制消息格式不正确。")
            kind = event.get("type")
            if kind == "stop":
                return
            if not self._ready:
                raise _ProtocolError("语音还没有连接完成。")
            if self.mode == "continuous":
                await self._continuous_control(event)
                continue
            if kind == "interrupt":
                played = event.get("played_ms", 0)
                if type(played) is not int or not 0 <= played <= MAX_OUTPUT_SECONDS * 1000:
                    raise _ProtocolError("播放进度无效。")
                await self._interrupt(played)
            elif kind == "input.start":
                if self._capture:
                    raise _ProtocolError("录音已开始。")
                if self._turn >= MAX_TURNS:
                    raise _ProtocolError("这次语音已到 12 轮，请重新开启。")
                await self._interrupt()
                self._ticket = self._budget.reserve(MAX_INPUT_SECONDS, MAX_OUTPUT_TOKENS)
                if self._ticket is None:
                    raise _ProtocolError("今天的桌面语音轮数用完了。")
                self._turn += 1
                self._capture, self._input_bytes, self._capture_at = True, 0, time.monotonic()
                await self._upstream.send_json({"type": "input_audio_buffer.clear"})
                await self._upstream.send_json({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "system", "content": [{"type": "input_text", "text": _context(self.cfg)}]}})
                await self._emit("state", state="listening")
            elif kind == "input.cancel":
                await self._cancel_capture()
                await self._emit("state", state="idle")
            elif kind == "input.commit":
                if not self._capture or self._input_bytes < _BYTES_PER_SECOND // 10:
                    await self._cancel_capture()
                    await self._emit("state", state="idle")
                    continue
                self._capture = False
                try:
                    await asyncio.wait_for(self._response_finished.wait(), 3)
                except asyncio.TimeoutError:
                    raise _ProtocolError("上一句还没能结束，请重新开启语音。") from None
                await self._upstream.send_json({"type": "input_audio_buffer.commit"})
                await self._create_response()
            else:
                raise _ProtocolError("不支持这条语音控制消息。")

    async def _create_response(self):
        self._response_finished.clear()
        self._response_turn = self._turn
        self._response_id = None
        self._response_at = time.monotonic()
        self._discard = self._cancel_sent = self._proposal_sent = False
        self._last_item = None
        self._audio_bytes = 0
        self._transcript = ""
        self._truncated = False
        await self._upstream.send_json({"type": "response.create", "response": {
            "output_modalities": ["audio"], "max_output_tokens": MAX_OUTPUT_TOKENS}})
        await self._emit("state", state="thinking")

    async def _continuous_control(self, event):
        kind = event.get("type")
        if kind == "interrupt":
            played, speech_id = event.get("played_ms"), event.get("speech_id")
            if type(played) is not int or not 0 <= played <= MAX_OUTPUT_SECONDS * 1000:
                raise _ProtocolError("播放进度无效。")
            if type(speech_id) is not int:
                raise _ProtocolError("语音轮次无效。")
            if speech_id != self._speech_id or not self._ack_pending:
                return  # An acknowledgement for a superseded utterance must not trim a new reply.
            async with self._control_lock:
                await self._interrupt(played)
                self._ack_pending = False
                self._interrupt_ack.set()
        elif kind in ("listen.pause", "listen.resume"):
            self._paused = kind == "listen.pause"
            if self._speech is not None and not self._speech.stopped:
                self._speech_id += 1
                self._speech = None
                self._ack_pending = False
                self._interrupt_ack.set()
            async with self._control_lock:
                await self._upstream.send_json({"type": "input_audio_buffer.clear"})
            await self._emit("state", state="paused" if self._paused else "listening")
        else:
            raise _ProtocolError("不支持这条连续语音控制消息。")

    async def _continuous_vad(self, event):
        kind = event["type"]
        if self._paused and kind != "input_audio_buffer.committed":
            return
        if kind == "input_audio_buffer.speech_started":
            item, start = event.get("item_id"), event.get("audio_start_ms")
            if not isinstance(item, str) or not 0 < len(item) <= 200 or type(start) is not int or start < 0:
                raise _ProtocolError("语音服务返回了无效轮次。")
            if self._turn >= MAX_TURNS:
                raise _ProtocolError("这次语音已到 12 轮，请重新开启。")
            self._speech_id += 1
            self._speech = SimpleNamespace(serial=self._speech_id, item_id=item, start_ms=start,
                                           started_at=time.monotonic(), stopped=False, committed=False)
            if self._last_item is not None and not self._truncated or not self._response_finished.is_set():
                self._ack_pending = True
                self._interrupt_ack.clear()
            else:
                self._ack_pending = False
                self._interrupt_ack.set()
            # Prevent old chunks from being forwarded while the native audio device stops.
            if not self._response_finished.is_set():
                self._discard = True
            await self._emit("speech.started", speech_id=self._speech_id)
            async with self._control_lock:
                await self._cancel_response()
            await self._emit("state", state="listening")
        elif kind == "input_audio_buffer.speech_stopped":
            speech = self._speech
            if speech is None or event.get("item_id") != speech.item_id or speech.committed:
                return
            end = event.get("audio_end_ms")
            if type(end) is not int or not speech.start_ms < end <= speech.start_ms + MAX_INPUT_SECONDS * 1000:
                raise _ProtocolError("一次最多说 20 秒，请分开说。")
            speech.stopped = True
        elif kind == "input_audio_buffer.committed":
            speech = self._speech
            if (speech is None or not speech.stopped or speech.committed
                    or event.get("item_id") != speech.item_id):
                return
            speech.committed = True
            if self._utterances.full():
                self._utterances.get_nowait()
            self._utterances.put_nowait(speech)

    async def _continuous_responses(self):
        while True:
            speech = await self._utterances.get()
            try:
                await asyncio.wait_for(self._response_finished.wait(), 3)
                if speech.serial != self._speech_id:
                    continue
                await asyncio.wait_for(self._interrupt_ack.wait(), 2)
            except asyncio.TimeoutError:
                raise _ProtocolError("上一句还没能结束，请重新开启语音。") from None
            async with self._control_lock:
                if speech.serial != self._speech_id:
                    continue
                if self._turn >= MAX_TURNS:
                    raise _ProtocolError("这次语音已到 12 轮，请重新开启。")
                await self._upstream.send_json({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "system", "content": [{"type": "input_text", "text": _context(self.cfg)}]}})
                if speech.serial != self._speech_id:
                    continue
                ticket = self._budget.reserve(MAX_INPUT_SECONDS, MAX_OUTPUT_TOKENS)
                if ticket is None:
                    raise _ProtocolError("今天的桌面语音轮数用完了。")
                # No transcription request is configured. In VAD mode input is charged when
                # a response is created; silent listening and cancelled speech stay uncharged.
                ticket.commit()
                self._turn += 1
                await self._create_response()

    async def _cancel_capture(self):
        self._capture = False
        if self._ticket:
            self._ticket.release()
            self._ticket = None
        await self._upstream.send_json({"type": "input_audio_buffer.clear"})

    async def _watchdog(self):
        while True:
            now = time.monotonic()
            if (not self._ready and now - self._started_at > 15
                    or self._capture and now - self._capture_at > MAX_INPUT_SECONDS
                    or (self.mode == "continuous" and self._speech is not None
                        and not self._speech.stopped and now - self._speech.started_at > MAX_INPUT_SECONDS)
                    or not self._response_finished.is_set() and now - self._response_at > MAX_OUTPUT_SECONDS):
                raise _ProtocolError("录音或回复等待超时，请重新开启语音。")
            await asyncio.sleep(0.1)

    async def _provider(self):
        async for msg in self._upstream:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            event = json.loads(msg.data)
            kind = event.get("type")
            if kind == "session.updated" and not self._ready:
                self._ready = True
                await self._emit("ready", mode=self.mode, rate=RATE, channels=1, max_input_seconds=MAX_INPUT_SECONDS,
                                 max_session_seconds=MAX_SESSION_SECONDS, max_turns=MAX_TURNS)
            elif self.mode == "continuous" and isinstance(kind, str) and kind.startswith("input_audio_buffer."):
                await self._continuous_vad(event)
            elif kind == "error":
                error = event.get("error") or {}
                # The provider message may contain request data. Only fixed schema codes leave.
                code = str(error.get("code") or "provider_error")
                if code == "response_cancel_not_active" and self._discard:
                    continue  # A response may finish immediately before manual interruption.
                allowed = {"invalid_api_key", "model_not_found", "permission_denied", "rate_limit_exceeded",
                           "insufficient_quota", "invalid_request_error", "invalid_value", "unknown_parameter",
                           "missing_required_parameter", "response_cancel_not_active",
                           "input_audio_buffer_commit_empty", "conversation_already_has_active_response"}
                safe_code = code if code in allowed else "provider_error"
                error_type = error.get("type")
                safe_type = error_type if error_type in {"invalid_request_error", "server_error", "authentication_error"} else "provider_error"
                await self._emit("error", message="语音服务拒绝了本次请求，没有重试。", code=safe_code, error_type=safe_type)
                return
            elif kind == "response.created":
                self._response_id = event.get("response", {}).get("id")
            elif kind == "response.done":
                response = event.get("response") or {}
                if not self._discard and response.get("status") == "completed":
                    await self._proposals(response.get("output", []))
                elif not self._discard and response.get("status") not in ("cancelled", "completed"):
                    await self._emit("error", message="这句语音没有完成，请重新说一句。", code="response_incomplete")
                self._response_finished.set()
                if self._audio_open:
                    self._audio_open = False
                    await self._emit("audio.end", turn_id=self._response_turn)
                if self.mode == "continuous":
                    state = ("paused" if self._paused else "listening" if self._speech is not None
                             and not self._speech.stopped else "idle")
                    await self._emit("state", state=state)
                elif not self._capture:
                    await self._emit("state", state="idle")
            elif not self._discard and not self._response_finished.is_set():
                if event.get("response_id") and self._response_id and event["response_id"] != self._response_id:
                    continue
                if kind == "response.output_audio.delta":
                    try:
                        pcm = base64.b64decode(event.get("delta", ""), validate=True)
                    except (ValueError, binascii.Error):
                        raise _ProtocolError("语音服务返回了无效音频。") from None
                    if not pcm or len(pcm) % 2:
                        raise _ProtocolError("语音服务返回了无效音频。")
                    self._audio_bytes += len(pcm)
                    if self._audio_bytes > _BYTES_PER_SECOND * MAX_OUTPUT_SECONDS:
                        raise _ProtocolError("回复超过 30 秒，已结束这次语音。")
                    if not self._audio_open:
                        self._last_item = event.get("item_id")
                        self._last_content_index = event.get("content_index", 0)
                        self._audio_open = True
                        await self._emit("audio.start", turn_id=self._response_turn, item_id=self._last_item)
                        await self._emit("state", state="speaking")
                    # Native max_msg_size is kept small even if upstream batches its chunks.
                    for pos in range(0, len(pcm), MAX_CHUNK_BYTES):
                        await self._native.send_bytes(pcm[pos:pos + MAX_CHUNK_BYTES])
                elif kind == "response.output_audio_transcript.delta":
                    delta = event.get("delta", "")
                    if not isinstance(delta, str) or len(self._transcript) + len(delta) > 8000:
                        raise _ProtocolError("语音文字超出限制。")
                    self._transcript += delta
                    await self._emit("transcript", role="assistant", text=self._transcript, final=False)
                elif kind == "response.output_audio_transcript.done":
                    final = event.get("transcript", self._transcript)
                    if not isinstance(final, str) or len(final) > 8000:
                        raise _ProtocolError("语音文字超出限制。")
                    self._transcript = final
                    await self._emit("transcript", role="assistant", text=final, final=True)
        raise _ProtocolError("语音服务断开了连接，没有自动重连。")

    async def _proposals(self, output):
        if not isinstance(output, list):
            return
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            action = None
            try:
                raw = item.get("arguments", "")
                args = json.loads(raw) if isinstance(raw, str) and len(raw) <= 2048 else None
                name = item.get("name")
                flag = name.removeprefix("propose_") if isinstance(name, str) and name.startswith("propose_") else None
                if flag in local_chat.MANAGED.values() and isinstance(args, dict) and set(args) == {"enabled"}:
                    action = local_chat.proposal({"kind": "toggle", "flag": flag, "enabled": args["enabled"]})
                elif name == "propose_toggle" and isinstance(args, dict) and set(args) == {"flag", "enabled"}:
                    # Old saved replays remain readable; new sessions never advertise this tool.
                    action = local_chat.proposal({"kind": "toggle", **args})
                elif name == "propose_handoff" and args == {}:
                    action = local_chat.proposal({"kind": "handoff"})
            except (ValueError, TypeError):
                pass
            offered = bool(action and not self._proposal_sent)
            if offered:
                self._proposal_sent = True
                await self._emit("proposal", action=action)
            call_id = item.get("call_id")
            if isinstance(call_id, str) and 0 < len(call_id) <= 200:
                await self._upstream.send_json({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": call_id,
                    "output": json.dumps({"status": "confirmation_required" if offered else "ignored", "executed": False})}})
        # No automatic response.create: a tool never starts another paid turn.
