"""本机面板的基本配置：安全视图、一次原子保存和新进程环境。"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from regular.keys import read_api_keys
from regular import openai_defaults
import storage

PROVIDERS = ("openai", "zhipu", "gemini")
_BASIC_KEYS = {"QQ_APP_ID", "QQ_APP_SECRET", "BOT_NAMES", "AI_PROVIDER", "CHAT_PROVIDER", "DAILY_BUDGET"}


def instance_dir(cfg):
    root = getattr(cfg, "BASE_DIR", None) or Path(cfg.DATA_DIR).parent
    return Path(root).expanduser().resolve()


def _read(cfg):
    path = instance_dir(cfg) / ".env"
    return dict(dotenv_values(path, interpolate=False, encoding="utf-8")) if path.exists() else {}


def _external_keys(cfg):
    # Real config records this before load_dotenv. Minimal test/config adapters
    # can explicitly supply the same set, otherwise no inherited values are used.
    return frozenset(getattr(cfg, "EXTERNAL_ENV_KEYS", ()))


def child_environment(cfg):
    """Remove imported old dotenv values, retain actual environment overrides."""
    env = dict(os.environ)
    file_keys = set(_read(cfg)) | set(getattr(cfg, "DOTENV_KEYS_AT_IMPORT", ()))
    external = _external_keys(cfg)
    for key in file_keys - external:
        env.pop(key, None)
    env["REGULAR_HOME"] = str(instance_dir(cfg))
    return env


def _values(cfg):
    saved = _read(cfg)
    effective = dict(saved)
    for key in _external_keys(cfg):
        if key in os.environ:
            effective[key] = os.environ[key]
    return saved, effective


def _provider(cfg, values, provider=None):
    name = str(provider or values.get("CHAT_PROVIDER") or values.get("AI_PROVIDER") or getattr(cfg, "AI_PROVIDER", "openai")).strip().lower()
    if name not in PROVIDERS:
        raise ValueError("请选择 OpenAI-compatible、智谱或 Gemini。")
    return name


def public_settings(cfg, provider=None):
    """Only non-secret values and configuration facts may reach a web response."""
    saved, effective = _values(cfg)
    try:
        active = _provider(cfg, effective, effective.get("CHAT_PROVIDER") or None)
        invalid_provider = False
    except ValueError:
        active, invalid_provider = "openai", True
    selected = _provider(cfg, {"AI_PROVIDER": active}, provider)
    prefix = selected.upper()
    presets = getattr(cfg, "PROVIDER_PRESETS", {})
    preset = presets.get(selected, {})
    base = str(effective.get(f"{prefix}_BASE_URL") or preset.get("base_url", "") or "").strip()
    try:
        _endpoint(_text(base, "接口地址"))
    except ValueError:
        # Existing hand-edited files may contain URL credentials; never put
        # those into the public form, even when this panel did not save them.
        base = ""
    models = str(effective.get(f"{prefix}_MODELS") or "").strip()
    if not models:
        if selected == "openai" and openai_defaults.official_base(base):
            models = ",".join(preset.get("models") or openai_defaults.CHAT_MODELS)
        elif selected != "openai" or base == preset.get("base_url"):
            models = ",".join(preset.get("models", []))
    model_list = list(dict.fromkeys(item.strip() for item in models.split(",") if item.strip()))
    key_file = instance_dir(cfg) / f"api_keys_{selected}.txt"
    file_keys = read_api_keys(key_file) if key_file.exists() else []
    key = str(effective.get(f"{prefix}_API_KEY") or "").strip()
    qq_id = str(effective.get("QQ_APP_ID") or "").strip()
    qq_secret = bool(str(effective.get("QQ_APP_SECRET") or "").strip())
    fields = _BASIC_KEYS | {f"{prefix}_{suffix}" for suffix in ("BASE_URL", "MODELS", "API_KEY")}
    missing = []
    if invalid_provider:
        missing.append("有效的模型供应商")
    for configured, label in ((qq_id, "QQ AppID"), (qq_secret, "QQ AppSecret"),
                              (base, "模型接口地址"), (model_list, "回复模型"),
                              (bool(file_keys or key), "模型 API Key")):
        if not configured:
            missing.append(label)
    try:
        budget = int(str(effective.get("DAILY_BUDGET", getattr(cfg, "DAILY_BUDGET", 3000))).strip())
    except (ValueError, TypeError):
        budget = 3000
        missing.append("有效的每日调用上限")
    if budget < 0:
        missing.append("非负每日调用上限")
    return {
        "provider": selected, "active_provider": active, "base_url": base, "models": models,
        "primary_model": model_list[0] if model_list else "",
        "fallback_models": ", ".join(model_list[1:]), "model_options": model_list,
        "fallback_provider": str(effective.get("FALLBACK_PROVIDER") or "").strip().lower() if str(effective.get("FALLBACK_PROVIDER") or "").strip().lower() in PROVIDERS else "",
        "qq_app_id": qq_id, "bot_names": str(effective.get("BOT_NAMES") or ""),
        "daily_budget": budget, "qq_secret_configured": qq_secret,
        "key_configured": bool(file_keys or key), "key_source": "file" if file_keys else "environment" if key and f"{prefix}_API_KEY" in _external_keys(cfg) else "config" if key else "none",
        "missing": missing, "ready": not missing,
        "overridden": sorted(fields & _external_keys(cfg)),
        "instance_dir": str(instance_dir(cfg)), "config_file": str(instance_dir(cfg) / ".env"),
        "data_dir": str(getattr(cfg, "DATA_DIR", instance_dir(cfg) / "data")),
        "config_exists": (instance_dir(cfg) / ".env").exists(),
    }


def _text(value, label, limit=2048):
    value = str(value or "").strip()
    if len(value) > limit or any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError(f"{label}格式不正确。")
    return value


def _endpoint(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        parsed.port
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("接口地址必须是没有内嵌凭据的 HTTP 或 HTTPS 地址。")
    return value


def save_settings(cfg, values):
    """Validate all fields before one replacement; empty passwords keep old ones."""
    values = _chat_selection(cfg, values)
    saved, _ = _values(cfg)
    provider = _provider(cfg, saved, values.get("provider"))
    prefix = provider.upper()
    # Basic setup is the chat choice. An old CHAT_PROVIDER must not silently
    # override the service the user just selected in this form.
    changes = {"AI_PROVIDER": provider, "CHAT_PROVIDER": provider}
    for field, name, label in (("qq_app_id", "QQ_APP_ID", "QQ AppID"),
                               ("bot_names", "BOT_NAMES", "机器人称呼"),
                               ("models", f"{prefix}_MODELS", "模型名")):
        if field in values:
            changes[name] = _text(values[field], label)
    if "fallback_models" in values:
        primary = _text(values.get("models"), "聊天模型")
        if "," in primary:
            raise ValueError("聊天模型请只选一个；其他模型放在备用模型中。")
        fallbacks = _text(values.get("fallback_models"), "备用模型")
        ordered = list(dict.fromkeys(item.strip() for item in [primary, *fallbacks.split(",")] if item.strip()))
        if fallbacks and not primary:
            raise ValueError("请先填写聊天模型，再添加备用模型。")
        changes[f"{prefix}_MODELS"] = ",".join(ordered)
    if "base_url" in values:
        changes[f"{prefix}_BASE_URL"] = _endpoint(_text(values["base_url"], "接口地址"))
    for field, name in (("qq_app_secret", "QQ_APP_SECRET"), ("api_key", f"{prefix}_API_KEY")):
        if field in values:
            secret = _text(values[field], "密钥", 4096)
            if secret:
                changes[name] = secret
    if "daily_budget" in values:
        try:
            budget = int(str(values["daily_budget"]).strip())
        except (TypeError, ValueError):
            raise ValueError("每日调用上限必须是非负整数。") from None
        if budget < 0:
            raise ValueError("每日调用上限必须是非负整数。")
        changes["DAILY_BUDGET"] = str(budget)
    write_values(cfg, changes)
    return public_settings(cfg, provider)


def _chat_selection(cfg, values):
    """Resolve the explicit picker without changing legacy form submissions."""
    if "model_choice" not in values:
        return values
    values = dict(values)
    current = public_settings(cfg, values.get("provider"))
    choice = _text(values["model_choice"], "聊天模型")
    if choice == "__custom__":
        primary = _text(values.get("models"), "聊天模型")
    elif choice in current["model_options"]:
        primary = choice
    else:
        raise ValueError("请选择已配置模型，或填写其他模型。")
    if not primary or "," in primary:
        raise ValueError("请填写一个聊天模型。")
    values["models"] = primary
    original = current["model_options"]
    fallbacks = _text(values.get("fallback_models", current["fallback_models"]), "备用模型")
    submitted = list(dict.fromkeys(item.strip() for item in fallbacks.split(",") if item.strip()))
    # Picking a saved backup promotes it and keeps the old primary as a backup.
    # An explicitly edited backup list wins over that convenience.
    if (choice != "__custom__" and original and primary != original[0]
            and submitted == original[1:]):
        submitted = [model for model in original if model != primary]
    values["fallback_models"] = ",".join(submitted)
    return values


def save_identity(cfg, values):
    """Save QQ identity alone; changing a name cannot change the chat service."""
    changes = {}
    for field, key, label in (("qq_app_id", "QQ_APP_ID", "QQ AppID"),
                              ("bot_names", "BOT_NAMES", "机器人称呼")):
        if field in values:
            changes[key] = _text(values[field], label)
    if "qq_app_secret" in values:
        secret = _text(values["qq_app_secret"], "密钥", 4096)
        if secret:
            changes["QQ_APP_SECRET"] = secret
    if changes:
        write_values(cfg, changes)
    return public_settings(cfg)


def decision_key_from_chat(cfg, endpoint):
    """Copy a trusted credential only to the same configured service, never to UI data."""
    endpoint = _endpoint(_text(endpoint, "判断接口地址")).rstrip("/")
    if not endpoint.endswith("/decisions"):
        raise ValueError("请填写同一服务的判断地址，或使用新的 Key。")
    _, effective = _values(cfg)
    provider = _provider(cfg, effective)
    prefix = provider.upper()
    preset = getattr(cfg, "PROVIDER_PRESETS", {}).get(provider, {})
    base = _endpoint(_text(effective.get(f"{prefix}_BASE_URL", preset.get("base_url", "")), "聊天接口地址"))
    if not base or endpoint[:-len("/decisions")] != base.rstrip("/"):
        raise ValueError("聊天服务的 Key 不能用于这个判断地址。")
    default_key = _text(effective.get(f"{prefix}_API_KEY", ""), "密钥", 4096)
    keys = read_api_keys(instance_dir(cfg) / f"api_keys_{provider}.txt", default_key)
    if not keys:
        raise ValueError("请先连接聊天服务，或填写新的 Key。")
    return keys[0]


def write_values(cfg, changes):
    """Atomically save already-validated explicit fields without touching chat choices."""
    root = instance_dir(cfg)
    target = root / ".env"
    original = target.read_text(encoding="utf-8") if target.exists() else "# The Regular 本机配置。请勿公开此文件。\n"
    # python-dotenv preserves comments and unrelated keys. Work in memory rather
    # than replacing the real file once per submitted field.
    # set_key operates on a path, so use its same parser/rewrite format against a
    # private temporary file, then atomically publish exactly once.
    import tempfile
    root.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".regular-settings-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(original)
        for key, value in changes.items():
            set_key(temp_path, key, value, quote_mode="always", encoding="utf-8")
        text = Path(temp_path).read_text(encoding="utf-8")
        storage.atomic_write_text(str(target), text)
        os.chmod(target, 0o600)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    Path(getattr(cfg, "DATA_DIR", root / "data")).mkdir(parents=True, exist_ok=True, mode=0o700)
