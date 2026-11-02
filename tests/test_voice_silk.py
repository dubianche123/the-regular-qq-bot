"""Synthetic SILK only: encoded silence and in-memory mock transcription."""
import asyncio
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import wave

import pytest
from voice_amr import decode_silk, validate_silk
from voice_input import VoiceAttachment, VoiceProcessor

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def synthetic_silk(require_silk_codec):
    node, codec_entry = require_silk_codec
    result = subprocess.run(
        [node, "--input-type=module", "-e",
         "import {pathToFileURL} from 'node:url';"
         "const {encode}=await import(pathToFileURL(process.argv[1]).href);"
         "const result=await encode(Buffer.alloc(48000),24000);"
         "process.stdout.write(result.data);", codec_entry],
        cwd=ROOT, env={"PATH": "/usr/bin:/bin"}, capture_output=True,
        timeout=10, check=True)
    return result.stdout


@pytest.fixture
def structural_silk():
    # Valid framing for parser and subprocess-control tests; it is not playable audio.
    return b"\x02#!SILK_V3" + (b"\x01\x00\x00" * 50)


def test_tencent_and_standard_silk_decode_to_bounded_pcm(synthetic_silk):
    assert synthetic_silk.startswith(b"\x02#!SILK_V3")
    for data in (synthetic_silk, synthetic_silk[1:], synthetic_silk[1:] + b"\xff\xff"):
        assert validate_silk(data, 60) == 50
        wav_bytes = asyncio.run(decode_silk(data))
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            assert audio.getframerate() == 24000
            assert audio.getnchannels() == 1
            assert audio.getnframes() == 24000


def test_silk_rejects_bad_header_frames_and_excess_duration(structural_silk):
    assert validate_silk(structural_silk, 60) == 50
    cases = (b"#!SILK_V2" + structural_silk[10:], structural_silk[:-1],
             b"#!SILK_V3\x01", b"#!SILK_V3\x00\x00", b"#!SILK_V3\x01\x04" + b"x" * 1025)
    for data in cases:
        with pytest.raises(ValueError):
            validate_silk(data, 60)
    with pytest.raises(ValueError, match="too_long"):
        validate_silk(structural_silk, 0.5)


def test_silk_flows_to_transcriber_once_as_wav(synthetic_silk):
    async def check():
        transcribe = AsyncMock(return_value="synthetic text")
        processor = VoiceProcessor(transcribe)
        attachment = VoiceAttachment(audio_bytes=synthetic_silk)
        try:
            results = await asyncio.gather(*(processor.process(attachment, event_key="same") for _ in range(3)))
            assert all(result.status == "ok" for result in results)
            transcribe.assert_awaited_once()
            data = transcribe.await_args.args[0]
            assert data.startswith(b"RIFF") and data[8:12] == b"WAVE"
        finally:
            await processor.close()
    asyncio.run(check())


def test_cancellation_kills_decoder(structural_silk):
    async def check():
        started = asyncio.Event()
        async def blocked(_):
            started.set()
            await asyncio.Event().wait()
        process = SimpleNamespace(communicate=AsyncMock(side_effect=blocked),
                                  kill=Mock(), wait=AsyncMock(), returncode=None)
        with patch("voice_amr.shutil.which", return_value="/fake/node"), \
             patch("voice_amr._codec_entry", return_value="/fake/silk.cjs"), \
             patch("voice_amr.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(decode_silk(structural_silk))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
    asyncio.run(check())


def test_timeout_kills_decoder(structural_silk):
    async def check():
        async def blocked(_):
            await asyncio.Event().wait()
        process = SimpleNamespace(communicate=AsyncMock(side_effect=blocked),
                                  kill=Mock(), wait=AsyncMock(), returncode=None)
        with patch("voice_amr.shutil.which", return_value="/fake/node"), \
             patch("voice_amr._codec_entry", return_value="/fake/silk.cjs"), \
             patch("voice_amr.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with pytest.raises(asyncio.TimeoutError):
                await decode_silk(structural_silk, timeout=0.01)
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
    asyncio.run(check())
