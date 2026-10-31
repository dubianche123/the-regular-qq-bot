"""本地保存的判断 API 配置（OpenAI 原生 GPT Decisions，不是 /chat/completions）。

只把 endpoint / model / api_key 三个字段写到 data_dir/decision_api.json（0600）。
这条配置与当前聊天模型完全独立：不改 .env、不碰前台 provider、不重启机器人。
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


def _validate(endpoint, model, api_key):
    """清洗 + 校验三个字段；不合格抛 ValueError（通用文案，绝不带回显）。"""
    endpoint = str(endpoint or "").strip()
    model = str(model or "").strip()
    api_key = str(api_key or "").strip()
    for value in (endpoint, model, api_key):
        if not value or "\r" in value or "\n" in value:
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
    """读完整配置（含 api_key，仅供受信后端用）。文件缺失/损坏/字段脏 → None。"""
    try:
        with open(_file(data_dir), encoding="utf-8") as fp:
            row = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict):
        return None
    try:
        endpoint, model, api_key = _validate(
            row.get("endpoint"), row.get("model"), row.get("api_key"))
    except ValueError:
        return None
    return {"endpoint": endpoint, "model": model, "api_key": api_key}


def public_settings(data_dir):
    """只给渲染用的安全视图：endpoint/model/key_configured，绝不含 key 或其片段。"""
    row = load_settings(data_dir)
    if row is None:
        return {"endpoint": DEFAULT_ENDPOINT, "model": DEFAULT_MODEL,
                "key_configured": False}
    return {"endpoint": row["endpoint"], "model": row["model"],
            "key_configured": True}


def save_settings(data_dir, *, endpoint, model, api_key):
    """先校验再原子落盘（0600）。每次保存都必须带上完整的新 key。"""
    endpoint, model, api_key = _validate(endpoint, model, api_key)
    os.makedirs(str(data_dir), exist_ok=True)
    path = _file(data_dir)
    storage.atomic_write_text(
        path, json.dumps({"endpoint": endpoint, "model": model,
                          "api_key": api_key},
                         ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)
