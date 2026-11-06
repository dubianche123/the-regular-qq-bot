"""Synthetic, offline desktop audio protocol tests. No device or API requests."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest

from regular import realtime_voice as voice
from voice_budget import Budget


def asynchronous(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def cfg_for(tmp_path, **values):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return SimpleNamespace(BASE_DIR=tmp_path, DATA_DIR=data, INTERACTION_LOG_DIR=data / "interactions",
                           BOT_NAMES=["合成测试群友"], VOICE_API_KEY="SYNTHETIC_SECRET", SSL_VERIFY=True,
                           VOICE_BASE_URL="https://api.openai.com/v1", VOICE_OUTPUT_MODEL="gpt-realtime-2.1-mini",
                           VOICE_OUTPUT_VOICE="marin", EXTERNAL_ENV_KEYS=set(),
                           **values)


class Native:
    def __init__(self):
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.sent = []

    async def receive(self):
        return await self.input.get()

    def send(self, kind, **fields):
        self.input.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                                              data=json.dumps({"type": kind, **fields})))

    def pcm(self, data=b"\0\0" * 2400):
        self.input.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=data))

    async def send_json(self, value):
        self.sent.append(value)
        self.output.put_nowait(value)

    async def send_bytes(self, value):
        self.sent.append(value)
        self.output.put_nowait(value)

    async def expect(self, kind):
        while True:
            event = await asyncio.wait_for(self.output.get(), 1)
            if isinstance(event, dict) and event.get("type") == kind:
                return event


class Provider:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []

    async def send_json(self, value):
        self.sent.append(value)
        if value["type"] == "session.update":
            self.push("session.updated")
        elif value["type"] == "response.create":
            self.push("response.created", response={"id": "synthetic_response"})
        elif value["type"] == "response.cancel":
            self.push("response.done", response={"status": "cancelled"})

    def push(self, kind, **fields):
        self.events.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                                               data=json.dumps({"type": kind, **fields})))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()


class Harness:
    def __init__(self, cfg, *, budget=None):
        self.native = Native()
        self.provider = Provider()
        self.connections = []

        @asynccontextmanager
        async def connector(url, key):
            self.connections.append((url, key))
            yield self.provider

        self.session = voice.RealtimeSession(cfg, connector=connector, budget=budget)
        self.task = None

    async def start(self):
        self.task = asyncio.create_task(self.session.run(self.native))
        await self.native.expect("ready")
        return self

    async def record(self):
        self.native.send("input.start")
        await self.native.expect("state")
        self.native.pcm()
        self.native.send("input.commit")
        while True:
            state = await self.native.expect("state")
            if state["state"] == "thinking":
                break

    async def finish(self):
        self.native.send("stop")
        await asyncio.wait_for(self.task, 1)


def test_readiness_is_configuration_only_and_qq_flags_do_not_gate_it(tmp_path):
    cfg = cfg_for(tmp_path, VOICE_INPUT_ENABLED=False, VOICE_OUTPUT_ENABLED=False)
    status = voice.readiness(cfg)
    assert status == {"available": True, "reason": "", "model": "gpt-realtime-2.1-mini", "voice": "marin", "daily_limit": 30}
    assert not (cfg.DATA_DIR / "voice-outbound-budget.sqlite3").exists()
    assert "SYNTHETIC_SECRET" not in json.dumps(status)


def test_saved_new_session_settings_and_official_key_boundary(tmp_path):
    cfg = cfg_for(tmp_path)
    (tmp_path / ".env").write_text("VOICE_OUTPUT_MODEL=saved-model\nVOICE_OUTPUT_VOICE=cedar\nDESKTOP_VOICE_DAILY_LIMIT=0\n")
    status = voice.readiness(cfg)
    assert status["model"] == "saved-model" and status["voice"] == "cedar"
    assert not status["available"] and status["daily_limit"] == 0
    (tmp_path / ".env").write_text("VOICE_API_KEY=\nVOICE_BASE_URL=https://not-openai.example/v1\nOPENAI_API_KEY=synthetic-shared\nOPENAI_BASE_URL=https://api.openai.com/v1\n")
    assert not voice.readiness(cfg)["available"]
    assert not voice._settings(cfg).key


def test_real_durable_budget_category_is_separate_and_used_in_readiness(tmp_path):
    cfg = cfg_for(tmp_path, DESKTOP_VOICE_DAILY_LIMIT=1)
    db = cfg.DATA_DIR / "voice-outbound-budget.sqlite3"
    Budget(db, "provider", 1).reserve().commit()
    assert voice.readiness(cfg)["available"]
    Budget(db, "desktop_realtime", 1).reserve().commit()
    assert not voice.readiness(cfg)["available"]


@asynchronous
async def test_ptt_audio_stream_and_transcript_without_extra_asr(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    try:
        request = h.provider.sent[0]["session"]
        assert request["audio"]["input"]["turn_detection"] is None
        assert "transcription" not in request["audio"]["input"]
        assert request["max_output_tokens"] == 1024
        assert not any(e["type"] == "response.create" for e in h.provider.sent)
        await h.record()
        h.provider.push("response.output_audio.delta", item_id="synthetic_item", content_index=0,
                        delta=base64.b64encode(b"\0\0" * 2400).decode())
        h.provider.push("response.output_audio_transcript.delta", delta="你好")
        h.provider.push("response.output_audio_transcript.delta", delta="呀")
        h.provider.push("response.output_audio_transcript.done", transcript="你好呀")
        h.provider.push("response.done", response={"status": "completed", "output": []})
        await h.native.expect("audio.end")
        assert b"\0\0" * 2400 in h.native.sent
        assert [e["text"] for e in h.native.sent if isinstance(e, dict) and e["type"] == "transcript"] == ["你好", "你好呀", "你好呀"]
        assert h.connections == [("wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini", "SYNTHETIC_SECRET")]
        assert "SYNTHETIC_SECRET" not in str(h.native.sent)
    finally:
        await h.finish()


@asynchronous
async def test_fresh_local_flags_and_only_numeric_group_context_each_turn(tmp_path):
    cfg = cfg_for(tmp_path)
    cfg.INTERACTION_LOG_DIR.mkdir()
    from archive import day_key
    (cfg.INTERACTION_LOG_DIR / (day_key() + ".jsonl")).write_text(json.dumps({
        "kind": "incoming", "group": "PRIVATE_GROUP_ID", "text": "DO_NOT_SEND_GROUP_TEXT"}) + "\n")
    h = await Harness(cfg).start()
    try:
        await h.record()
        h.provider.push("response.done", response={"status": "completed"})
        await h.native.expect("state")
        (tmp_path / ".env").write_text("IMAGE_GENERATION_ENABLED=false\n")
        await h.record()
        contexts = [e["item"]["content"][0]["text"] for e in h.provider.sent
                    if e["type"] == "conversation.item.create" and e["item"]["type"] == "message"]
        assert "画图开" in contexts[0] and "画图关" in contexts[1]
        assert "1 条消息" in contexts[0]
        assert "DO_NOT_SEND_GROUP_TEXT" not in json.dumps(h.provider.sent)
        assert "PRIVATE_GROUP_ID" not in json.dumps(h.provider.sent)
    finally:
        await h.finish()


@asynchronous
async def test_interrupt_cancels_and_truncates_only_played_duration(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    try:
        await h.record()
        h.provider.push("response.output_audio.delta", item_id="synthetic_item", content_index=0,
                        delta=base64.b64encode(b"\0\0" * 24000).decode())
        await h.native.expect("audio.start")
        h.native.send("interrupt", played_ms=400)
        await h.native.expect("audio.end")
        assert any(e["type"] == "response.cancel" for e in h.provider.sent)
        truncate = next(e for e in h.provider.sent if e["type"] == "conversation.item.truncate")
        assert truncate == {"type": "conversation.item.truncate", "item_id": "synthetic_item", "content_index": 0, "audio_end_ms": 400}
    finally:
        await h.finish()


@asynchronous
async def test_manager_proposals_never_write_or_start_extra_response(tmp_path):
    cfg = cfg_for(tmp_path)
    h = await Harness(cfg).start()
    try:
        await h.record()
        h.provider.push("response.done", response={"status": "completed", "output": [
            {"type": "function_call", "name": "propose_toggle", "call_id": "synthetic_call_1",
             "arguments": '{"flag":"image","enabled":false}'},
            {"type": "function_call", "name": "propose_handoff", "call_id": "synthetic_call_2", "arguments": "{}"}]})
        proposal = await h.native.expect("proposal")
        await h.native.expect("state")
        assert proposal["action"] == {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        assert sum(e["type"] == "proposal" for e in h.native.sent if isinstance(e, dict)) == 1
        assert sum(e["type"] == "response.create" for e in h.provider.sent) == 1
        results = [json.loads(e["item"]["output"]) for e in h.provider.sent
                   if e["type"] == "conversation.item.create" and e["item"]["type"] == "function_call_output"]
        assert results == [{"status": "confirmation_required", "executed": False}, {"status": "ignored", "executed": False}]
        assert not (tmp_path / ".env").exists()
    finally:
        await h.finish()


@asynchronous
@pytest.mark.parametrize("arguments", ['{"flag":"image","enabled":"false"}', '{"flag":[],"enabled":false}', '{"flag":"shell","enabled":true}', '{"flag":"image","enabled":false,"extra":1}'])
async def test_invalid_manager_function_is_not_a_proposal(tmp_path, arguments):
    h = await Harness(cfg_for(tmp_path)).start()
    try:
        await h.record()
        h.provider.push("response.done", response={"status": "completed", "output": [{
            "type": "function_call", "name": "propose_toggle", "call_id": "synthetic_call", "arguments": arguments}]})
        await h.native.expect("state")
        assert not any(e.get("type") == "proposal" for e in h.native.sent if isinstance(e, dict))
    finally:
        await h.finish()


@asynchronous
async def test_provider_error_never_exposes_message_or_unknown_code(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    h.provider.push("error", error={"message": "SYNTHETIC_SECRET raw provider message", "code": "SYNTHETIC_SECRET", "type": "invalid_request_error"})
    error = await h.native.expect("error")
    await asyncio.wait_for(h.task, 1)
    assert error["code"] == "provider_error" and error["error_type"] == "invalid_request_error"
    assert "SYNTHETIC_SECRET" not in str(h.native.sent)


@asynchronous
async def test_unsent_cancel_releases_budget_but_any_audio_spends_it(tmp_path):
    cfg = cfg_for(tmp_path, DESKTOP_VOICE_DAILY_LIMIT=1)
    h = await Harness(cfg).start()
    h.native.send("input.start")
    await h.native.expect("state")
    h.native.send("input.cancel")
    await h.native.expect("state")
    assert voice.readiness(cfg)["available"]
    h.native.send("input.start")
    await h.native.expect("state")
    h.native.pcm()
    h.native.send("input.cancel")
    await h.native.expect("state")
    assert not voice.readiness(cfg)["available"]
    await h.finish()


@asynchronous
@pytest.mark.parametrize("event", ["session.update", "response.create", "unknown"])
async def test_native_cannot_forward_arbitrary_provider_events(tmp_path, event):
    h = await Harness(cfg_for(tmp_path)).start()
    h.native.send(event, instructions="override")
    assert (await h.native.expect("error"))["code"] == "protocol"
    await asyncio.wait_for(h.task, 1)
    assert not any(e.get("instructions") == "override" for e in h.provider.sent)


@asynchronous
async def test_twenty_second_pcm_limit_is_enforced_before_forward(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    h.native.send("input.start")
    await h.native.expect("state")
    chunk = b"\0\0" * 24000
    for _ in range(21):
        h.native.pcm(chunk)
    await h.native.expect("error")
    await asyncio.wait_for(h.task, 1)
    assert sum(e["type"] == "input_audio_buffer.append" for e in h.provider.sent) == 20


@asynchronous
async def test_twelve_turns_limit_survives_cancel_and_stops_thirteenth(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    for _ in range(12):
        h.native.send("input.start")
        await h.native.expect("state")
        h.native.send("input.cancel")
        await h.native.expect("state")
    h.native.send("input.start")
    assert "12" in (await h.native.expect("error"))["message"]
    await asyncio.wait_for(h.task, 1)


@asynchronous
async def test_connector_rejects_redirect_before_any_followup_request():
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def ws_connect(self, url, **kwargs):
            seen["headers"] = kwargs["headers"]
            raise AssertionError("not needed for redirect trace test")

    with patch.object(voice.aiohttp, "ClientSession", Client):
        with pytest.raises(AssertionError):
            async with voice.official_connector("wss://api.openai.com/v1/realtime", "synthetic"):
                pass
    callback = list(seen["trace_configs"][0].on_request_redirect)[0]
    with pytest.raises(aiohttp.ClientConnectionError, match="voice_redirect_rejected"):
        await callback(None, None, None)


@asynchronous
async def test_session_deadline_cleans_up_both_protocol_tasks(tmp_path):
    with patch.object(voice, "MAX_SESSION_SECONDS", 0.05):
        h = await Harness(cfg_for(tmp_path)).start()
        assert (await h.native.expect("error"))["code"] == "session_timeout"
        await asyncio.wait_for(h.task, 1)
    assert all(task.done() for task in h.session._tasks)


@asynchronous
async def test_thirty_second_output_ceiling_stops_extra_pcm(tmp_path):
    h = await Harness(cfg_for(tmp_path)).start()
    await h.record()
    pcm = b"\0\0" * 24000
    for _ in range(31):
        h.provider.push("response.output_audio.delta", item_id="synthetic_item", content_index=0,
                        delta=base64.b64encode(pcm).decode())
    assert "30 秒" in (await h.native.expect("error"))["message"]
    await asyncio.wait_for(h.task, 1)
    assert sum(len(e) for e in h.native.sent if isinstance(e, bytes)) == voice.RATE * 2 * 30


@asynchronous
async def test_sent_audio_spends_budget_even_if_response_disconnects(tmp_path):
    cfg = cfg_for(tmp_path, DESKTOP_VOICE_DAILY_LIMIT=1)
    h = await Harness(cfg).start()
    await h.record()
    h.provider.events.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None))
    await h.native.expect("error")
    await asyncio.wait_for(h.task, 1)
    assert not voice.readiness(cfg)["available"]
