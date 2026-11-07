"""Offline end-to-end routing tests for synthetic QQ voice references."""
import tempfile
import asyncio
import logging
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import bot
import config
import runtime
from archive import MessageArchive
from voice_input import VoiceAttachment, VoiceResult
from interactions import InteractionLog


class ReachedRouting(Exception):
    """转写回填、进了背景缓存之后的第一步（认主检查）：走到这儿就停，不往下发消息。"""


async def _check_group_reference_backfilled_once_before_decision(long_text=False, spoken_at=False):
    with tempfile.TemporaryDirectory() as tmp:
        archive = MessageArchive(tmp, keep_days=0)
        state = SimpleNamespace(data={"buffers": {}}, mark_dirty=Mock())
        started, release = asyncio.Event(), asyncio.Event()
        async def transcribe(*args, **kwargs):
            started.set()
            await release.wait()
            return VoiceResult("ok", "小王去公园" if spoken_at else "去公园", "qq_reference")
        processor = SimpleNamespace(process=AsyncMock(side_effect=transcribe))
        message = SimpleNamespace(id="voice-group", content="字" * 600 if long_text else "", group_openid="group-voice",
                                  author=SimpleNamespace(member_openid="speaker"), mentions=[],
                                  attachments=[], _voice_attachments=(VoiceAttachment(reference_text="去公园"),))
        client = SimpleNamespace(robot=SimpleNamespace(id="bot", name=""))
        bot.processed_msg_ids.clear()
        bot.group_buffers.pop("group-voice", None)
        try:
            with patch.object(runtime, "ARCHIVE", archive), patch.object(runtime, "STATE", state), \
                 patch.object(bot, "VOICE", SimpleNamespace(processor=processor)), \
                 patch.object(bot.naming, "bot_names", return_value=["小王"]), \
                 patch.object(bot, "deliver_carried", AsyncMock()), \
                 patch.object(bot, "looks_like_claim_attempt", side_effect=ReachedRouting):
                first = asyncio.create_task(bot.GroupBot.handle_group_msg(client, message))
                await started.wait()
                await bot.GroupBot.handle_group_msg(client, message)
                release.set()
                with pytest.raises(ReachedRouting):
                    await first
            rows, _ = archive.pending("group-voice")
            assert len(rows) == 2
            assert rows[0]["text"].startswith("[语音]")
            if long_text:
                assert len(rows[0]["text"]) == archive.max_text
            assert rows[1]["kind"] == "voice_transcript"
            assert rows[1]["text"] == ("[语音转写：小王去公园]" if spoken_at else "[语音转写：去公园]")
            if spoken_at:
                assert rows[1]["at"] == 1
                assert "at" not in rows[0]
            assert bot.group_buffers["group-voice"][0]["text"].startswith(rows[1]["text"])
            if long_text:
                assert bot.group_buffers["group-voice"][0]["text"].endswith("字" * 600)
            assert processor.process.await_count == 1
        finally:
            archive.close()
            bot.group_buffers.pop("group-voice", None)
            bot.processed_msg_ids.clear()


async def _check_private_voice_is_independent_and_no_duplicate_reply():
    processor = SimpleNamespace(process=AsyncMock(return_value=VoiceResult("ok", "一起去公园", "qq_reference")))
    message = SimpleNamespace(id="voice-private", content="", author=SimpleNamespace(user_openid="owner"),
                              _voice_attachments=(VoiceAttachment(reference_text="一起去公园"),))
    reply = AsyncMock()
    bot.processed_msg_ids.clear()
    try:
        with patch.object(bot, "VOICE", SimpleNamespace(processor=processor)), \
             patch.object(runtime, "OWNER_OPENID", "owner"), \
             patch.object(bot, "reply_in_session", reply), \
             patch.object(runtime.STATE, "mark_dirty"), \
             patch.object(runtime.BUDGET, "can_consume", return_value=True):
            await bot.GroupBot.on_c2c_message_create(None, message)
            await bot.GroupBot.on_c2c_message_create(None, message)
        assert processor.process.await_count == 1
        assert reply.await_count == 1
        assert reply.await_args.args[2] == "一起去公园"
        assert reply.await_args.kwargs["private"] is True
    finally:
        bot.processed_msg_ids.clear()


async def _check_private_sensitive_transcript_not_forwarded():
    processor = SimpleNamespace(process=AsyncMock(return_value=VoiceResult("ok", "sensitive", "qq_reference")))
    message = SimpleNamespace(id="voice-private-filtered", content="", author=SimpleNamespace(user_openid="owner"),
                              _voice_attachments=(VoiceAttachment(reference_text="sensitive"),))
    reply, safe = AsyncMock(), AsyncMock()
    bot.processed_msg_ids.clear()
    try:
        with patch.object(bot, "VOICE", SimpleNamespace(processor=processor)), \
             patch.object(bot.wordfilter, "has_hit", return_value=True), \
             patch.object(bot, "reply_in_session", reply), patch.object(bot, "safe_reply", safe):
            await bot.GroupBot.on_c2c_message_create(None, message)
        reply.assert_not_awaited()
        safe.assert_awaited_once()
    finally:
        bot.processed_msg_ids.clear()


