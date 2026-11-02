"""Independent, not wired to the bot. No QQ or model dependencies.

Transport.send must report SENT, FAILED (definitely not delivered), or UNKNOWN.
Quota.reserve() returns None or a reservation with synchronous commit()/release().
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import signal
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Delivery(Enum):
    SENT = 'sent'
    FAILED = 'failed'  # transport must be certain that delivery did not occur
    UNKNOWN = 'unknown'


class Reservation(Protocol):
    def commit(self) -> None: ...
    def release(self) -> None: ...


class Quota(Protocol):
    def reserve(self) -> Reservation | None: ...


class Transport(Protocol):
    async def upload(self, silk: Path) -> str: ...  # opaque file_info; no QQ message
    async def send(self, file_info: str) -> Delivery: ...


class Synthesizer(Protocol):
    async def synthesize(self, text: str, directory: Path, *, tone_context: tuple[str, ...] = ()) -> Path: ...


@dataclass(frozen=True)
class Result:
    status: str
    text_fallback_allowed: bool = False


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    auto_probability: float = 0.0
    max_chars: int = 120
    upload_timeout: float = 15
    send_timeout: float = 15

    def __post_init__(self):
        if not 0 <= self.auto_probability <= 1 or not 1 <= self.max_chars <= 120:
            raise ValueError('invalid voice policy')
        if not 0 < self.upload_timeout <= 60 or not 0 < self.send_timeout <= 60:
            raise ValueError('invalid timeout')


async def _run(*args: str, input_bytes: bytes | None = None, timeout: float) -> None:
    """No shell; on timeout/cancellation kill and reap the entire subprocess group."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _, _ = await asyncio.wait_for(proc.communicate(input_bytes), timeout)
        if proc.returncode != 0:
            raise RuntimeError('audio subprocess failed')
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise


class ProviderRefused(Exception):
    pass


class TranscriptMismatch(Exception):
    pass


class SynthesisTimeout(Exception):
    pass


class ProviderUncertain(Exception):
    pass


class VoiceOutbound:
    def __init__(self, synth: Synthesizer, transport: Transport, quota: Quota,
                 settings: Settings, *, random_value=None, temp_root: str | None = None):
        self.synth, self.transport, self.quota, self.settings = synth, transport, quota, settings
        self.random_value = random_value or (lambda: secrets.randbelow(1_000_000) / 1_000_000)
        self.temp_root = temp_root
        self._lock = asyncio.Lock()

    async def attempt(self, text: str, *, explicit: bool = False, spoken_text: str | None = None,
                      tone_context: tuple[str, ...] = ()) -> Result:
        s = self.settings
        if not s.enabled or (not explicit and self.random_value() >= s.auto_probability):
            return Result('not_selected')
        if not isinstance(text, str) or not text.strip() or len(text) > s.max_chars:
            return Result('invalid_text', True)
        audio_text = text if spoken_text is None else spoken_text
        if not isinstance(audio_text, str) or not audio_text.strip() or len(audio_text) > s.max_chars:
            return Result('invalid_text', True)
        if self._lock.locked():
            return Result('busy', True)
        async with self._lock:
            ticket = self.quota.reserve()
            if ticket is None:
                return Result('quota_exhausted', True)
            delivery_started = False
            settled = False
            try:
                with tempfile.TemporaryDirectory(prefix='qq-voice-', dir=self.temp_root) as tmp:
                    directory = Path(tmp)
                    os.chmod(directory, 0o700)
                    try:
                        if tone_context:
                            silk = await self.synth.synthesize(audio_text, directory, tone_context=tone_context)
                        else:
                            silk = await self.synth.synthesize(audio_text, directory)
                        # Never allow the provider to escape the private directory.
                        if not silk.is_file() or silk.is_symlink() or silk.resolve().parent != directory.resolve():
                            raise ValueError('invalid local audio file')
                        os.chmod(silk, 0o600)
                        info = await asyncio.wait_for(self.transport.upload(silk), s.upload_timeout)
                        if not isinstance(info, str) or not info:
                            raise ValueError('missing upload file_info')
                    except asyncio.CancelledError:
                        raise
                    except ProviderRefused:
                        return Result('provider_refused')
                    except TranscriptMismatch:
                        return Result('transcript_mismatch')
                    except ProviderUncertain:
                        return Result('provider_uncertain')
                    except SynthesisTimeout:
                        return Result('synthesis_timeout', True)
                    except Exception:
                        return Result('pre_send_failed', True)
                    if getattr(ticket, 'day', None) and ticket.day != time.strftime('%Y-%m-%d'):
                        return Result('pre_send_failed', True)
                    delivery_started = True
                    try:
                        outcome = await asyncio.wait_for(self.transport.send(info), s.send_timeout)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        outcome = Delivery.UNKNOWN
                    if outcome is Delivery.FAILED:
                        delivery_started = False  # transport confirmed no message was sent
                        return Result('send_failed', True)
                    # An unknown/malformed outcome is charged and must never be retried or text-fallbacked.
                    ticket.commit()
                    settled = True
                    return Result('sent' if outcome is Delivery.SENT else 'delivery_unknown')
            finally:
                if not settled:
                    if delivery_started:
                        # Cancellation after dispatch could mean QQ already received the message.
                        ticket.commit()
                    else:
                        ticket.release()
