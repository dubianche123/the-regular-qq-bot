"""Bounded AMR and SILK decoding in a short-lived, in-memory Node subprocess."""
import asyncio
import os
import shutil
from pathlib import Path

import config

_NB = (13, 14, 16, 18, 20, 21, 27, 32, 6, 6, 6, 6, 1, 1, 1, 1)
_WB = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6, 1, 1, 1, 1, 1, 1)


def validate_amr(data, max_seconds):
    if data.startswith(b"#!AMR-WB\n"):
        pos, sizes = 9, _WB
    elif data.startswith(b"#!AMR\n"):
        pos, sizes = 6, _NB
    else:
        raise ValueError("unsupported_format")
    count = 0
    while pos < len(data):
        header = data[pos]
        if header & 0x83:  # storage padding bits must be clear
            raise ValueError("invalid_audio")
        pos += sizes[(header >> 3) & 15]
        count += 1
        if count > int(max_seconds * 50):
            raise ValueError("too_long")
    if pos != len(data) or not count:
        raise ValueError("invalid_audio")
    return count


async def decode_amr(data, *, max_seconds=60, timeout=10):
    """Return WAV bytes or raise a fixed error; do not persist input or output."""
    validate_amr(data, max_seconds)
    return await _decode_codec(data, max_seconds=max_seconds, timeout=timeout, max_rate=16000)


def validate_silk(data, max_seconds):
    if data.startswith(b"\x02#!SILK_V3"):
        pos = 10
    elif data.startswith(b"#!SILK_V3"):
        pos = 9
    else:
        raise ValueError("unsupported_format")
    count = 0
    while pos < len(data):
        if pos + 2 > len(data):
            raise ValueError("invalid_audio")
        size = int.from_bytes(data[pos:pos + 2], "little", signed=True)
        pos += 2
        if size == -1 and pos == len(data):
            break
        if size <= 0 or size > 1024 or pos + size > len(data):
            raise ValueError("invalid_audio")
        pos += size
        count += 1
        if count > int(max_seconds * 50):
            raise ValueError("too_long")
    if not count:
        raise ValueError("invalid_audio")
    return count


async def decode_silk(data, *, max_seconds=60, timeout=10):
    """Decode Tencent or standard SILK using bounded, memory-only pipes."""
    validate_silk(data, max_seconds)
    return await _decode_codec(data, max_seconds=max_seconds, timeout=timeout, max_rate=24000)


def _codec_entry(max_rate):
    """Resolve only the configured private codec installation, never NODE_PATH."""
    try:
        root = Path(config.VOICE_CODEC_DIR).expanduser()
        if not root.is_absolute() or len(str(root)) > 4096:
            raise ValueError("decoder_unavailable")
        root = root.resolve(strict=True)
        relative = ("node_modules/@audio/decode-amr/decode-amr.js" if max_rate == 16000
                    else "node_modules/silk-wasm/lib/index.cjs")
        entry = (root / relative).resolve(strict=True)
        if root not in entry.parents or not entry.is_file() or len(str(entry)) > 4096:
            raise ValueError("decoder_unavailable")
        return str(entry)
    except (OSError, RuntimeError):
        raise ValueError("decoder_unavailable") from None


async def _decode_codec(data, *, max_seconds, timeout, max_rate):
    if not isinstance(data, bytes) or len(data) > 4 * 1024 * 1024 or not 0 < max_seconds <= 60:
        raise ValueError("invalid_audio")
    node = shutil.which("node")
    if not node:
        node = next((p for p in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
                     if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not node:
        raise ValueError("decoder_unavailable")
    script = os.path.join(os.path.dirname(__file__), "regular", "voice_amr.mjs")
    entry = _codec_entry(max_rate)
    try:
        proc = await asyncio.create_subprocess_exec(
            node, "--max-old-space-size=64", script, str(max_seconds), entry,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
    except OSError:
        raise ValueError("decoder_unavailable") from None
    try:
        output, _ = await asyncio.wait_for(proc.communicate(data), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    if proc.returncode or not output or len(output) > int(max_seconds * max_rate * 2) + 44:
        raise ValueError("decoder_failed")
    return output
