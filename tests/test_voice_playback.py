"""Network jitter versus the soundcard clock, without opening any audio device."""
import importlib.util
import struct
from types import SimpleNamespace
import unittest

from regular.desktop_voice import RATE, SAMPLE_BYTES, SoundDeviceAudio, NativeVoiceSession


class PlaybackDiagnosticsTest(unittest.TestCase):
    def test_a_playback_problem_is_logged_once_without_audio_or_transcript(self):
        session = NativeVoiceSession(None, "", lambda event: None)
        session._audio = SimpleNamespace(playback_stats=lambda: {
            "underflows": 2, "underruns": 1, "played_frames": 24000})
        session._playback_reported = False
        with self.assertLogs('regular.desktop_voice', level='WARNING') as logs:
            session._report_playback()
            session._report_playback()
        self.assertEqual(len(logs.output), 1)
        self.assertIn('device_underflows=2 network_underruns=1 played_frames=24000', logs.output[0])


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class Output:
    def __init__(self, clock, **settings):
        self.clock, self.settings = clock, settings
        self.started = self.aborted = self.closed = False

    @property
    def time(self):
        return self.clock.value

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def render(self, frames, *, cffi_buffer=False, status=False):
        if cffi_buffer:
            from cffi import FFI
            ffi = FFI()
            memory = ffi.new('char[]', frames * SAMPLE_BYTES)
            target = ffi.buffer(memory, frames * SAMPLE_BYTES)
        else:
            target = bytearray(frames * SAMPLE_BYTES)
        timing = SimpleNamespace(outputBufferDacTime=self.clock.value + .1)
        self.settings['callback'](target, frames, timing, status)
        return bytes(target)


class Device:
    def __init__(self, clock):
        self.clock, self.output = clock, None

    def RawOutputStream(self, **settings):
        self.output = Output(self.clock, **settings)
        return self.output


def pcm(frames, sample=2500):
    return struct.pack('<h', sample) * frames


class VoicePlaybackTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.device = Device(self.clock)
        self.audio = SoundDeviceAudio(self.device, clock=self.clock)
        self.audio.begin_playback()
        self.stream = self.device.output
        self.addCleanup(self.audio.close)

    def test_start_has_a_short_time_lead_even_when_first_packet_is_large(self):
        first = pcm(RATE * 2 // 5)  # A single provider packet can contain 400 ms.
        self.audio.feed_audio(first)
        self.assertEqual(self.stream.render(480), bytes(960))
        self.clock.value = .119
        self.assertEqual(self.stream.render(480), bytes(960))
        self.assertEqual(self.audio._queued_bytes, len(first))
        self.clock.value = .12
        self.assertEqual(self.stream.render(480), first[:960])
        self.assertEqual(self.stream.settings['latency'], .1)
        self.assertEqual(self.stream.settings['samplerate'], 24000)
        self.assertEqual(self.stream.settings['dtype'], 'int16')
        self.assertEqual(self.stream.settings['channels'], 1)

    def test_short_completed_reply_flushes_without_waiting_and_uses_dac_time(self):
        tail = pcm(480)
        self.audio.feed_audio(tail)
        self.assertEqual(self.stream.render(480), bytes(960))
        self.audio.end_playback()
        self.assertEqual(self.stream.render(480), tail)
        self.assertEqual(self.audio.played_ms(), 0)  # queued to DAC, not heard yet
        self.clock.value = .105
        self.assertIn(self.audio.played_ms(), (4, 5))
        self.assertFalse(self.audio.playback_done())
        self.clock.value = .121
        self.assertTrue(self.audio.playback_done())
        self.assertEqual(self.audio.abort_playback(), 20)

    def test_variable_callback_sizes_preserve_split_pcm_samples_exactly(self):
        # Every sample is nonzero and distinguishable, so byte order and gaps matter.
        data = b''.join(struct.pack('<h', 300 + i % 20000) for i in range(8000))
        for start, stop in ((0, 2), (2, 42), (42, 482), (482, 4096), (4096, len(data))):
            self.audio.feed_audio(data[start:stop])
        self.audio.end_playback()
        actual = bytearray()
        for frames in (127, 509, 256, 1024, 17, 2048, 4096):
            actual.extend(self.stream.render(frames))
        self.assertEqual(actual[:len(data)], data)
        self.assertFalse(any(actual[len(data):]))

    @unittest.skipUnless(importlib.util.find_spec('cffi'), 'optional native audio dependency')
    def test_real_cffi_raw_buffer_has_the_same_sample_layout(self):
        data = struct.pack('<hhhh', -32768, -1, 1, 32767) * 120
        self.audio.feed_audio(data)
        self.audio.end_playback()
        self.assertEqual(self.stream.render(480, cffi_buffer=True), data)

    def test_bursty_provider_timing_has_no_silent_holes_inside_speech(self):
        # Synthetic schedule shaped like real traffic: first 400 ms arrives now,
        # second packet just after those 400 ms, then later packets arrive in bursts.
        packets = [(0., 9600), (.405, 9600), (.450, 9600), (.500, 9600), (.550, 9600), (.900, 2400)]
        expected_frames = sum(frames for _, frames in packets)
        pending = list(packets)
        actual = bytearray()
        sizes = (127, 256, 511, 240)
        step = 0
        while self.clock.value < 3:
            while pending and pending[0][0] <= self.clock.value:
                _, frames = pending.pop(0)
                self.audio.feed_audio(pcm(frames))
                if not pending:
                    self.audio.end_playback()
            frames = sizes[step % len(sizes)]
            actual.extend(self.stream.render(frames))
            self.clock.value += frames / RATE
            step += 1
        values = struct.unpack('<' + 'h' * (len(actual) // 2), actual)
        start = next(i for i, value in enumerate(values) if value)
        stop = len(values) - next(i for i, value in enumerate(reversed(values)) if value)
        self.assertGreaterEqual(start / RATE, .12)
        self.assertEqual(stop - start, expected_frames)
        self.assertTrue(all(value == 2500 for value in values[start:stop]))

    def test_starvation_keeps_partial_audio_until_a_new_lead_is_available(self):
        first = pcm(4320, 500)  # 180 ms
        self.audio.feed_audio(first)
        self.clock.value = .12
        self.assertEqual(self.stream.render(3840), first[:7680])  # 160 ms
        self.clock.value = .28
        self.assertEqual(self.stream.render(960), bytes(1920))
        self.assertEqual(self.audio._queued_bytes, 960)  # remaining 20 ms kept
        next_part = pcm(2880, 900)
        self.clock.value = .29
        self.audio.feed_audio(next_part)
        self.clock.value = .40
        self.assertEqual(self.stream.render(960), bytes(1920))
        self.clock.value = .42
        self.assertEqual(self.stream.render(960), first[7680:] + next_part[:960])

    def test_interrupt_during_prebuffer_discards_unheard_audio(self):
        self.audio.feed_audio(pcm(4000))
        self.assertEqual(self.stream.render(256), bytes(512))
        self.assertEqual(self.audio.abort_playback(), 0)
        self.assertEqual(self.audio._queued_bytes, 0)
        self.assertFalse(self.audio._segments)
        self.assertTrue(self.stream.aborted)
        self.assertTrue(self.stream.closed)

    def test_device_status_is_retained_even_while_waiting_for_first_audio(self):
        self.assertEqual(self.stream.render(256, status=True), bytes(512))
        self.assertIn('卡顿', self.audio._output_error)

    def test_statistics_count_device_and_network_gaps_without_audio_content(self):
        status = SimpleNamespace(output_underflow=True)
        self.stream.render(256, status=status)
        self.assertEqual(self.audio.playback_stats(), {'underflows': 1, 'underruns': 0, 'played_frames': 0})
        self.audio.feed_audio(pcm(2880))
        self.clock.value = .12
        self.stream.render(2880)
        self.clock.value = .24
        self.stream.render(480)
        self.stream.render(480)  # a continuing wait is one gap, not a new gap per callback
        self.clock.value = .35
        stats = self.audio.playback_stats()
        self.assertEqual(stats, {'underflows': 1, 'underruns': 1, 'played_frames': 2880})
        self.audio.abort_playback()
        self.assertEqual(self.audio.playback_stats(), stats)
        self.audio.begin_playback()
        self.assertEqual(self.audio.playback_stats(), {'underflows': 0, 'underruns': 0, 'played_frames': 0})


if __name__ == '__main__':
    unittest.main()
