"""桌宠优先入口：复用本机面板，或在当前终端管理一个仅本机 UI 服务。"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
import webbrowser

import httpx

from panel import TOKEN_FILE, TOKEN_HEADER
from regular.instance_settings import child_environment, instance_dir

PANEL_ID_HEADER = "X-Regular-Panel"


class CompanionError(RuntimeError):
    """Safe local-client error with no credentials or response dump."""


class Reply(str):
    """Her words; .action is at most one proposal the maintainer still has to confirm."""
    action = None


class PanelClient:
    """Only fixed local status/chat/settings destinations are available."""
    def __init__(self, port, token):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid local port")
        self.base, self._token = f"http://127.0.0.1:{port}", token

    def _request(self, method, path, data=None, *, accept=None):
        headers = {TOKEN_HEADER: self._token, **({"Accept": accept} if accept else {})}
        try:
            with httpx.Client(timeout=httpx.Timeout(80.0 if method == "POST" and path == "/companion/chat" else 2.0, connect=2.0),
                              trust_env=False, follow_redirects=False) as client:
                response = client.request(method, self.base + path, headers=headers, data=data)
        except httpx.HTTPError:
            raise CompanionError("连接中断或等待超时，操作结果未确认；没有自动重试。"
                                 if method == "POST" else "本机面板暂时无法连接。") from None
        if response.is_redirect:
            raise CompanionError("本机请求发生跳转，已停止，没有继续发送凭据。")
        if response.status_code != 200:
            if (path == "/companion/voice/start" and response.status_code in (409, 503)
                    and response.headers.get(PANEL_ID_HEADER) == "1"):
                try:
                    message = response.json().get("error")
                except (ValueError, AttributeError):
                    message = None
                if isinstance(message, str) and 0 < len(message) <= 300:
                    raise CompanionError(message)
            if response.status_code in (401, 403):
                raise CompanionError("本机会话已更新，请刷新连接后再发送；没有自动重试。")
            if response.status_code == 409:
                raise CompanionError("上一句还在回复，请稍等。")
            raise CompanionError("本机对话暂时不可用，请检查运行状态；没有自动重试。")
        if response.headers.get(PANEL_ID_HEADER) != "1":
            raise CompanionError("这个端口不是已认证的老群友面板。")
        try:
            body = response.json()
        except ValueError:
            raise CompanionError("未收到有效本机响应；没有自动重试。") from None
        if not isinstance(body, dict):
            raise CompanionError("未收到有效本机响应；没有自动重试。")
        return body

    def session(self):
        body = self._request("GET", "/companion/session")
        csrf = body.get("csrf")
        if (not isinstance(csrf, str) or not 16 <= len(csrf) <= 512
                or type(body.get("available")) is not bool):
            raise CompanionError("面板尚未提供桌宠会话，请先更新并刷新本机面板。")
        return {"csrf": csrf, "available": body["available"],
                "reason": str(body.get("reason") or "")[:300],
                "qq_connected": bool(body.get("qq_connected"))}

    def send(self, text, csrf):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise CompanionError("请发送 1–2000 个字符。")
        if not isinstance(csrf, str) or not 16 <= len(csrf) <= 512:
            raise CompanionError("请先刷新本机连接。")
        body = self._request("POST", "/companion/chat", {"text": text.strip(), "csrf": csrf})
        if not isinstance(body.get("text"), str):
            raise CompanionError("未收到有效回复，结果未确认；没有自动重试。")
        from regular.local_chat import proposal
        reply = Reply(body["text"])
        reply.action = proposal(body.get("action"))
        return reply

    def voice_status(self):
        return self._request("GET", "/companion/voice/status")

    def voice_start(self, csrf, *, mode="ptt"):
        if not isinstance(csrf, str) or not 16 <= len(csrf) <= 512:
            raise CompanionError("请先刷新本机连接。")
        if mode not in ("ptt", "continuous"):
            raise CompanionError("语音模式不正确。")
        form = {"csrf": csrf}
        if mode != "ptt":
            form["mode"] = mode
        body = self._request("POST", "/companion/voice/start", form)
        ticket = body.get("ticket")
        if (not isinstance(ticket, str) or not 24 <= len(ticket) <= 256
                or not ticket.isascii() or not all(c.isalnum() or c in "_-" for c in ticket)
                or body.get("stream") != "/companion/voice/stream"):
            raise CompanionError("未收到有效语音连接，请重新开始。")
        return body

    def voice_endpoint(self, ticket):
        if (not isinstance(ticket, str) or not 24 <= len(ticket) <= 256
                or not ticket.isascii() or not all(c.isalnum() or c in "_-" for c in ticket)):
            raise CompanionError("语音连接已失效，请重新开始。")
        return (self.base.replace("http://", "ws://", 1) + "/companion/voice/stream",
                {TOKEN_HEADER: self._token, "X-Voice-Ticket": ticket})

    def toggle(self, flag, enabled, csrf):
        """The same switch as on the capabilities page; only after the maintainer confirmed it."""
        body = self._request("POST", "/capabilities/toggle",
                             {"flag": flag, "enabled": "true" if enabled else "false", "csrf": csrf},
                             accept="application/json")
        if body.get("ok") is not True:
            raise CompanionError("开关没保存上，请到「能力与服务」里再试。")
        return str(body.get("timing") or "")

    def presence(self, since=None):
        """Content-free agent state; an old panel without the route reads as unknown."""
        path = "/companion/presence" + ("" if since is None else "?" + urlencode({"since": f"{since:.3f}"}))
        return self._request("GET", path)

    def care(self, action, csrf):
        if action not in ("pat", "feed"):
            raise ValueError("unsupported local care action")
        body = self._request("POST", "/companion/care", {"action": action, "csrf": csrf})
        event = body.get("care_event")
        if (not isinstance(body.get("care"), dict) or not isinstance(event, dict)
                or event.get("action") != action
                or event.get("result") not in ("gain", "cooldown", "full", "fed_full")):
            raise CompanionError("这次照顾还没有确认，请等连接恢复后再看看。")
        return body

    def cue(self, action, text, csrf):
        body = self._request("POST", "/companion/cue", {"action": action or "", "text": text or "", "csrf": csrf})
        if not isinstance(body.get("queued"), str):
            raise CompanionError("桌宠没有收下这条提示。")
        return body["queued"]

    def management_url(self, path):
        if path not in ("/", "/setup", "/capabilities", "/checkup", "/iterate"):
            raise ValueError("unsupported local destination")
        return self.base + path + "?" + urlencode({"token": self._token})


def _open_browser(url):
    print("改用紧凑网页；浏览器模式不是系统桌面悬浮窗。")
    try:
        opened = webbrowser.open(url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print("无法打开系统浏览器，请在已经打开的本机面板中进入桌宠页面。", file=sys.stderr)
        return 1
    return 0


def _read_token(cfg):
    try:
        token = (Path(cfg.DATA_DIR) / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    return token if 24 <= len(token) <= 512 and not any(char.isspace() for char in token) else ""


def _panel_ready(port, token):
    if not token:
        return False
    try:
        with httpx.Client(timeout=2.0, trust_env=False, follow_redirects=False) as client:
            response = client.get(f"http://127.0.0.1:{port}/companion?mode=pet",
                                  headers={TOKEN_HEADER: token})
    except httpx.ConnectError:
        return False
    except httpx.HTTPError:
        raise CompanionError("本机面板未及时响应，请检查正在运行的面板；没有重复启动。") from None
    if response.is_redirect:
        raise CompanionError("面板请求发生跳转，已停止连接；没有继续发送凭据。")
    if response.status_code != 200 or response.headers.get(PANEL_ID_HEADER) != "1":
        raise CompanionError("这个端口不是已认证的同实例面板。请关闭冲突服务，或使用 pet --port 指定其他本机端口。")
    return True


def _stop_owned_panel(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _ensure_panel(cfg, port):
    """Return (token, owned child); never spawn the QQ runtime here."""
    token = _read_token(cfg)
    if _panel_ready(port, token):
        return token, None
    root = instance_dir(cfg)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = Path(cfg.DATA_DIR) / "desktop-panel.log"
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(log, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "regular", "--directory", str(root),
             "panel", "--port", str(port)],
            cwd=str(root), env=child_environment(cfg), stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, shell=False)
    print("正在准备本机设置服务；尚未连接 QQ，也不会自动启动机器人。", flush=True)
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CompanionError("本机设置服务未能启动。请检查端口是否被占用；详细记录在实例 data/desktop-panel.log。")
            token = _read_token(cfg)
            if _panel_ready(port, token):
                return token, process
            time.sleep(0.1)
        raise CompanionError("本机设置服务启动超时；已结束本次启动，请检查实例 data/desktop-panel.log。")
    except BaseException:
        _stop_owned_panel(process)
        raise


def _show_pet(port, token, browser, preferences_path=None):
    url = f"http://127.0.0.1:{port}/companion?" + urlencode({"mode": "pet", "token": token})
    if browser:
        return _open_browser(url)
    if (sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("QT_QPA_PLATFORM") not in ("offscreen", "minimal")):
        print("当前没有桌面显示环境，改用浏览器入口。")
        return _open_browser(url)
    try:
        native = importlib.import_module("regular.desktop_pet")
    except ImportError:
        print('未安装原生桌宠组件。在源码目录运行 python -m pip install ".[desktop]" 后可启用原生窗口。')
        return _open_browser(url)
    except Exception as error:
        print(f"原生组件暂不可用（{type(error).__name__}）。")
        return _open_browser(url)
    try:
        print("鼠标靠近老群友可聊天、查看通知或打开设置。关闭人物不会停止面板或机器人。")
        return native.run(port=port, token=token, preferences_path=preferences_path)
    except Exception as error:
        # Backend errors may include the authenticated URL. Do not echo them.
        print(f"原生窗口暂不可用（{type(error).__name__}）。")
        return _open_browser(url)


def cue(cfg, *, action="", text="", port=8765):
    """Ask an already running pet to act or say one line. Never starts a panel or the bot."""
    from regular.pet_brain import CUE_ACTIONS
    if action and action not in CUE_ACTIONS:
        print("未知动作。可用：" + " ".join(CUE_ACTIONS), file=sys.stderr)
        return 2
    if not action and not text.strip():
        print("请给出动作，或用 --say 说一句话。", file=sys.stderr)
        return 2
    if len(" ".join(text.split())) > 120:
        print("一句话最多 120 个字符。", file=sys.stderr)
        return 2
    token = _read_token(cfg)
    try:
        if not _panel_ready(port, token):
            print("本机面板没有运行，桌宠收不到提示。先运行 regular pet。", file=sys.stderr)
            return 1
        client = PanelClient(port, token)
        client.cue(action, text, client.session()["csrf"])
    except CompanionError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("已交给桌宠。")
    return 0


def run(cfg, *, port=8765, browser=False):
    """Open the pet first. Own only a panel child, never an existing process."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        print("面板端口必须在 1 到 65535 之间。", file=sys.stderr)
        return 1
    # Once the source launcher has built the branded app, CLI reopening uses it
    # too. Never recurse from its frozen executable or route browser requests
    # to a native bundle. Missing builds retain the ordinary development path.
    if sys.platform == "darwin" and not getattr(sys, "frozen", False) and not browser:
        from regular.desktop_bundle import bundle_current
        output = instance_dir(cfg) / "desktop"
        executable = output / "老群友.app/Contents/MacOS/老群友"
        if bundle_current(Path(__file__).resolve().parent.parent, output):
            return subprocess.run([str(executable), "--directory", str(instance_dir(cfg)), "pet", "--port", str(port)],
                                  env=child_environment(cfg), shell=False).returncode
    owned = None
    try:
        token, owned = _ensure_panel(cfg, port)
        result = _show_pet(port, token, browser, preferences_path=Path(cfg.DATA_DIR) / "desktop-pet.json")
        if result == 0 and owned is not None:
            # The panel may now own a bot explicitly started by the user. Keep
            # supervising it in this terminal, rather than kill it with the pet
            # or detach an orphan server. An external panel is never waited on.
            print("本机设置服务仍在此终端运行。关闭人物不会停止机器人；按 Ctrl+C 退出本次服务。", flush=True)
            result = owned.wait() or 0
        return result
    except CompanionError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n正在退出本次本机服务；已有外部面板和机器人不受影响。", flush=True)
        return 130
    finally:
        _stop_owned_panel(owned)
