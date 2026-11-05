"""Only start/stop the fixed bot child owned by this local panel."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from regular.instance_settings import child_environment, instance_dir, public_settings


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) may terminate a Windows process; only query a handle.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class PanelRuntime:
    def __init__(self, cfg):
        self.cfg = cfg
        self.process = None
        self.log_file = Path(cfg.DATA_DIR) / "panel-start.log"

    def snapshot(self):
        try:
            status = json.loads(Path(self.cfg.STATUS_FILE).read_text(encoding="utf-8"))
            if not isinstance(status, dict) or status.get("schema_version") != 1:
                status = {}
            age = max(0, time.time() - float(status.get("updated_at") or 0))
        except (OSError, ValueError, TypeError):
            status, age = {}, float("inf")
        code = self.process.poll() if self.process is not None else None
        owned = self.process is not None and code is None
        source_pid = status.get("pid")
        known_exited = self.process is not None and code is not None and source_pid == self.process.pid
        external = not owned and not known_exited and _pid_alive(source_pid)
        matching = (owned and source_pid == self.process.pid) or external
        fresh = bool(status) and age <= 120 and matching
        return {"owned": owned, "running": owned or external, "external": bool(external),
                "pid": self.process.pid if owned else source_pid if external else None,
                "exit_code": code, "gateway_connected": bool(fresh and status.get("gateway_connected")),
                "stale": not fresh, "updated_at": status.get("updated_at"),
                "lifecycle": status.get("lifecycle", "unknown") if fresh else "starting" if owned else "stopped",
                "log_file": str(self.log_file)}

    def start(self):
        state = self.snapshot()
        if state["external"]:
            raise ValueError("此实例已有其他进程在运行；本面板不会重启或停止它。")
        if state["owned"]:
            return state
        if not public_settings(self.cfg)["ready"]:
            raise ValueError("请先补齐 QQ 和回复模型的必填配置。")
        self.log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.log_file, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "regular", "--directory", str(instance_dir(self.cfg)), "run"],
                cwd=str(instance_dir(self.cfg)), env=child_environment(self.cfg),
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, shell=False)
        return self.snapshot()

    async def stop(self):
        if self.process is None:
            raise ValueError("此机器人不是本面板启动的，不能在这里停止。")
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                return self.snapshot()
            try:
                await asyncio.to_thread(self.process.wait, timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                await asyncio.to_thread(self.process.wait, timeout=3)
        return self.snapshot()

    async def close(self):
        if self.process is not None and self.process.poll() is None:
            await self.stop()
