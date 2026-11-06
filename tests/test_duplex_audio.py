"""Duplex device/reference tests; no real audio device, network or model."""
import importlib.util
import math
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from regular.desktop_voice import DuplexAudio, RATE, VoiceError

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
HAS_AEC = importlib.util.find_spec('pywebrtc_audio') is not None


class Clock:
    value = 0.

    def __call__(self):
        return self.value


class Stream:
    def __init__(self, clock, **settings):
        self.clock, self.settings = clock, settings
        self.started = self.closed = self.aborted = False

    @property
    def time(self):
        return self.clock.value

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def callback(self, near, *, status=False, delay=.2):
        out = bytearray(len(near))
        timing = SimpleNamespace(outputBufferDacTime=self.clock.value + .1,
                                 inputBufferAdcTime=self.clock.value + .1 - delay)
        self.settings['callback'](near, out, len(near) // 2, timing, status)
        return bytes(out)


class Device:
    def __init__(self, clock):
        self.clock, self.streams = clock, []

    def RawStream(self, **settings):
        stream = Stream(self.clock, **settings)
        self.streams.append(stream)
        return stream


class Processor:
    def __init__(self):
        self.frames, self.stream_delay_ms, self.fail = [], 0, False

    def process(self, near, far):
        self.frames.append((near.copy(), far.copy(), self.stream_delay_ms))
        if self.fail:
            raise RuntimeError('synthetic processing failure')
        return near + 1


@unittest.skipUnless(HAS_NUMPY, 'optional native audio dependency')
class DuplexAudioTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np, self.clock = np, Clock()
        self.device, self.processor = Device(self.clock), Processor()
        self.factory = Mock(return_value=self.processor)
        self.audio = DuplexAudio(self.device, processor_factory=self.factory, clock=self.clock)
        self.received, self.errors = [], []
        self.addCleanup(self.audio.close)

    def pcm(self, value=100, frames=240):
        return self.np.full(frames, value, dtype=self.np.int16).tobytes()

    def start(self):
        self.audio.start_conversation(self.received.append, self.errors.append)
        return self.device.streams[-1]

    def test_lazy_one_ten_ms_duplex_clock(self):
        self.assertFalse(self.device.streams)
        self.factory.assert_not_called()
        stream = self.start()
        self.assertTrue(stream.started)
        config = stream.settings
        self.assertEqual((config['samplerate'], config['channels'], config['dtype'], config['blocksize']),
                         (24000, 1, 'int16', 240))
        self.assertEqual(config['latency'], .1)
        self.assertTrue(self.factory.call_args.kwargs['echo_cancellation'])
        stream.callback(self.pcm())
        self.assertEqual(self.received, [self.pcm(101)])
        self.assertFalse(self.errors)

    def test_aec_reference_is_actual_output_and_delay_uses_adc_dac_clocks(self):
        stream = self.start()
        self.audio.begin_playback()
        reply = self.pcm(1200, 4800)
        self.audio.feed_audio(reply)
        self.assertEqual(stream.callback(self.pcm()), bytes(480))
        self.assertFalse(self.processor.frames[-1][1].any())
        self.clock.value = .12
        actual = stream.callback(self.pcm(), delay=.237)
        self.assertEqual(actual, reply[:480])
        near, far, delay = self.processor.frames[-1]
        self.assertEqual(far.tobytes(), actual)
        self.assertEqual(len(near), len(far))
        self.assertEqual(delay, 237)

    def test_mute_blocks_pcm_but_keeps_aec_reference_processing(self):
        stream = self.start()
        self.audio.set_muted(True)
        stream.callback(self.pcm())
        self.assertEqual(len(self.processor.frames), 1)
        self.assertFalse(self.received)
        self.audio.set_muted(False)
        stream.callback(self.pcm())
        self.assertEqual(self.received, [self.pcm(101)])

    def test_interrupt_discards_reply_without_stopping_capture_or_resetting_aec(self):
        stream = self.start()
        self.audio.begin_playback()
        self.audio.feed_audio(self.pcm(1800, 1000))
        self.audio.end_playback()
        stream.callback(self.pcm())
        self.clock.value = .115
        self.assertEqual(self.audio.abort_playback(), 10)
        self.assertFalse(stream.aborted or stream.closed)
        self.assertEqual(self.audio._queued_bytes, 0)
        self.assertEqual(stream.callback(self.pcm()), bytes(480))
        self.assertEqual(len(self.received), 2)
        self.assertEqual(len(self.processor.frames), 2)
        self.audio.begin_playback()
        self.assertEqual(len(self.device.streams), 1)

    def test_close_is_terminal_and_late_callbacks_cannot_upload(self):
        stream = self.start()
        stream.callback(self.pcm())
        self.audio.close()
        self.assertTrue(stream.aborted and stream.closed)
        self.assertEqual(stream.callback(self.pcm()), bytes(480))
        self.assertEqual(len(self.received), 1)
        with self.assertRaises(VoiceError):
            self.start()
        self.assertEqual(len(self.device.streams), 1)

    def test_processing_failure_does_not_forward_raw_mic(self):
        stream = self.start()
        self.processor.fail = True
        stream.callback(self.pcm())
        stream.callback(self.pcm())
        self.assertFalse(self.received)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('回声处理', self.errors[0])
        self.assertEqual(len(self.processor.frames), 1)

    def test_input_overflow_fails_once_without_forwarding_damaged_audio(self):
        stream = self.start()
        stream.callback(self.pcm(), status=SimpleNamespace(input_overflow=True))
        stream.callback(self.pcm())
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.received or self.processor.frames)

    def test_cancelled_open_closes_constructed_device_without_starting_it(self):
        entered, release = threading.Event(), threading.Event()
        created, errors = [], []
        def construct(**config):
            entered.set()
            self.assertTrue(release.wait(3))
            stream = Stream(self.clock, **config)
            created.append(stream)
            return stream
        self.device.RawStream = construct
        def begin():
            try:
                self.start()
            except VoiceError as error:
                errors.append(error)
        opener = threading.Thread(target=begin)
        opener.start()
        self.assertTrue(entered.wait(3))
        closer = threading.Thread(target=self.audio.close)
        closer.start()
        self.assertTrue(self.audio._terminal.wait(1))
        release.set()
        opener.join(3)
        closer.join(3)
        self.assertFalse(opener.is_alive() or closer.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertTrue(created[0].closed)
        self.assertFalse(created[0].started)
        self.assertIsNone(self.audio._duplex)


@unittest.skipUnless(HAS_NUMPY and HAS_AEC, 'optional native WebRTC AEC wheel')
class NativeAecTest(unittest.TestCase):
    def test_real_24k_aec_reduces_delayed_echo_and_preserves_double_talk(self):
        import numpy as np
        from pywebrtc_audio import AudioProcessor
        count = RATE * 12
        rng = np.random.default_rng(24)
        t = np.arange(count) / RATE
        far = np.convolve(rng.normal(0, 1, count), np.ones(9) / 9, mode='same') * 4500
        far += 1800 * np.sin(2 * np.pi * 173 * t) + 900 * np.sin(2 * np.pi * 331 * t)
        delay = RATE * 120 // 1000
        echo = np.concatenate([np.zeros(delay), far[:-delay]]) * .55
        speech = 2100 * np.sin(np.cumsum(2 * np.pi * (191 + 35 * np.sin(2 * np.pi * .7 * t)) / RATE))
        speech += 850 * np.sin(np.cumsum(2 * np.pi * (420 + 45 * np.sin(2 * np.pi * .3 * t)) / RATE))
        speech *= (t >= 4) & (t < 8)
        near = np.clip(echo + speech, -30000, 30000).astype(np.int16)
        far = far.astype(np.int16)
        processor = AudioProcessor(sample_rate=RATE, echo_cancellation=True,
                                   noise_suppression=True, auto_gain_control=False,
                                   ns_level=1, stream_delay_ms=120)
        clean = np.concatenate([processor.process(near[i:i + 240], far[i:i + 240])
                                for i in range(0, count, 240)])
        self.assertEqual(clean.dtype, np.dtype('int16'))
        self.assertEqual(len(clean), count)
        rms = lambda a: math.sqrt(float(np.mean(np.asarray(a, dtype=np.float64) ** 2)))
        for begin, end in ((2, 4), (10, 12)):
            a, b = begin * RATE, end * RATE
            erle = 20 * math.log10(rms(near[a:b]) / max(1, rms(clean[a:b])))
            self.assertGreater(erle, 12, f'echo reduction: {erle:.2f} dB')
        # The high-pass/AEC processing introduces a small group delay.
        a, b = RATE * 5, RATE * 7
        correlation = max(float(np.corrcoef(clean[a:b], speech[a - lag:b - lag])[0, 1])
                          for lag in range(-480, 481, 6))
        self.assertGreater(correlation, .5)
        self.assertGreater(rms(clean[a:b]) / rms(speech[a:b]), .35)


if __name__ == '__main__':
    unittest.main()
