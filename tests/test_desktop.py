"""桌宠优先启动：同实例认证、UI 服务所有权、降级和关闭语义。"""
import contextlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from regular import cli, desktop
from test_instance_settings import configuration


class DesktopTest(unittest.TestCase):
    TOKEN = "private-synthetic-panel-token-0123456789"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg = configuration(self.root)
        env = patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"})
        env.start()
        self.addCleanup(env.stop)
        self.token_file = Path(self.cfg.DATA_DIR) / desktop.TOKEN_FILE
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stderr = contextlib.redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def token(self):
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(self.TOKEN + "\n")

    def client(self, *, status=200, headers=None, error=None):
        headers = {desktop.PANEL_ID_HEADER: "1"} if headers is None else headers
        patcher = patch.object(desktop.httpx, "Client")
        factory = patcher.start()
        self.addCleanup(patcher.stop)
        client = factory.return_value.__enter__.return_value
        client.get.return_value = httpx.Response(status, headers=headers)
        if error:
            client.get.side_effect = error
        return factory, client

    def assert_bootstrap_url(self, url, port=8765):
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.netloc, f"127.0.0.1:{port}")
        self.assertEqual(parsed.path, "/companion")
        self.assertEqual(parse_qs(parsed.query), {"mode": ["pet"], "token": [self.TOKEN]})

    def test_owned_ui_service_stays_supervised_after_pet_closes(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(desktop, "_ensure_panel", return_value=(self.TOKEN, process)), \
             patch.object(desktop, "_show_pet", return_value=0):
            self.assertEqual(desktop.run(self.cfg), 0)
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        self.assertIn("仍在此终端运行", self.output.getvalue())
        self.assertIn("Ctrl+C", self.output.getvalue())

    def test_missing_panel_starts_only_a_local_panel_child_and_waits_for_authentication(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "_read_token", side_effect=["", self.TOKEN]), \
             patch.object(desktop, "_panel_ready", side_effect=[False, True]), \
             patch.object(desktop.subprocess, "Popen", return_value=process) as spawn:
            token, owned = desktop._ensure_panel(self.cfg, 8890)
        self.assertEqual(token, self.TOKEN)
        self.assertIs(owned, process)
        command = spawn.call_args.args[0]
        self.assertEqual(command[-3:], ["panel", "--port", "8890"])
        self.assertEqual(command[2:5], ["regular", "--directory", str(self.root)])
        self.assertNotIn("run", command)
        self.assertFalse(spawn.call_args.kwargs["shell"])
        self.assertEqual(spawn.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(spawn.call_args.kwargs["env"]["REGULAR_HOME"], str(self.root))
        process.terminate.assert_not_called()
        if os.name != "nt":
            self.assertEqual((Path(self.cfg.DATA_DIR) / "desktop-panel.log").stat().st_mode & 0o777, 0o600)

    def test_failed_owned_service_is_not_restarted_or_sent_to_a_browser(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(desktop, "_panel_ready", return_value=False), \
             patch.object(desktop.subprocess, "Popen", return_value=process) as spawn, \
             patch.object(desktop.webbrowser, "open") as browser:
            self.assertEqual(desktop.run(self.cfg), 1)
        spawn.assert_called_once()
        browser.assert_not_called()
        self.assertIn("端口", self.output.getvalue())
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_interrupt_stops_only_owned_panel_and_waits_for_cleanup(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [KeyboardInterrupt, 0]
        with patch.object(desktop, "_ensure_panel", return_value=(self.TOKEN, process)), \
             patch.object(desktop, "_show_pet", return_value=0):
            self.assertEqual(desktop.run(self.cfg), 130)
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_args.kwargs, {"timeout": 12})

    def test_native_interruption_cleans_owned_panel_without_browser_fallback(self):
        process = Mock()
        process.poll.return_value = None
        gui = SimpleNamespace(run=Mock(side_effect=KeyboardInterrupt))
        with patch.object(desktop, "_ensure_panel", return_value=(self.TOKEN, process)), \
             patch.object(desktop.importlib, "import_module", return_value=gui), \
             patch.object(desktop.webbrowser, "open") as browser:
            self.assertEqual(desktop.run(self.cfg), 130)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=12)
        browser.assert_not_called()

    def test_read_only_probe_is_bounded_loopback_and_never_sends_token_in_its_url(self):
        self.token()
        factory, client = self.client()
        with patch.object(desktop.webbrowser, "open", return_value=True) as browser, \
             patch.object(desktop.importlib, "import_module") as gui:
            self.assertEqual(desktop.run(self.cfg, port=8789, browser=True), 0)
        factory.assert_called_once_with(timeout=2.0, trust_env=False, follow_redirects=False)
        client.get.assert_called_once_with("http://127.0.0.1:8789/companion?mode=pet",
                                            headers={desktop.TOKEN_HEADER: self.TOKEN})
        gui.assert_not_called()
        self.assert_bootstrap_url(browser.call_args.args[0], 8789)
        self.assertNotIn(self.TOKEN, self.output.getvalue())
        self.assertIn("不是系统桌面悬浮窗", self.output.getvalue())

    def test_redirect_is_not_followed_and_native_or_browser_never_opens_it(self):
        self.token()
        _, client = self.client(status=302, headers={"Location": "https://outside.example/steal"})
        with patch.object(desktop.webbrowser, "open") as browser, \
             patch.object(desktop.importlib, "import_module") as gui:
            self.assertEqual(desktop.run(self.cfg), 1)
        client.get.assert_called_once()
        browser.assert_not_called()
        gui.assert_not_called()
        self.assertNotIn("outside.example", self.output.getvalue())
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_wrong_token_or_unrelated_service_cannot_be_treated_as_the_panel(self):
        self.token()
        for status, headers in ((401, {desktop.PANEL_ID_HEADER: "1"}), (200, {}), (500, {})):
            with self.subTest(status=status):
                _, _ = self.client(status=status, headers=headers)
                with patch.object(desktop.webbrowser, "open") as browser, \
                     patch.object(desktop.importlib, "import_module") as gui:
                    self.assertEqual(desktop.run(self.cfg), 1)
                browser.assert_not_called()
                gui.assert_not_called()

    def test_unreachable_existing_panel_can_be_prepared_in_the_same_instance(self):
        self.token()
        _, client = self.client(error=httpx.ConnectError("PRIVATE_ERROR_DETAIL"))
        self.assertFalse(desktop._panel_ready(8890, self.TOKEN))
        client.get.assert_called_once()
        self.assertNotIn("PRIVATE_ERROR_DETAIL", self.output.getvalue())

    def test_slow_existing_service_does_not_spawn_a_duplicate(self):
        self.token()
        self.client(error=httpx.ReadTimeout("PRIVATE_ERROR_DETAIL"))
        with patch.object(desktop.subprocess, "Popen") as spawn:
            self.assertEqual(desktop.run(self.cfg), 1)
        spawn.assert_not_called()
        self.assertNotIn("PRIVATE_ERROR_DETAIL", self.output.getvalue())

    def test_missing_optional_package_opens_the_same_compact_page_and_gives_install_hint(self):
        self.token()
        self.client()
        with patch.object(desktop.importlib, "import_module", side_effect=ImportError()), \
             patch.object(desktop.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(desktop.run(self.cfg), 0)
        self.assert_bootstrap_url(browser.call_args.args[0])
        self.assertIn('.[desktop]', self.output.getvalue())
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_native_dispatch_is_only_the_transparent_pet_client(self):
        self.token()
        self.client()
        gui = SimpleNamespace(run=Mock(return_value=0))
        with patch.object(desktop.importlib, "import_module", return_value=gui), \
             patch.object(desktop.webbrowser, "open") as browser:
            self.assertEqual(desktop.run(self.cfg), 0)
        gui.run.assert_called_once_with(port=8765, token=self.TOKEN,
                                       preferences_path=Path(self.cfg.DATA_DIR) / "desktop-pet.json")
        browser.assert_not_called()
        self.assertIn("不会停止面板或机器人", self.output.getvalue())
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_cli_reopens_the_matching_branded_bundle_without_starting_an_extra_panel(self):
        import json
        import time
        from regular import __version__
        output = self.root / "desktop"
        exe = output / "老群友.app/Contents/MacOS/老群友"
        exe.parent.mkdir(parents=True)
        exe.touch()
        (output / "desktop-build.json").write_text(json.dumps({"schema": 1, "version": __version__, "built_at": time.time() + 1,
            "source": str(Path(desktop.__file__).resolve().parent.parent)}))
        with patch.object(desktop.sys, "platform", "darwin"), \
             patch.object(desktop.sys, "frozen", False, create=True), \
             patch.object(desktop, "_ensure_panel") as ensure, \
             patch.object(desktop.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as opened:
            self.assertEqual(desktop.run(self.cfg, port=8881), 0)
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0], [str(exe), "--directory", str(self.root), "pet", "--port", "8881"])
        ensure.assert_not_called()

    def test_gui_backend_error_falls_back_without_echoing_authenticated_url(self):
        self.token()
        self.client()
        gui = SimpleNamespace(run=Mock(side_effect=RuntimeError("FAILED " + self.TOKEN)))
        with patch.object(desktop.importlib, "import_module", return_value=gui), \
             patch.object(desktop.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(desktop.run(self.cfg), 0)
        self.assert_bootstrap_url(browser.call_args.args[0])
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_native_import_failure_also_falls_back_without_dumping_error_details(self):
        self.token()
        self.client()
        with patch.object(desktop.importlib, "import_module", side_effect=OSError("PRIVATE_BACKEND_PATH")), \
             patch.object(desktop.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(desktop.run(self.cfg), 0)
        self.assert_bootstrap_url(browser.call_args.args[0])
        self.assertNotIn("PRIVATE_BACKEND_PATH", self.output.getvalue())

    def test_invalid_ports_and_browser_failure_are_reported_without_launching_any_process(self):
        with patch.object(desktop.httpx, "Client") as client:
            for port in (0, -1, 65536, True):
                with self.subTest(port=port):
                    self.assertEqual(desktop.run(self.cfg, port=port), 1)
        client.assert_not_called()
        self.token()
        self.client()
        with patch.object(desktop.webbrowser, "open", return_value=False):
            self.assertEqual(desktop.run(self.cfg, browser=True), 1)
        self.assertNotIn(self.TOKEN, self.output.getvalue())

    def test_cli_without_subcommand_opens_pet_first(self):
        with patch.object(cli, "load_config", return_value=self.cfg), \
             patch.object(desktop, "run", return_value=0) as run:
            self.assertEqual(cli.main(["--directory", str(self.root)]), 0)
        run.assert_called_once_with(self.cfg, port=8765, browser=False)

    def test_cli_pet_dispatches_lazily_with_same_directory_and_port(self):
        with patch.object(cli, "load_config", return_value=self.cfg) as load, \
             patch.object(desktop, "run", return_value=0) as run:
            self.assertEqual(cli.main(["--directory", str(self.root), "pet", "--port", "8891", "--browser"]), 0)
        load.assert_called_once_with(str(self.root))
        run.assert_called_once_with(self.cfg, port=8891, browser=True)


class PanelClientTest(unittest.TestCase):
    TOKEN = "private-synthetic-client-token-0123456789"
    CSRF = "synthetic-csrf-0123456789"

    def setUp(self):
        self.client = desktop.PanelClient(8877, self.TOKEN)
        self.calls = []
        self.real_client = httpx.Client

    def transport(self, response):
        def handle(request):
            self.calls.append(request)
            if isinstance(response, Exception):
                raise response
            return response

        factory = patch.object(desktop.httpx, "Client", side_effect=lambda **kw: self.real_client(
            transport=httpx.MockTransport(handle), **kw))
        result = factory.start()
        self.addCleanup(factory.stop)
        return result

    def test_session_is_one_authenticated_read_with_fixed_local_destination(self):
        factory = self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"},
            json={"csrf": self.CSRF, "available": True, "reason": "", "qq_connected": False}))
        result = self.client.session()
        self.assertTrue(result["available"])
        self.assertEqual(result["csrf"], self.CSRF)
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(str(request.url), "http://127.0.0.1:8877/companion/session")
        self.assertEqual(request.headers[desktop.TOKEN_HEADER], self.TOKEN)
        self.assertEqual(request.method, "GET")
        self.assertFalse(factory.call_args.kwargs["trust_env"])
        self.assertFalse(factory.call_args.kwargs["follow_redirects"])

    def test_send_uses_existing_csrf_and_exactly_one_request(self):
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"}, json={"text": "真实回复"}))
        self.assertEqual(self.client.send("  合成请求  ", self.CSRF), "真实回复")
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "http://127.0.0.1:8877/companion/chat")
        self.assertEqual(parse_qs(request.content.decode()), {"text": ["合成请求"], "csrf": [self.CSRF]})

    def test_care_uses_the_same_panel_owner_and_does_not_retry(self):
        result = {'care': {'warmth': .26, 'updated_at': 100},
                  'care_event': {'id': 'care-1', 'kind': 'care', 'action': 'feed', 'result': 'gain'}}
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: '1'}, json=result))
        self.assertEqual(self.client.care('feed', self.CSRF), result)
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(str(request.url), 'http://127.0.0.1:8877/companion/care')
        self.assertEqual(parse_qs(request.content.decode()), {'action': ['feed'], 'csrf': [self.CSRF]})

    def test_voice_ticket_is_explicit_and_credentials_stay_in_headers(self):
        ticket = "synthetic-single-use-voice-ticket-0123456"
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"},
                                     json={"ticket": ticket, "stream": "/companion/voice/stream"}))
        self.assertEqual(self.client.voice_start(self.CSRF)["ticket"], ticket)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(parse_qs(request.content.decode()), {"csrf": [self.CSRF]})
        url, headers = self.client.voice_endpoint(ticket)
        self.assertEqual(url, "ws://127.0.0.1:8877/companion/voice/stream")
        self.assertEqual(headers, {desktop.TOKEN_HEADER: self.TOKEN, "X-Voice-Ticket": ticket})
        self.assertNotIn(ticket, url)
        self.assertNotIn(self.TOKEN, url)

    def test_voice_ticket_never_accepts_a_server_supplied_destination(self):
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"},
                                     json={"ticket": "synthetic-ticket-0123456789", "stream": "https://outside.example"}))
        with self.assertRaises(desktop.CompanionError):
            self.client.voice_start(self.CSRF)
        for ticket in ("", "short", "x" * 257, "x" * 25 + "\r\n", "语" * 25):
            with self.subTest(ticket=ticket), self.assertRaises(desktop.CompanionError):
                self.client.voice_endpoint(ticket)

    def test_continuous_voice_mode_is_explicit_in_the_ticket_request(self):
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"},
                                     json={"ticket": "synthetic-ticket-0123456789", "stream": "/companion/voice/stream"}))
        self.client.voice_start(self.CSRF, mode="continuous")
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "http://127.0.0.1:8877/companion/voice/start")
        self.assertEqual(parse_qs(request.content.decode()), {"csrf": [self.CSRF], "mode": ["continuous"]})

    def test_invalid_voice_mode_is_rejected_before_io(self):
        with patch.object(desktop.httpx, "Client") as client:
            with self.assertRaises(desktop.CompanionError):
                self.client.voice_start(self.CSRF, mode="automatic")
        client.assert_not_called()

    def test_voice_unavailable_keeps_the_local_configuration_explanation(self):
        self.transport(httpx.Response(503, headers={desktop.PANEL_ID_HEADER: "1"},
                                     json={"error": "请先在语音服务中配置 API Key。"}))
        with self.assertRaisesRegex(desktop.CompanionError, "配置 API Key"):
            self.client.voice_start(self.CSRF)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_send_result_is_not_retried_or_dumped(self):
        self.transport(httpx.ReadTimeout("PRIVATE " + self.TOKEN))
        with self.assertRaises(desktop.CompanionError) as raised:
            self.client.send("只发一次", self.CSRF)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("结果未确认", str(raised.exception))
        self.assertIn("没有自动重试", str(raised.exception))
        self.assertNotIn(self.TOKEN, str(raised.exception))

    def test_redirects_and_invalid_schema_do_not_trigger_another_request(self):
        for response in (httpx.Response(302, headers={"Location": "https://outside.example/"}),
                         httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"}, json={"available": True}),
                         httpx.Response(200, json={"csrf": self.CSRF, "available": True})):
            with self.subTest(status=response.status_code):
                self.calls.clear()
                self.transport(response)
                with self.assertRaises(desktop.CompanionError):
                    self.client.session()
                self.assertEqual(len(self.calls), 1)

    def test_invalid_length_csrf_or_management_targets_are_rejected_before_io(self):
        with patch.object(desktop.httpx, "Client") as factory:
            for text, csrf in (("", self.CSRF), ("x" * 2001, self.CSRF), ("hello", "")):
                with self.subTest(length=len(text)):
                    with self.assertRaises(desktop.CompanionError):
                        self.client.send(text, csrf)
        factory.assert_not_called()
        for path in ("https://outside.example/", "/runtime/stop", "/runtime/start"):
            with self.assertRaises(ValueError):
                self.client.management_url(path)
        url = self.client.management_url("/setup")
        self.assertEqual(urlsplit(url).netloc, "127.0.0.1:8877")
        self.assertEqual(parse_qs(urlsplit(url).query), {"token": [self.TOKEN]})


