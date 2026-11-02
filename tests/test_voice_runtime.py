import asyncio
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import storage
from voice_input import VoiceAttachment
from voice_runtime import VoiceRuntime


def test_reference_avoids_model_and_wav_spends_one_budget_unit():
    async def check():
        actual = AsyncMock(return_value="synthetic")
        client = SimpleNamespace(close=AsyncMock())
        budget = SimpleNamespace(try_consume=Mock(return_value=True))
        order = []
        dirty = Mock(side_effect=lambda: order.append("dirty"))
        persist = Mock(side_effect=lambda: order.append("persist"))
        actual.side_effect = lambda _: order.append("request") or "synthetic"
        with patch("voice_runtime.AsyncOpenAI", return_value=client), \
             patch("voice_runtime.OpenAIWavTranscriber", return_value=actual) as constructor:
            voice = VoiceRuntime(budget=budget, mark_dirty=dirty, persist=persist, api_key="dummy")
        constructor.assert_called_once_with(client, model="gpt-transcribe")
        try:
            result = await voice.processor.process(VoiceAttachment(reference_text="qq reference"), event_key="a")
            assert result.transcript == "qq reference"
            budget.try_consume.assert_not_called()
            # Exercise the bounded transcriber wrapper directly; WAV validation
            # and content dedup are tested separately in test_voice_input.
            assert await voice.processor._transcriber(b"synthetic") == "synthetic"
            budget.try_consume.assert_called_once()
            dirty.assert_called_once()
            assert order == ["dirty", "persist", "request"]
            actual.assert_awaited_once()
        finally:
            await voice.close()
        client.close.assert_awaited_once()
    asyncio.run(check())


def test_unofficial_endpoint_never_constructs_client():
    with patch("voice_runtime.AsyncOpenAI") as client:
        voice = VoiceRuntime(budget=Mock(), mark_dirty=Mock(), persist=Mock(), api_key="dummy",
                             base_url="https://untrusted.invalid/v1")
    client.assert_not_called()
    assert voice.processor._transcriber is None
    asyncio.run(voice.close())


def test_budget_is_durable_before_request_and_failed_save_prevents_request():
    async def check():
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            state = storage.StateStore(path)
            budget = storage.DailyBudget(5, state.data["usage"])
            async def actual(_):
                reloaded = storage.StateStore(path)
                assert storage.DailyBudget(5, reloaded.data["usage"]).used == 1
                return "ok"
            client = SimpleNamespace(close=AsyncMock())
            with patch("voice_runtime.AsyncOpenAI", return_value=client), \
                 patch("voice_runtime.OpenAIWavTranscriber", return_value=actual):
                voice = VoiceRuntime(budget=budget, mark_dirty=state.mark_dirty,
                                     persist=state.save_now, api_key="dummy")
            try:
                assert await voice.processor._transcriber(b"synthetic") == "ok"
            finally:
                await voice.close()
            actual_mock = AsyncMock()
            with patch("voice_runtime.AsyncOpenAI", return_value=client), \
                 patch("voice_runtime.OpenAIWavTranscriber", return_value=actual_mock):
                voice = VoiceRuntime(budget=budget, mark_dirty=state.mark_dirty,
                                     persist=Mock(side_effect=OSError("disk")), api_key="dummy")
            try:
                with pytest.raises(OSError):
                    await voice.processor._transcriber(b"synthetic")
                actual_mock.assert_not_awaited()
            finally:
                await voice.close()
    asyncio.run(check())