def test_group_reference_backfilled_once_before_decision():
    asyncio.run(_check_group_reference_backfilled_once_before_decision())

def test_group_voice_marker_survives_original_text_truncation():
    asyncio.run(_check_group_reference_backfilled_once_before_decision(long_text=True))

def test_spoken_bot_name_marks_derived_row_without_mutating_source():
    asyncio.run(_check_group_reference_backfilled_once_before_decision(spoken_at=True))

def test_private_voice_is_independent_and_no_duplicate_reply():
    asyncio.run(_check_private_voice_is_independent_and_no_duplicate_reply())

def test_private_sensitive_transcript_not_forwarded():
    asyncio.run(_check_private_sensitive_transcript_not_forwarded())


def test_multiple_voices_are_bounded_ordered_and_partials_marked():
    async def check():
        async def process(voice, **kwargs):
            if voice.reference_text == "bad":
                return VoiceResult("unsupported_format")
            return VoiceResult("ok", voice.reference_text, "qq_reference")
        processor = SimpleNamespace(process=AsyncMock(side_effect=process))
        voices = tuple(VoiceAttachment(reference_text=value) for value in ("one", "bad", "two"))
        with patch.object(bot, "VOICE", SimpleNamespace(processor=processor)):
            result = await bot._recognize_voices(voices, "event")
            assert result == "one；（一段语音未识别）；two"
            assert processor.process.await_count == 3
            assert await bot._recognize_voices(voices + (voices[0],), "other") is None
            assert processor.process.await_count == 3
    asyncio.run(check())


def test_voice_activity_finishes_after_cancellation_and_never_contains_the_audio_or_transcript():
    async def check():
        with tempfile.TemporaryDirectory() as tmp:
            log = InteractionLog(tmp)
            started = asyncio.Event()
            async def process(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()
            processor = SimpleNamespace(process=AsyncMock(side_effect=process))
            try:
                with patch.object(bot, "VOICE", SimpleNamespace(processor=processor)), \
                     patch.object(runtime, "INTERACTIONS", log):
                    task = asyncio.create_task(bot._recognize_voices(
                        (VoiceAttachment(reference_text="synthetic-secret-transcript"),),
                        "synthetic-private-source", group="synthetic-group"))
                    await started.wait()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                rows = [json.loads(line) for path in Path(tmp).glob("*.jsonl")
                        for line in path.read_text().splitlines()]
                assert [row["status"] for row in rows] == ["started", "finished"]
                assert all(row["kind"] == "perception" and row["medium"] == "voice" for row in rows)
                assert rows[1]["activity"] == rows[0]["event_id"]
                assert "synthetic-secret-transcript" not in json.dumps(rows)
                assert "synthetic-private-source" not in json.dumps(rows)
            finally:
                log.close()
    asyncio.run(check())


def test_private_and_disabled_voice_do_not_publish_group_activity():
    async def check():
        with tempfile.TemporaryDirectory() as tmp:
            log = InteractionLog(tmp)
            processor = SimpleNamespace(process=AsyncMock(return_value=VoiceResult("ok", "你好", "qq_reference")))
            voice = (VoiceAttachment(reference_text="你好"),)
            try:
                with patch.object(bot, "VOICE", SimpleNamespace(processor=processor)), \
                     patch.object(runtime, "INTERACTIONS", log):
                    assert await bot._recognize_voices(voice, "private") == "你好"
                    with patch.object(bot.capabilities, "enabled", return_value=False):
                        assert await bot._recognize_voices(voice, "disabled", group="synthetic-group") is None
                assert list(Path(tmp).glob("*.jsonl")) == []
            finally:
                log.close()
    asyncio.run(check())


def test_botpy_raw_gateway_debug_is_disabled():
    root, sdk = logging.getLogger(), logging.getLogger("botpy")
    old_root, old_sdk, old_handlers = root.level, sdk.level, list(root.handlers)
    try:
        sdk.setLevel(logging.NOTSET)
        with patch.object(config, "LOG_LEVEL", logging.DEBUG), \
             patch("bot.os.makedirs"), \
             patch("bot.logging.handlers.RotatingFileHandler", return_value=logging.NullHandler()), \
             patch("bot.logging.StreamHandler", return_value=logging.NullHandler()):
            bot.setup_logging()
        assert sdk.getEffectiveLevel() >= logging.INFO
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_root)
        sdk.setLevel(old_sdk)
