"""Saved capability switches gate actual work before downloads or model charges."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
import image_generation
import media
import providers
from regular import capabilities
from realtime_synth import RealtimeSilkSynth
from voice_input import VoiceAttachment, VoiceProcessor
from voice_runtime import VoiceRuntime
from voice_output_runtime import VoiceOutputRuntime


def test_disabled_transcription_keeps_qq_reference_and_never_downloads_audio():
    async def check():
        switch = [False]
        download, transcribe = AsyncMock(), AsyncMock()
        processor = VoiceProcessor(transcribe, downloader=download, transcription_enabled=lambda: switch[0])
        try:
            result = await processor.process(VoiceAttachment(reference_text='平台已有转写'), event_key='reference')
            assert result.transcript == '平台已有转写'
            result = await processor.process(VoiceAttachment(wav_url='https://qqbot.ugcimg.cn/voice'), event_key='audio')
            assert result.status == 'transcription_disabled'
            download.assert_not_awaited()
            transcribe.assert_not_awaited()
        finally:
            await processor.close()
    asyncio.run(check())


def test_voice_input_total_switch_ignores_even_qq_reference():
    async def check():
        processor = SimpleNamespace(process=AsyncMock())
        with patch.object(bot, 'VOICE', SimpleNamespace(processor=processor)), \
             patch.object(capabilities, 'enabled', return_value=False):
            assert await bot._recognize_voices((VoiceAttachment(reference_text='平台已有转写'),), 'synthetic') is None
        processor.process.assert_not_awaited()
    asyncio.run(check())


def test_voice_runtime_checks_hot_transcription_flag_before_budget_or_request():
    async def check():
        budget = SimpleNamespace(try_consume=Mock(return_value=True))
        client = SimpleNamespace(close=AsyncMock())
        request = AsyncMock()
        with patch('voice_runtime.AsyncOpenAI', return_value=client), \
             patch('voice_runtime.OpenAIWavTranscriber', return_value=request):
            voice = VoiceRuntime(budget=budget, mark_dirty=Mock(), persist=Mock(), api_key='synthetic',
                                 transcription_enabled=lambda: False)
        try:
            try: await voice.processor._transcriber(b'not-sent')
            except RuntimeError: pass
            else: raise AssertionError('disabled transcription was allowed')
            budget.try_consume.assert_not_called()
            request.assert_not_awaited()
        finally:
            await voice.close()
    asyncio.run(check())


def test_hot_vision_switch_blocks_caption_and_direct_provider_before_work():
    async def check():
        state = SimpleNamespace(data={}, mark_dirty=Mock())
        service = media.CaptionService(state, Mock(), {}, Mock(), describe=AsyncMock())
        with patch.object(capabilities, 'enabled', return_value=False), \
             patch.object(providers, 'initialize') as initialize, \
             patch.object(providers, '_complete', new=AsyncMock()) as complete:
            service.remember('g', 'm', 'u', 'https://synthetic.invalid/p.png')
            assert not service._owed
            assert await service.caption_for('g', 'm', 'u', 'https://synthetic.invalid/p.png') is None
            assert await providers.describe_image('https://synthetic.invalid/p.png') is None
        initialize.assert_not_called()
        complete.assert_not_awaited()
        service.describe.assert_not_awaited()
    asyncio.run(check())


def test_image_credentials_cannot_cross_api_bases():
    c = image_generation.config
    with patch.object(c, 'IMAGE_GENERATION_API_KEY', ''), \
         patch.object(c, 'load_api_keys', return_value=['synthetic-official-key']), \
         patch.object(c, 'PROVIDER_PRESETS', {'openai': {'base_url': 'https://api.openai.com/v1'}}), \
         patch.object(image_generation.decision_settings, 'load_settings', return_value=None):
        with patch.object(c, 'IMAGE_GENERATION_BASE_URL', 'https://different.invalid/v1'):
            assert image_generation._key() == ''
        with patch.object(c, 'IMAGE_GENERATION_BASE_URL', 'https://api.openai.com/v1/'):
            assert image_generation._key() == 'synthetic-official-key'


def test_disabled_output_stops_before_dependency_or_budget(tmp_path):
    async def check():
        runtime = VoiceOutputRuntime(SimpleNamespace(DATA_DIR=str(tmp_path)), 'synthetic')
        with patch.object(capabilities, 'enabled', return_value=False), \
             patch('voice_output_runtime.Budget') as budget, \
             patch('voice_output_runtime.RealtimeSilkSynth') as synth:
            assert await runtime.attempt(object(), 'hello', object(), lambda: True) == (None, None)
        budget.assert_not_called()
        synth.assert_not_called()
    asyncio.run(check())


def test_selected_voice_model_and_voice_reach_actual_websocket_payload():
    async def check():
        sent, connected = [], []
        class Socket:
            async def send_json(self, value): sent.append(value)
            def __aiter__(self):
                async def events():
                    for event in ({'type': 'session.updated'},
                                  {'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\0' * 4800).decode()},
                                  {'type': 'response.output_audio_transcript.delta', 'delta': '你好'},
                                  {'type': 'response.done', 'response': {'status': 'completed', 'output': []}}):
                        yield SimpleNamespace(type=1, data=json.dumps(event))
                return events()
        @asynccontextmanager
        async def connector(url, key):
            connected.append(url)
            yield Socket()
        synth = RealtimeSilkSynth('synthetic', object(), '/node', '/silk', '/encoder', connector,
                                  model='custom-model', voice='custom-voice')
        assert await synth._generate('你好', {'requested': False, 'reservation_day': None})
        assert connected == ['wss://api.openai.com/v1/realtime?model=custom-model']
        assert sent[0]['session']['audio']['output']['voice'] == 'custom-voice'
    asyncio.run(check())
