"""Explicit push-to-talk audio for the native pet; never opens a device on import.

Only this module owns sounddevice streams and the authenticated loopback WebSocket.
Qt receives small events through the injected callback, and never waits for audio or HTTP.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import sys
import threading
import time
from urllib.parse import urlsplit

import aiohttp

RATE = 24000
SAMPLE_BYTES = 2
MAX_INPUT_SECONDS = 20
MAX_OUTPUT_BYTES = RATE * SAMPLE_BYTES * 30


class VoiceError(RuntimeError):
    """A message safe to show beside the character."""


def request_microphone_permission(callback):
    """Called only by the user's Start button; permission alone never starts recording."""
    if sys.platform != "darwin":
        callback(True, "")
        return
    try:
        from Foundation import NSBundle
        from AVFoundation import (AVCaptureDevice, AVMediaTypeAudio,
                                  AVAuthorizationStatusAuthorized, AVAuthorizationStatusNotDetermined)
        if not NSBundle.mainBundle().objectForInfoDictionaryKey_("NSMicrophoneUsageDescription"):
            callback(False, "请用老群友.app 开启语音；源码安装请先构建桌面应用。")
            return
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if status == AVAuthorizationStatusAuthorized:
            callback(True, "")
        elif status == AVAuthorizationStatusNotDetermined:
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeAudio, lambda granted: callback(bool(granted), "" if granted else _DENIED))
        else:
            callback(False, _DENIED)
    except (ImportError, OSError):
        callback(False, "语音组件还没装好，请更新桌面应用后再试。")


_DENIED = "麦克风未获允许。请到系统设置 → 隐私与安全性 → 麦克风，允许老群友。"


