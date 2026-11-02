"""Missing optional codecs differ from explicitly broken installations."""
from pathlib import Path

import pytest

import config
import conftest


@pytest.fixture
def default_codec_tree(tmp_path, monkeypatch):
    for name in ("VOICE_CODEC_DIR", "VOICE_OUTPUT_NODE", "VOICE_OUTPUT_SILK_ENTRY"):
        monkeypatch.delenv(name, raising=False)
    instance = tmp_path / "instance"
    instance.mkdir()
    root = instance / ".voice-codecs"
    monkeypatch.setattr(config, "DATA_DIR", str(instance / "data"))
    monkeypatch.setattr(config, "VOICE_CODEC_DIR", str(root))
    monkeypatch.setattr(config, "VOICE_OUTPUT_SILK_ENTRY", str(root / "node_modules/silk-wasm/lib/index.cjs"))
    node = tmp_path / "node"
    node.write_text("synthetic")
    node.chmod(0o700)
    monkeypatch.setattr(config, "VOICE_OUTPUT_NODE", str(node))
    return root


@pytest.mark.parametrize("rate,label", [(16000, "AMR"), (24000, "SILK")])
def test_absent_default_codec_tree_is_optional(default_codec_tree, rate, label):
    with pytest.raises(pytest.skip.Exception, match=f"{label} codec is unavailable"):
        conftest._codec_entry_or_skip(rate, label)


@pytest.mark.parametrize("rate,label", [(16000, "AMR"), (24000, "SILK")])
def test_existing_but_incomplete_codec_tree_is_error(default_codec_tree, rate, label):
    default_codec_tree.mkdir()
    with pytest.raises(pytest.fail.Exception, match=f"{label} codec is unavailable"):
        conftest._codec_entry_or_skip(rate, label)


def test_dangling_default_codec_tree_is_error(default_codec_tree, tmp_path):
    default_codec_tree.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(pytest.fail.Exception, match="SILK codec is unavailable"):
        conftest._codec_entry_or_skip(24000, "SILK")


def test_explicitly_configured_absent_tree_is_error(default_codec_tree, monkeypatch):
    monkeypatch.setenv("VOICE_CODEC_DIR", str(default_codec_tree))
    with pytest.raises(pytest.fail.Exception, match="AMR codec is unavailable"):
        conftest._codec_entry_or_skip(16000, "AMR")


def test_codec_symlink_escaping_existing_tree_is_error(default_codec_tree, tmp_path):
    outside = tmp_path / "outside.cjs"
    outside.write_text("synthetic")
    parent = default_codec_tree / "node_modules/silk-wasm/lib"
    parent.mkdir(parents=True)
    (parent / "index.cjs").symlink_to(outside)
    with pytest.raises(pytest.fail.Exception, match="SILK codec is unavailable"):
        conftest._codec_entry_or_skip(24000, "SILK")


def test_encoder_skips_only_when_default_codec_tree_absent(default_codec_tree):
    with pytest.raises(pytest.skip.Exception, match="SILK encoder is unavailable"):
        conftest._silk_encoder_or_skip()
    default_codec_tree.mkdir()
    with pytest.raises(pytest.fail.Exception, match="SILK encoder is unavailable"):
        conftest._silk_encoder_or_skip()


def test_explicit_missing_encoder_is_error(default_codec_tree, monkeypatch):
    monkeypatch.setenv("VOICE_OUTPUT_SILK_ENTRY", str(Path(config.VOICE_OUTPUT_SILK_ENTRY)))
    with pytest.raises(pytest.fail.Exception, match="SILK encoder is unavailable"):
        conftest._silk_encoder_or_skip()
