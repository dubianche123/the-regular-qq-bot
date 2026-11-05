"""浏览器首次使用：私有配置、显式启动、真实连接状态和无聊天交接。"""
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer
from dotenv import dotenv_values

import panel
from test_instance_settings import configuration, complete_values


class _Inputs(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.fields = {}
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs


class PanelOnboardingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "instance & <fresh>").resolve()
        self.cfg = configuration(
            self.root, KNOWLEDGE_DIR=str(self.root / "data/knowledge"),
            MEMORY_DIR=str(self.root / "data/memory"), STATE_FILE=str(self.root / "data/state.json"),
            OWNER_FILE=str(self.root / "data/owner.txt"), LOG_FILE=str(self.root / "data/bot.log"),
            SEARCH_ENABLED=False)
        self.file = self.root / ".env"
        self.managed = {"owned": False, "running": False, "external": False, "pid": None,
                        "gateway_connected": False, "stale": True, "lifecycle": "stopped",
                        "log_file": str(self.root / "data/panel-start.log")}
        self.runner = Mock()
        self.runner.snapshot.side_effect = lambda: dict(self.managed)
        self.runner.start = Mock()
        self.runner.stop = AsyncMock()
        self.runner.close = AsyncMock()
        self.token = "synthetic-onboarding-token-0123456789"
        with patch.object(panel, "PanelRuntime", return_value=self.runner):
            self.app = panel.create_app(self.cfg, token=self.token)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def auth(self):
        return {panel.TOKEN_HEADER: self.token}

    async def setup_form(self, query=""):
        response = await self.client.get("/setup" + query, headers=self.auth())
        self.assertEqual(response.status, 200)
        text = await response.text()
        return text, _Inputs(text).fields

    async def save(self, **changes):
        _, fields = await self.setup_form()
        values = complete_values(**changes)
        values["csrf"] = fields["csrf"]["value"]
        return await self.client.post("/setup/save", data=values, headers=self.auth(), allow_redirects=False)

    def health(self, *, age=0, pid=9876):
        path = Path(self.cfg.STATUS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "updated_at": time.time() - age,
                                    "pid": pid, "lifecycle": "ready", "gateway_connected": True,
                                    "code": {"release": "synthetic-release", "source_root": "<code & root>"}}))

    async def test_empty_instance_lands_on_setup_without_creating_config_or_starting(self):
        response = await self.client.get("/", headers=self.auth())
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertIn("先让它走进你的 QQ 群", text)
        self.assertIn('action="/setup/save"', text)
        self.assertIn(html.escape(str(self.root)), text)
        self.assertNotIn(str(self.root), text)
        self.assertFalse(self.file.exists())
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_mutations_require_auth_csrf_and_post(self):
        _, fields = await self.setup_form()
        for route in ("/setup/save", "/runtime/start", "/runtime/stop"):
            with self.subTest(route=route):
                response = await self.client.post(route, data={"csrf": fields["csrf"]["value"]})
                self.assertEqual(response.status, 401)
                for data in ({}, {"csrf": "wrong-token"}):
                    response = await self.client.post(route, data=data, headers=self.auth())
                    self.assertEqual(response.status, 400)
                response = await self.client.get(route, headers=self.auth())
                self.assertEqual(response.status, 405)
        self.assertFalse(self.file.exists())
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_passwords_are_never_reflected_and_blank_values_keep_previous_secrets(self):
        response = await self.save()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/setup?saved=1")
        text, fields = await self.setup_form("?saved=1")
        self.assertIn("下次启动时读取新配置", text)
        self.assertNotIn("qq-private-secret", text)
        self.assertNotIn("private-model-key", text)
        for name in ("api_key", "qq_app_secret"):
            self.assertEqual(fields[name]["type"], "password")
            self.assertEqual(fields[name]["value"], "")
            self.assertIn("留空保留", fields[name]["placeholder"])
        response = await self.save(qq_app_secret="", api_key="")
        self.assertEqual(response.status, 302)
        saved = dotenv_values(self.file, interpolate=False)
        self.assertEqual(saved["QQ_APP_SECRET"], "qq-private-secret")
        self.assertEqual(saved["OPENAI_API_KEY"], "private-model-key")
        self.runner.start.assert_not_called()

    async def test_supplier_switch_reads_its_own_fields_and_does_not_borrow_a_key(self):
        self.assertEqual((await self.save()).status, 302)
        _, fields = await self.setup_form("?provider=zhipu")
        self.assertEqual(fields["provider"]["value"], "zhipu")
        self.assertEqual(fields["base_url"]["value"], "https://zhipu.example/v1")
        self.assertEqual(fields["models"]["value"], "z-model")
        self.assertIn("填写这个服务", fields["api_key"]["placeholder"])
        self.assertEqual(dotenv_values(self.file)["AI_PROVIDER"], "openai")
        response = await self.save(provider="zhipu", base_url="https://zhipu.example/v1", models="z-model",
                                   api_key="", qq_app_secret="")
        self.assertEqual(response.status, 302)
        text, fields = await self.setup_form()
        self.assertEqual(fields["provider"]["value"], "zhipu")
        self.assertIn("模型 API Key", text)
        self.assertNotIn("private-model-key", text)
        self.assertNotIn("ZHIPU_API_KEY", dotenv_values(self.file))

    async def test_bad_setup_returns_a_generic_error_without_echoing_url_credentials(self):
        response = await self.save(base_url="https://user:PRIVATE_URL_SECRET@model.example/v1",
                                   api_key="PRIVATE_NEW_KEY")
        self.assertEqual(response.status, 400)
        text = await response.text()
        self.assertNotIn("PRIVATE_URL_SECRET", text)
        self.assertNotIn("PRIVATE_NEW_KEY", text)
        self.assertFalse(self.file.exists())

    async def test_external_instance_has_no_stop_control_and_cannot_be_stopped_via_post(self):
        self.assertEqual((await self.save()).status, 302)
        self.managed.update(external=True, running=True, pid=1234)
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertIn("其他终端或常驻服务管理", text)
        self.assertNotIn('action="/runtime/stop"', text)
        self.assertNotIn('action="/runtime/start"', text)
        _, fields = await self.setup_form()
        self.runner.stop.side_effect = ValueError("PRIVATE_INTERNAL_DETAIL")
        response = await self.client.post("/runtime/stop", data={"csrf": fields["csrf"]["value"]},
                                          headers=self.auth())
        self.assertEqual(response.status, 409)
        self.assertNotIn("PRIVATE_INTERNAL_DETAIL", await response.text())

    async def test_expired_ready_snapshot_never_claims_qq_is_connected(self):
        self.assertEqual((await self.save()).status, 302)
        self.health(age=121)
        self.managed.update(external=True, running=True, pid=9876)
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertNotIn("已连上 QQ", text)
        self.assertNotIn("QQ 在线", text)
        self.assertIn("超过两分钟未更新", text)

    async def test_new_child_does_not_inherit_previous_pids_fresh_ready_claim(self):
        self.assertEqual((await self.save()).status, 302)
        self.health(pid=1111)
        self.managed.update(owned=True, running=True, pid=9876, gateway_connected=False, stale=True)
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertNotIn("已连上 QQ", text)
        self.assertNotIn("QQ 在线", text)
        self.assertIn("等待 QQ 连接确认", text)

    async def test_browser_save_start_wait_and_confirm_flow_uses_explicit_actions(self):
        self.assertEqual((await self.save()).status, 302)
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertIn('action="/runtime/start"', text)
        self.runner.start.assert_not_called()
        fields = _Inputs(text).fields

        def started():
            self.managed.update(owned=True, running=True, pid=9876, lifecycle="starting")

        self.runner.start.side_effect = started
        response = await self.client.post("/runtime/start", data={"csrf": fields["csrf"]["value"]},
                                          headers=self.auth(), allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.runner.start.assert_called_once()
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertIn("等待 QQ 连接确认", text)
        self.assertIn('action="/runtime/stop"', text)
        self.assertIn('http-equiv="refresh"', text)
        self.health()
        self.managed.update(gateway_connected=True, stale=False, lifecycle="ready")
        response = await self.client.get("/", headers=self.auth())
        text = await response.text()
        self.assertIn("已连上 QQ", text)
        self.assertIn("先给机器人发一条私聊", text)
        self.assertIn("synthetic-release", text)
        self.assertIn("&lt;code &amp; root&gt;", text)
        self.runner.stop.assert_not_awaited()

    async def test_agent_handoff_download_contains_paths_and_workflow_but_no_chat_or_secrets(self):
        self.assertEqual((await self.save()).status, 302)
        Path(self.cfg.STATE_FILE).write_text(json.dumps({"private_chat": "PRIVATE_CHAT_SCENE"}))
        Path(self.cfg.LOG_FILE).write_text("PRIVATE_CHAT_SCENE\n")
        response = await self.client.get("/iterate", headers=self.auth())
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertIn("自迭代 skill", text)
        self.assertIn("不会自动上传聊天", text)
        self.assertIn("--plan", text)
        self.assertIn("--allow-paid", text)
        self.assertIn(html.escape(str(self.root)), text)
        self.assertNotIn(str(self.root), text)
        for value in ("PRIVATE_CHAT_SCENE", "private-model-key", "qq-private-secret"):
            self.assertNotIn(value, text)
        download = await self.client.get("/iterate/brief", headers=self.auth())
        self.assertEqual(download.status, 200)
        self.assertEqual(download.content_type, "text/plain")
        self.assertEqual(download.headers["Content-Disposition"], 'attachment; filename="regular-agent-brief.md"')
        brief = await download.text()
        self.assertIn(str(self.root), brief)
        self.assertIn(".agents/skills/regular-iterate/SKILL.md", brief)
        self.assertIn("基线目录", brief)
        self.assertIn("不假定 HEAD", brief)
        for value in ("PRIVATE_CHAT_SCENE", "private-model-key", "qq-private-secret"):
            self.assertNotIn(value, brief)
        self.runner.start.assert_not_called()

    async def test_wheel_installation_is_guided_to_an_independent_source_workspace(self):
        installed = self.root / "fake-site-packages/panel.py"
        with patch.object(panel, "__file__", str(installed)):
            response = await self.client.get("/iterate", headers=self.auth())
            text = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("当前是运行安装包", text)
        self.assertIn("先下载源码工作区", text)
        self.assertIn("实例数据继续用原目录", text)

    async def test_new_routes_do_not_expose_any_page_without_panel_auth(self):
        for route in ("/setup", "/iterate", "/iterate/brief"):
            with self.subTest(route=route):
                response = await self.client.get(route)
                self.assertEqual(response.status, 401)
        self.runner.start.assert_not_called()
