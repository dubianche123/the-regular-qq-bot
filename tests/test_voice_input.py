"""Offline-only tests: synthetic WAVs, fake DNS/HTTP and mocked transcription."""
import asyncio
import io
import math
import wave
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from voice_input import (
    OpenAIWavTranscriber, QQPublicResolver, VoiceAttachment, VoiceProcessor,
    download_qq_voice, validate_qq_media_url, validate_wav,
)


def wav(seconds=0.1, rate=16000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return stream.getvalue()


class URLTests(unittest.TestCase):
    def test_only_exact_https_qq_hosts(self):
        self.assertEqual(validate_qq_media_url("//qqbot.ugcimg.cn/a"),
                         ("https://qqbot.ugcimg.cn/a", "qqbot.ugcimg.cn"))
        self.assertEqual(validate_qq_media_url("https://multimedia.nt.qq.com.cn:443/a")[1],
                         "multimedia.nt.qq.com.cn")
        for value in ("http://qqbot.ugcimg.cn/a", "https://qqbot.ugcimg.cn.evil/a",
                      "https://evil.qqbot.ugcimg.cn/a", "https://qqbot.ugcimg.cn@evil/a",
                      "https://user@qqbot.ugcimg.cn/a", "https://127.0.0.1/a",
                      "https://qqbot.ugcimg.cn:444/a", "https://qqbot.ugcimg.cn/a#secret",
                      " https://qqbot.ugcimg.cn/a", "https://qqbot.ugcimg.cn/\na", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_qq_media_url(value)

    def test_wav_and_silk_and_limits(self):
        data = wav()
        validate_wav(data, max_bytes=100000, max_seconds=1)
        for value, reason in ((b"#!SILK_V3...", "unsupported_format"),
                              (data[:-4], "invalid_wav")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                validate_wav(value, max_bytes=100000, max_seconds=1)
        with self.assertRaisesRegex(ValueError, "too_large"):
            validate_wav(data, max_bytes=10, max_seconds=1)
        with self.assertRaisesRegex(ValueError, "too_long"):
            validate_wav(data, max_bytes=100000, max_seconds=0.01)

    def test_invalid_limits_fail_closed(self):
        for values in ({"max_bytes": 0}, {"max_bytes": 100_000_000},
                       {"max_seconds": math.nan}, {"download_timeout": math.inf},
                       {"dedup_capacity": 0}, {"dedup_capacity": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                VoiceProcessor(None, **values)


class FakeResolver:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def resolve(self, host, port=0, family=0):
        self.calls += 1
        return self.rows

    async def close(self):
        pass


class DNSAndDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_filters_private_and_fake_addresses(self):
        r = QQPublicResolver(FakeResolver([{"host": "127.0.0.1"}, {"host": "10.0.0.1"},
                                           {"host": "198.18.0.1"}, {"host": "8.8.8.8"}]))
        self.assertEqual(await r.resolve("qqbot.ugcimg.cn", 443), [{"host": "8.8.8.8"}])
        with self.assertRaises(OSError):
            await r.resolve("evil.invalid", 443)
        r = QQPublicResolver(FakeResolver([{"host": "127.0.0.1"}]))
        with self.assertRaises(OSError):
            await r.resolve("qqbot.ugcimg.cn", 443)

    async def test_no_redirects_bounded_stream_and_scoped_auth(self):
        class Content:
            def __init__(self, chunks): self.chunks = chunks
            async def iter_chunked(self, _n):
                for c in self.chunks: yield c

        class Response:
            def __init__(self, status=200, headers=None, chunks=()):
                self.status, self.headers, self.content = status, headers or {}, Content(chunks)
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass

        calls = []
        current = {"response": Response(chunks=(b"a", b"b"))}
        class Session:
            def __init__(self, **kwargs):
                self.connector = kwargs["connector"]
                self.options = kwargs
            async def __aenter__(self): return self
            async def __aexit__(self, *_): await self.connector.close()
            def get(self, url, **kwargs):
                calls.append((url, kwargs, self.options))
                return current["response"]

        args = {"max_bytes": 2, "timeout": 1, "session_factory": Session,
                "resolver_factory": lambda: QQPublicResolver(FakeResolver([]))}
        self.assertEqual(await download_qq_voice("https://qqbot.ugcimg.cn/a", **args), b"ab")
        self.assertFalse(calls[-1][1]["allow_redirects"])
        self.assertFalse(calls[-1][2]["trust_env"])
        self.assertFalse(calls[-1][2]["auto_decompress"])
        current["response"] = Response(status=302, headers={"Location": "https://evil.invalid"})
        with self.assertRaises(ValueError):
            await download_qq_voice("https://qqbot.ugcimg.cn/a", **args)
        self.assertEqual(len(calls), 2)  # no follow-up request
        current["response"] = Response(headers={"Content-Length": "3"})
        with self.assertRaisesRegex(ValueError, "too_large"):
            await download_qq_voice("https://qqbot.ugcimg.cn/a", **args)
        current["response"] = Response(chunks=(b"aa", b"b"))
        with self.assertRaisesRegex(ValueError, "too_large"):
            await download_qq_voice("https://qqbot.ugcimg.cn/a", **args)
        current["response"] = Response(chunks=(b"a",))
        with self.assertRaisesRegex(ValueError, "invalid_auth_target"):
            await download_qq_voice("https://qqbot.ugcimg.cn/a", qq_authorization="secret", **args)
        await download_qq_voice("https://multimedia.nt.qq.com.cn/a", qq_authorization="secret", **args)
        self.assertEqual(calls[-1][1]["headers"], {"Authorization": "secret"})
        await download_qq_voice("https://multimedia.nt.qq.com.cn/a", **args)
        self.assertEqual(calls[-1][1]["headers"], {})
        before = len(calls)
        with self.assertRaisesRegex(ValueError, "invalid_url"):
            await download_qq_voice("https://multimedia.nt.qq.com.cn.evil.invalid/a",
                                    qq_authorization="secret", **args)
        self.assertEqual(len(calls), before)

    async def test_global_insecure_connector_patch_blocks_download(self):
        original = aiohttp.TCPConnector.__init__
        def force_insecure(instance, *args, **kwargs):
            kwargs["ssl"] = False
            original(instance, *args, **kwargs)
        session = MagicMock()
        with patch.object(aiohttp.TCPConnector, "__init__", force_insecure):
            with self.assertRaisesRegex(ValueError, "tls_verification_required"):
                await download_qq_voice("https://multimedia.nt.qq.com.cn/a", max_bytes=2,
                                        timeout=1, qq_authorization="secret",
                                        session_factory=session,
                                        resolver_factory=lambda: QQPublicResolver(FakeResolver([])))
        session.assert_not_called()


class TranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def test_exactly_one_client_call_without_retry(self):
        call = AsyncMock(return_value=SimpleNamespace(text="recognized"))
        configured = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=call)))
        options = MagicMock(return_value=configured)
        client = SimpleNamespace(base_url="https://api.openai.com/v1/", with_options=options)
        data = wav()
        result = await OpenAIWavTranscriber(client)(data)
        self.assertEqual(result, "recognized")
        options.assert_called_once_with(max_retries=0)
        call.assert_awaited_once()
        self.assertEqual(call.await_args.kwargs["model"], "gpt-transcribe")
        self.assertEqual(call.await_args.kwargs["file"], ("voice.wav", data, "audio/wav"))
        with self.assertRaisesRegex(ValueError, "unsupported_format"):
            await OpenAIWavTranscriber(client)(b"#!SILK_V3")
        call.assert_awaited_once()  # invalid audio never leaves the machine

    def test_untrusted_endpoint_rejected(self):
        for u in ("http://api.openai.com/v1", "https://api.openai.com.evil/v1",
                  "https://user@api.openai.com/v1"):
            with self.subTest(url=u), self.assertRaises(ValueError):
                OpenAIWavTranscriber(SimpleNamespace(base_url=u))


class ProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transcribe = AsyncMock(return_value="recognized private content")
        self.download = AsyncMock(return_value=wav())
        self.p = VoiceProcessor(self.transcribe, downloader=self.download)

    async def asyncTearDown(self):
        await self.p.close()

    async def test_reference_wins_without_network_or_transcription(self):
        a = VoiceAttachment(reference_text="  private transcript  ", wav_url="https://evil.invalid/x")
        r = await self.p.process(a, event_key="chat:event")
        self.assertEqual((r.status, r.transcript, r.source), ("ok", "private transcript", "qq_reference"))
        self.assertNotIn("private transcript", repr(r))
        self.assertNotIn("private transcript", repr(a))
        self.download.assert_not_awaited()
        self.transcribe.assert_not_awaited()

    async def test_invalid_empty_and_silk_never_transcribe(self):
        for i, attachment in enumerate((VoiceAttachment(),
                VoiceAttachment(audio_bytes=b"#!SILK_V3..."),
                VoiceAttachment(audio_bytes=b""), VoiceAttachment(wav_url="https://evil.invalid/"))):
            r = await self.p.process(attachment, event_key=f"event-{i}")
            self.assertNotEqual(r.status, "ok")
        self.download.assert_not_awaited()
        self.transcribe.assert_not_awaited()

    async def test_event_and_identical_audio_dedup(self):
        a = VoiceAttachment(wav_url="https://qqbot.ugcimg.cn/example")
        same_event = await asyncio.gather(*(self.p.process(a, event_key="same") for _ in range(12)))
        self.assertTrue(all(r.status == "ok" for r in same_event))
        self.download.assert_awaited_once()
        self.transcribe.assert_awaited_once()
        other_event = await self.p.process(VoiceAttachment(audio_bytes=wav()), event_key="another")
        self.assertEqual(other_event.status, "ok")
        self.transcribe.assert_awaited_once()  # same content hash, different event

    async def test_cancellation_of_one_waiter_does_not_duplicate_call(self):
        gate = asyncio.Event()
        async def slow(_):
            await gate.wait()
            return "result"
        self.p._transcriber = AsyncMock(side_effect=slow)
        a = VoiceAttachment(audio_bytes=wav())
        first = asyncio.create_task(self.p.process(a, event_key="same"))
        second = asyncio.create_task(self.p.process(a, event_key="same"))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        gate.set()
        self.assertEqual((await second).status, "ok")
        self.p._transcriber.assert_awaited_once()

    async def test_exceptions_are_not_leaked_and_cached(self):
        self.download.side_effect = RuntimeError("PRIVATE_URL_OR_TOKEN")
        a = VoiceAttachment(wav_url="https://qqbot.ugcimg.cn/secret")
        r = await self.p.process(a, event_key="same")
        self.assertEqual(r.status, "download_failed")
        self.assertNotIn("PRIVATE", repr(r))
        await self.p.process(a, event_key="same")
        self.download.assert_awaited_once()
        self.transcribe.assert_not_awaited()

    async def test_transcription_exception_is_sanitized_and_not_retried(self):
        self.transcribe.side_effect = RuntimeError("PRIVATE_TRANSCRIPT_OR_CREDENTIAL")
        a = VoiceAttachment(audio_bytes=wav())
        r = await self.p.process(a, event_key="event")
        self.assertEqual(r.status, "transcription_failed")
        self.assertNotIn("PRIVATE", repr(r))
        await self.p.process(a, event_key="event")
        self.transcribe.assert_awaited_once()

    async def test_shared_cache_expires_without_retaining_audio(self):
        ticks = [10.0]
        p = VoiceProcessor(self.transcribe, clock=lambda: ticks[0], dedup_ttl=2)
        try:
            raw = wav()
            result = await p.process(VoiceAttachment(audio_bytes=raw), event_key="one")
            self.assertEqual(result.status, "ok")
            await asyncio.sleep(0)  # allow completion callbacks to set expiry
            result = await p.process(VoiceAttachment(audio_bytes=raw), event_key="two")
            self.assertEqual(result.status, "ok")
            self.transcribe.assert_awaited_once()
            await asyncio.sleep(0)
            ticks[0] += 3
            result = await p.process(VoiceAttachment(audio_bytes=raw), event_key="three")
            self.assertEqual(result.status, "ok")
            self.assertEqual(self.transcribe.await_count, 2)
        finally:
            await p.close()


if __name__ == "__main__":
    unittest.main()
