"""Synthetic AMR frames decoded entirely in subprocess memory, no network."""
import asyncio
import io
from pathlib import Path
import wave
from unittest.mock import AsyncMock

import pytest
from voice_amr import decode_amr, validate_amr
from voice_input import VoiceAttachment, VoiceProcessor

SAMPLES = {"test-nb.amr": b"#!AMR\n" + (b"\x04" + bytes(12)) * 50,
           "test-wb.amr": b"#!AMR-WB\n" + (b"\x04" + bytes(17)) * 50}


@pytest.mark.parametrize("name", ["test-nb.amr", "test-wb.amr"])
def test_validate_public_amr_without_codec(name):
    assert validate_amr(SAMPLES[name], 60) == 50


@pytest.mark.parametrize("name,rate", [("test-nb.amr", 8000), ("test-wb.amr", 16000)])
def test_decode_public_amr_to_pcm(name, rate, require_amr_codec):
    raw = SAMPLES[name]
    assert validate_amr(raw, 60) == 50
    output = asyncio.run(decode_amr(raw))
    with wave.open(io.BytesIO(output), "rb") as audio:
        assert audio.getframerate() == rate
        assert audio.getnframes() == rate
        assert audio.getnchannels() == 1


def test_rejects_truncated_and_oversized_duration():
    raw = SAMPLES["test-wb.amr"]
    with pytest.raises(ValueError):
        validate_amr(raw[:-1], 60)
    with pytest.raises(ValueError, match="too_long"):
        validate_amr(raw, 0.5)


def test_amr_goes_to_transcriber_as_wav_without_files(require_amr_codec):
    async def check():
        transcribe = AsyncMock(return_value="hello")
        processor = VoiceProcessor(transcribe)
        try:
            result = await processor.process(VoiceAttachment(audio_bytes=SAMPLES["test-wb.amr"]),
                                             event_key="synthetic")
            assert result.status == "ok"
            assert result.transcript == "hello"
            data = transcribe.await_args.args[0]
            assert data.startswith(b"RIFF") and data[8:12] == b"WAVE"
        finally:
            await processor.close()
    asyncio.run(check())
