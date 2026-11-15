"""稳定头的长时缓存（2026-11-16）：gpt-6-luna 的回复档在官方端点改走 Responses，稳定头打 12 小时缓存点。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError

import providers

OFFICIAL = "https://api.openai.com/v1"
TEXT = [{"role": "system", "content": "稳定头"}, {"role": "system", "content": "每日记忆"},
        {"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"},
        {"role": "user", "content": "本轮动态\n这一句"}]


def _client(base=OFFICIAL):
    return SimpleNamespace(base_url=httpx.URL(base + "/"), api_key="sk-test")


class _Http:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload, []

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return httpx.Response(self.status, json=self.payload or {}, request=httpx.Request("POST", url))


DONE = {"status": "completed",
        "output": [{"type": "reasoning"},
                   {"type": "message", "content": [{"type": "output_text", "text": "好呀"}]}],
        "usage": {"input_tokens": 5400, "output_tokens": 30,
                  "input_tokens_details": {"cached_tokens": 5000, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 12}}}


class LongCacheTest(unittest.IsolatedAsyncioTestCase):
    def test_only_text_replies_to_the_official_endpoint(self):
        self.assertEqual(providers._long_cache_ttl(_client(), "gpt-6-luna", "chat", TEXT), "12h")
        self.assertIsNone(providers._long_cache_ttl(_client(), "gpt-6-luna", "digest", TEXT))
        self.assertIsNone(providers._long_cache_ttl(_client("https://gw.example/v1"), "gpt-6-luna", "chat", TEXT))
        self.assertIsNone(providers._long_cache_ttl(_client(), "glm-5.3-flash", "chat", TEXT))
        image = TEXT[:-1] + [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
        self.assertIsNone(providers._long_cache_ttl(_client(), "gpt-6-luna", "chat", image))

    def test_breakpoints_sit_on_the_stable_head_and_memory_only(self):
        items = providers._responses_input(TEXT, "12h")
        self.assertEqual(items[0]["role"], "developer")
        self.assertEqual(items[0]["content"][0]["prompt_cache_breakpoint"], {"mode": "explicit", "ttl": "12h"})
        self.assertEqual(items[1]["content"][0]["prompt_cache_breakpoint"], {"mode": "explicit"})
        self.assertEqual(items[2:], [{"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"},
                                     {"role": "user", "content": "本轮动态\n这一句"}])

    async def test_request_body_does_not_store_chat_and_maps_the_result(self):
        http = _Http(payload=DONE)
        with patch.object(providers, "_llm_http", http):
            result = await providers._responses_create(
                _client(), "12h", model="gpt-6-luna", messages=TEXT,
                max_completion_tokens=320, extra_body={"reasoning_effort": "low"})
        url, body, headers = http.calls[0]
        self.assertEqual(url, OFFICIAL + "/responses")
        self.assertIs(body["store"], False)
        self.assertEqual(body["max_output_tokens"], 320)
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertEqual(body["prompt_cache_options"], {"mode": "explicit"})
        self.assertNotIn("reasoning_effort", body)
        choice = result.choices[0]
        self.assertEqual((choice.message.content, choice.finish_reason), ("好呀", "stop"))
        metrics = providers._reply_metrics(result, choice)
        self.assertEqual((metrics["prompt"], metrics["cached"], metrics["written"], metrics["thinking"]),
                         (5400, 5000, 0, 12))

    async def test_truncation_reads_as_length(self):
        cut = dict(DONE, status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                   output=[{"type": "reasoning"}])
        with patch.object(providers, "_llm_http", _Http(payload=cut)):
            result = await providers._responses_create(_client(), "12h", model="gpt-6-luna", messages=TEXT)
        self.assertEqual((result.choices[0].message.content, result.choices[0].finish_reason), ("", "length"))

    async def test_http_errors_keep_the_failover_rules(self):
        with patch.object(providers, "_llm_http", _Http(status=429, payload={"error": {}})):
            with self.assertRaises(APIStatusError) as caught:
                await providers._responses_create(_client(), "12h", model="gpt-6-luna", messages=TEXT)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(providers._failure_action("openai-test", caught.exception), "provider")
        providers._provider_state.pop("openai-test", None)

    async def test_complete_sends_chat_through_responses_and_digest_through_chat_completions(self):
        http = _Http(payload=DONE)
        created = []

        async def create(**kw):
            created.append(kw)
            msg = SimpleNamespace(content="摘要", reasoning_content=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)

        client = _client()
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        budget = SimpleNamespace(try_consume=lambda: True)
        with patch.object(providers, "_llm_http", http), patch.object(providers, "_initialized", True), \
                patch.dict(providers.AI_CLIENTS, {"openai": [client]}), \
                patch.object(providers, "_bound_budget", lambda: budget), \
                patch.object(providers, "_available", lambda name: True):
            chat = await providers._complete(TEXT, 320, "chat", None, ["openai"], {"openai": ["gpt-6-luna"]})
            digest = await providers._complete(TEXT, 320, "digest", None, ["openai"], {"openai": ["gpt-6-luna"]})
        self.assertEqual((chat, digest), ("好呀", "摘要"))
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(len(created), 1)
        self.assertNotIn("responses_ttl", created[0])
        self.assertIn("12h", json.dumps(http.calls[0][1]))


if __name__ == "__main__":
    unittest.main()
