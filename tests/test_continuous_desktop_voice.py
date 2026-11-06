"""Continuous calls with memory audio, without devices or model requests."""
import asyncio
import unittest
from unittest.mock import Mock

import test_desktop_voice as ptt
from regular.desktop_voice import NativeVoiceSession


class ConversationAudio(ptt._Audio):
    def __init__(self):
        super().__init__()
        self.conversations = 0
        self.muted = False
        self.mute_changes = []

    def start_conversation(self, pcm, error):
        self.conversations += 1
        self.recordings += 1
        self.pcm, self.error = pcm, error

    def set_muted(self, muted):
        self.muted = muted
        self.mute_changes.append(muted)


class ContinuousNativeVoiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws, self.audio, self.events = ptt._WS(), ConversationAudio(), []
        self.client = Mock()
        self.client.voice_status.return_value = {"available": True}
        self.client.voice_start.return_value = {"ticket": "synthetic-ticket"}
        self.client.voice_endpoint.return_value = (
            "ws://127.0.0.1:8765/companion/voice/stream",
            {"X-Panel-Token": "synthetic-token", "X-Voice-Ticket": "synthetic-ticket"})
        self.http = None

        def session(**kwargs):
            self.http = ptt._HTTP(self.ws, **kwargs)
            return self.http

        self.voice = NativeVoiceSession(self.client, "synthetic-csrf", self.events.append,
                                       continuous=True, audio_factory=lambda: self.audio,
                                       session_factory=session)
        self.task = asyncio.create_task(self.voice._run())
        await self.until(lambda: self.http is not None)

    async def asyncTearDown(self):
        self.voice.close()
        await self.task

    async def until(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(.005)
        self.fail("continuous voice did not reach the expected state")

    async def ready(self):
        self.ws.event(type="ready", rate=24000, channels=1, mode="continuous")
        await self.until(lambda: self.audio.conversations == 1 and bool(self.audio.mute_changes))

    def controls(self):
        return [event for event in self.ws.sent if isinstance(event, dict)]

    async def test_ready_opens_continuous_capture_without_pressing_or_committing(self):
        self.assertEqual(self.audio.recordings, 0)
        await self.ready()
        self.client.voice_start.assert_called_once_with("synthetic-csrf", mode="continuous")
        self.assertFalse(self.voice.press())
        sample = b"\x01\x00" * 240
        self.audio.pcm(sample)
        await self.until(lambda: sample in self.ws.sent)
        self.voice.release()
        self.assertIn({"type": "listen.resume"}, self.controls())
        self.assertFalse(any(event["type"] in ("input.start", "input.commit", "input.cancel")
                             for event in self.controls()))
        self.assertEqual(self.audio.recordings, 1)
        self.assertEqual(self.audio.stops, 0)

    async def test_mute_drops_pending_and_new_pcm_then_resumes_the_same_device(self):
        await self.ready()
        queued = b"\x02\x00" * 240
        muted = b"\x03\x00" * 240
        self.audio.pcm(queued)
        self.assertTrue(self.voice.set_muted(True))
        self.audio.pcm(muted)
        await self.until(lambda: self.audio.muted)
        self.assertNotIn(queued, self.ws.sent)
        self.assertNotIn(muted, self.ws.sent)
        self.assertIn({"type": "listen.pause"}, self.controls())
        self.assertEqual(self.audio.stops, 0)
        self.assertTrue(self.voice.set_muted(False))
        await self.until(lambda: not self.audio.muted)
        resumed = b"\x04\x00" * 240
        self.audio.pcm(resumed)
        await self.until(lambda: resumed in self.ws.sent)
        self.assertEqual(self.audio.conversations, 1)
        self.assertEqual(self.audio.mute_changes, [False, True, False])
        self.assertEqual(self.audio.closed, 0)

    async def test_speech_start_interrupts_playback_even_when_upload_is_backed_up(self):
        await self.ready()
        self.ws.event(type="audio.start", turn_id=1, item_id="synthetic-item")
        await self.until(lambda: self.audio.playbacks == 1)
        self.ws.binary(b"\x05\x00" * 240)
        await self.until(lambda: bool(self.audio.chunks))
        self.audio.played = 57
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_send(data):
            entered.set()
            await release.wait()
            self.ws.sent.append(data)

        self.ws.send_bytes = blocked_send
        self.audio.pcm(b"\x06\x00" * 240)
        await asyncio.wait_for(entered.wait(), 1)
        try:
            self.ws.event(type="speech.started", speech_id="synthetic-speech-2")
            await self.until(lambda: not self.audio.chunks)
            self.assertEqual(self.audio.closed, 0)
            self.assertEqual(self.audio.stops, 0)
            self.assertEqual(self.audio.conversations, 1)
            expected = {"type": "interrupt", "played_ms": 57, "speech_id": "synthetic-speech-2"}
            self.assertNotIn(expected, self.controls())
        finally:
            release.set()
        await self.until(lambda: expected in self.controls())
        next_sample = b"\x07\x00" * 240
        self.audio.pcm(next_sample)
        await self.until(lambda: next_sample in self.ws.sent)

    async def test_capture_error_ends_audio_and_transport_without_reconnecting(self):
        await self.ready()
        self.audio.error("模拟麦克风故障")
        await self.until(lambda: self.task.done())
        self.assertTrue(self.ws.closed)
        self.assertGreaterEqual(self.audio.closed, 1)
        self.assertFalse(self.voice._ready)
        self.assertTrue(any(event["type"] == "error" and "模拟麦克风故障" in event["message"]
                            for event in self.events))
        self.client.voice_start.assert_called_once()

    async def test_an_old_panel_without_mode_never_opens_the_microphone(self):
        self.ws.event(type="ready", rate=24000, channels=1)
        await self.until(lambda: self.task.done())
        self.assertEqual(self.audio.conversations, 0)
        self.assertIsNone(self.audio.pcm)
        self.assertNotIn({"type": "listen.resume"}, self.controls())
        self.assertTrue(any(event["type"] == "error" and "尚不支持持续对话" in event["message"]
                            for event in self.events))

    async def test_close_discards_further_capture_and_closes_the_device(self):
        await self.ready()
        self.voice.close()
        await self.task
        self.audio.pcm(b"\x08\x00" * 240)
        self.assertGreaterEqual(self.audio.closed, 1)
        self.assertTrue(self.ws.closed)
        self.assertNotIn(b"\x08\x00" * 240, self.ws.sent)
        self.assertEqual(self.audio.conversations, 1)
