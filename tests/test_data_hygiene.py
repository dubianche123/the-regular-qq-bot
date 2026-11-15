# -*- coding: utf-8 -*-
"""本机数据的卫生边界（2026-11-15）。

  · 聊天原话只进带保留期的归档，日志只记长度：日志不轮转的那份（launchd 的 service.log）不再抄一遍；
  · 本进程和常驻服务新建的文件只给本人读写（umask 077）；
  · 面板被桌宠或服务拉起时，stdout 是日志文件，带 token 的地址不写进去。
"""
import io
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
import panel
from delivery import Sender
from regular import cli, service


class LogContentTest(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_log_records_length_not_the_reply(self):
        m = SimpleNamespace(id="synthetic-source", group_openid="synthetic-group",
                            reply=AsyncMock(return_value={"id": "receipt"}))
        with self.assertLogs("qqbot", level="INFO") as logs:
            await Sender().reply(m, "一句只属于群里的悄悄话")
        joined = "\n".join(logs.output)
        self.assertIn("已送达", joined)
        self.assertNotIn("悄悄话", joined)
        self.assertIn("11 字", joined)

    def test_service_mode_does_not_copy_the_log_into_stdout(self):
        root = logging.getLogger()
        before = list(root.handlers)
        # botpy 一 import 就 basicConfig()，根 logger 上会先有一个打到 stderr 的控制台 handler
        console = logging.StreamHandler()
        root.addHandler(console)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "LOG_FILE", os.path.join(tmp, "bot.log")), \
                patch("sys.stdout.isatty", return_value=False):
            try:
                bot.setup_logging()
                self.assertNotIn(console, root.handlers, "service.log 不该再收到一份日志")
                added = [h for h in root.handlers if h not in before]
                self.assertEqual([type(h).__name__ for h in added], ["RotatingFileHandler"])
            finally:
                for handler in root.handlers[:]:
                    if handler not in before:
                        root.removeHandler(handler)
                        handler.close()
                for handler in before:
                    if handler not in root.handlers:
                        root.addHandler(handler)


class PrivateFilesTest(unittest.TestCase):
    def test_cli_makes_new_files_owner_only(self):
        previous = os.umask(0o022)
        try:
            with patch("regular.service.main", return_value=0):
                cli.main(["--directory", "/tmp/unused", "service", "launchd", "--output", "/tmp/unused.plist"])
            self.assertEqual(os.umask(0o022), 0o077)
        finally:
            os.umask(previous)

    def test_launchd_jobs_create_owner_only_files(self):
        self.assertEqual(service.launchd("/tmp/instance", "/usr/bin/python3")["Umask"], 0o077)
        self.assertEqual(service.launchd_nightly("/tmp/instance", "/usr/bin/python3")["Umask"], 0o077)
        self.assertIn("UMask=0077", service.systemd("/tmp/instance", "/usr/bin/python3"))


class PanelTokenTest(unittest.TestCase):
    def test_panel_started_into_a_log_does_not_print_its_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(DATA_DIR=tmp)
            out = io.StringIO()          # not a TTY, like the pet's desktop-panel.log
            with patch.object(panel, "create_app", return_value=object()), \
                    patch.object(panel.web, "run_app"), patch("sys.stdout", out):
                panel.run(cfg)
            token = panel.load_or_create_token(cfg)
        self.assertNotIn(token, out.getvalue())
        self.assertIn("data/panel_token", out.getvalue())


if __name__ == "__main__":
    unittest.main()
