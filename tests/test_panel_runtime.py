"""本机面板只管理自己启动的固定机器人进程，不碰外部服务。"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from regular import instance_settings, panel_runtime
from test_instance_settings import configuration, complete_values


class Child:
    def __init__(self, *, timeout=False):
        self.pid = 9876
        self.code = None
        self.terminated = self.killed = 0
        self.timeout = timeout

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1

    def wait(self, timeout):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("fixed bot", timeout)
        self.code = 0
        return self.code


class PanelRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg = configuration(self.root)
        self.runtime = panel_runtime.PanelRuntime(self.cfg)

    def ready(self):
        instance_settings.save_settings(self.cfg, complete_values())

    def health(self, *, pid=9876, age=0, **updates):
        path = Path(self.cfg.STATUS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "pid": pid, "updated_at": time.time() - age,
                                    "lifecycle": "ready", "gateway_connected": True, **updates}))

    async def test_incomplete_configuration_cannot_start_a_child(self):
        with patch.object(panel_runtime.subprocess, "Popen") as spawn:
            with self.assertRaises(ValueError):
                self.runtime.start()
        spawn.assert_not_called()
        self.assertFalse(self.runtime.log_file.exists())

    async def test_fixed_start_is_idempotent_and_waits_for_actual_matching_status(self):
        self.ready()
        child = Child()
        with patch.object(panel_runtime.subprocess, "Popen", return_value=child) as spawn:
            first = self.runtime.start()
            second = self.runtime.start()
        spawn.assert_called_once()
        args, kw = spawn.call_args
        self.assertEqual(args[0], [sys.executable, "-m", "regular", "--directory", str(self.root), "run"])
        self.assertFalse(kw["shell"])
        self.assertEqual(kw["cwd"], str(self.root))
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["stderr"], subprocess.STDOUT)
        self.assertEqual(kw["env"]["REGULAR_HOME"], str(self.root))
        self.assertTrue(first["owned"] and second["running"])
        self.assertFalse(first["gateway_connected"])
        self.assertEqual(first["lifecycle"], "starting")
        self.assertEqual(stat.S_IMODE(self.runtime.log_file.stat().st_mode), 0o600)
        self.health(pid=111)
        self.assertFalse(self.runtime.snapshot()["gateway_connected"])
        self.health(pid=child.pid)
        self.assertTrue(self.runtime.snapshot()["gateway_connected"])
        self.health(pid=child.pid, age=121)
        self.assertFalse(self.runtime.snapshot()["gateway_connected"])
        await self.runtime.close()

    async def test_existing_external_process_cannot_be_started_or_stopped_here(self):
        self.ready()
        self.health(pid=1234)
        with patch.object(panel_runtime, "_pid_alive", return_value=True), \
             patch.object(panel_runtime.subprocess, "Popen") as spawn:
            state = self.runtime.snapshot()
            self.assertTrue(state["external"] and state["running"])
            with self.assertRaises(ValueError):
                self.runtime.start()
            with self.assertRaises(ValueError):
                await self.runtime.stop()
            await self.runtime.close()
        spawn.assert_not_called()

    async def test_stale_but_living_external_process_is_not_duplicated(self):
        self.ready()
        self.health(pid=1234, age=500)
        with patch.object(panel_runtime, "_pid_alive", return_value=True):
            state = self.runtime.snapshot()
            self.assertTrue(state["stale"])
            self.assertFalse(state["gateway_connected"])
            with self.assertRaises(ValueError):
                self.runtime.start()

    async def test_dead_snapshot_does_not_block_a_new_child(self):
        self.ready()
        self.health(pid=1234)
        child = Child()
        with patch.object(panel_runtime, "_pid_alive", return_value=False), \
             patch.object(panel_runtime.subprocess, "Popen", return_value=child) as spawn:
            self.assertTrue(self.runtime.start()["owned"])
        spawn.assert_called_once()
        await self.runtime.close()

    async def test_start_environment_reads_new_file_instead_of_old_imported_values(self):
        self.ready()
        self.cfg.EXTERNAL_ENV_KEYS = frozenset({"SSL_VERIFY"})
        self.cfg.DOTENV_KEYS_AT_IMPORT = frozenset({"QQ_APP_SECRET", "OPENAI_API_KEY", "REMOVED_SETTING"})
        child = Child()
        with patch.dict(os.environ, {"QQ_APP_SECRET": "old-secret", "OPENAI_API_KEY": "old-key",
                                     "REMOVED_SETTING": "old", "SSL_VERIFY": "true"}), \
             patch.object(panel_runtime.subprocess, "Popen", return_value=child) as spawn:
            self.runtime.start()
        env = spawn.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("QQ_APP_SECRET", env)
        self.assertNotIn("REMOVED_SETTING", env)
        self.assertEqual(env["SSL_VERIFY"], "true")
        await self.runtime.close()

    async def test_graceful_stop_and_shutdown_only_terminate_owned_child_once(self):
        child = Child()
        self.runtime.process = child
        result = await self.runtime.stop()
        self.assertFalse(result["running"])
        self.assertEqual(child.terminated, 1)
        self.assertEqual(child.killed, 0)
        await self.runtime.close()
        self.assertEqual(child.terminated, 1)

    async def test_timeout_kills_only_the_owned_child_with_bounded_waits(self):
        child = Child(timeout=True)
        self.runtime.process = child
        state = await self.runtime.stop()
        self.assertEqual(child.terminated, 1)
        self.assertEqual(child.killed, 1)
        self.assertFalse(state["running"])
        self.assertEqual(state["exit_code"], 0)

    async def test_known_exited_child_can_restart_despite_its_previous_ready_snapshot(self):
        self.ready()
        old = Child()
        old.code = 1
        self.runtime.process = old
        self.health(pid=old.pid)
        new = Child()
        with patch.object(panel_runtime, "_pid_alive", return_value=True), \
             patch.object(panel_runtime.subprocess, "Popen", return_value=new) as spawn:
            self.assertTrue(self.runtime.start()["owned"])
        spawn.assert_called_once()
        await self.runtime.close()
