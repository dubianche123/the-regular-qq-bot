"""Offline check of the outgoing Realtime voice instructions."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from realtime_synth import RealtimeSilkSynth


def test_expression_uses_same_one_request_and_preserves_reply():
    text = '刚醒，脑子还在开机中。'
    sent = []

    class Socket:
        async def send_json(self, item):
            sent.append(item)

        def __aiter__(self):
            events = [
                {'type': 'session.updated'},
                {'type': 'response.output_audio.delta',
                 'delta': base64.b64encode(b'\0' * 4800).decode()},
                {'type': 'response.output_audio_transcript.delta', 'delta': text},
                {'type': 'response.done', 'response': {'status': 'completed', 'output': []}},
            ]

            async def stream():
                for event in events:
                    yield SimpleNamespace(type=1, data=json.dumps(event))

            return stream()

    @asynccontextmanager
    async def connector(url, key):
        yield Socket()

    async def run():
        synth = RealtimeSilkSynth('test-key', None, '/node', '/entry', '/encoder', connector)
        state = {'requested': False, 'reservation_day': None, 'reason': None}
        result = await synth._generate(text, state)
        assert result == b'\0' * 4800
        assert state['reason'] == 'safe_completed'

    asyncio.run(run())
    assert [item['type'] for item in sent] == ['session.update', 'response.create']
    instructions = sent[1]['response']['instructions']
    assert '只用来调语调' in instructions
    assert '只逐字朗读 spoken_reply，不增减文字' in instructions
    message = sent[1]['response']['input'][0]
    assert message['role'] == 'user'
    assert json.loads(message['content'][0]['text']) == {
        'tone': '', 'spoken_reply': text,
    }
