"""原生 Decisions 传输：离线伪配置 + 伪 HTTP，不打网络。"""
import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import providers
from regular import decision_settings


class CountingBudget:
    def __init__(self, limit=5):
        self.used = 0
        self.limit = limit
    def try_consume(self):
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def fake_response(status=200, payload=None, raw=None):
    resp = SimpleNamespace(status_code=status)
    if raw is not None:
        def boom():
            raise ValueError("not json")
        resp.json = boom
    else:
        resp.json = lambda: payload
    return resp


class CallDecisionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        decision_settings.save_settings(
            self.dir, endpoint="https://api.openai.com/v1/decisions",
            model="gpt-6-luna", api_key="sk-fake-never-real")
        self.budget = CountingBudget()
        self.http = SimpleNamespace(post=AsyncMock(
            return_value=fake_response(payload={"answers": ["a", "b"]})))
        for target, name, value in (
                (config, "DATA_DIR", self.dir),
                (config, "DECISION_ENABLED", True),
                (providers, "_llm_http", self.http),
                (providers, "_initialized", True),
                (providers, "_bound_budget", lambda: self.budget),
                (providers, "_bound_decision_budget", lambda: self.budget)):
            ctx = patch.object(target, name, value)
            ctx.start(); self.addCleanup(ctx.stop)

    async def test_decision_never_spends_the_chat_budget(self):
        """每条群消息都分诊之后，判断和回复必须各记各的：刷屏不能把回复额度烧光。"""
        chat = CountingBudget()
        with patch.object(providers, "_bound_budget", lambda: chat):
            await providers.call_decision("some input", [{"q": 1}])
        self.assertEqual((self.budget.used, chat.used), (1, 0))

    async def test_exact_native_request_shape(self):
        got = await providers.call_decision("some input", [{"q": 1}])
        self.assertEqual(got, {"answers": ["a", "b"]})
        kw = self.http.post.await_args.kwargs
        self.assertEqual(self.http.post.await_args.args[0] if
                         self.http.post.await_args.args else
                         kw.get("url"), "https://api.openai.com/v1/decisions")
        self.assertEqual(kw["json"], {"model": "gpt-6-luna",
                                      "input": "some input",
                                      "questions": [{"q": 1}]})
        self.assertEqual(kw["headers"],
                         {"Authorization": "Bearer sk-fake-never-real"})
        for forbidden in ("max_tokens", "temperature", "stream", "messages"):
            self.assertNotIn(forbidden, kw["json"])
        self.assertEqual(self.budget.used, 1)

    async def test_native_answers_passthrough_untouched(self):
        payload = {"answers": [{"name": "x", "type": "refusal", "choice": None}]}
        self.http.post.return_value = fake_response(payload=payload)
        self.assertIs(await providers.call_decision("i", []), payload)

    async def test_disabled_sends_nothing_and_spends_nothing(self):
        with patch.object(config, "DECISION_ENABLED", False):
            self.assertIsNone(await providers.call_decision("i", []))
        self.http.post.assert_not_awaited()
        self.assertEqual(self.budget.used, 0)

    async def test_missing_and_corrupt_settings_send_nothing(self):
        os.remove(f"{self.dir}/decision_api.json")
        self.assertIsNone(await providers.call_decision("i", []))
        with open(f"{self.dir}/decision_api.json", "w") as fp:
            fp.write("{bad")
        self.assertIsNone(await providers.call_decision("i", []))
        self.http.post.assert_not_awaited()
        self.assertEqual(self.budget.used, 0)

    async def test_failures_consume_one_and_never_retry(self):
        for outcome in (fake_response(status=500),
                        RuntimeError("conn boom")):
            self.budget.used = 0
            self.http.post.reset_mock()
            self.http.post.side_effect = ([outcome] if isinstance(
                outcome, Exception) else None)
            if not isinstance(outcome, Exception):
                self.http.post.return_value = outcome
            self.assertIsNone(await providers.call_decision("i", []))
            self.assertEqual(self.budget.used, 1)
            self.assertEqual(self.http.post.await_count, 1)

    async def test_timeout_consumes_one(self):
        async def slow(*a, **k):
            await asyncio.sleep(5)
        self.http.post.side_effect = slow
        with patch.object(config, "DECISION_TIMEOUT_SECONDS", 0.01):
            self.assertIsNone(await providers.call_decision("i", []))
        self.assertEqual(self.budget.used, 1)
        self.assertEqual(self.http.post.await_count, 1)

    async def test_budget_exhausted_sends_nothing(self):
        self.budget.limit = 0
        self.assertIsNone(await providers.call_decision("i", []))
        self.http.post.assert_not_awaited()
        self.assertEqual(self.budget.used, 0)

    async def test_bad_payloads_are_none(self):
        for payload in ("text", [], {"answers": "nope"}, {"answers": None}, {}):
            self.budget.used = 0
            self.http.post.reset_mock()
            self.http.post.return_value = fake_response(payload=payload)
            self.assertIsNone(await providers.call_decision("i", []), payload)
            self.http.post.assert_awaited_once()
        self.budget.used = 0
        self.http.post.reset_mock()
        self.http.post.return_value = fake_response(raw="x")
        self.assertIsNone(await providers.call_decision("i", []))
        self.http.post.assert_awaited_once()

    async def test_cancellation_propagates(self):
        self.http.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await providers.call_decision("i", [])
        self.assertEqual(self.budget.used, 1)

    async def test_completion_meta_untouched(self):
        providers._completion_meta.set({"provider": "chat-trace", "model": "m"})
        await providers.call_decision("i", [])
        self.assertEqual(providers.completion_meta()["provider"], "chat-trace")
        self.http.post.return_value = fake_response(status=500)
        await providers.call_decision("i", [])
        self.assertEqual(providers.completion_meta()["provider"], "chat-trace")
        providers.reset_completion_meta()

    async def test_chat_reservation_still_one_attempt_same_kwargs(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(
                create=AsyncMock(return_value="ok"))))
        got = await providers._request_completion(
            client, model="m", messages=[], max_tokens=1, temperature=0.5,
            extra_body=None)
        self.assertEqual(got, "ok")
        self.assertEqual(self.budget.used, 1)
        kw = client.chat.completions.create.await_args.kwargs
        self.assertEqual(set(kw), {"model", "messages", "max_tokens",
                                   "temperature", "extra_body"})


if __name__ == "__main__":
    unittest.main()
