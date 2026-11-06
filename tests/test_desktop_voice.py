"""Native PTT tests use memory audio/WS adapters: no microphone, speakers or paid API."""
import asyncio
import importlib.util
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import aiohttp

from regular import desktop_voice as voice


class _Audio:
    def __init__(self):
        self.recordings = self.stops = self.closed = self.playbacks = 0
        self.pcm = None
        self.chunks = []
        self.played = 0
        self.ended = False

    def start_capture(self, pcm, error):
        self.recordings += 1
        self.pcm, self.error = pcm, error

    def stop_capture(self):
        self.stops += 1

    def begin_playback(self):
        self.playbacks += 1
        self.ended = False

    def feed_audio(self, data):
        self.chunks.append(data)

    def end_playback(self):
        self.ended = True

    def playback_done(self):
        return self.ended

    def abort_playback(self):
        result, self.played = self.played, 0
        self.chunks.clear()
        return result

    def close(self):
        self.closed += 1


class _WS:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self._response = SimpleNamespace(headers={"X-Regular-Panel": "1"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()

    def event(self, **event):
        self.events.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, json=lambda: event))

    def binary(self, data):
        self.events.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=data))

    async def send_json(self, data):
        self.sent.append(data)

    async def send_bytes(self, data):
        self.sent.append(data)


class _HTTP:
    def __init__(self, ws, **kwargs):
        self.ws, self.kwargs = ws, kwargs
        self.connect = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def ws_connect(self, url, **kwargs):
        self.connect = (url, kwargs)
        return self.ws


class NativeTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws, self.audio, self.events = _WS(), _Audio(), []
        self.client = Mock()
        self.client.voice_status.return_value = {"available": True}
        self.client.voice_start.return_value = {"ticket": "synthetic-ticket"}
        self.client.voice_endpoint.return_value = (
            "ws://127.0.0.1:8765/companion/voice/stream",
            {"X-Panel-Token": "synthetic-token", "X-Voice-Ticket": "synthetic-ticket"})
        self.http = None

        def session(**kwargs):
            self.http = _HTTP(self.ws, **kwargs)
            return self.http
        self.voice = voice.NativeVoiceSession(self.client, "synthetic-csrf", self.events.append,
                                               audio_factory=lambda: self.audio, session_factory=session)
        self.task = asyncio.create_task(self.voice._run())
        await self.until(lambda: self.http is not None)

    async def asyncTearDown(self):
        self.voice.close()
        await self.task

    async def until(self, predicate):
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(.005)
        self.fail("native worker did not reach the expected state")

    async def ready(self):
        self.ws.event(type="ready", rate=24000, channels=1)
        await self.until(lambda: self.voice._ready)

    async def test_ticket_and_token_stay_in_headers_and_start_never_records(self):
        await self.ready()
        self.assertEqual(self.audio.recordings, 0)
        url, kwargs = self.http.connect
        self.assertNotIn("synthetic", url)
        self.assertEqual(kwargs["headers"]["X-Voice-Ticket"], "synthetic-ticket")
        self.assertFalse(self.http.kwargs["trust_env"])
        redirect = self.http.kwargs["trace_configs"][0].on_request_redirect[0]
        with self.assertRaisesRegex(voice.VoiceError, "跳转"):
            await redirect(None, None, None)

    async def test_press_streams_pcm_release_commits_and_next_press_interrupts_heard_audio(self):
        await self.ready()
        self.assertTrue(self.voice.press())
        await self.until(lambda: self.audio.recordings == 1)
        self.audio.pcm(b"\x01\x00" * 480)
        self.voice.release()
        await self.until(lambda: {"type": "input.commit"} in self.ws.sent)
        self.assertEqual([x["type"] for x in self.ws.sent if isinstance(x, dict)],
                         ["interrupt", "input.start", "input.commit"])
        self.assertTrue(any(isinstance(x, bytes) for x in self.ws.sent))
        self.ws.event(type="audio.start", turn_id=1, item_id="synthetic-item")
        self.ws.binary(b"\x02\x00" * 480)
        await self.until(lambda: bool(self.audio.chunks))
        self.audio.played = 37
        self.voice.press()
        await self.until(lambda: self.audio.recordings == 2)
        self.assertIn({"type": "interrupt", "played_ms": 37}, self.ws.sent)
        self.assertEqual(self.audio.chunks, [])
        self.voice.cancel_input()
        await self.until(lambda: {"type": "input.cancel"} in self.ws.sent)
        self.assertEqual(sum(x == {"type": "input.commit"} for x in self.ws.sent), 1)

    async def test_pending_capture_does_not_use_a_zero_timestamp_for_the_twenty_second_limit(self):
        await self.ready()
        self.voice._holding.set()
        self.assertIsNone(self.voice._capture_started)
        await asyncio.sleep(.07)
        self.assertFalse(any(event["type"] == "error" for event in self.events))
        self.voice.cancel_input()

    async def test_naturally_finished_audio_keeps_its_heard_duration_for_the_next_interrupt(self):
        await self.ready()
        self.ws.event(type="audio.start", turn_id=1, item_id="synthetic-item")
        await self.until(lambda: self.audio.playbacks == 1)
        self.audio.played = 42
        self.ws.event(type="audio.end", turn_id=1)
        await self.until(lambda: not self.voice._playing)
        self.voice.press()
        await self.until(lambda: self.audio.recordings == 1)
        self.assertIn({"type": "interrupt", "played_ms": 42}, self.ws.sent)

    async def test_cancel_closes_the_device_even_when_the_network_sender_is_backed_up(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_send(data):
            entered.set()
            await release.wait()
        self.ws.send_bytes = blocked_send
        await self.ready()
        self.voice.press()
        await self.until(lambda: self.audio.recordings == 1)
        self.audio.pcm(b"\x01\x00" * 480)
        await entered.wait()
        self.voice.cancel_input()
        await self.until(lambda: self.audio.stops > 0)
        self.assertNotIn({"type": "input.cancel"}, self.ws.sent)
        release.set()
        await self.until(lambda: {"type": "input.cancel"} in self.ws.sent)

    async def test_repress_clears_playback_without_waiting_for_a_backed_up_upload(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_send(data):
            entered.set()
            await release.wait()
        self.ws.send_bytes = blocked_send
        await self.ready()
        self.voice.press()
        await self.until(lambda: self.audio.recordings == 1)
        self.audio.pcm(b"\x01\x00" * 480)
        await entered.wait()
        self.voice.release()
        self.audio.chunks.append(b"synthetic pending audio")
        self.audio.played = 57
        self.voice.press()
        await self.until(lambda: not self.audio.chunks)
        self.assertNotIn({"type": "interrupt", "played_ms": 57}, self.ws.sent)
        release.set()
        await self.until(lambda: {"type": "interrupt", "played_ms": 57} in self.ws.sent)

    async def test_a_stalled_ready_has_a_short_deadline_and_no_reconnect(self):
        now = [self.voice.clock()]
        self.voice.clock = lambda: now[0]
        await asyncio.sleep(.01)
        now[0] += 21
        await self.until(lambda: self.task.done())
        self.assertTrue(any("等待太久" in event.get("message", "") for event in self.events))
        self.client.voice_start.assert_called_once()

    async def test_overflow_ends_the_session_instead_of_silently_dropping_speech(self):
        await self.ready()
        self.voice._overflow.set()
        await self.until(lambda: self.task.done())
        self.assertEqual(self.audio.closed, 1)
        self.assertTrue(any("传输跟不上" in event.get("message", "") for event in self.events))


class AudioAdapterTest(unittest.TestCase):
    def test_dac_progress_counts_only_audio_already_heard_and_abort_discards_pending_audio(self):
        sd = Mock()
        output = sd.RawOutputStream.return_value
        output.time = 9.0
        audio = voice.SoundDeviceAudio(sd)
        audio.begin_playback()
        self.assertEqual(sd.RawOutputStream.call_args.kwargs["samplerate"], 24000)
        self.assertEqual(sd.RawOutputStream.call_args.kwargs["dtype"], "int16")
        callback = sd.RawOutputStream.call_args.kwargs["callback"]
        audio.feed_audio(b"\x01\x00" * 480)
        audio.end_playback()  # A short completed reply drains without the jitter-buffer lead.
        buffer = bytearray(960)
        callback(buffer, 480, SimpleNamespace(outputBufferDacTime=10.0), False)
        self.assertEqual(audio.played_ms(), 0)
        output.time = 10.01
        self.assertIn(audio.played_ms(), (9, 10))
        self.assertFalse(audio.playback_done())
        output.time = 10.1
        self.assertTrue(audio.playback_done())
        self.assertEqual(audio.abort_playback(), 20)
        output.abort.assert_called_once()
        self.assertEqual(audio.played_ms(), 0)

    def test_input_is_lazy_and_output_queue_is_bounded(self):
        sd = Mock()
        audio = voice.SoundDeviceAudio(sd)
        sd.RawInputStream.assert_not_called()
        audio.start_capture(Mock(), Mock())
        self.assertEqual(sd.RawInputStream.call_args.kwargs["channels"], 1)
        with self.assertRaisesRegex(voice.VoiceError, "过长"):
            audio.feed_audio(b"\x00" * (voice.MAX_OUTPUT_BYTES + 2))
        audio.close()

    def test_a_removed_output_device_still_closes_and_discards_its_buffer(self):
        class Removed:
            abort = Mock()
            close = Mock()

            @property
            def time(self):
                raise RuntimeError("synthetic removed device")
        audio = voice.SoundDeviceAudio(Mock())
        output = audio._output = Removed()
        self.assertEqual(audio.abort_playback(), 0)
        output.close.assert_called_once()

    def test_missing_mac_bundle_description_never_requests_permission_from_python_app(self):
        bundle = Mock()
        bundle.mainBundle.return_value.objectForInfoDictionaryKey_.return_value = None
        device = Mock()
        av = SimpleNamespace(AVCaptureDevice=device, AVMediaTypeAudio="audio",
                             AVAuthorizationStatusAuthorized=3, AVAuthorizationStatusNotDetermined=0)
        callback = Mock()
        with patch.object(voice.sys, "platform", "darwin"), patch.dict("sys.modules", {
                "Foundation": SimpleNamespace(NSBundle=bundle), "AVFoundation": av}):
            voice.request_microphone_permission(callback)
        device.requestAccessForMediaType_completionHandler_.assert_not_called()
        self.assertFalse(callback.call_args.args[0])
        self.assertIn("老群友.app", callback.call_args.args[1])


HAS_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_QT, "optional desktop extra is not installed")
class NativeVoiceCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from regular.desktop_pet import SpriteBank
        cls.app = QApplication.instance() or QApplication(["voice-card-test"])
        cls.sprites = SpriteBank()

    def setUp(self):
        from regular.desktop_pet import PetWidget
        self.session = Mock()
        self.session.press.return_value = True
        self.factory = Mock(return_value=self.session)
        self.permission = Mock(side_effect=lambda callback: callback(True, ""))
        self.pet = PetWidget(Mock(), quit_on_close=False, sprites=self.sprites,
                             voice_factory=self.factory, permission_request=self.permission)
        for timer in (self.pet.animation, self.pet.poll, self.pet.presence_poll):
            timer.stop()
        self.pet.csrf = "synthetic-csrf"

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()

    def test_opening_starts_a_continuous_call_with_one_mute_control(self):
        pet = self.pet
        pet.available = False  # voice uses its own service, not QQ's text-chat bridge
        pet.show_voice()
        self.assertEqual(pet.voice_card.grab().toImage().pixelColor(0, 0).alpha(), 0)
        self.permission.assert_called_once()
        self.session.start.assert_called_once()
        self.assertTrue(self.factory.call_args.kwargs['continuous'])
        self.session.press.assert_not_called()
        self.assertFalse(hasattr(pet.voice_card, "start_button"))
        self.assertFalse(pet.voice_card.talk.isEnabled())
        from PySide6.QtWidgets import QPushButton
        self.assertEqual(len(pet.voice_card.findChildren(QPushButton)), 2)
        generation = pet._voice_generation
        pet._voice_event(generation, {"type": "ready"})
        self.assertEqual(pet.voice_card.talk.text(), "静音麦克风")
        self.assertTrue(pet.voice_card.talk.isEnabled())
        # The actual rendered control has transparent corners, not a native bezel.
        card = pet.voice_card.grab().toImage()
        button = pet.voice_card.talk
        self.assertEqual(card.pixelColor(button.x(), button.y()).name(), "#fffdf7")
        self.assertEqual(card.pixelColor(button.x() + 8, button.y() + button.height() // 2).name(), "#173b32")
        pet.voice_card.talk.click()
        self.session.set_muted.assert_called_once_with(True)
        self.assertEqual(pet.voice_state, "muted")
        self.assertEqual(pet.voice_card.talk.text(), "取消静音")
        pet.voice_card.talk.click()
        self.assertEqual(self.session.set_muted.call_args.args, (False,))
        self.assertEqual(pet.voice_state, "listening")
        self.session.press.assert_not_called()
        self.session.release.assert_not_called()
        from PySide6.QtCore import QEvent
        self.app.sendEvent(pet.voice_card.talk, QEvent(QEvent.Type.Leave))
        self.session.cancel_input.assert_not_called()

    def test_error_retries_with_the_same_button_and_close_ends_the_session(self):
        pet = self.pet
        pet.show_voice()
        generation = pet._voice_generation
        pet._voice_event(generation, {"type": "error", "message": "连接中断了，请重试。"})
        pet._voice_event(generation, {"type": "closed"})
        self.assertEqual(pet.voice_card.talk.text(), "重新连接")
        self.assertTrue(pet.voice_card.talk.isEnabled())
        pet.voice_card.talk.click()
        self.assertEqual(self.permission.call_count, 2)
        self.assertEqual(self.session.start.call_count, 2)
        self.session.press.assert_not_called()
        pet.voice_card.close_button.click()
        self.session.close.assert_called_once()
        self.assertFalse(pet.voice_card.isVisible())

    def test_voice_subtitles_extend_one_bubble_without_restarting_the_text_or_pose(self):
        pet = self.pet
        tick = [pet.now()]
        pet.clock = lambda: tick[0]
        pet.brain.motion = True
        generation = pet._voice_generation
        with patch.object(pet.brain, 'chat_reply', wraps=pet.brain.chat_reply) as reply:
            pet._voice_event(generation, {'type': 'transcript', 'role': 'assistant', 'text': '你', 'final': False})
            line = pet.brain.bubble
            pet.bubble.show_bubble(line, tick[0])
            started = pet.bubble.started
            for text, final in (('你好，', False), ('你好，今天怎么样？', False), ('你好，今天怎么样？', True)):
                tick[0] += .03
                pet._voice_event(generation, {'type': 'transcript', 'role': 'assistant', 'text': text, 'final': final})
                self.assertIs(pet.brain.bubble, line)
                pet.bubble.show_bubble(pet.brain.bubble, tick[0])
                self.assertEqual(pet.bubble.visible_text(tick[0]), text)
                self.assertEqual(pet.bubble.started, started)
            self.assertEqual(reply.call_count, 1)
        self.assertEqual(pet.brain.bubble.text, '你好，今天怎么样？')
        self.assertFalse(pet.brain.chat_pending)
        self.assertTrue(any('你好，今天怎么样？' in text for _, text in pet.activity))

    def test_dismissed_voice_subtitle_stays_dismissed_until_the_next_turn(self):
        pet = self.pet
        generation = pet._voice_generation
        pet._voice_event(generation, {'type': 'transcript', 'role': 'assistant', 'text': '你好', 'final': False})
        previous_serial = pet.brain.bubble.serial
        pet.bubble.mousePressEvent(Mock())
        pet._voice_event(generation, {'type': 'transcript', 'role': 'assistant', 'text': '你好呀。', 'final': True})
        self.assertIsNone(pet.brain.bubble)
        pet._voice_event(generation, {'type': 'state', 'state': 'thinking'})
        pet._voice_event(generation, {'type': 'transcript', 'role': 'assistant', 'text': '你好，又见面了。', 'final': False})
        self.assertGreater(pet.brain.bubble.serial, previous_serial)
        self.assertEqual(pet.brain.bubble.text, '你好，又见面了。')

    def test_normal_text_chat_keeps_its_typewriter_after_voice_subtitles(self):
        pet = self.pet
        pet.brain.motion = True
        now = pet.now()
        pet._voice_event(pet._voice_generation, {'type': 'transcript', 'role': 'assistant', 'text': '语音字幕', 'final': True})
        pet.bubble.show_bubble(pet.brain.bubble, now)
        self.assertEqual(pet.bubble.visible_text(now), '语音字幕')
        pet.brain.chat_reply(now + 1, '这是一条普通文字回复。')
        pet.bubble.show_bubble(pet.brain.bubble, now + 1)
        self.assertEqual(pet.bubble.visible_text(now + 1), '这')

    def test_deactivation_keeps_the_call_open_close_ends_and_errors_survive_closed(self):
        from PySide6.QtCore import QEvent
        pet = self.pet
        pet.show_voice()
        pet.start_voice()
        generation = pet._voice_generation
        pet._voice_event(generation, {"type": "ready"})
        self.app.sendEvent(pet.voice_card, QEvent(QEvent.Type.WindowDeactivate))
        self.session.cancel_input.assert_not_called()
        self.session.set_muted.assert_not_called()
        self.session.close.assert_not_called()
        pet._voice_event(generation, {"type": "error", "message": "Key 不可用；没有重试。"})
        pet._voice_event(generation, {"type": "closed"})
        self.assertIn("Key 不可用", pet.voice_message)
        self.assertFalse(pet._voice_started)
        pet.start_voice()
        pet.voice_card.close()
        self.session.close.assert_called_once()
        self.assertFalse(pet._voice_started)

    def test_voice_proposal_uses_the_existing_local_confirmation_card(self):
        pet = self.pet
        pet._voice_event(pet._voice_generation, {"type": "proposal", "action": {
            "kind": "toggle", "flag": "image", "label": "画图", "enabled": False}})
        self.assertTrue(pet.proposal_card.isVisible())
        pet.client.toggle.assert_not_called()
        self.assertTrue(pet._attention())

    def test_idle_and_proposal_finish_a_voice_turn_without_a_spoken_reply(self):
        pet = self.pet
        pet.start_voice()
        generation = pet._voice_generation
        for event in ({"type": "state", "state": "idle"}, {"type": "proposal", "action": {
                "kind": "toggle", "flag": "image", "label": "画图", "enabled": False}}):
            with self.subTest(event=event["type"]):
                pet._voice_event(generation, {"type": "state", "state": "thinking"})
                self.assertTrue(pet.brain.chat_pending)
                self.assertEqual(pet.brain.bubble.kind, "think")
                pet._voice_event(generation, event)
                self.assertFalse(pet.brain.chat_pending)
                self.assertIsNone(pet.brain.bubble)
                self.assertEqual(pet.voice_state, "idle")

    def test_voice_and_text_are_mutually_exclusive_without_losing_the_typed_draft(self):
        pet = self.pet
        pet.available = True
        pet.show_chat()
        pet.chat_bar.entry.setText("留在这里的草稿")
        pet.start_voice()
        self.assertFalse(pet.chat_bar.send_button.isEnabled())
        pet.chat_bar.send()
        pet.send_chat("这句也不能绕过入口")
        pet.client.send.assert_not_called()
        self.assertEqual(pet.chat_bar.entry.text(), "留在这里的草稿")
        pet.end_voice()
        self.assertTrue(pet.chat_bar.send_button.isEnabled())
        pet.chat_busy = True
        pet.brain.chat_sent(pet.now())
        pet.start_voice()
        self.assertFalse(pet._voice_started)
        pet.show_voice()
        pet.voice_card.close()
        self.assertTrue(pet.brain.chat_pending)
        self.assertEqual(pet.brain.bubble.kind, "think")

