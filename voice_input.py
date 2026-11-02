"""Bounded, in-memory QQ voice transcription; no QQ client or storage integration.

The caller selects a voice attachment from a trusted QQ event and supplies an
opaque event key scoped by chat and message ID. No raw audio or transcript is
logged or written. Pending/completed deduplication is in-memory and expires.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import math
import ssl
import time
import wave
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver


# Exact QQ media hosts, not wildcard subdomains. Unexpected official hosts fail
# closed and can be reviewed before adding them.
QQ_MEDIA_HOSTS = frozenset({"qqbot.ugcimg.cn", "multimedia.nt.qq.com.cn"})
MAX_TRANSCRIPT_CHARS = 10000


@dataclass(frozen=True)
class VoiceAttachment:
    reference_text: str | None = field(default=None, repr=False)
    wav_url: str | None = field(default=None, repr=False)
    audio_url: str | None = field(default=None, repr=False)
    audio_bytes: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class VoiceResult:
    status: str
    transcript: str | None = field(default=None, repr=False)
    source: str | None = None


def validate_qq_media_url(url: str) -> tuple[str, str]:
    """Normalize protocol-relative QQ URLs and reject non-QQ/unsafe origins."""
    if not isinstance(url, str) or not url or len(url) > 8192 or url != url.strip():
        raise ValueError("invalid_url")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("invalid_url")
    if url.startswith("//"):
        url = "https:" + url
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        raise ValueError("invalid_url") from None
    host = p.hostname
    if (p.scheme != "https" or host not in QQ_MEDIA_HOSTS or port not in (None, 443)
            or p.username is not None or p.password is not None or p.fragment):
        raise ValueError("invalid_url")
    return url, host


def _public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


class QQPublicResolver(AbstractResolver):
    """Resolve only approved hosts and hand aiohttp only public address records.

    Checking records in the connector resolver binds validation to the addresses
    actually used by aiohttp, rather than checking DNS and resolving again later.
    """

    def __init__(self, delegate=None):
        self._delegate = delegate or aiohttp.DefaultResolver()

    async def resolve(self, host, port=0, family=0):
        if host not in QQ_MEDIA_HOSTS or port != 443:
            raise OSError("blocked_media_host")
        rows = await self._delegate.resolve(host, port, family)
        safe = [row for row in rows if _public_address(row.get("host", ""))]
        if not safe:
            raise OSError("no_public_media_address")
        return safe

    async def close(self):
        await self._delegate.close()


async def download_qq_voice(url: str, *, max_bytes: int, timeout: float,
                            qq_authorization: str | None = None,
                            resolver_factory: Callable[[], AbstractResolver] = QQPublicResolver,
                            session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession) -> bytes:
    """One HTTPS request, no redirects, bounded bytes; optional QQ header scoped to its host."""
    url, host = validate_qq_media_url(url)
    if max_bytes <= 0 or timeout <= 0:
        raise ValueError("invalid_limit")
    headers = {}
    if qq_authorization:
        if host != "multimedia.nt.qq.com.cn":
            raise ValueError("invalid_auth_target")
        headers["Authorization"] = qq_authorization
    connector = aiohttp.TCPConnector(resolver=resolver_factory(), use_dns_cache=False,
                                     ssl=ssl.create_default_context())
    # The existing bot has an optional global aiohttp compatibility monkeypatch
    # that can replace any connector's SSL setting with False. Never request
    # media (or attach its optional credential) unless verification survived it.
    verified = getattr(connector, "_ssl", None)
    if (not isinstance(verified, ssl.SSLContext) or not verified.check_hostname
            or verified.verify_mode != ssl.CERT_REQUIRED):
        await connector.close()
        raise ValueError("tls_verification_required")
    limits = aiohttp.ClientTimeout(total=timeout)
    async with session_factory(connector=connector, timeout=limits, trust_env=False,
                               auto_decompress=False) as session:
        async with session.get(url, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("download_failed")
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ValueError("unsupported_encoding")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    length = int(declared)
                except ValueError:
                    raise ValueError("invalid_length") from None
                if length < 0 or length > max_bytes:
                    raise ValueError("too_large")
            chunks, count = [], 0
            async for chunk in response.content.iter_chunked(min(65536, max_bytes + 1)):
                count += len(chunk)
                if count > max_bytes:
                    raise ValueError("too_large")
                chunks.append(chunk)
            return b"".join(chunks)


def validate_wav(data: bytes, *, max_bytes: int, max_seconds: float) -> None:
    """Accept complete, bounded PCM WAV only; SILK and compressed WAV fail closed."""
    if not isinstance(data, bytes) or len(data) > max_bytes:
        raise ValueError("too_large")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("unsupported_format")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels, width, rate, frames = (audio.getnchannels(), audio.getsampwidth(),
                                             audio.getframerate(), audio.getnframes())
            if (audio.getcomptype() != "NONE" or channels not in (1, 2)
                    or width not in (1, 2, 3, 4) or not 8000 <= rate <= 48000
                    or frames <= 0):
                raise ValueError("unsupported_format")
            if frames / rate > max_seconds:
                raise ValueError("too_long")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise ValueError("invalid_wav")
    except (wave.Error, EOFError, OSError):
        raise ValueError("invalid_wav") from None


class OpenAIWavTranscriber:
    """One gpt-transcribe call via an injected, already-configured async client."""

    def __init__(self, client, *, model="gpt-transcribe", timeout=30.0):
        try:
            endpoint = urlsplit(str(client.base_url))
            if (endpoint.scheme != "https" or endpoint.hostname != "api.openai.com"
                    or endpoint.port not in (None, 443) or endpoint.username is not None
                    or endpoint.password is not None or endpoint.query or endpoint.fragment):
                raise ValueError("invalid_transcription_endpoint")
        except (AttributeError, ValueError):
            raise ValueError("invalid_transcription_endpoint") from None
        if not isinstance(model, str) or not model or not 0 < timeout <= 300:
            raise ValueError("invalid_transcription_config")
        # Do not depend on the caller's retry default or mutate its client.
        self._client = client.with_options(max_retries=0)
        self._model = model
        self._timeout = timeout

    async def __call__(self, data: bytes) -> str:
        # Enforce the public wrapper contract even when used outside VoiceProcessor.
        validate_wav(data, max_bytes=25_000_000, max_seconds=300)
        result = await self._client.audio.transcriptions.create(
            model=self._model, file=("voice.wav", data, "audio/wav"), timeout=self._timeout)
        return getattr(result, "text", None) or ""


@dataclass
class _Entry:
    task: asyncio.Task
    expires_at: float | None = None


class VoiceProcessor:
    """QQ ASR first, else bounded PCM/AMR/SILK transcription; short-lived dedup."""

    def __init__(self, transcriber: Callable[[bytes], Awaitable[str]] | None, *,
                 downloader=download_qq_voice, max_bytes=4 * 1024 * 1024,
                 max_seconds=60.0, download_timeout=15.0, transcription_timeout=30.0,
                 dedup_ttl=120.0, dedup_capacity=128, clock=time.monotonic):
        if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
                or not 1 <= max_bytes <= 25_000_000
                or not isinstance(dedup_capacity, int) or isinstance(dedup_capacity, bool)
                or not 1 <= dedup_capacity <= 1024
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) or not 0 < value <= limit
                       for value, limit in ((max_seconds, 300), (download_timeout, 300),
                                            (transcription_timeout, 300), (dedup_ttl, 3600)))):
            raise ValueError("invalid_limit")
        self._transcriber, self._downloader = transcriber, downloader
        self._max_bytes, self._max_seconds = max_bytes, max_seconds
        self._download_timeout, self._transcription_timeout = download_timeout, transcription_timeout
        self._ttl, self._capacity, self._clock = dedup_ttl, dedup_capacity, clock
        self._events, self._content = OrderedDict(), OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False

    async def _once(self, table, key, operation):
        async with self._lock:
            if self._closed:
                return VoiceResult("closed")
            now = self._clock()
            for k, value in list(table.items()):
                if value.expires_at is not None and value.expires_at <= now:
                    del table[k]
            entry = table.get(key)
            if entry is None:
                while len(table) >= self._capacity:
                    victim = next((k for k, v in table.items() if v.task.done()), None)
                    if victim is None:
                        return VoiceResult("busy")
                    del table[victim]
                task = asyncio.create_task(operation())
                entry = _Entry(task)
                table[key] = entry

                def finished(_):
                    if table.get(key) is entry:
                        entry.expires_at = self._clock() + self._ttl
                task.add_done_callback(finished)
            else:
                table.move_to_end(key)
            task = entry.task
        # Cancellation of one observer must not trigger a duplicate paid request
        # for another observer. close() cancels shared work on service shutdown.
        return await asyncio.shield(task)

    async def process(self, attachment: VoiceAttachment, *, event_key: str) -> VoiceResult:
        if not isinstance(attachment, VoiceAttachment) or not isinstance(event_key, str) \
                or not event_key or len(event_key) > 256:
            return VoiceResult("invalid_input")
        return await self._once(self._events, event_key, lambda: self._process(attachment))

    async def _process(self, attachment):
        reference = attachment.reference_text
        if isinstance(reference, str) and reference.strip():
            text = reference.strip()
            if len(text) > MAX_TRANSCRIPT_CHARS:
                return VoiceResult("transcript_too_long")
            return VoiceResult("ok", text, "qq_reference")
        if self._transcriber is None:
            return VoiceResult("transcription_unconfigured")
        data = attachment.audio_bytes
        if data is None:
            url = attachment.wav_url or attachment.audio_url
            if not url:
                return VoiceResult("missing_audio")
            try:
                # Validate here too, even if an injected downloader is used.
                url, _ = validate_qq_media_url(url)
                data = await asyncio.wait_for(self._downloader(
                    url, max_bytes=self._max_bytes, timeout=self._download_timeout),
                    timeout=self._download_timeout)
            except ValueError as error:
                reason = str(error)
                return VoiceResult(reason if reason in {"invalid_url", "too_large"} else "download_failed")
            except Exception:
                return VoiceResult("download_failed")
        if not isinstance(data, bytes):
            return VoiceResult("invalid_audio")
        try:
            validate_wav(data, max_bytes=self._max_bytes, max_seconds=self._max_seconds)
        except ValueError as error:
            reason = str(error)
            if reason == "unsupported_format" and data.startswith((b"#!AMR\n", b"#!AMR-WB\n")):
                from voice_amr import decode_amr
                try:
                    data = await decode_amr(data, max_seconds=min(self._max_seconds, 60))
                    validate_wav(data, max_bytes=self._max_bytes, max_seconds=self._max_seconds)
                except (ValueError, asyncio.TimeoutError):
                    return VoiceResult("decoder_failed")
            elif reason == "unsupported_format" and data.startswith((b"\x02#!SILK_V3", b"#!SILK_V3")):
                from voice_amr import decode_silk
                try:
                    data = await decode_silk(data, max_seconds=min(self._max_seconds, 60))
                    validate_wav(data, max_bytes=self._max_bytes, max_seconds=self._max_seconds)
                except (ValueError, asyncio.TimeoutError):
                    return VoiceResult("decoder_failed")
            else:
                return VoiceResult(reason if reason in {"too_large", "too_long", "unsupported_format", "invalid_wav"}
                                   else "invalid_audio")
        digest = hashlib.sha256(data).digest()
        return await self._once(self._content, digest, lambda: self._transcribe(data))

    async def _transcribe(self, data):
        try:
            text = await asyncio.wait_for(self._transcriber(data), timeout=self._transcription_timeout)
        except Exception:
            return VoiceResult("transcription_failed")
        if not isinstance(text, str) or not text.strip():
            return VoiceResult("empty_transcript")
        text = text.strip()
        if len(text) > MAX_TRANSCRIPT_CHARS:
            return VoiceResult("transcript_too_long")
        return VoiceResult("ok", text, "transcription")

    async def close(self):
        async with self._lock:
            self._closed = True
            tasks = {entry.task for table in (self._events, self._content) for entry in table.values()}
            self._events.clear()
            self._content.clear()
            for task in tasks:
                if not task.done():
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
