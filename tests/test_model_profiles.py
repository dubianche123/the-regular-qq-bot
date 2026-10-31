"""模型调用档案与「各司其职」（2026-11-01）。

每个模型怎么调各不相同，错一个参数就是 HTTP 400 或一句空回复 —— 都是实测出来的：
gpt-6-luna 不收 max_tokens、不收温度；hy4-preview 不认 thinking=disabled，推理把 256 的额度
吃光后一个字不写；审核要推理才认得出谐音，而推理在 400 的额度里经常写不出结论。
这里钉住「同一个模型在不同档怎么调」，以及「每一档走哪家」。
"""
import unittest
from unittest.mock import patch

import config
import providers

ZHIPU = {"extra_body": {"thinking": {"type": "disabled"}}, "temperature": 0.7}


class RequestParamsTest(unittest.TestCase):
    def params(self, model, tier="chat", max_tokens=256, temperature=None, preset=ZHIPU):
        return providers._request_params(model, tier, max_tokens, temperature, preset)

    def test_unregistered_model_keeps_the_old_request(self):
        self.assertEqual(self.params("some-new-model"),
                         {"max_tokens": 256, "temperature": providers._temperature(ZHIPU, None),
                          "extra_body": {"thinking": {"type": "disabled"}}})

    def test_production_glm_request_is_unchanged(self):
        """默认配置下线上那个模型收到的请求必须逐字不变。"""
        self.assertEqual(self.params("glm-5.3-flash"), self.params("some-new-model"))
        self.assertEqual(self.params("glm-5.3-flash", tier="judge", max_tokens=400),
                         self.params("some-new-model", tier="judge", max_tokens=400))

    def test_luna_uses_its_own_token_field_and_no_temperature(self):
        got = self.params("gpt-6-luna", temperature=0.0, preset={})
        self.assertEqual(got, {"max_completion_tokens": 256,
                               "extra_body": {"reasoning_effort": "none"}})

    def test_judge_tier_may_reason_with_room_to_finish(self):
        got = self.params("gpt-6-luna", tier="judge", max_tokens=400, preset={})
        self.assertEqual(got, {"max_completion_tokens": config.REASONING_MAX_TOKENS,
                               "extra_body": None})

    def test_profile_replaces_the_preset_switch_instead_of_adding_to_it(self):
        """hy4-preview 收到智谱那句 thinking=disabled 照样推理 —— 只能换成它认的写法。"""
        got = self.params("hy4-preview")
        self.assertEqual(got["extra_body"], {"reasoning_effort": "none"})

    def test_gateway_prefix_is_ignored_when_looking_up(self):
        self.assertEqual(self.params("cn:hy4-preview"), self.params("hy4-preview"))

    def test_model_that_cannot_reason_stays_off_even_for_the_judge(self):
        got = self.params("gemini-3.5-flash-lite", tier="judge", max_tokens=400, preset={})
        self.assertEqual(got["max_tokens"], 400)
        self.assertIsNone(got["extra_body"])

    def test_each_tier_can_set_its_own_reasoning_level(self):
        """2026-11-01 统一到 luna：回复 low（盲评第一），摘要 xhigh（不再把敏感词写进记忆）。"""
        levels = {"chat": "low", "digest": "xhigh", "judge": "", "vision": "none"}
        with patch.dict(config.TIER_REASONING, levels):
            chat = self.params("gpt-6-luna", tier="chat", max_tokens=256, preset={})
            digest = self.params("gpt-6-luna", tier="digest", max_tokens=1200, preset={})
            vision = self.params("gpt-6-luna", tier="vision", max_tokens=300, preset={})
            judge = self.params("gpt-6-luna", tier="judge", max_tokens=400, preset={})
            glm = self.params("glm-5.3-flash", tier="chat")
        self.assertEqual(chat, {"max_completion_tokens": config.REASONING_MAX_TOKENS,
                                "extra_body": {"reasoning_effort": "low"}})
        self.assertEqual(digest["extra_body"], {"reasoning_effort": "xhigh"})
        self.assertEqual(vision, {"max_completion_tokens": 300,
                                  "extra_body": {"reasoning_effort": "none"}})
        self.assertEqual(judge["extra_body"], None)            # 没配就照旧按 on
        self.assertEqual(glm, self.params("some-new-model"))   # 不认强度的模型不受影响


class DigestTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_digest_gets_its_own_time_limit(self):
        """摘要开 xhigh 一次要一分多钟：不能吃回复那 20 秒的单次时限。"""
        seen = []

        async def fake_wait_for(coro, timeout):
            coro.close()
            seen.append(timeout)
            raise TimeoutError

        fake = object()
        with patch.dict(providers.AI_CLIENTS, {"zhipu": [fake]}), \
                patch.object(providers, "_available", lambda name: True), \
                patch.object(providers, "initialize", lambda **kw: None), \
                patch.object(providers.asyncio, "wait_for", fake_wait_for), \
                patch.object(config, "AI_MAX_ATTEMPTS", 1), \
                patch.object(config, "AI_TIMEOUT_SECONDS", 20.0), \
                patch.object(config, "DIGEST_TIMEOUT_SECONDS", 240.0):
            for tier in ("chat", "digest"):
                await providers._complete([], 100, tier, None, ["zhipu"], {"zhipu": ["glm-5.3-flash"]})
        self.assertEqual(seen[0], 20.0)
        self.assertGreater(seen[1], 200.0)


class TierRoutingTest(unittest.TestCase):
    def test_default_shares_one_chain_object(self):
        """默认（各档都跟 AI_PROVIDER）：三档共用同一个列表，行为与以前一样。"""
        chat = ["zhipu"]
        chains = providers._tier_chains({"chat": "zhipu", "digest": "zhipu", "judge": "zhipu"},
                                        "", chat)
        self.assertIs(chains["digest"], chat)
        self.assertIs(chains["judge"], chat)

    def test_each_tier_can_go_to_its_own_provider(self):
        chains = providers._tier_chains({"chat": "zhipu", "digest": "gemini", "judge": "openai"},
                                        "zhipu", ["zhipu"])
        self.assertEqual(chains["judge"], ["openai", "zhipu"])
        self.assertEqual(chains["digest"], ["gemini", "zhipu"])

    def test_call_model_uses_the_tier_chain(self):
        seen = {}

        async def fake_complete(messages, max_tokens, tier, temperature, names, chains, **kw):
            seen[tier] = names
            return "ok"
        with patch.object(providers, "_complete", fake_complete), \
                patch.dict(providers._TIER_PROVIDER_CHAINS, {"judge": ["openai"]}):
            import asyncio
            asyncio.run(providers.call_model([], 10, tier="judge"))
        self.assertEqual(seen["judge"], ["openai"])

    def test_a_tier_provider_without_keys_is_reported_at_startup(self):
        other = next(n for n in config.PROVIDER_PRESETS if n != config.AI_PROVIDER)
        label = config.PROVIDER_PRESETS[other]["label"]
        with patch.dict(config.TIER_PROVIDERS, {"judge": other}), \
                patch.object(config, "load_api_keys",
                             lambda name=None: [] if name == other else ["k"]):
            self.assertTrue(any(label in e and "API Key" in e for e in config.validate()))


if __name__ == "__main__":
    unittest.main()
