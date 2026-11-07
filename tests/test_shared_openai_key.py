"""一把 OpenAI Key：动态绑定，不复制第二份，也不会送到别家接口。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import providers
from regular import capabilities, decision_settings


class Budget:
    def __init__(self, limit=5):
        self.used, self.limit = 0, limit

    def try_consume(self):
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def answer():
    return SimpleNamespace(status_code=200, json=lambda: {"answers": []})


class SharedOpenAIKeyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.env = self.root / ".env"
        self.set_key("sk-first")
        decision_settings.save_settings(
            self.data, endpoint="https://api.openai.com/v1/decisions",
            model="gpt-6-luna", credential="shared")
        self.http = SimpleNamespace(post=AsyncMock(return_value=answer()))
        self.budget = Budget()
        for target, name, value in (
                (config, "BASE_DIR", str(self.root)),
                (config, "DATA_DIR", str(self.data)),
                # This fixture models file-managed credentials. The offline
                # runner's intentionally blank key is not this instance's override.
                (config, "EXTERNAL_ENV_KEYS", frozenset()),
                (config, "DECISION_ENABLED", True),
                (providers, "_llm_http", self.http),
                (providers, "_initialized", True),
                (providers, "_bound_budget", lambda: self.budget),
                (providers, "_bound_decision_budget", lambda: self.budget)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    def set_key(self, key):
        self.env.write_text(
            "AI_PROVIDER=openai\nCHAT_PROVIDER=openai\n"
            "OPENAI_BASE_URL=https://api.openai.com/v1\n"
            f"OPENAI_API_KEY={key}\n", encoding="utf-8")

    def test_shared_settings_store_no_second_key(self):
        saved = json.loads((self.data / decision_settings.FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(saved, {"endpoint": "https://api.openai.com/v1/decisions",
                                 "model": "gpt-6-luna", "credential": "shared"})
        loaded = decision_settings.load_settings(self.data)
        self.assertEqual((loaded["credential"], loaded["api_key"]), ("shared", ""))
        self.assertTrue(decision_settings.public_settings(self.data, shared_ready=True)["key_configured"])
        self.assertFalse(decision_settings.public_settings(self.data, shared_ready=False)["key_configured"])

    def test_resolver_only_serves_the_same_service(self):
        self.assertEqual(capabilities.key_for_endpoint(config, "https://api.openai.com/v1"),
                         "sk-first")
        self.assertEqual(capabilities.key_for_endpoint(
            config, "https://api.openai.com/v1/decisions", suffix="/decisions"), "sk-first")
        self.assertEqual(capabilities.key_for_endpoint(config, "https://other.example/v1"), "")

    async def test_decision_request_follows_the_key_when_it_changes(self):
        await providers.call_decision("i", [])
        self.assertEqual(self.http.post.await_args.kwargs["headers"],
                         {"Authorization": "Bearer sk-first"})
        self.set_key("sk-second")
        self.http.post.reset_mock()
        await providers.call_decision("i", [])
        self.assertEqual(self.http.post.await_args.kwargs["headers"],
                         {"Authorization": "Bearer sk-second"})

    async def test_official_key_can_judge_before_a_separate_connection_is_saved(self):
        (self.data / decision_settings.FILENAME).unlink()
        await providers.call_decision("synthetic input", [])
        self.http.post.assert_awaited_once()
        args = self.http.post.await_args
        self.assertEqual(args.args[0], decision_settings.DEFAULT_ENDPOINT)
        self.assertEqual(args.kwargs['json']['model'], 'gpt-6-luna')
        self.assertEqual(args.kwargs['headers'], {'Authorization': 'Bearer sk-first'})
        self.assertFalse((self.data / decision_settings.FILENAME).exists())

    async def test_explicit_environment_key_overrides_file_credentials(self):
        with patch.object(config, "EXTERNAL_ENV_KEYS", frozenset({"OPENAI_API_KEY"})), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "sk-external"}):
            await providers.call_decision("i", [])
            self.assertEqual(self.http.post.await_args.kwargs["headers"],
                             {"Authorization": "Bearer sk-external"})
            self.set_key("sk-second")
            self.http.post.reset_mock()
            await providers.call_decision("i", [])
            self.assertEqual(self.http.post.await_args.kwargs["headers"],
                             {"Authorization": "Bearer sk-external"})

    def test_environment_service_binding_does_not_leak_key_to_file_endpoint(self):
        with patch.object(config, "EXTERNAL_ENV_KEYS",
                          frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL"})), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "sk-external",
                                     "OPENAI_BASE_URL": "https://gateway.example/v1"}):
            self.assertEqual(capabilities.key_for_endpoint(config, "https://api.openai.com/v1"), "")
            self.assertEqual(capabilities.key_for_endpoint(config, "https://gateway.example/v1"),
                             "sk-external")

    async def test_non_matching_endpoint_never_sends_the_key(self):
        decision_settings.save_settings(
            self.data, endpoint="https://other.example/v1/decisions",
            model="gpt-6-luna", credential="shared")
        self.assertIsNone(await providers.call_decision("i", []))
        self.http.post.assert_not_awaited()

    def test_old_file_with_its_own_key_still_reads_as_dedicated(self):
        decision_settings.save_settings(
            self.data, endpoint="https://api.openai.com/v1/decisions",
            model="gpt-6-luna", api_key="sk-legacy")
        loaded = decision_settings.load_settings(self.data)
        self.assertEqual((loaded["credential"], loaded["api_key"]),
                         ("dedicated", "sk-legacy"))
