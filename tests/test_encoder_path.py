"""Exercise the production runtime's packaged encoder path without QQ or API calls."""
import asyncio
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import wave

import pytest
import voice_output_runtime as runtime


def test_runtime_uses_existing_packaged_encoder(tmp_path):
    config = SimpleNamespace(
        VOICE_OUTPUT_ENABLED=True, SSL_VERIFY=True,
        PROVIDER_PRESETS={'openai': {'base_url': 'https://api.openai.com/v1'}},
        VOICE_OUTPUT_NODE='/opt/homebrew/bin/node', VOICE_OUTPUT_SILK_ENTRY='/unused/silk.cjs',
        DATA_DIR=str(tmp_path), VOICE_OUTPUT_DAILY_LIMIT=20, VOICE_OUTPUT_AUTO_PROBABILITY=0,
    )
    outgoing = SimpleNamespace(attempt=AsyncMock(return_value=SimpleNamespace(status='sent')))
    with patch.object(runtime, 'RealtimeSilkSynth') as synth, \
         patch.object(runtime, 'VoiceOutbound', return_value=outgoing), \
         patch.object(runtime, 'Budget'):
        result, _ = asyncio.run(runtime.VoiceOutputRuntime(config, 'synthetic-key').attempt(
            object(), 'hello', object(), lambda: True, tone='困'))
    assert result.status == 'sent'
    encoder = Path(synth.call_args.args[4])
    assert encoder == Path(runtime.__file__).resolve().parent / 'regular' / 'voice_encode.mjs'
    assert encoder.is_file()


def test_packaged_encoder_converts_synthetic_wav(tmp_path):
    node = shutil.which('node')
    entry = os.environ.get('TEST_SILK_ENTRY')
    if not node or not entry:
        pytest.skip('set TEST_SILK_ENTRY to an installed silk-wasm lib/index.cjs')
    encoder = Path(runtime.__file__).resolve().parent / 'regular' / 'voice_encode.mjs'
    assert encoder.is_file()
    src = tmp_path / 'synthetic.wav'
    dest = tmp_path / 'synthetic.silk'
    rate = 24000
    pcm = b''.join(struct.pack('<h', int(4000 * math.sin(2 * math.pi * 440 * n / rate)))
                   for n in range(rate // 2))
    with wave.open(str(src), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    subprocess.run([node, str(encoder), str(src), str(dest), entry, '30000'],
                   check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert dest.is_file() and dest.stat().st_size > 0
