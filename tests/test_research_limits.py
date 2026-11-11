"""普通联网查询只允许一次内置工具调用；研究也受现有输出预算约束。"""
import unittest
from unittest.mock import AsyncMock, patch

import config
import providers


class ResearchLimitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_has_one_tool_call_and_reasoning_output_budget(self):
        http = AsyncMock(return_value={"id": "response-search"})
        reserve = unittest.mock.Mock()
        with patch.object(providers, "research_available", return_value=True), \
                patch.object(providers, "initialize"), \
                patch.object(providers, "_reserve_attempt", reserve), \
                patch.object(providers, "_research_http", http), \
                patch.object(config, "RESEARCH_MODEL", "configured-search-model"), \
                patch.object(config, "REASONING_MAX_TOKENS", 12345):
            handle = await providers.start_research("测试问题", instructions="检索要求")

        self.assertEqual(handle, "response-search")
        reserve.assert_called_once_with()
        http.assert_awaited_once_with("POST", body={
            "model": "configured-search-model",
            "instructions": "检索要求",
            "input": "测试问题",
            "background": True,
            "store": True,
            "max_output_tokens": 12345,
            "max_tool_calls": 1,
            "tool_choice": "required",
            "tools": [{"type": "web_search", "search_context_size": "medium"}],
            "reasoning": {"effort": "low"},
        })

    async def test_deep_research_retains_multiple_tools_but_has_output_budget(self):
        http = AsyncMock(return_value={"id": "response-deep"})
        with patch.object(providers, "research_available", return_value=True), \
                patch.object(providers, "initialize"), \
                patch.object(providers, "_reserve_attempt"), \
                patch.object(providers, "_research_http", http), \
                patch.object(config, "REASONING_MAX_TOKENS", 9876):
            handle = await providers.start_research("深入测试", instructions="调研要求", deep=True)

        self.assertEqual(handle, "response-deep")
        body = http.await_args.kwargs["body"]
        self.assertNotIn("max_tool_calls", body)
        self.assertEqual(body["max_output_tokens"], 9876)
        self.assertEqual(body["tools"], [{"type": "web_search", "search_context_size": "high"}])
        self.assertEqual(body["reasoning"], {"effort": "high"})

    async def test_exhausted_budget_starts_no_request(self):
        http = AsyncMock()
        with patch.object(providers, "research_available", return_value=True), \
                patch.object(providers, "initialize"), \
                patch.object(providers, "_reserve_attempt", side_effect=providers._BudgetExhausted), \
                patch.object(providers, "_research_http", http):
            handle = await providers.start_research("测试问题", instructions="检索要求")

        self.assertIsNone(handle)
        http.assert_not_awaited()