class SoundDeviceAudio:
    """Small PCM adapter. Callbacks only move bounded memory; no Qt, HTTP or file I/O."""
    def __init__(self, sounddevice=None):
        self._sd = sounddevice
        self._input = self._output = None
        self._lock = threading.Lock()
        self._capture_lock = threading.RLock()
        self._chunks = deque()
        self._offset = self._queued_bytes = 0
        self._segments = deque()
        self._played_frames = 0
        self._ended = False
        self._output_error = None

    def _module(self):
        if self._sd is None:
            try:
                import sounddevice
            except (ImportError, OSError):
                raise VoiceError("语音组件还没装好，请更新桌面应用后再试。") from None
            self._sd = sounddevice
        return self._sd

    def start_capture(self, on_pcm, on_error):
        def capture(data, frames, timing, status):
            if status:
                on_error("收音中断了，这一句已取消。请松开后再试。")
                return
            on_pcm(bytes(data))

        with self._capture_lock:
            self.stop_capture()
            sd = self._module()
            try:
                self._input = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                               blocksize=0, callback=capture)
                self._input.start()
            except Exception:
                self.stop_capture()
                raise VoiceError("麦克风暂时打不开，请检查系统权限和当前输入设备。") from None

    def stop_capture(self):
        with self._capture_lock:
            stream, self._input = self._input, None
            if stream is not None:
                with suppress(Exception):
                    stream.abort()
                with suppress(Exception):
                    stream.close()

    def begin_playback(self):
        self.abort_playback()
        sd = self._module()
        with self._lock:
            self._ended = False
            self._output_error = None

        def playback(outdata, frames, timing, status):
            outdata[:] = b"\0" * len(outdata)
            with self._lock:
                used = 0
                while self._chunks and used < len(outdata):
                    chunk = self._chunks[0]
                    count = min(len(chunk) - self._offset, len(outdata) - used)
                    outdata[used:used + count] = chunk[self._offset:self._offset + count]
                    used += count
                    self._offset += count
                    self._queued_bytes -= count
                    if self._offset == len(chunk):
                        self._chunks.popleft()
                        self._offset = 0
                if used:
                    # DAC timestamps account for device buffering; queued samples are not heard yet.
                    self._segments.append((float(timing.outputBufferDacTime), used // SAMPLE_BYTES))
                if status:
                    self._output_error = "播放有些卡顿，可以按住说话重新接一句。"

        try:
            self._output = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                             blocksize=0, callback=playback, latency="low")
            self._output.start()
        except Exception:
            self.abort_playback()
            raise VoiceError("声音暂时播不出来，请检查当前输出设备。") from None

    def feed_audio(self, data):
        if not isinstance(data, bytes) or len(data) % SAMPLE_BYTES:
            raise VoiceError("收到的语音格式不正确，已停止播放。")
        with self._lock:
            if self._queued_bytes + len(data) > MAX_OUTPUT_BYTES:
                raise VoiceError("语音回复过长，已停止播放。")
            self._chunks.append(data)
            self._queued_bytes += len(data)

    def end_playback(self):
        with self._lock:
            self._ended = True

    def played_ms(self):
        stream = self._output
        if stream is None:
            return 0
        now = stream.time
        with self._lock:
            while self._segments and now >= self._segments[0][0] + self._segments[0][1] / RATE:
                self._played_frames += self._segments.popleft()[1]
            partial = sum(min(frames, max(0, int((now - start) * RATE)))
                          for start, frames in self._segments)
            return (self._played_frames + partial) * 1000 // RATE

    def playback_done(self):
        self.played_ms()
        with self._lock:
            return self._ended and not self._chunks and not self._segments

    def abort_playback(self):
        try:
            played = self.played_ms()
        except Exception:
            played = 0  # A removed device cannot report what reached its DAC.
        stream, self._output = self._output, None
        if stream is not None:
            with suppress(Exception):
                stream.abort()
            with suppress(Exception):
                stream.close()
        with self._lock:
            self._chunks.clear()
            self._segments.clear()
            self._offset = self._queued_bytes = self._played_frames = 0
            self._ended = False
        return played

    def close(self):
        try:
            self.stop_capture()
        finally:
            self.abort_playback()


class NativeVoiceSession:
    """One user-started session, one worker loop, no reconnect or automatic recording."""
    def __init__(self, client, csrf, on_event, *, audio_factory=SoundDeviceAudio,
                 session_factory=aiohttp.ClientSession, clock=time.monotonic):
        self.client, self.csrf, self.on_event = client, csrf, on_event
        self.audio_factory, self.session_factory, self.clock = audio_factory, session_factory, clock
        self._loop = self._task = self._thread = self._queue = None
        self._closed = threading.Event()
        self._holding = threading.Event()
        self._overflow = threading.Event()
        self._ready = False
        self._capture_started = None
        self._capture_generation = 0
        self._max_input = MAX_INPUT_SECONDS
        self._audio = None
        self._playing = False
        self._last_played_ms = 0

    def _emit(self, kind, **values):
        if not self._closed.is_set() or kind == "closed":
            self.on_event({"type": kind, **values})

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_thread, name="regular-pet-voice", daemon=True)
        self._thread.start()

    def press(self):
        if not self._ready or self._closed.is_set() or self._holding.is_set():
            return False
        self._holding.set()
        self._post("input.start")
        return True

    def release(self):
        if self._holding.is_set():
            self._holding.clear()
            self._stop_microphone()
            self._post("input.commit")

    def cancel_input(self):
        if self._holding.is_set():
            self._holding.clear()
            self._stop_microphone()
            self._post("input.cancel")

    def _stop_microphone(self):
        # Do not wait for a backed-up WebSocket sender before closing the device.
        if self._loop is not None and self._audio is not None:
            with suppress(RuntimeError):
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(asyncio.to_thread(self._audio.stop_capture)))

    def close(self):
        self._holding.clear()
        self._stop_microphone()
        self._closed.set()
        if self._loop is not None and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass

    def _post(self, kind, data=None):
        loop = self._loop
        if loop is None or self._closed.is_set():
            return

        def put():
            if self._queue.full():
                self._overflow.set()
                self._holding.clear()
            else:
                payload = asyncio.create_task(self._interrupt_local()) if kind == "input.start" else data
                self._queue.put_nowait((kind, payload))
        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass

    async def _interrupt_local(self):
        # The user should hear the stop now, even if a prior upload is backed up.
        played = max(self._last_played_ms, await asyncio.to_thread(self._audio.abort_playback))
        self._last_played_ms = played
        self._playing = False
        return played

    def _run_thread(self):
        try:
            asyncio.run(self._run())
        except Exception:
            self._emit("error", message="语音连接没有完成，请结束后重新开始；没有自动重连。")

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._queue = asyncio.Queue(maxsize=128)
        self._audio = self.audio_factory()
        self._emit("state", state="connecting")
        ws = None
        try:
            status = await asyncio.to_thread(self.client.voice_status)
            if not status.get("available"):
                raise VoiceError(str(status.get("reason") or "语音还没配置好，请到模型与能力里看看。"))
            ticket = await asyncio.to_thread(self.client.voice_start, self.csrf)
            url, headers = self.client.voice_endpoint(ticket["ticket"])
            parsed = urlsplit(url)
            if (parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.query
                    or parsed.path != "/companion/voice/stream" or parsed.username or parsed.password):
                raise VoiceError("语音连接地址不正确，已停止。")
            trace = aiohttp.TraceConfig()

            async def reject_redirect(*args):
                raise VoiceError("语音连接发生跳转，已停止；没有继续发送凭据。")
            trace.on_request_redirect.append(reject_redirect)
            timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=None)
            async with self.session_factory(trust_env=False, trace_configs=[trace], timeout=timeout) as session:
                async with session.ws_connect(url, headers=headers, max_msg_size=MAX_OUTPUT_BYTES) as ws:
                    if ws._response.headers.get("X-Regular-Panel") != "1":
                        raise VoiceError("这个端口不是已认证的老群友面板。")
                    tasks = [asyncio.create_task(self._receive(ws)), asyncio.create_task(self._send(ws)),
                             asyncio.create_task(self._watch(ws))]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        if not ws.closed:
                            await ws.send_json({"type": "stop"})
        except asyncio.CancelledError:
            pass
        except VoiceError as error:
            self._emit("error", message=str(error))
        except Exception as error:
            from regular.desktop import CompanionError
            self._emit("error", message=str(error) if isinstance(error, CompanionError)
                       else "语音连接中断了，请结束后重新开始；没有自动重连。")
        finally:
            self._ready = False
            self._holding.clear()
            try:
                await asyncio.to_thread(self._audio.close)
            finally:
                self._emit("closed", reason="语音已结束")
                self._closed.set()

    async def _send(self, ws):
        recording = False
        while True:
            kind, data = await self._queue.get()
            if kind == "input.start":
                played = await data
                await ws.send_json({"type": "interrupt", "played_ms": played})
                await ws.send_json({"type": kind})
                self._capture_generation += 1
                generation = self._capture_generation
                self._capture_started = None
                recording = True

                def pcm(chunk, captured=generation):
                    if self._holding.is_set() and self._capture_generation == captured:
                        self._post("pcm", (captured, chunk))

                if self._holding.is_set():
                    await asyncio.to_thread(self._audio.start_capture, pcm,
                                            lambda message: self._post("capture.error", message))
                if self._holding.is_set():
                    self._capture_started = self.clock()
                    self._emit("state", state="listening")
            elif kind in ("input.commit", "input.cancel", "capture.error"):
                await asyncio.to_thread(self._audio.stop_capture)
                recording = False
                self._capture_started = None
                self._capture_generation += 1
                await ws.send_json({"type": "input.commit" if kind == "input.commit" else "input.cancel"})
                self._emit("state", state="thinking" if kind == "input.commit" else "idle")
                if kind == "capture.error":
                    self._holding.clear()
                    self._emit("error", message=data)
            elif kind == "pcm":
                generation, chunk = data
                if recording and generation == self._capture_generation:
                    for offset in range(0, len(chunk), 32 * 1024):
                        await ws.send_bytes(chunk[offset:offset + 32 * 1024])

    async def _receive(self, ws):
        async for message in ws:
            if message.type == aiohttp.WSMsgType.BINARY:
                if self._playing and not self._holding.is_set():
                    self._audio.feed_audio(bytes(message.data))
            elif message.type == aiohttp.WSMsgType.TEXT:
                event = message.json()
                kind = event.get("type")
                if kind == "ready":
                    if event.get("rate") != RATE or event.get("channels") != 1:
                        raise VoiceError("语音格式不匹配，请更新桌面应用。")
                    self._max_input = min(MAX_INPUT_SECONDS, max(1, int(event.get("max_input_seconds", 20))))
                    self._ready = True
                    self._emit("ready")
                elif kind == "audio.start":
                    if not self._holding.is_set():
                        await asyncio.to_thread(self._audio.begin_playback)
                        self._last_played_ms = 0
                        self._playing = True
                        self._emit("state", state="speaking")
                elif kind == "audio.end":
                    self._audio.end_playback()
                elif kind == "state":
                    if (event.get("state") != "listening" and not self._holding.is_set()
                            and not (self._playing and event.get("state") == "idle")):
                        self._emit("state", state=event.get("state"))
                elif kind in ("transcript", "proposal", "error"):
                    self._emit(kind, **{k: v for k, v in event.items() if k != "type"})
                elif kind == "closed":
                    return
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise VoiceError("语音连接中断了；没有自动重连。")

    async def _watch(self, ws):
        started = self.clock()
        while self.clock() - started < 180:
            await asyncio.sleep(0.05)
            if not self._ready and self.clock() - started >= 20:
                raise VoiceError("语音连接等待太久了，请结束后重新开始；没有自动重连。")
            if self._overflow.is_set():
                raise VoiceError("语音传输跟不上，已结束收音；没有自动重试。")
            if (self._holding.is_set() and self._capture_started is not None
                    and self.clock() - self._capture_started >= self._max_input):
                self.cancel_input()
                self._emit("error", message="这一句超过 20 秒，已停止，不生成回复。请分短一点再说。")
            if self._playing and self._audio.playback_done():
                self._last_played_ms = await asyncio.to_thread(self._audio.abort_playback)
                self._playing = False
                self._emit("state", state="idle")
        self._emit("error", message="这次语音已满 3 分钟，休息一下再开始吧。")
