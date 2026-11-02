"""Synthetic NB/WB and silence SILK against the instance's explicit codec paths."""
import asyncio
import io
from pathlib import Path
import tempfile
from unittest.mock import patch
import wave
import pytest
import voice_amr

@pytest.mark.parametrize('raw,rate', [
    (b'#!AMR\n' + (b'\x04' + bytes(12)) * 50, 8000),
    (b'#!AMR-WB\n' + (b'\x04' + bytes(17)) * 50, 16000),
])
def test_synthetic_amr_with_private_entry(raw, rate):
    wav = asyncio.run(voice_amr.decode_amr(raw))
    with wave.open(io.BytesIO(wav), 'rb') as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getnframes()) == (rate, 1, rate)

def test_codec_entry_rejects_missing_or_relative_path():
    for value in ('relative', '/definitely/does/not/exist'):
        with patch.object(voice_amr.config, 'VOICE_CODEC_DIR', value):
            with pytest.raises(ValueError, match='decoder_unavailable'):
                voice_amr._codec_entry(24000)

def test_codec_entry_rejects_symlink_escape():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)/'private'; outside=Path(temp)/'outside'; root.mkdir(); outside.mkdir()
        (outside/'index.cjs').write_text('// synthetic')
        (root/'node_modules'/'silk-wasm').mkdir(parents=True)
        (root/'node_modules'/'silk-wasm'/'lib').symlink_to(outside)
        with patch.object(voice_amr.config, 'VOICE_CODEC_DIR', str(root)):
            with pytest.raises(ValueError, match='decoder_unavailable'):
                voice_amr._codec_entry(24000)

def test_synthetic_wav_encodes_using_private_silk_entry(tmp_path):
    import config
    from voice_outbound import _run
    wav = tmp_path / 'synthetic.wav'
    with wave.open(str(wav), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(bytes(48000))
    silk = tmp_path / 'synthetic.silk'
    encoder = Path(__file__).resolve().parents[1] / 'regular' / 'voice_encode.mjs'
    asyncio.run(_run(config.VOICE_OUTPUT_NODE, str(encoder), str(wav), str(silk),
                     config.VOICE_OUTPUT_SILK_ENTRY, '30000', timeout=10))
    assert voice_amr.validate_silk(silk.read_bytes(), 30) == 50
