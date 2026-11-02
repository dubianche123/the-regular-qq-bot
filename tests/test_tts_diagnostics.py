"""Fixed-only diagnostic labels; all transports and audio are synthetic."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from realtime_synth import RealtimeSilkSynth
from test_realtime_synth_offline import Budget, Socket, ev, events
from voice_outbound import ProviderRefused, ProviderUncertain, TranscriptMismatch


class Diagnostics(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, socket, expected_reason, expected_exception=None,
                       expected_text='AI 合成语音。PRIVATE_TEXT', timeout=1):
        budget = Budget()
        @asynccontextmanager
        async def connector(url, key):
            yield socket
        synth = RealtimeSilkSynth('PRIVATE_TOKEN', budget, '/node', '/entry', '/encoder',
                                  connector, timeout=timeout)
        async def fake_encode(*args, **kwargs):
            Path(args[3]).write_bytes(b'fake-silk')
        with tempfile.TemporaryDirectory() as tmp, patch('realtime_synth._run', side_effect=fake_encode):
            with self.assertLogs('qqbot', 'INFO') as logs:
                if expected_exception:
                    with self.assertRaises(expected_exception):
                        await synth.synthesize(expected_text, Path(tmp))
                    self.assertEqual(list(Path(tmp).iterdir()), [])
                else:
                    await synth.synthesize(expected_text, Path(tmp))
        self.assertEqual(logs.output, [f'INFO:qqbot:VOICE_TTS_DIAG {expected_reason}'])
        self.assertNotIn('PRIVATE_TEXT', repr(logs.output))
        self.assertNotIn('PRIVATE_TOKEN', repr(logs.output))
        return budget

    async def test_safe_completion(self):
        await self.run_case(Socket(events('AI 合成语音。PRIVATE_TEXT')), 'safe_completed')

    async def test_whitespace_difference_is_not_spoken(self):
        await self.run_case(Socket(events('AI合成语音。PRIVATE_TEXT')), 'safe_completed')

    async def test_punctuation_difference_is_not_spoken(self):
        await self.run_case(Socket(events('AI 合成语音 PRIVATE_TEXT')), 'safe_completed')

    async def test_character_mismatch_still_rejected(self):
        await self.run_case(Socket(events('DIFFERENT_PRIVATE_CONTENT')),
                            'transcript_characters_differ', TranscriptMismatch)

    async def test_missing_transcript(self):
        data = events(); data.pop(2)
        await self.run_case(Socket(data), 'transcript_missing', TranscriptMismatch)

    async def test_explicit_refusal(self):
        await self.run_case(Socket([ev('session.updated'), ev('response.refusal.delta')]),
                            'refusal', ProviderRefused)

    async def test_provider_error_has_no_error_details(self):
        await self.run_case(Socket([ev('session.updated'), ev('error',
            error={'code':'PRIVATE_TOKEN', 'message':'PRIVATE_TEXT'})]),
            'provider_error', ProviderUncertain)

    async def test_incomplete_stream(self):
        await self.run_case(Socket([ev('session.updated')]),
                            'stream_ended_before_done', ProviderUncertain)

    async def test_incomplete_status(self):
        await self.run_case(Socket([ev('session.updated'), ev('response.done',
            response={'status':'incomplete'})]), 'response_not_completed', ProviderUncertain)

    async def test_timeout_after_request(self):
        budget = await self.run_case(Socket([ev('session.updated'), 'wait']),
            'timeout_after_request', ProviderUncertain, timeout=0.01)
        self.assertEqual(budget.ticket.status, 'committed')

    async def test_connection_exception_does_not_log_message(self):
        class FailedSocket(Socket):
            async def __anext__(self):
                raise ConnectionResetError('PRIVATE_TOKEN PRIVATE_TEXT')
        budget = Budget()
        @asynccontextmanager
        async def connector(url, key):
            yield FailedSocket([])
        synth = RealtimeSilkSynth('PRIVATE_TOKEN', budget, '/node', '/entry', '/encoder', connector)
        with tempfile.TemporaryDirectory() as tmp, self.assertLogs('qqbot', 'INFO') as logs:
            with self.assertRaises(ConnectionResetError):
                await synth.synthesize('PRIVATE_TEXT', Path(tmp))
        self.assertEqual(logs.output, ['INFO:qqbot:VOICE_TTS_DIAG connection_error'])
        self.assertEqual(budget.ticket.status, 'released')

    async def test_connection_after_request_remains_uncertain(self):
        class FailedSocket(Socket):
            async def __anext__(self):
                if self.events:
                    return await super().__anext__()
                raise ConnectionResetError('PRIVATE_TOKEN PRIVATE_TEXT')
        await self.run_case(FailedSocket([ev('session.updated')]),
                            'connection_error', ProviderUncertain)

    async def test_broken_log_handler_does_not_change_success(self):
        budget = Budget()
        @asynccontextmanager
        async def connector(url, key):
            yield Socket(events('PRIVATE_TEXT'))
        synth = RealtimeSilkSynth('PRIVATE_TOKEN', budget, '/node', '/entry', '/encoder', connector)
        async def fake_encode(*args, **kwargs):
            Path(args[3]).write_bytes(b'fake-silk')
        with tempfile.TemporaryDirectory() as tmp, \
             patch('realtime_synth._run', side_effect=fake_encode), \
             patch('realtime_synth._LOG.info', side_effect=RuntimeError('logger failed')):
            result = await synth.synthesize('PRIVATE_TEXT', Path(tmp))
            self.assertTrue(result.is_file())
        self.assertEqual(budget.ticket.status, 'committed')


if __name__ == '__main__':
    unittest.main()
