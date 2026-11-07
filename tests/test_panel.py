# -*- coding: utf-8 -*-
"""本机面板：token 鉴权、知识库编辑、记忆只读、搜索调试。

面板是维护者工具，不参与 QQ 连接；测试只验证本地 aiohttp 路由和文件边界。
"""
import json
import os
import re
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, "..")

import panel


class PanelTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.knowledge_dir = root / "knowledge"
        self.memory_dir = root / "memory"
        self.knowledge_dir.mkdir()
        self.memory_dir.mkdir()
        (self.knowledge_dir / "all.md").write_text(
            "## 群规\n关键词：群规\n本群不发广告。\n", encoding="utf-8")
        (self.memory_dir / "latest_g1.md").write_text(
            "# 群史记\n\n这是记忆内容。\n", encoding="utf-8")
        (root / "bot.log").write_text("line one\nline two\n", encoding="utf-8")
        (root / "status.json").write_text(json.dumps({
            "updated_at": time.time(),
            "lifecycle": "ready",
            "gateway_connected": True,
            "providers": {
                "providers": [{"label": "测试模型", "models": ["test-model"]}],
                "vision": {"model": "test-model"},
            },
            "budget": {"used": 3, "limit": 100},
        }), encoding="utf-8")
        (root / "state.json").write_text(json.dumps({
            "groups": {"g1": {"last_seen": time.time()}},
            "modes": {"g1": "normal"},
            "digests": {"g1": {"summary": {"updated": time.time()}}},
            "relations": {"g1|u1": {}, "g1|u2": {}},
            "promises": {"g1": []},
        }), encoding="utf-8")
        self.cfg = SimpleNamespace(
            DATA_DIR=str(root),
            KNOWLEDGE_DIR=str(self.knowledge_dir),
            MEMORY_DIR=str(self.memory_dir),
            STATE_FILE=str(root / "state.json"),
            STATUS_FILE=str(root / "status.json"),
            LOG_FILE=str(root / "bot.log"),
            SEARCH_ENABLED=True,
        )
        self.token = "test-token-0123456789abcdef"
        self.app = panel.create_app(self.cfg, token=self.token)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def auth(self):
        return {panel.TOKEN_HEADER: self.token}

    async def test_requires_token_and_sets_security_headers(self):
        unauth = await self.client.get("/")
        self.assertEqual(unauth.status, 401)
        response = await self.client.get("/", headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])

    async def test_query_token_redirects_and_sets_cookie(self):
        response = await self.client.get(f"/?token={self.token}", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertIn("panel_token", response.cookies)
        page = await self.client.get("/")
        self.assertEqual(page.status, 200)

    async def test_dashboard_shows_status_groups_and_knowledge(self):
        state = json.loads(Path(self.cfg.STATE_FILE).read_text())
        state["groups"]["g1"]["name"] = "老友茶馆"
        Path(self.cfg.STATE_FILE).write_text(json.dumps(state))
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertIn("老友茶馆", text)
        self.assertNotIn('>g1<', text)
        self.assertNotIn('>normal<', text)
        self.assertIn("3 / 100", text)
        self.assertIn("all.md", text)
        state["groups"]["g1"].pop("name")
        Path(self.cfg.STATE_FILE).write_text(json.dumps(state))
        text = await (await self.client.get("/", headers=self.auth())).text()
        self.assertIn('群 · ', text)
        self.assertNotIn('>g1<', text)

    async def test_knowledge_edit_round_trip(self):
        text = await (await self.client.get('/knowledge', headers=self.auth())).text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', text).group(1)
        response = await self.client.post(
            "/knowledge/save",
            data={"csrf": csrf, "file": "all.md", "content": "## 新事实\n关键词：测试\n这是新内容。\n"},
            headers=self.auth(), allow_redirects=False)
        self.assertEqual(response.status, 302)
        path = self.knowledge_dir / "all.md"
        self.assertIn("这是新内容", path.read_text(encoding="utf-8"))
        page = await self.client.get("/knowledge?file=all.md", headers=self.auth())
        text = await page.text()
        self.assertIn("这是新内容", text)
        self.assertIn("1 条知识", text)

    async def test_new_group_knowledge_file_can_be_created(self):
        text = await (await self.client.get('/knowledge', headers=self.auth())).text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', text).group(1)
        response = await self.client.post(
            "/knowledge/save",
            data={"csrf": csrf, "file": "806abc.md", "content": "## 群规\n关键词：群规\n新群规则。\n"},
            headers=self.auth(), allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertTrue((self.knowledge_dir / "806abc.md").is_file())

    async def test_knowledge_library_previews_entries_and_keeps_templates_as_unsaved_drafts(self):
        original = (self.knowledge_dir / "all.md").read_bytes()
        response = await self.client.get("/knowledge?file=all.md", headers=self.auth())
        text = await response.text()
        self.assertIn('id="kb-scope"', text)
        self.assertIn('aria-label="已保存知识条目"', text)
        self.assertIn("本群不发广告", text)
        self.assertNotIn("data-kb-editor", text)
        self.assertNotIn("示例草稿", text)
        self.assertEqual((self.knowledge_dir / "all.md").read_bytes(), original)
        response = await self.client.get("/knowledge?file=806abc.md&template=names", headers=self.auth())
        text = await response.text()
        self.assertIn("示例草稿", text)
        self.assertIn("一个称呼的来历", text)
        self.assertFalse((self.knowledge_dir / "806abc.md").exists())

    async def test_knowledge_preview_keeps_untrusted_markup_literal(self):
        (self.knowledge_dir / "all.md").write_text(
            '## <img src=x onerror=alert(1)>\n关键词：测试\n<script>bad()</script>\n')
        response = await self.client.get("/knowledge", headers=self.auth())
        text = await response.text()
        self.assertNotIn('<img src=x', text)
        self.assertNotIn('<script>bad()', text)
        self.assertIn('&lt;script&gt;bad()', text)

    async def test_path_traversal_is_rejected(self):
        text = await (await self.client.get('/knowledge', headers=self.auth())).text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', text).group(1)
        response = await self.client.post(
            "/knowledge/save",
            data={"csrf": csrf, "file": "../evil.md", "content": "bad"},
            headers=self.auth(), allow_redirects=False)
        self.assertEqual(response.status, 400)
        self.assertFalse((Path(self.cfg.DATA_DIR).parent / "evil.md").exists())

    async def test_memory_is_read_only_and_path_checked(self):
        page = await self.client.get("/memory?file=latest_g1.md", headers=self.auth())
        self.assertEqual(page.status, 200)
        self.assertIn("这是记忆内容", await page.text())
        bad = await self.client.get("/memory?file=../state.json", headers=self.auth())
        self.assertEqual(bad.status, 400)

    async def test_memory_is_an_index_of_real_pages_not_a_raw_dump(self):
        (self.memory_dir / "2026-11-06_g1.md").write_text(
            "# 群史记 · 2026-11-06\n\n## 摘要\n今天聊了炒饭。\n\n"
            "## 名场面\n- 「葱分三次」 —— 很认真。\n", encoding="utf-8")
        page = await (await self.client.get("/memory", headers=self.auth())).text()
        self.assertIn("<h1>群史记</h1>", page)
        self.assertIn("今天聊了炒饭。", page)
        self.assertIn("葱分三次", page)
        self.assertIn('href="/memory?file=2026-11-06_g1.md"', page)
        self.assertNotIn("<pre>", page)
        detail = await (await self.client.get(
            "/memory?file=2026-11-06_g1.md", headers=self.auth())).text()
        self.assertIn("<h2>群史记 · 2026-11-06</h2>", detail)
        self.assertIn("← 全部群史记", detail)

    async def test_checkup_opens_on_a_day_with_something_to_read(self):
        root = Path(self.cfg.DATA_DIR)
        (root / "checkups").mkdir()
        (root / "interactions").mkdir()
        (root / "checkups" / "2026-11-06.json").write_text(json.dumps({
            "schema": 1, "day": "2026-11-06", "findings": [{"id": "abc"}],
            "inputs": {}, "counts": {}, "precision": {}, "policy": {},
        }), encoding="utf-8")
        (root / "interactions" / "2026-11-07.jsonl").write_text("", encoding="utf-8")
        page = await (await self.client.get("/checkup", headers=self.auth())).text()
        self.assertIn('href="/checkup?day=2026-11-06" aria-current=page', page)
        self.assertIn('class="wx-checkup-days"', page)

    async def test_checkup_computes_latest_logs_before_consulting_old_cached_findings(self):
        root = Path(self.cfg.DATA_DIR)
        (root / "checkups").mkdir()
        (root / "interactions").mkdir()
        (root / "checkups" / "2026-11-06.json").write_text(json.dumps({
            "findings": [{"id": "old"}],
        }), encoding="utf-8")
        (root / "interactions" / "2026-11-07.jsonl").write_text(json.dumps({
            "schema": "regular.interaction.v1", "kind": "incoming", "ts": 1,
            "group": "synthetic-group", "user": "synthetic-user", "text": "早上好",
            "message_id": "synthetic-message",
        }) + "\n", encoding="utf-8")
        page = await (await self.client.get("/checkup", headers=self.auth())).text()
        self.assertIn('href="/checkup?day=2026-11-07" aria-current=page', page)
        self.assertIn('收到 1 条消息', page)
        self.assertNotIn('今天还没攒出可看的', page)
        self.assertTrue((root / "checkups" / "2026-11-07.json").is_file())
        # A day not computed yet must not be presented as a confirmed zero.
        (root / "interactions" / "2026-11-05.jsonl").write_text("", encoding="utf-8")
        page = await (await self.client.get("/checkup", headers=self.auth())).text()
        self.assertIn('11-05<span>待体检</span>', page)

    async def test_search_debug_uses_search_module(self):
        result = [{"title": "Example", "snippet": "Snippet", "url": "https://example.com/a"}]
        with patch.object(panel.search_mod, "search_web", new=AsyncMock(return_value=result)):
            page = await self.client.get("/search?q=python", headers=self.auth())
        text = await page.text()
        self.assertIn("Example", text)
        self.assertIn("https://example.com/a", text)

    async def _decision_page(self):
        page = await self.client.get("/decision", headers=self.auth())
        self.assertEqual(page.status, 200)
        text = await page.text()
        match = re.search(r'name="csrf" value="([^"]+)"', text)
        self.assertIsNotNone(match)
        return text, match.group(1)

    async def test_decision_page_shows_form_without_secret(self):
        text, _csrf = await self._decision_page()
        self.assertIn("模型与能力", text)
        self.assertIn('id="decision" data-tone="todo"><details class="cap-more" name="cap" open>', text)
        self.assertIn('class="ms-diagnostics ms-advanced" open', text)
        self.assertIn("/v1/decisions", text)
        self.assertIn("gpt-6-luna", text)
        self.assertIn('name="api_key"', text)
        # key 输入框永远空白，页面上没有任何已存值
        self.assertIn('name="api_key" value=""', text)
        self.assertIn("还没连接", text)

    async def test_decision_save_requires_auth_and_csrf(self):
        post = {"csrf": "bogus", "endpoint": "https://api.openai.com/v1/decisions",
                "model": "gpt-6-luna", "api_key": "sk-fake-1"}
        unauth = await self.client.post("/decision/save", data=post)
        self.assertEqual(unauth.status, 401)
        bad_csrf = await self.client.post("/decision/save", data=post,
                                          headers=self.auth())
        self.assertEqual(bad_csrf.status, 400)
        self.assertFalse((Path(self.cfg.DATA_DIR) / "decision_api.json").exists())

    async def test_decision_save_round_trip_never_echoes_key(self):
        # 「保存没顺手 import 运行时」的判法：对比前后模块对象本身，
        # 不管测试套件顺序里 bot 是否早已被别的用例引过。
        before = {name: sys.modules.get(name)
                  for name in ("bot", "runtime", "providers")}
        _text, csrf = await self._decision_page()
        with patch("aiohttp.ClientSession",
                   side_effect=AssertionError("save must stay offline")):
            response = await self.client.post(
                "/decision/save", headers=self.auth(),
                data={"csrf": csrf,
                      "endpoint": "https://api.openai.com/v1/decisions",
                      "model": "gpt-6-luna", "api_key": "sk-fake-secret-001"},
                allow_redirects=False)
        self.assertEqual(response.status, 302)
        saved = Path(self.cfg.DATA_DIR) / "decision_api.json"
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        row = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(row["api_key"], "sk-fake-secret-001")
        # GET 回显的只有「已配置」状态，key 一个字节都不上页面
        text, _ = await self._decision_page()
        self.assertIn('id="decision"', text)
        self.assertIn("gpt-6-luna", text)
        self.assertNotIn("sk-fake-secret-001", text)
        self.assertIn('name="api_key" value=""', text)
        # 不碰 .env、不建其他文件
        self.assertFalse((Path(self.cfg.DATA_DIR) / ".env").exists())
        self.assertEqual({name: sys.modules.get(name)
                          for name in ("bot", "runtime", "providers")}, before)

    async def test_decision_save_rejects_bad_fields_without_echo(self):
        _text, csrf = await self._decision_page()
        saved = Path(self.cfg.DATA_DIR) / "decision_api.json"
        for bad in (
                {"endpoint": "http://api.openai.com/v1/decisions"},
                {"endpoint": "https://user:pw@api.openai.com/v1/decisions"},
                {"endpoint": "https://api.openai.com/v1/decisions?x=1"},
                {"endpoint": "https://api.openai.com/v1/decisions\nevil"},
                {"api_key": ""}, {"api_key": "  "}):
            post = {"csrf": csrf,
                    "endpoint": "https://api.openai.com/v1/decisions",
                    "model": "gpt-6-luna", "api_key": "sk-echo-me-not"}
            post.update(bad)
            response = await self.client.post("/decision/save", data=post,
                                              headers=self.auth())
            self.assertEqual(response.status, 400, bad)
            body = await response.text()
            self.assertNotIn("sk-echo-me-not", body)
        self.assertFalse(saved.exists())

    async def test_decision_resave_requires_fresh_key(self):
        _text, csrf = await self._decision_page()
        response = await self.client.post(
            "/decision/save", headers=self.auth(),
            data={"csrf": csrf,
                  "endpoint": "https://api.openai.com/v1/decisions",
                  "model": "gpt-6-luna", "api_key": " "})
        self.assertEqual(response.status, 400)

    async def test_token_file_is_private_and_stable(self):
        token = panel.load_or_create_token(self.cfg)
        self.assertGreaterEqual(len(token), 24)
        path = Path(self.cfg.DATA_DIR) / panel.TOKEN_FILE
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o777, 0o600)
        self.assertEqual(panel.load_or_create_token(self.cfg), token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