class PetPresenceClientTest(PanelClientTest):
    def test_presence_is_one_read_with_an_optional_since_cursor(self):
        body = {"bot": "ready", "events": [], "mood": 0.1}
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"}, json=body))
        self.assertEqual(self.client.presence(), body)
        self.assertEqual(self.client.presence(1793925000.5), body)
        self.assertEqual([str(r.url) for r in self.calls],
                         ["http://127.0.0.1:8877/companion/presence",
                          "http://127.0.0.1:8877/companion/presence?since=1793925000.500"])
        self.assertTrue(all(r.headers[desktop.TOKEN_HEADER] == self.TOKEN for r in self.calls))

    def test_cue_posts_action_text_and_csrf_once(self):
        self.transport(httpx.Response(200, headers={desktop.PANEL_ID_HEADER: "1"}, json={"queued": "abc"}))
        self.assertEqual(self.client.cue("cheer", "测试全过了", self.CSRF), "abc")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(parse_qs(self.calls[0].content.decode()),
                         {"action": ["cheer"], "text": ["测试全过了"], "csrf": [self.CSRF]})


class PetCueCommandTest(DesktopTest):
    def test_cli_cue_reaches_only_a_running_panel_and_never_starts_one(self):
        with patch.object(cli, "load_config", return_value=self.cfg), \
             patch.object(desktop, "cue", return_value=0) as cue:
            self.assertEqual(cli.main(["--directory", str(self.root), "pet-cue", "cheer", "--say", "跑完了"]), 0)
        cue.assert_called_once_with(self.cfg, action="cheer", text="跑完了", port=8765)
        self.token()
        with patch.object(desktop, "_panel_ready") as probe:
            for action, text in (("dance", ""), ("", "  "), ("wave", "长" * 121)):
                self.assertEqual(desktop.cue(self.cfg, action=action, text=text), 2)
            probe.assert_not_called()
        self.assertIn("可用：wave", self.output.getvalue())
        with patch.object(desktop, "_panel_ready", return_value=False), \
             patch.object(desktop.subprocess, "Popen") as popen:
            self.assertEqual(desktop.cue(self.cfg, action="wave"), 1)
        popen.assert_not_called()
        with patch.object(desktop, "_panel_ready", return_value=True), \
             patch.object(desktop.PanelClient, "session", return_value={"csrf": "c" * 20}), \
             patch.object(desktop.PanelClient, "cue", return_value="id") as send:
            self.assertEqual(desktop.cue(self.cfg, action="wave", text="嗨"), 0)
        send.assert_called_once_with("wave", "嗨", "c" * 20)
