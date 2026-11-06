"""Private capability configuration; hot flags, restart-bound service settings.

No provider clients or runtime state are created here. Feature gates reread the
instance's one .env file, respecting real external environment overrides.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from regular import instance_settings
from regular.keys import read_api_keys

FLAGS = {
    "vision": ("VISION_ENABLED", True),
    "image": ("IMAGE_GENERATION_ENABLED", True),
    "gif": ("IMAGE_GENERATION_GIF_ENABLED", True),
    "voice_input": ("VOICE_INPUT_ENABLED", True),
    "transcription": ("VOICE_TRANSCRIPTION_ENABLED", True),
    "voice_output": ("VOICE_OUTPUT_ENABLED", False),
}
_TEXT = {
    "vision": {"model": "VISION_MODEL", "input_mode": "VISION_INPUT_MODE"},
    "image": {"model": "IMAGE_GENERATION_MODEL", "quality": "IMAGE_GENERATION_QUALITY",
              "size": "IMAGE_GENERATION_SIZE", "gif_size": "IMAGE_GENERATION_GIF_SIZE"},
    "voice_input": {"model": "VOICE_TRANSCRIPTION_MODEL"},
    "voice_output": {"model": "VOICE_OUTPUT_MODEL", "voice": "VOICE_OUTPUT_VOICE",
                     "node": "VOICE_OUTPUT_NODE", "silk_entry": "VOICE_OUTPUT_SILK_ENTRY"},
}
_LIMITS = {
    "vision": {"daily_limit": ("VISION_DAILY_CAP", 10000)},
    "image": {"daily_limit": ("IMAGE_GENERATION_DAILY_CAP", 10000),
              "user_daily_limit": ("IMAGE_USER_DAILY_CAP", 1000)},
    "voice_input": {},
    "voice_output": {"daily_limit": ("VOICE_OUTPUT_DAILY_LIMIT", 100)},
    "voice": {"desktop_daily_limit": ("DESKTOP_VOICE_DAILY_LIMIT", 100)},
}


def _values(cfg):
    if not getattr(cfg, "BASE_DIR", None) and not getattr(cfg, "DATA_DIR", None):
        return {}
    return instance_settings._values(cfg)[1]


def _value(cfg, values, name, default=""):
    if name in values:
        return values[name] if values[name] is not None else default
    return getattr(cfg, name, default)


def _bool(value):
    return value if type(value) is bool else str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _integer(value, default):
    try:
        result = int(value)
        return result if result >= 0 else default
    except (TypeError, ValueError):
        return default


def enabled(cfg, name):
    """Gate a new operation. Existing in-flight operations are not interrupted."""
    key, default = FLAGS[name]
    try:
        return _bool(_value(cfg, _values(cfg), key, default))
    except OSError:
        return False


def official(endpoint):
    try:
        parts = urlsplit(str(endpoint))
        return (parts.scheme == "https" and parts.hostname == "api.openai.com"
                and parts.port in (None, 443) and not parts.username and not parts.password
                and not parts.query and not parts.fragment)
    except ValueError:
        return False


def same_endpoint(left, right):
    """A key belongs to its configured base URL, not every compatible API."""
    try:
        a, b = urlsplit(str(left)), urlsplit(str(right))
        return bool(a.scheme and a.hostname and
                    (a.scheme, a.hostname, a.port, a.path.rstrip('/')) ==
                    (b.scheme, b.hostname, b.port, b.path.rstrip('/')) and
                    not any((a.username, a.password, a.query, a.fragment,
                             b.username, b.password, b.query, b.fragment)))
    except ValueError:
        return False


def audio_key(cfg, fallback=""):
    """Runtime-only credentials; never returned by public_settings."""
    dedicated = getattr(cfg, "VOICE_API_KEY", "")
    if dedicated:
        return dedicated
    endpoint = getattr(cfg, "VOICE_BASE_URL", "https://api.openai.com/v1")
    source = getattr(cfg, "PROVIDER_PRESETS", {}).get("openai", {}).get("base_url", "")
    return fallback if official(endpoint) and official(source) else ""


def dependencies(node, silk):
    return {"node": bool(node and Path(node).is_absolute() and Path(node).is_file()
                         and os.access(node, os.X_OK)),
            "silk": bool(silk and Path(silk).is_absolute() and Path(silk).is_file())}


def runtime_status(cfg):
    """The running process attests hot-gate support and its loaded model names."""
    try:
        values = _values(cfg)
    except OSError:
        values = {key: False for key, _ in FLAGS.values()}
    return {"live_flags": 1,
            "enabled": {name: _bool(_value(cfg, values, key, default))
                        for name, (key, default) in FLAGS.items()},
            "models": {"vision": str(getattr(cfg, "VISION_MODEL", "") or ""),
                       "image": str(getattr(cfg, "IMAGE_GENERATION_MODEL", "") or ""),
                       "voice_input": str(getattr(cfg, "VOICE_TRANSCRIPTION_MODEL", "") or ""),
                       "voice_output": str(getattr(cfg, "VOICE_OUTPUT_MODEL", "gpt-realtime-2.1-mini") or "")},
            "voice": str(getattr(cfg, "VOICE_OUTPUT_VOICE", "marin") or "")}


def _endpoint(value):
    try:
        return instance_settings._endpoint(instance_settings._text(value, "接口地址"))
    except ValueError:
        return ""


def _provider(cfg, values, name):
    preset = getattr(cfg, "PROVIDER_PRESETS", {}).get(name, {})
    prefix = name.upper()
    base = _endpoint(values.get(f"{prefix}_BASE_URL", preset.get("base_url", "")))
    models = str(values.get(f"{prefix}_MODELS", ",".join(preset.get("models", []))) or "")
    key_file = instance_settings.instance_dir(cfg) / f"api_keys_{name}.txt"
    keys = read_api_keys(key_file) if key_file.is_file() else []
    secret = str(values.get(f"{prefix}_API_KEY", "") or "").strip()
    return base, models.split(",")[0].strip(), bool(keys or secret), "file" if keys else "openai" if secret else "none"


def public_settings(cfg):
    """Saved/effective configuration facts, never credentials or provider results."""
    values = _values(cfg)
    overrides = instance_settings._external_keys(cfg)
    primary = str(values.get("AI_PROVIDER", getattr(cfg, "AI_PROVIDER", "openai")) or "openai")
    provider = str(values.get("VISION_PROVIDER") or primary)
    if provider not in instance_settings.PROVIDERS:
        provider = primary if primary in instance_settings.PROVIDERS else "openai"
    vbase, inherited_model, vkey, vsource = _provider(cfg, values, provider)
    vision_model = str(values.get("VISION_MODEL", "" if getattr(cfg, "VISION_MODEL_INHERITED", False)
                                  else getattr(cfg, "VISION_MODEL", "")) or "")
    vision = {
        "enabled": enabled(cfg, "vision"), "provider": provider,
        "model": vision_model,
        "effective_model": vision_model or inherited_model,
        "input_mode": str(_value(cfg, values, "VISION_INPUT_MODE", "url")),
        "daily_limit": _integer(_value(cfg, values, "VISION_DAILY_CAP", 30), 30),
        "key_configured": vkey, "key_source": provider if vkey else "none",
    }
    vision["missing"] = [label for ok, label in ((vbase, "识图服务地址"), (vision["effective_model"], "识图模型"), (vkey, "识图服务的 Key")) if not ok]
    image_base = _endpoint(_value(cfg, values, "IMAGE_GENERATION_BASE_URL", "https://api.openai.com/v1"))
    obase, _, okey, _ = _provider(cfg, values, "openai")
    image_dedicated = bool(str(values.get("IMAGE_GENERATION_API_KEY") or "").strip())
    image_inherited = okey and same_endpoint(image_base, obase)
    image_source = "dedicated" if image_dedicated else "openai" if image_inherited else "none"
    if image_source == "none" and official(image_base):
        from regular import decision_settings
        decision = decision_settings.load_settings(getattr(cfg, "DATA_DIR", ""))
        if decision and official(decision.get("endpoint")):
            image_source = "decision"
    image = {
        "enabled": enabled(cfg, "image"), "gif_enabled": enabled(cfg, "gif"),
        "base_url": image_base, "model": str(_value(cfg, values, "IMAGE_GENERATION_MODEL", "gpt-image-2.6-flare")),
        "quality": str(_value(cfg, values, "IMAGE_GENERATION_QUALITY", "medium")),
        "size": str(_value(cfg, values, "IMAGE_GENERATION_SIZE", "1024x1024")),
        "gif_size": str(_value(cfg, values, "IMAGE_GENERATION_GIF_SIZE", "512x512")),
        "daily_limit": _integer(_value(cfg, values, "IMAGE_GENERATION_DAILY_CAP", 40), 40),
        "user_daily_limit": _integer(_value(cfg, values, "IMAGE_USER_DAILY_CAP", 5), 5),
        "key_configured": image_source != "none", "key_source": image_source,
    }
    image["missing"] = [label for ok, label in ((image_base, "生图服务地址"), (image["model"], "生图模型"), (image["key_configured"], "生图 Key")) if not ok]
    audio_base = _endpoint(_value(cfg, values, "VOICE_BASE_URL", "https://api.openai.com/v1"))
    audio_dedicated = bool(str(values.get("VOICE_API_KEY") or "").strip())
    audio_inherited = okey and official(obase) and official(audio_base)
    audio_source = "dedicated" if audio_dedicated else "openai" if audio_inherited else "none"
    common = {"base_url": audio_base, "key_configured": audio_source != "none", "key_source": audio_source,
              "desktop_daily_limit": _integer(_value(cfg, values, "DESKTOP_VOICE_DAILY_LIMIT", 30), 30)}
    voice_input = {**common, "enabled": enabled(cfg, "voice_input"),
                   "transcription_enabled": enabled(cfg, "transcription"),
                   "model": str(_value(cfg, values, "VOICE_TRANSCRIPTION_MODEL", "gpt-transcribe"))}
    tls = _bool(_value(cfg, values, "SSL_VERIFY", True))
    voice_input["missing"] = [label for ok, label in ((official(audio_base), "官方 OpenAI 语音地址"), (common["key_configured"], "语音 Key"), (voice_input["model"], "转写模型")) if not ok]
    voice_input["transcription_missing"] = list(voice_input["missing"])
    if not voice_input["transcription_enabled"]:
        voice_input["missing"] = []  # QQ-provided text needs no model or service credential.
    node = str(_value(cfg, values, "VOICE_OUTPUT_NODE", ""))
    silk = str(_value(cfg, values, "VOICE_OUTPUT_SILK_ENTRY", ""))
    deps = dependencies(node, silk)
    voice_output = {**common, "enabled": enabled(cfg, "voice_output"),
                    "model": str(_value(cfg, values, "VOICE_OUTPUT_MODEL", "gpt-realtime-2.1-mini")),
                    "voice": str(_value(cfg, values, "VOICE_OUTPUT_VOICE", "marin")),
                    "daily_limit": _integer(_value(cfg, values, "VOICE_OUTPUT_DAILY_LIMIT", 10), 10),
                    "node": node, "silk_entry": silk, "dependencies": deps}
    voice_output["missing"] = [label for ok, label in ((official(audio_base), "官方 OpenAI 语音地址"), (common["key_configured"], "语音 Key"), (voice_output["model"], "语音模型"), (deps["node"], "本机 Node"), (deps["silk"], "SILK 编码器"), (tls, "TLS 证书验证")) if not ok]
    sections = {"vision": vision, "image": image, "voice_input": voice_input, "voice_output": voice_output}
    for name, section in sections.items():
        section["configured"] = not section["missing"]
        fields = set(_TEXT[name].values()) | {key for key, _ in _LIMITS[name].values()}
        fields |= {FLAGS[name][0]}
        if name == "vision": fields |= {"VISION_PROVIDER"}
        if name == "image": fields |= {"IMAGE_GENERATION_BASE_URL", "IMAGE_GENERATION_API_KEY", FLAGS["gif"][0]}
        if name.startswith("voice_"): fields |= {"VOICE_BASE_URL", "VOICE_API_KEY"}
        if name == "voice_input": fields |= {FLAGS["transcription"][0]}
        section["overridden"] = sorted(fields & overrides)
    return sections


NAMES = (("vision", "读图"), ("image", "画图"), ("voice_input", "听语音"), ("voice_output", "发语音"))


def state_line(name, row):
    """(tone, words) for one capability in plain language: ok / todo / off."""
    if not row.get("enabled"):
        return "off", "已关闭"
    if name == "voice_input" and not row.get("transcription_enabled"):
        return "ok", "只用 QQ 自带的转写"
    missing = row.get("missing") or []
    if missing:
        if name == "voice_output" and missing[0] in ("本机 Node", "SILK 编码器"):
            return "todo", "需要安装语音组件"
        return "todo", "还差" + missing[0] + (f" 等 {len(missing)} 项" if len(missing) > 1 else "")
    model = row.get("effective_model") if name == "vision" else row.get("model")
    return "ok", "已配置" + (f" · {model}" if model else "")


def overview(sections):
    """Switch positions and one-line states: the same view for the page, the pet and coding agents."""
    flags = {"vision": sections["vision"]["enabled"], "image": sections["image"]["enabled"],
             "gif": sections["image"]["gif_enabled"], "voice_input": sections["voice_input"]["enabled"],
             "transcription": sections["voice_input"]["transcription_enabled"],
             "voice_output": sections["voice_output"]["enabled"]}
    states = {name: dict(zip(("tone", "text"), state_line(name, sections[name]))) for name, _ in NAMES}
    return {"flags": flags, "states": states}


def set_enabled(cfg, name, value):
    """One switch, saved at once; the running bot rereads it for the next request."""
    if not isinstance(name, str) or name not in FLAGS:
        raise ValueError("请选择要开关的能力。")
    selected = _switch(value)
    key = FLAGS[name][0]
    if key in instance_settings._external_keys(cfg) and key in os.environ and _bool(os.environ[key]) != selected:
        raise ValueError("该开关由启动环境覆盖，请先调整启动环境。")
    instance_settings.write_values(cfg, {key: "true" if selected else "false"})
    return public_settings(cfg)


def _switch(value):
    if type(value) is bool:
        return value
    if isinstance(value, str):
        selected = value.strip().lower()
        if selected in ("true", "on", "1"):
            return True
        if selected in ("false", "off", "0"):
            return False
    raise ValueError("开关状态必须明确为开或关。")


# Switches a section form may carry. Absent means "not sent", never "off": the page saves
# switches on their own (set_enabled), so saving a model name cannot turn a feature off.
_SECTION_FLAGS = {"vision": {"enabled": "vision"},
                  "image": {"enabled": "image", "gif_enabled": "gif"},
                  "voice_input": {"enabled": "voice_input", "transcription_enabled": "transcription"},
                  "voice_output": {"enabled": "voice_output"},
                  "voice": {}}


def save_settings(cfg, form):
    """A section save affects only its own fields; blank passwords preserve secrets.

    section "voice" is the audio service shared by hearing and speaking (VOICE_BASE_URL / VOICE_API_KEY).
    """
    section = str(form.get("section") or "")
    if section not in _SECTION_FLAGS:
        raise ValueError("请选择要保存的能力。")
    changes = {FLAGS[name][0]: "true" if _switch(form[field]) else "false"
               for field, name in _SECTION_FLAGS[section].items() if field in form}
    for field, key in _TEXT.get(section, {}).items():
        if field in form:
            value = instance_settings._text(form[field], "能力配置", 512)
            if field in ("node", "silk_entry") and value and not Path(value).is_absolute():
                raise ValueError("编码器路径必须是绝对路径。")
            changes[key] = value
    if section == "vision":
        if "provider" in form:
            provider = str(form["provider"] or "").strip().lower()
            if provider not in instance_settings.PROVIDERS:
                raise ValueError("请选择已支持的视觉服务。")
            changes["VISION_PROVIDER"] = provider
        if changes.get("VISION_INPUT_MODE", "url") not in ("url", "download"):
            raise ValueError("图片输入方式必须是 url 或 download。")
    if section == "image":
        if "base_url" in form:
            changes["IMAGE_GENERATION_BASE_URL"] = instance_settings._endpoint(instance_settings._text(form["base_url"], "生图接口地址"))
            old = _values(cfg)
            previous = str(_value(cfg, old, "IMAGE_GENERATION_BASE_URL", "https://api.openai.com/v1"))
            dedicated = str(_value(cfg, old, "IMAGE_GENERATION_API_KEY") or "").strip()
            if (changes["IMAGE_GENERATION_BASE_URL"] and not same_endpoint(previous, changes["IMAGE_GENERATION_BASE_URL"])
                    and dedicated and not str(form.get("api_key") or "").strip()):
                raise ValueError("更换生图接口时，请同时填写该接口的专用 Key。")
        if "api_key" in form:
            secret = instance_settings._text(form["api_key"], "生图 Key", 4096)
            if secret: changes["IMAGE_GENERATION_API_KEY"] = secret
    if section.startswith("voice"):
        if "base_url" in form:
            base = instance_settings._endpoint(instance_settings._text(form["base_url"], "语音接口地址"))
            if base and not official(base):
                raise ValueError("当前语音链路仅支持官方 OpenAI 接口。")
            changes["VOICE_BASE_URL"] = base
        if "voice_api_key" in form:
            secret = instance_settings._text(form["voice_api_key"], "语音 Key", 4096)
            if secret: changes["VOICE_API_KEY"] = secret
    for field, (key, maximum) in _LIMITS.get(section, {}).items():
        if field not in form: continue
        try: value = int(str(form[field]).strip())
        except (TypeError, ValueError): raise ValueError("每日上限必须是整数。") from None
        if not 0 <= value <= maximum:
            raise ValueError(f"每日上限必须在 0 到 {maximum} 之间。")
        changes[key] = str(value)
    if changes:
        instance_settings.write_values(cfg, changes)
    return public_settings(cfg)
