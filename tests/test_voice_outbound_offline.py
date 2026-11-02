import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from voice_outbound import Delivery, Settings, VoiceOutbound, ProviderRefused, TranscriptMismatch, SynthesisTimeout, ProviderUncertain


class Ticket:
    def __init__(self): self.state = 'reserved'
    def commit(self):
        assert self.state == 'reserved'
        self.state = 'committed'
    def release(self):
        assert self.state == 'reserved'
        self.state = 'released'


class Quota:
    def __init__(self): self.tickets = []
    def reserve(self):
        ticket = Ticket(); self.tickets.append(ticket); return ticket


class Synth:
    def __init__(self): self.paths = []; self.texts = []
    async def synthesize(self, text, directory):
        self.paths.append(directory)
        self.texts.append(text)
        out = directory / 'voice.silk'
        out.write_bytes(b'synthetic fake audio')
        return out


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.synth, self.quota = Synth(), Quota()
        self.transport = type('Transport', (), {})()
        self.transport.upload = AsyncMock(return_value='opaque')
        self.transport.send = AsyncMock(return_value=Delivery.SENT)
        self.bot = VoiceOutbound(self.synth, self.transport, self.quota,
                                 Settings(enabled=True), temp_root=self.temp.name)

    def clean(self):
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    async def test_success_and_selection(self):
        self.assertEqual((await self.bot.attempt('你好')).status, 'not_selected')
        result = await self.bot.attempt('你好', explicit=True)
        self.assertEqual(result.status, 'sent')
        self.assertFalse(result.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'committed')
        self.transport.upload.assert_awaited_once()
        self.transport.send.assert_awaited_once_with('opaque')
        self.clean()

    async def test_spoken_disclosure_is_passed_to_synth(self):
        result = await self.bot.attempt('你好', explicit=True, spoken_text='AI 合成语音。你好')
        self.assertEqual(result.status, 'sent')
        self.assertEqual(self.synth.texts, ['AI 合成语音。你好'])
        self.clean()

    async def test_policy_and_invalid(self):
        for text in ['', 'a' * 121]:
            self.assertEqual((await self.bot.attempt(text, explicit=True)).status, 'invalid_text')
        self.assertEqual(self.quota.tickets, [])
        self.assertEqual((await VoiceOutbound(self.synth, self.transport, self.quota,
            Settings(enabled=True, auto_probability=1), random_value=lambda: 0.5,
            temp_root=self.temp.name).attempt('好')).status, 'sent')

    async def test_pre_send_failure_allows_fallback(self):
        self.transport.upload.side_effect = RuntimeError('do not log secret')
        r = await self.bot.attempt('test', explicit=True)
        self.assertTrue(r.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.transport.send.assert_not_awaited()
        self.clean()

    async def test_send_definite_failure_releases_quota(self):
        self.transport.send.return_value = Delivery.FAILED
        r = await self.bot.attempt('test', explicit=True)
        self.assertEqual(r.status, 'send_failed')
        self.assertTrue(r.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.clean()

    async def test_send_exception_is_unknown_no_fallback(self):
        self.transport.send.side_effect = TimeoutError()
        r = await self.bot.attempt('test', explicit=True)
        self.assertEqual(r.status, 'delivery_unknown')
        self.assertFalse(r.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'committed')
        self.transport.send.assert_awaited_once()
        self.clean()

    async def test_cancel_during_send_counts_as_unknown(self):
        started = asyncio.Event()
        async def send(_):
            started.set()
            await asyncio.Event().wait()
        self.transport.send.side_effect = send
        task = asyncio.create_task(self.bot.attempt('test', explicit=True))
        await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.quota.tickets[0].state, 'committed')
        self.clean()

    async def test_cancel_before_send_releases(self):
        started = asyncio.Event()
        async def upload(_):
            started.set(); await asyncio.Event().wait()
        self.transport.upload.side_effect = upload
        task = asyncio.create_task(self.bot.attempt('test', explicit=True))
        await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.transport.send.assert_not_awaited()
        self.clean()

    async def test_concurrency(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def upload(_):
            started.set(); await release.wait(); return 'opaque'
        self.transport.upload.side_effect = upload
        task = asyncio.create_task(self.bot.attempt('a', explicit=True))
        await started.wait()
        self.assertEqual((await self.bot.attempt('b', explicit=True)).status, 'busy')
        release.set(); await task
        self.assertEqual(len(self.quota.tickets), 1)
        self.clean()

    async def test_send_timeout_is_unknown(self):
        async def hanging(_): await asyncio.Event().wait()
        self.transport.send.side_effect = hanging
        self.bot.settings = Settings(enabled=True, send_timeout=0.01)
        r = await self.bot.attempt('test', explicit=True)
        self.assertEqual(r.status, 'delivery_unknown')
        self.assertFalse(r.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'committed')
        self.clean()

    async def test_upload_timeout_is_safe_to_fallback(self):
        async def hanging(_): await asyncio.Event().wait()
        self.transport.upload.side_effect = hanging
        self.bot.settings = Settings(enabled=True, upload_timeout=0.01)
        r = await self.bot.attempt('test', explicit=True)
        self.assertEqual(r.status, 'pre_send_failed')
        self.assertTrue(r.text_fallback_allowed)
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.transport.send.assert_not_awaited()
        self.clean()

    async def test_external_synthesis_path_rejected(self):
        outside = Path(self.temp.name) / 'outside.silk'
        outside.write_bytes(b'a')
        self.synth.synthesize = AsyncMock(return_value=outside)
        result = await self.bot.attempt('test', explicit=True)
        self.assertEqual(result.status, 'pre_send_failed')
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.transport.upload.assert_not_awaited()
        outside.unlink()
        self.clean()

    async def test_provider_outcomes_do_not_bypass_refusal(self):
        for error, status, fallback in [(ProviderRefused, 'provider_refused', False),
                                         (TranscriptMismatch, 'transcript_mismatch', False),
                                         (ProviderUncertain, 'provider_uncertain', False),
                                         (SynthesisTimeout, 'synthesis_timeout', True)]:
            with self.subTest(status=status):
                self.synth.synthesize = AsyncMock(side_effect=error())
                result = await self.bot.attempt('test', explicit=True)
                self.assertEqual((result.status, result.text_fallback_allowed), (status, fallback))
                self.assertEqual(self.quota.tickets[-1].state, 'released')
                self.clean()
        self.transport.upload.assert_not_awaited()
        self.transport.send.assert_not_awaited()

    async def test_midnight_before_send_releases_and_does_not_send(self):
        def reserve():
            ticket = Ticket(); ticket.day = '1900-01-01'; self.quota.tickets.append(ticket); return ticket
        self.quota.reserve = reserve
        result = await self.bot.attempt('你好', explicit=True)
        self.assertEqual(result.status, 'pre_send_failed')
        self.assertEqual(self.quota.tickets[0].state, 'released')
        self.transport.send.assert_not_awaited()
        self.clean()

    async def test_quota_exhausted(self):
        self.quota.reserve = lambda: None
        result = await self.bot.attempt('a', explicit=True)
        self.assertTrue(result.text_fallback_allowed)
        self.transport.upload.assert_not_awaited()
        self.clean()

if __name__ == '__main__': unittest.main()
