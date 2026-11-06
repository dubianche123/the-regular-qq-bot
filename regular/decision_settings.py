"""本地保存的判断 API 配置（OpenAI 原生 GPT Decisions，不是 /chat/completions）。

写到 data_dir/decision_api.json（0600）。判断协议与聊天模型是两条调用链，
endpoint / model 仍然独立；但凭据不再复制第二份：
默认 credential="shared"，运行时从聊天设置那把 OPENAI_API_KEY 动态读取，
换 Key 只改一处。只有判断接口指向另一家服务时才保存专用 Key。
错误信息一律通用化 —— 任何校验/读写失败都不许回显原始输入或密钥片段。
"""
import json
import os
from urllib.parse import urlsplit

import storage

DEFAULT_ENDPOINT = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"
FILENAME = "decision_api.json"
_MAX_KEY_CHARS = 4096
_MAX_MODEL_CHARS = 200
_MAX_ENDPOINT_CHARS = 2048
# 只有本机回环才放行 http；其余一律 https。
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _file(data_dir):
    return os.path.join(str(data_dir), FILENAME)


def _validate(endpoint, model, api_key, *, allow_empty_key=False):
    """清洗 + 校验三个字段；不合格抛 ValueError（通用文案，绝不带回显）。"""
    endpoint = str(endpoint or "").strip()
    model = str(model or "").strip()
    api_key = str(api_key or "").strip()
    checked = (endpoint, model) if allow_empty_key else (endpoint, model, api_key)
    for value in checked:
        if not value or "\r" in value or "\n" in value:
            raise ValueError("invalid field")
    if api_key and ("\r" in api_key or "\n" in api_key):
        raise ValueError("invalid field")
    if (len(api_key) > _MAX_KEY_CHARS or len(model) > _MAX_MODEL_CHARS
            or len(endpoint) > _MAX_ENDPOINT_CHARS):
        raise ValueError("field too long")
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        raise ValueError("invalid endpoint") from None
    hostname = parts.hostname
    if not hostname:
        raise ValueError("invalid endpoint")
    if (parts.username or parts.password or parts.query or parts.fragment
            or parts.scheme not in ("http", "https")):
        raise ValueError("invalid endpoint")
    if parts.scheme != "https" and hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("endpoint must be https")
    return endpoint, model, api_key


def load_settings(data_dir):
    """读完整配置（仅供受信后端用）。文件缺失/损坏/字段脏 → None。

    shared 模式的 api_key 为空字符串：调用方去读聊天设置那把 Key，
    而不是把凭据复制进这个文件。旧文件里已经存过的 Key 仍按专用凭据读取。
    """
    try:
        with open(_file(data_dir), encoding="utf-8") as fp:
            row = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict):
        return None
    shared = row.get("credential") == "shared" or not str(row.get("api_key") or "").strip()
    try:
        endpoint, model, api_key = _validate(
            row.get("endpoint"), row.get("model"), row.get("api_key"),
            allow_empty_key=shared)
    except ValueError:
        return None
    return {"endpoint": endpoint, "model": model, "api_key": api_key,
            "credential": "shared" if shared else "dedicated"}


def public_settings(data_dir, *, shared_ready=False):
    """只给渲染用的安全视图：endpoint/model/key_configured，绝不含 key 或其片段。"""
    row = load_settings(data_dir)
    if row is None:
        return {"endpoint": DEFAULT_ENDPOINT, "model": DEFAULT_MODEL,
                "key_configured": False}
    ready = bool(row["api_key"]) or (row["credential"] == "shared" and shared_ready)
    return {"endpoint": row["endpoint"], "model": row["model"],
            "key_configured": ready}


def save_settings(data_dir, *, endpoint, model, api_key="", credential="dedicated"):
    """先校验再原子落盘（0600）。

    credential="shared"：只写 endpoint/model + 标记，绝不落第二份 Key；
    credential="dedicated"：旧行为，用于判断接口指向另一家服务的少数情况。
    """
    if credential not in ("shared", "dedicated"):
        raise ValueError("invalid credential mode")
    if credential == "shared":
        api_key = ""
    endpoint, model, api_key = _validate(
        endpoint, model, api_key, allow_empty_key=(credential == "shared"))
    os.makedirs(str(data_dir), exist_ok=True)
    path = _file(data_dir)
    payload = {"endpoint": endpoint, "model": model}
    if credential == "shared":
        payload["credential"] = "shared"
    if api_key:
        payload["api_key"] = api_key
    storage.atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)
