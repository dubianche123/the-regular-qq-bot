"""Private loopback chat bridge; only the running bot owns conversation state."""
from __future__ import annotations

import asyncio
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid

import aiohttp
from aiohttp import web

import expression
import storage
from regular.panel_runtime import _pid_alive

FILENAME = "local-chat.json"
MAX_TEXT_CHARS = 2000
TURN_TIMEOUT_SECONDS = 70
CLIENT_TIMEOUT_SECONDS = 75
STATUS_MAX_AGE_SECONDS = 120
_ACTION = re.compile(r"^\s*[〔【\[［]\s*(?:画|动图|语音)\s*[〕】\]］]", re.MULTILINE)
_INTERNAL = re.compile(r"<(CMD|DIGEST)>.*?(?:</\1>|$)", re.DOTALL)


class LocalChatError(RuntimeError):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise LocalChatError(400, "请先输入想说的话。")
    if len(value) > MAX_TEXT_CHARS:
        raise LocalChatError(400, f"一条消息最多 {MAX_TEXT_CHARS} 个字符。")
    return value.strip()


def strip_internal(body):
    """Remove whole internal blocks before generic markup cleanup loses their tags."""
    return _INTERNAL.sub("", str(body or "")).strip()


def text_reply(body):
    """Strip internal protocol; an action proposal must never look executed locally."""
    body = strip_internal(body)
    said = expression.parse(body)
    if said.kind == "pass":
        return "这句我先安静陪你。"
    if said.remind or said.kind in ("image", "voice") or _ACTION.search(body):
        return ("这里是本机文字聊天，这轮没有执行画图、语音或群提醒。"
                "这些功能请在 QQ 里使用；修改设置和管理运行请打开面板。")
    return said.text


class LocalReply:
    """A local sink with no QQ client, platform identity or group destination."""
    def __init__(self):
        self.id = "local-" + uuid.uuid4().hex
        self.text = None

    async def reply(self, *, content, msg_type=0, **kwargs):
        if msg_type != 0 or set(kwargs) - {"msg_seq"}:
            raise LocalChatError(400, "本机聊天只支持文字回复。")
        self.text = content
        return {"id": self.id}


def _descriptor_path(cfg):
    return Path(cfg.DATA_DIR) / FILENAME


