import asyncio, base64, json, tempfile, unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from realtime_synth import RealtimeSilkSynth, URL
from voice_outbound import ProviderRefused, TranscriptMismatch, SynthesisTimeout, ProviderUncertain

class Ticket:
    def __init__(self): self.status='reserved'
    def commit(self): assert self.status == 'reserved'; self.status='committed'
    def release(self): assert self.status == 'reserved'; self.status='released'
class Budget:
    def __init__(self): self.ticket = Ticket(); self.args = None
    def reserve(self, *args): self.args=args; return self.ticket
class Socket:
    def __init__(self, events): self.events=list(events); self.sent=[]
    async def send_json(self, item): self.sent.append(item)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.events: raise StopAsyncIteration
        x=self.events.pop(0)
        if x == 'wait': await asyncio.Event().wait()
        return SimpleNamespace(type=1, data=json.dumps(x))
def ev(kind, **kwargs): return {'type':kind, **kwargs}
def events(text='你好'):
    return [ev('session.updated'), ev('response.output_audio.delta', delta=base64.b64encode(b'\0' * 4800).decode()),
            ev('response.output_audio_transcript.delta', delta=text), ev('response.done', response={'status':'completed', 'output':[]})]
class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.budget=Budget(); self.socket=Socket(events()); self.connects=[]
        @asynccontextmanager
        async def connector(url, key): self.connects.append((url, key)); yield self.socket
        self.synth=RealtimeSilkSynth('SECRET_API_KEY', self.budget, '/node', '/entry', '/encoder', connector)
        async def fake_encode(*args, **kwargs): Path(args[3]).write_bytes(b'fake-silk')
        self.encoder=patch('realtime_synth._run', side_effect=fake_encode); self.encoder.start(); self.addCleanup(self.encoder.stop)
    async def test_success_one_request_and_no_cache(self):
        output=await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(output.read_bytes(), b'fake-silk'); self.assertEqual(self.budget.ticket.status, 'committed')
        self.assertEqual(self.budget.args, (120, 512)); self.assertEqual(self.connects, [(URL,'SECRET_API_KEY')])
        self.assertEqual([x['type'] for x in self.socket.sent], ['session.update', 'response.create'])
        self.assertEqual(self.socket.sent[0]['session']['audio']['output']['voice'], 'marin')
        self.assertNotIn('SECRET_API_KEY', repr(self.synth))
    async def test_disclosure_in_exact_transcript_is_required(self):
        self.socket.events = events('AI 合成语音。你好')
        await self.synth.synthesize('AI 合成语音。你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed')

    async def test_equivalent_punctuation_and_spacing_can_succeed(self):
        self.socket.events = events('AI  合成语音.你好!')
        await self.synth.synthesize('AI 合成语音。你好！', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed')

    async def test_completed_spoken_refusal_is_never_original_text_fallback(self):
        self.socket.events = events('抱歉，我不能朗读这段内容。')
        with self.assertRaises(TranscriptMismatch):
            await self.synth.synthesize('AI 合成语音。你好', Path(self.tmp.name))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_punctuation_with_numeric_semantics_not_deleted(self):
        self.socket.events = events('AI 合成语音。314')
        with self.assertRaises(TranscriptMismatch):
            await self.synth.synthesize('AI 合成语音。3.14', Path(self.tmp.name))

    async def test_fullwidth_punctuation_in_number_is_not_equivalent(self):
        self.socket.events = events('AI 合成语音。3。14')
        with self.assertRaises(TranscriptMismatch):
            await self.synth.synthesize('AI 合成语音。3.14', Path(self.tmp.name))

    async def test_omitted_disclosure_is_mismatch(self):
        with self.assertRaises(TranscriptMismatch):
            await self.synth.synthesize('AI 合成语音。你好', Path(self.tmp.name))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_transcript_mismatch_never_encodes(self):
        with self.assertRaises(TranscriptMismatch): await self.synth.synthesize('不同', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed'); self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
    async def test_refusal_event_never_encodes(self):
        self.socket.events=[ev('session.updated'),ev('response.refusal.delta')]
        with self.assertRaises(ProviderRefused): await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed'); self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
    async def test_refusal_content_filter(self):
        self.socket.events=[ev('session.updated'),ev('response.done', response={'status':'failed', 'status_details':{'reason':'content_filter'}})]
        with self.assertRaises(ProviderRefused): await self.synth.synthesize('你好', Path(self.tmp.name))
    async def test_timeout_after_request_counts_budget(self):
        self.synth.timeout=0.01; self.socket.events=[ev('session.updated'),'wait']
        with self.assertRaises(ProviderUncertain): await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed')
    async def test_spoken_refusal_then_disconnect_is_uncertain(self):
        self.socket.events=[ev('session.updated'),
                            ev('response.output_audio_transcript.delta', delta='抱歉，不能朗读')]
        with self.assertRaises(ProviderUncertain):
            await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_spoken_refusal_then_timeout_is_uncertain(self):
        self.synth.timeout=0.01
        self.socket.events=[ev('session.updated'),
                            ev('response.output_audio_transcript.delta', delta='抱歉，不能朗读'), 'wait']
        with self.assertRaises(ProviderUncertain):
            await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'committed')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_timeout_before_request_releases_budget(self):
        self.synth.timeout=0.01; self.socket.events=['wait']
        with self.assertRaises(SynthesisTimeout): await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.budget.ticket.status, 'released')
    async def test_truncated_audio_rejected(self):
        self.socket.events=events(); self.socket.events[1]['delta']=base64.b64encode(b'12').decode()
        with self.assertRaises(ProviderUncertain): await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
    async def test_midnight_before_generation_does_not_call_provider(self):
        self.budget.ticket.day = '1900-01-01'
        with self.assertRaises(RuntimeError):
            await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual([x['type'] for x in self.socket.sent], ['session.update'])
        self.assertEqual(self.budget.ticket.status, 'released')

    async def test_no_budget_never_connects(self):
        self.budget.reserve=lambda *args: None
        with self.assertRaises(RuntimeError): await self.synth.synthesize('你好', Path(self.tmp.name))
        self.assertEqual(self.connects, [])
if __name__ == '__main__': unittest.main()
