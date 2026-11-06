"""Wire in-memory voice recognition to the existing daily budget.

Only a QQ reference or valid WAV can currently be recognized; unsupported
audio formats return an explicit failure. No audio is written to disk.
"""
from urllib.parse import urlsplit

from openai import AsyncOpenAI

from voice_input import OpenAIWavTranscriber, VoiceProcessor


class VoiceRuntime:
    def __init__(self, *, budget, mark_dirty, persist, api_key="", base_url="https://api.openai.com/v1",
                 model="gpt-transcribe", processor=None, transcription_enabled=lambda: True):
        self._client = None
        if processor is not None:
            self.processor = processor
            return
        transcribe = None
        try:
            url = urlsplit(base_url)
            official = (url.scheme == "https" and url.hostname == "api.openai.com"
                        and url.port in (None, 443) and url.username is None
                        and url.password is None and not url.query and not url.fragment)
        except ValueError:
            official = False
        if api_key and official:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
            actual = OpenAIWavTranscriber(client, model=model)
            self._client = client

            async def transcribe(data):
                if not transcription_enabled():
                    raise RuntimeError("voice_transcription_disabled")
                if not budget.try_consume():
                    raise RuntimeError("voice_budget_exhausted")
                mark_dirty()
                # Fail closed on persistence errors, before any billable call.
                persist()
                return await actual(data)
        self.processor = VoiceProcessor(transcribe, transcription_enabled=transcription_enabled)

    async def close(self):
        await self.processor.close()
        if self._client is not None:
            await self._client.close()