def _read_descriptor(cfg):
    path = _descriptor_path(cfg)
    try:
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise LocalChatError(503, "本机对话连接文件的权限不正确，请重新启动机器人。")
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LocalChatError(503, "机器人尚未提供本机对话，请先启动或更新机器人。") from None
    if (not isinstance(descriptor, dict) or descriptor.get("schema") != 1
            or type(descriptor.get("pid")) is not int or descriptor["pid"] <= 0
            or type(descriptor.get("port")) is not int or not 1 <= descriptor["port"] <= 65535
            or not isinstance(descriptor.get("token"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", descriptor["token"])):
        raise LocalChatError(503, "本机对话连接信息无效，请重新启动机器人。")
    return descriptor


def _endpoint(cfg):
    descriptor = _read_descriptor(cfg)
    try:
        snapshot = json.loads(Path(cfg.STATUS_FILE).read_text(encoding="utf-8"))
        age = time.time() - float(snapshot["updated_at"])
        valid = (snapshot.get("schema_version") == 1
                 and type(snapshot.get("pid")) is int
                 and snapshot["pid"] == descriptor["pid"]
                 and math.isfinite(age) and -5 <= age <= STATUS_MAX_AGE_SECONDS
                 and snapshot.get("lifecycle") not in ("stopped", "error"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid or not _pid_alive(descriptor["pid"]):
        raise LocalChatError(503, "本机对话的运行状态已失效，请检查机器人是否仍在运行。")
    return descriptor


def bridge_status(cfg):
    """Safe public status; never expose the transport token or its file path."""
    try:
        _endpoint(cfg)
    except LocalChatError as error:
        return {"available": False, "reason": error.message}
    return {"available": True, "reason": ""}


async def send_chat(cfg, text):
    """Make one bounded request to this instance's bot; never start a worker."""
    text = _text(text)
    descriptor = _endpoint(cfg)
    url = f"http://127.0.0.1:{descriptor['port']}/chat"
    try:
        async with aiohttp.ClientSession(trust_env=False,
                timeout=aiohttp.ClientTimeout(total=CLIENT_TIMEOUT_SECONDS, connect=3)) as client:
            async with client.post(url, json={"text": text}, allow_redirects=False,
                    headers={"Authorization": "Bearer " + descriptor["token"]}) as response:
                if response.status == 409:
                    raise LocalChatError(409, "上一句还在回复，请稍等。")
                if response.status in (401, 403):
                    raise LocalChatError(503, "本机对话授权已失效，请刷新运行状态。")
                if response.status != 200:
                    raise LocalChatError(504 if response.status == 504 else 503,
                                         "本机对话暂时不可用，请检查机器人运行状态。")
                body = await response.json()
                if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                    raise LocalChatError(503, "未收到有效的本机回复；没有自动重试。")
                return {"text": body["text"]}
    except asyncio.TimeoutError:
        raise LocalChatError(504, "等待本机回复超时，结果未确认；没有自动重试。") from None
    except (aiohttp.ClientError, ValueError):
        raise LocalChatError(503, "本机对话连接中断，结果未确认；没有自动重试。") from None


class LocalChatBridge:
    def __init__(self, cfg, reply):
        self.cfg, self.reply = cfg, reply
        self.token = secrets.token_urlsafe(32)
        self.runner = None
        self.descriptor = None
        self.busy = False

    async def _chat(self, request):
        supplied = request.headers.get("Authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, ("Bearer " + self.token).encode("utf-8")):
            raise web.HTTPUnauthorized(text="本机对话需要授权。")
        try:
            if request.content_type != "application/json":
                raise LocalChatError(400, "请发送文字消息。")
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"text"}:
                raise LocalChatError(400, "本机对话只接受文字消息。")
            text = _text(payload["text"])
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text="消息格式无效。") from None
        except LocalChatError as error:
            raise web.HTTPBadRequest(text=error.message) from None
        if self.busy:
            raise web.HTTPConflict(text="上一句还在回复，请稍等。")
        self.busy = True
        try:
            reply = await asyncio.wait_for(self.reply(text), timeout=TURN_TIMEOUT_SECONDS)
            if not isinstance(reply, str):
                raise web.HTTPServiceUnavailable(text="未收到有效的本机回复。")
            return web.json_response({"text": reply}, headers={"Cache-Control": "no-store"})
        except asyncio.TimeoutError:
            raise web.HTTPGatewayTimeout(text="本机对话超时，没有自动重试。") from None
        except LocalChatError as error:
            return web.Response(status=error.status, text=error.message)
        except web.HTTPException:
            raise
        except Exception:
            raise web.HTTPServiceUnavailable(text="本机对话暂时不可用。") from None
        finally:
            self.busy = False

    async def start(self):
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_post("/chat", self._chat)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        self.descriptor = {"schema": 1, "pid": os.getpid(), "port": port, "token": self.token}
        path = _descriptor_path(self.cfg)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        storage.atomic_write_json(str(path), self.descriptor)
        os.chmod(path, 0o600)

    async def close(self):
        try:
            if self.runner is not None:
                await self.runner.cleanup()
        finally:
            if self.descriptor is not None:
                path = _descriptor_path(self.cfg)
                try:
                    if json.loads(path.read_text(encoding="utf-8")) == self.descriptor:
                        path.unlink()
                except (OSError, ValueError):
                    pass


async def serve(cfg, reply, *, sleep=asyncio.sleep):
    bridge = LocalChatBridge(cfg, reply)
    try:
        await bridge.start()
        while True:
            await sleep(30)
    finally:
        await bridge.close()
