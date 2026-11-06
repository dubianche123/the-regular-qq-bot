"""本机配置与维护面板 —— 只绑定 loopback，不直接连接 QQ 或请求模型。

用途是维护者视角的几个高频动作：
  · 看运行状态、预算、群列表；
  · 直接编辑 knowledge/*.md，保存后 bot 下一轮自动重载；
  · 查看每日记忆 MD（只读，摘要仍归压缩流程）；
  · 手动跑一次 DuckDuckGo，检查搜索是否可用。
  · 配置实例；用户点击后启动机器人，只停止本面板持有的子进程。

它不是远程后台：默认只监听 127.0.0.1，并要求本地 token。知识库保存走
storage.atomic_write_text，文件名校验后再落盘，避免路径穿越。
"""

from __future__ import annotations

from collections import deque
import html
import hmac
from importlib.resources import files
import json
import os
import secrets
import shlex
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from regular import decision_settings
from regular import instance_settings
from regular.panel_runtime import PanelRuntime

import knowledge as knowledge_mod
import search as search_mod
import storage

TOKEN_FILE = "panel_token"
MAX_EDIT_BYTES = 256 * 1024
MAX_MEMORY_BYTES = 512 * 1024
TOKEN_HEADER = "X-Panel-Token"
MASCOT_POSES = ("blink", "thinking", "celebrate", "sleeping", "wave", "knowledge", "checkup", "welcome")
MASCOT_STATES = ("idle", *MASCOT_POSES)
MASCOT_ASSETS = frozenset({"mascot.png", "app-icon.png", *(f"mascot-{state}.png" for state in MASCOT_POSES)})


def load_or_create_token(cfg):
    path = Path(cfg.DATA_DIR) / TOKEN_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(str(path), token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _read_text(path, limit=MAX_MEMORY_BYTES):
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _tail(path, lines=40):
    text = _read_text(path, limit=256 * 1024)
    return "\n".join(text.splitlines()[-lines:])


def _groups(state):
    groups = {}
    for gid, slot in (state.get("groups") or {}).items():
        groups.setdefault(gid, {})["slot"] = slot if isinstance(slot, dict) else {}
    for gid in (state.get("digests") or {}):
        groups.setdefault(gid, {})
    for gid, mode in (state.get("modes") or {}).items():
        groups.setdefault(gid, {})["mode"] = mode
    for key in (state.get("relations") or {}):
        gid = str(key).rsplit("|", 1)[0]
        groups.setdefault(gid, {})["relations"] = groups.get(gid, {}).get("relations", 0) + 1
    for gid in (state.get("promises") or {}):
        groups.setdefault(gid, {})["promises"] = len(state["promises"].get(gid) or [])
    return groups


def _knowledge_files(cfg):
    try:
        names = sorted(os.listdir(cfg.KNOWLEDGE_DIR))
    except OSError:
        return []
    return [name for name in names
            if name.endswith(".md") and name != "README.md" and _safe_name(name)]


def _safe_name(name):
    name = str(name or "").strip()
    if name == "all.md":
        return True
    if not name.endswith(".md"):
        return False
    stem = name[:-3]
    return (4 <= len(stem) <= 64
            and all(ch.isalnum() or ch in "_-" for ch in stem))


def _knowledge_path(cfg, name):
    if not _safe_name(name):
        raise web.HTTPBadRequest(text="非法知识库文件名")
    root = os.path.realpath(cfg.KNOWLEDGE_DIR)
    path = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath([root, path]) != root:
        raise web.HTTPBadRequest(text="非法知识库路径")
    return path


def _memory_files(cfg):
    try:
        names = os.listdir(cfg.MEMORY_DIR)
    except OSError:
        return []
    rows = []
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(cfg.MEMORY_DIR, name)
        try:
            rows.append((os.path.getmtime(path), name))
        except OSError:
            continue
    return [name for _mtime, name in sorted(rows, reverse=True)]


def _status(cfg):
    snapshot = _read_json(cfg.STATUS_FILE) or {}
    state = _read_json(cfg.STATE_FILE) or {}
    return snapshot, state


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _icon(name):
    paths = {
        "brand": '<path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-8l-6 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 10v2m8-2v2m-6 3h4"/>',
        "setup": '<path d="M9 7V3m6 4V3"/><path d="M6 7h12v4a6 6 0 0 1-12 0Z"/><path d="M12 17v4"/>',
        "home": '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
        "checkup": '<path d="M9 5H6v16h13V5h-3M9 3h6v4H9Z"/><path d="m9 14 2 2 5-5"/>',
        "iterate": '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
        "knowledge": '<path d="M3 4h7l2 2 2-2h7v15h-7l-2 2-2-2H3Z"/><path d="M12 6v15"/>',
        "memory": '<path d="M4 7h16v14H4ZM3 3h18v4H3Z"/><path d="M9 11h6"/>',
        "search": '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
        "decision": '<path d="M5 3v6a3 3 0 0 0 3 3h8a3 3 0 0 1 3 3v6M19 3v3a3 3 0 0 1-3 3H8a3 3 0 0 0-3 3v9"/>',
        "companion": '<path d="M4 4h16v13H9l-5 4Z"/><path d="M8 9h8m-8 4h5"/>',
        "capabilities": '<path d="M5 3v18M12 3v18M19 3v18"/><path d="M2 8h6m1 8h6m1-9h6"/>',
    }
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>'


def _mascot(state="idle", *, image_class="mascot-image", chat=False):
    if state not in MASCOT_STATES:
        state = "idle"
    if files("regular").joinpath("assets", "mascot.png").is_file():
        source = f"/assets/mascot-{state}.png" if state in MASCOT_POSES and files("regular").joinpath("assets", f"mascot-{state}.png").is_file() else "/assets/mascot.png"
        return (f'<span class="buddy-illustration"><img class="{image_class}" src="{source}" '
                f'data-mascot-state="{state}" data-static-src="{source}" '
                f'{"data-chat-mascot " if chat else ""}alt="老群友"></span>')
    return '<span class="buddy-illustration" aria-hidden="true">' + _icon("brand") + '</span>'


def _pet_clips():
    """The browser plays the same clip table as the native pet (single source: regular/pet_brain.py)."""
    from regular.pet_brain import CLIPS
    return json.dumps({name: [strip, seq, fps, loop] for name, (strip, seq, fps, loop, _) in CLIPS.items()},
                      separators=(",", ":"))


def _pet_sprite(clip="idle", *, size="stage", label="老群友"):
    """Animated chibi from regular/assets/pet; falls back to the static portrait without sprites."""
    if not files("regular").joinpath("assets", "pet", "sprites.json").is_file():
        return _mascot("idle", image_class="room-mascot", chat=size == "stage")
    return (f'<span class="pet-sprite pet-sprite-{_esc(size)}" data-pet-sprite data-clip="{_esc(clip)}" '
            f'role="img" aria-label="{_esc(label)}"><span class="pet-frame"></span></span>')


def _empty_state(title, detail, *, state="sleeping"):
    return f'<div class="companion-empty">{_mascot(state)}<div><h3>{_esc(title)}</h3><p>{_esc(detail)}</p></div></div>'


def _companion_dock(active):
    messages = {
        "setup": "这里接 QQ、选回复模型，一般配一次就好。保存以后，你来决定什么时候启动。",
        "home": "在这里确认我有没有连上 QQ。想单独聊两句，可以打开本机小窗。",
        "checkup": "接偏的那一句，也能成为下一次进步的起点。把现场交给你的 Agent 就好。",
        "iterate": "你说哪里不对，Agent 帮忙找原因；改得好不好，最后还是你来定。",
        "knowledge": "群里的规矩、常用称呼和约定，可以写在这里。聊天时我会按需参考。",
        "memory": "熟悉是慢慢攒下来的。这里放着已经整理好的群记忆。",
        "decision": "判断帮手负责什么时候接话，也帮提醒问清意思。配好以后，再慢慢玩。",
        "search": "先在这里检查搜索通不通。真正聊天时，只在明确需要时查一次。",
        "capabilities": "会看图、画图，还是听语音，都由你来选。每项的服务和开关放在一起。",
    }
    state = "thinking" if active in ("checkup", "iterate", "decision") else "idle"
    return f'''<details class="companion-dock" data-companion-dock><summary aria-label="打开老群友小助手">{_pet_sprite("think" if state == "thinking" else "idle", size="dock")}<span>老群友在这儿<span class="dock-hint">靠近，聊两句</span></span><span class="dock-spark" aria-hidden="true">✦</span></summary>
<div class="companion-bubble"><p>{_esc(messages.get(active, '想聊两句，或看看下一步做什么，我都在这里。'))}</p><a class="button" href="/companion">打开聊天小窗 →</a><div class="dock-links"><a href="/setup">设置</a><a href="/checkup">体检</a><a href="/iterate">让 Agent 帮忙</a></div></div></details>'''


def _page(title, body, active="", refresh=0, *, pet=False):
    # 按用的频率排：天天用的在最上面；接 QQ、选模型这种配一次就好的事放进底部「设置」
    # （没配完时首页那条进度会把人带过去）。
    nav = (
        ("", (("/", "我的群友", "home"), ("/companion", "聊两句", "companion"),
              ("/capabilities", "能力与服务", "capabilities"))),
        ("群里的事", (("/checkup", "每日体检", "checkup"), ("/knowledge", "群知识", "knowledge"),
                   ("/memory", "群记忆", "memory"))),
        ("交给 Agent", (("/iterate", "让 Agent 帮忙", "iterate"),)),
        ("设置", (("/setup", "QQ 与模型", "setup"), ("/decision", "判断 API", "decision"),
                ("/search", "搜索检查", "search"))),
    )
    links = "".join(('<span class="nav-label">' + label + '</span>' if label else '') + "".join(
        f'<a class="nav-item {"active" if key == active else ""}" href="{href}"'
        f'{" aria-current=page" if key == active else ""}><span class="nav-icon">{_icon(key)}</span>'
        f'<span class="nav-text">{text}</span></a>' for href, text, key in entries)
        for label, entries in nav)
    css = files("regular").joinpath("panel.css").read_text(encoding="utf-8")
    brand_icon = ('<img src="/assets/app-icon.png" alt="老群友">'
                  if files("regular").joinpath("assets", "app-icon.png").is_file() else _icon("brand"))
    pose_states = ",".join(state for state in MASCOT_POSES if files("regular").joinpath("assets", f"mascot-{state}.png").is_file())
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/assets/app-icon.png">
<title>{_esc(title)} · The Regular</title>
{f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ''}
<style>{css}</style><script src="/assets/panel.js" defer></script></head><body class="{'pet-mode' if pet else ''}" data-pose-states="{pose_states}" data-pet-clips='{_esc(_pet_clips())}'>
<div class="app-shell"><aside class="sidebar">
<a class="brand" href="/"><span class="brand-mark brand-portrait">{brand_icon}</span><span><span class="brand-name">老群友</span><span class="brand-sub">THE REGULAR</span></span></a>
<nav aria-label="主导航">{links}</nav><div class="sidebar-footer"><strong>今天也在群里。</strong><p>一点熟悉，一点新鲜。<br>慢慢变成你们的老群友。</p></div></aside>
<div class="main-shell"><header class="topbar"><div class="breadcrumb">你的老群友 <span aria-hidden="true">/</span> <strong>{_esc(title)}</strong></div><div class="topbar-tools"><button type="button" class="actions-toggle" data-actions-toggle aria-pressed="true" title="关闭后角色只保留静止姿势">角色动作：开</button><span class="workspace-pill">本机工作台</span></div></header>
<main><div class="wrap">{body}</div></main></div></div>{'' if active == 'companion' else _companion_dock(active)}
</body></html>'''


def _dashboard(cfg, *, csrf="", runner=None):
    snapshot, state = _status(cfg)
    managed = runner.snapshot() if runner is not None else {}
    if runner is not None:
        snapshot = {**snapshot, "gateway_connected": managed.get("gateway_connected", False)}
    groups = _groups(state)
    rows = []
    for gid in sorted(groups):
        info = groups[gid]
        last = (info.get("slot") or {}).get("last_seen")
        last_text = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "—"
        kfile = f"{gid}.md"
        klink = (f'<a href="/knowledge?file={_esc(kfile)}">群知识</a>'
                 if kfile in _knowledge_files(cfg) else '<span class="muted">暂无群知识</span>')
        rows.append(
            f'<tr><td><span class="mono">{_esc(gid)}</span>'
            f'<small>{_esc(info.get("mode") or "—")}</small></td>'
            f'<td>认识 {info.get("relations", 0)} 人<small>{info.get("promises", 0)} 项承诺</small></td>'
            f'<td>{_esc(last_text)}<small>{klink}</small></td></tr>')
    if not rows:
        group_table = '<p class="wx-empty">还没有群数据。QQ 连上后，先私聊认主，再邀请她进群。</p>'
    else:
        group_table = ('<table class="wx-groups"><thead><tr><th>群</th><th>熟悉程度</th>'
                       '<th>最近活动</th></tr></thead>'
                       "<tbody>" + "".join(rows) + "</tbody></table>")
    files = []
    for name in _knowledge_files(cfg):
        path = os.path.join(cfg.KNOWLEDGE_DIR, name)
        try:
            stat = os.stat(path)
            files.append(f"<tr><td><a href=\"/knowledge?file={_esc(name)}\">{_esc(name)}</a></td>"
                         f"<td>{stat.st_size} B</td>"
                         f"<td>{time.strftime('%m-%d %H:%M', time.localtime(stat.st_mtime))}</td></tr>")
        except OSError:
            continue
    knowledge_table = ("<table><thead><tr><th>文件</th><th>大小</th><th>更新</th></tr></thead><tbody>"
                       + "".join(files) + "</tbody></table>") if files else '<p class="muted">知识库还没有文件。</p>'
    log = _tail(cfg.LOG_FILE)
    budget = snapshot.get("budget") or {}
    provider = ((snapshot.get("providers") or {}).get("providers") or [{}])[0]
    model = (provider.get("models") or ["尚无运行记录"])[0]
    model_label = "最近报告的聊天模型"
    if managed.get("running") and not managed.get("stale") and snapshot.get("pid") == managed.get("pid"):
        model_label = "运行中的聊天模型"
    code = snapshot.get("code") or {}
    boot_log = managed.get("log_file")
    startup = (f'<h3>本面板启动日志</h3><pre>{_esc(_tail(boot_log) or "暂无本面板启动日志")}</pre>'
               if boot_log and boot_log != cfg.LOG_FILE else "")
    body = f"""
<header class="wx-heading"><div><h1>我的群友</h1><p>今天也在，慢慢熟悉你们。</p></div><a class="button secondary" href="/companion">聊两句 →</a></header>
{_runtime_controls(cfg, csrf, runner) if runner is not None else ''}
<dl class="wx-metrics" aria-label="运行概况"><div><dt>今日模型调用</dt><dd>{_esc(budget.get('used', '—'))} / {_esc(budget.get('limit', '—'))}</dd></div><div><dt>{model_label}</dt><dd>{_esc(model)}</dd></div><div><dt>已经加入</dt><dd>{len(groups)} 个群</dd></div></dl>
<section class="wx-section"><div class="wx-section-head"><h2>群里的近况</h2><a href="/checkup">看每日体检 →</a></div>{group_table}</section>
<details class="wx-disclosure"><summary>运行日志与版本</summary><p>运行代码：<code>{_esc(code.get('release') or '尚无运行记录')}</code></p><p>代码位置：<code>{_esc(code.get('source_root') or '—')}</code></p><h3>最近日志</h3><pre>{_esc(log or '暂无日志')}</pre>{startup}</details>
<details class="wx-disclosure"><summary>知识文件 · {len(files)} 份</summary>{knowledge_table}<a href="/knowledge">整理群知识 →</a></details>
"""
    return _page("概览", body, "home", refresh=5 if managed.get("owned") and managed.get("running")
                 and not managed.get("gateway_connected") else 0)


def _setup_page(cfg, request, csrf):
    try:
        settings = instance_settings.public_settings(cfg, request.query.get("provider"))
    except ValueError:
        raise web.HTTPBadRequest(text="请选择页面提供的模型供应商") from None
    provider = settings["provider"]
    choices = []
    for key, label in (("openai", "OpenAI 兼容"), ("zhipu", "智谱"), ("gemini", "Gemini")):
        option = instance_settings.public_settings(cfg, key)
        placeholder = "已配置，留空保留" if option["key_configured"] else "填写这个服务的 Key"
        choices.append(f'<button type="button" class="provider-option {"selected" if key == provider else ""}" '
                       f'data-provider-option="{key}" data-base-url="{_esc(option["base_url"])}" '
                       f'data-models="{_esc(option["primary_model"])}" data-fallback-models="{_esc(option["fallback_models"])}" '
                       f'data-key-placeholder="{placeholder}" '
                       f'aria-pressed="{"true" if key == provider else "false"}">{label}</button>')
    notice = ('<div class="notice good">配置已保存。运行中的机器人在下次启动时读取新配置。'
              '<a href="/">去启动和检查连接 →</a></div>') if request.query.get("saved") else ""
    if settings.get("overridden"):
        notice += '<div class="notice">这些项目由启动环境设置，环境变量优先生效：' + _esc(
            "、".join(settings["overridden"])) + '。请在启动面板的环境中调整。</div>'
    missing = "、".join(settings.get("missing") or ())
    readiness = f"还需填写：{_esc(missing)}" if missing else "必填配置齐了，可以到“我的群友”启动。"
    qq_ready = bool(settings.get("qq_app_id") and settings.get("qq_secret_configured"))
    model_ready = bool(settings.get("base_url") and settings.get("primary_model") and settings.get("key_configured"))
    body = f'''<div class="setup-hero"><div class="page-heading"><span class="eyebrow">HELLO, NEW FRIEND</span>
<h1>先让它走进你的 QQ 群</h1><p>会接梗，也知道什么时候安静。<br>先接上 QQ 和回复模型，剩下的慢慢一起玩。</p></div>{_mascot('celebrate' if request.query.get('saved') else 'welcome')}</div>
<div class="setup-progress" aria-label="上手步骤">
<div class="progress-item is-current" aria-current="step"><span class="progress-index">1</span><span>配置服务</span></div>
<div class="progress-item"><span class="progress-index">2</span><span>启动确认</span></div>
<div class="progress-item"><span class="progress-index">3</span><span>适应你的群</span></div></div>
{notice}<div class="setup-layout"><form method="post" action="/setup/save" class="setup" data-setup-form>
<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="provider" value="{_esc(provider)}">
<section class="form-section"><div class="section-heading"><div><h2 class="section-title">它在 QQ 的身份</h2>
<p class="section-description">在 <a href="https://q.qq.com/" target="_blank" rel="noreferrer">QQ 开放平台 ↗</a>创建机器人，取得这两项。</p></div></div>
<div class="field-grid"><label class="field"><span class="field-label">AppID</span><input type="text" name="qq_app_id" value="{_esc(settings.get('qq_app_id'))}" autocomplete="off" placeholder="QQ 机器人的 AppID"></label>
<label class="field"><span class="field-label">AppSecret</span><input type="password" name="qq_app_secret" value="" autocomplete="new-password" placeholder="{'已配置，留空保留' if settings.get('qq_secret_configured') else '填写 QQ AppSecret'}"></label>
<label class="field field-wide"><span class="field-label">大家怎么叫它？<small>可选</small></span><input type="text" name="bot_names" value="{_esc(settings.get('bot_names'))}" placeholder="小王,老群友"><span class="field-hint">多个名字用逗号分隔。群友叫它的名字时，它也能认出来。</span></label></div>
<p class="form-footer-note">QQ 控制台还需启用私聊与所需群消息事件，并配置测试或使用范围。</p></section>
<section class="form-section" id="chat-model"><div class="section-heading"><div><h2 class="section-title">聊天时，用谁来回答？</h2>
<p class="section-description">先选聊天 API，再指定一个首选模型。QQ 回复和本机聊天共用这一选择。</p></div></div>
<div class="provider-choices" role="group" aria-label="聊天模型服务">{''.join(choices)}</div>
<p class="field-hint" data-provider-status aria-live="polite">切换服务会保留本页输入；只有点保存才会生效。</p>
<div class="field-grid"><label class="field field-wide"><span class="field-label">接口地址</span><input type="text" name="base_url" value="{_esc(settings.get('base_url'))}" placeholder="供应商提供的完整 API 基础地址" autocomplete="off"></label>
<label class="field"><span class="field-label">聊天用这个模型</span><input type="text" name="models" list="chat-model-options" value="{_esc(settings.get('primary_model'))}" placeholder="选择或填写一个聊天模型 ID" autocomplete="off"><datalist id="chat-model-options">{''.join(f'<option value="{_esc(model)}"></option>' for model in settings.get('model_options', []))}</datalist><span class="field-hint">从已配置的模型中选一个，或填写服务提供的模型 ID。</span></label>
<label class="field"><span class="field-label">API Key</span><input type="password" name="api_key" value="" autocomplete="new-password" placeholder="{'已配置，留空保留' if settings.get('key_configured') else '填写这个服务的 Key'}"></label>
<label class="field field-wide"><span class="field-label">同一 API 的备用模型<small>可选</small></span><input type="text" name="fallback_models" value="{_esc(settings.get('fallback_models'))}" placeholder="例如 model-b, model-c"><span class="field-hint">首选失败后才按顺序尝试，仍受整轮次数和超时限制；不是随机挑选。{'另有跨 API 备用：' + _esc(settings['fallback_provider']) + '（沿用已有配置）。' if settings.get('fallback_provider') else '没有配置跨 API 备用。'}</span></label>
<div class="chat-choice-summary field-wide" data-chat-choice-summary aria-live="polite">QQ 和桌边聊天的首选：<strong>{_esc(provider)} · {_esc(settings.get('primary_model') or '还没选模型')}</strong><span>保存后由你重启机器人生效，判断 API 在对应页面单独配置。</span></div>
<label class="field field-wide"><span class="field-label">每日 AI 调用次数上限</span><input type="number" name="daily_budget" min="0" value="{_esc(settings.get('daily_budget', 3000))}"><span class="field-hint">按调用次数计，不是金额。可以先保守一点，之后随时调整。</span></label></div></section>
<div class="setup-actions"><button type="submit">保存配置 <span aria-hidden="true">→</span></button><span class="form-footer-note">保存不调用模型，不消耗额度。</span></div></form>
<aside class="setup-aside"><div class="welcome-note"><span class="aside-kicker">YOUR NEW REGULAR</span><h2 class="aside-title">先认识，再熟悉。</h2><p>从一句招呼开始。群里的梗、大家的称呼，还有它偶尔接偏的话，都可以成为下一次改进的起点。</p></div>
<div class="setup-checklist"><h3>准备清单</h3>
<div class="checklist-item {'complete' if qq_ready else 'pending'}"><span>QQ 身份 <small>{'已填写' if qq_ready else '待填写'}</small></span></div>
<div class="checklist-item {'complete' if model_ready else 'pending'}"><span>回复模型 <small>{'已填写' if model_ready else '待填写'}</small></span></div>
<div class="checklist-item pending"><span>启动后确认 QQ 连接</span></div><p class="field-hint">{readiness}</p></div>
<div class="welcome-note"><span class="tag lilac">之后还可以</span><h3>画图、语音、到点提醒</h3><p>先聊起来，再添喜欢的能力。判断 API 也负责提醒审核，未配置时不会保存新提醒。</p><a class="text-link" href="/capabilities">选择能力与服务 →</a><a class="text-link" href="/decision">配置判断 API →</a></div></aside></div>
<details class="advanced"><summary>配置和数据保存在哪里？</summary><p>实例：<code>{_esc(settings.get('instance_dir'))}</code></p>
<p>配置：<code>{_esc(settings.get('config_file'))}</code></p><p>聊天与私有数据：<code>{_esc(settings.get('data_dir'))}</code></p>
<p>源码可以升级，实例目录请保留。不要把配置、群聊记录或密钥推送到公开仓库。</p></details>'''
    return _page("QQ 与模型", body, "setup")


# 能力与服务：给人看的那一页。一项能力一行（名字、一句话状态、开关），细节点开才看。
# 开关点了就存（capabilities.set_enabled，只写那一项）；模型、上限这类设置在展开的表单里单独保存。
_CAPABILITIES = (
    ("vision", "读图", "看懂群里发的图片"),
    ("image", "画图", "按群友的要求画图，也能画短动图"),
    ("voice_input", "听语音", "听懂群里的语音消息"),
    ("voice_output", "发语音", "偶尔用一句语音回群友"),
)
_CAP_ICONS = {
    "vision": '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "image": '<path d="m15 5 4 4L9 19H5v-4Z"/><path d="m13 7 4 4"/>',
    "voice_input": '<path d="M6 8.5a6 6 0 1 1 9.4 4.9c-1 .7-1.4 1.6-1.4 2.8a3 3 0 0 1-5.6 1.4"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0"/>',
    "voice_output": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
}


def _cap_state(name, row):
    from regular import capabilities
    return capabilities.state_line(name, row)


def _cap_snapshot(sections):
    from regular import capabilities
    return capabilities.overview(sections)


def _cap_runtime(cfg, runner):
    """Whether a switch reaches the running bot now, later, or only after an update."""
    snapshot = _read_json(cfg.STATUS_FILE) or {}
    managed = runner.snapshot() if runner is not None else {}
    reported = snapshot.get("capabilities") or {}
    matching = bool(managed.get("running") and not managed.get("stale") and snapshot.get("pid") == managed.get("pid"))
    if matching and reported.get("live_flags") == 1:
        return "ok", "机器人在运行：开关点了就对新消息生效；模型、地址、上限下次启动机器人时生效。", reported
    if managed.get("running"):
        return "todo", "正在运行的机器人还不认这里的开关：更新并重启后才生效。保存不会改动旧进程。", {}
    return "off", "机器人没在运行：这里的设置会在下次启动时用上。", {}


def _capabilities_page(cfg, request, csrf, runner=None):
    from regular import capabilities
    sections = capabilities.public_settings(cfg)
    chat = instance_settings.public_settings(cfg)
    judgment = decision_settings.public_settings(cfg.DATA_DIR)
    tone, timing, reported = _cap_runtime(cfg, runner)
    saved = str(request.query.get("saved") or "")
    overridden = {key for row in sections.values() for key in row.get("overridden") or ()}

    def switch(flag, on, label, *, parent_on=True):
        locked = capabilities.FLAGS[flag][0] in overridden
        title = ' title="由启动环境决定，这里改不了"' if locked else ""
        return (f'<form method="post" action="/capabilities/toggle" class="cap-toggle" data-cap-toggle>'
                f'<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="flag" value="{flag}">'
                f'<button type="submit" class="cap-switch" name="enabled" value="{"false" if on else "true"}" role="switch"'
                f' aria-checked="{"true" if on else "false"}" aria-label="{_esc(label)}"{title}'
                f'{" disabled" if locked or not parent_on else ""}><span aria-hidden="true"></span></button></form>')

    def field(section, row, name, label, *, secret=False, number=False, hint="", choices=None, placeholder=""):
        value = row.get(name, "")
        field_id = f"cap-{section}-{name}"
        if choices is not None:
            control = f'<select id="{field_id}" name="{name}">' + "".join(
                f'<option value="{_esc(key)}"{" selected" if str(value) == key else ""}>{_esc(text)}</option>'
                for key, text in choices) + '</select>'
        else:
            kind = "password" if secret else "number" if number else "text"
            if secret:
                placeholder = "已配置，留空就不改" if row.get("key_configured") else (placeholder or "粘贴 API Key")
            attrs = (' min="0" step="1"' if number else '') + (' autocomplete="new-password"' if secret else '')
            control = (f'<input id="{field_id}" type="{kind}" name="{name}" value="{_esc("" if secret else value)}"'
                       f'{attrs} placeholder="{_esc(placeholder)}">')
        note = f'<small>{_esc(hint)}</small>' if hint else ''
        return f'<label class="cap-field" for="{field_id}"><span>{label}</span>{control}{note}</label>'

    def settings_form(section, title, main, advanced="", extra=""):
        fold = (f'<details class="cap-advanced"><summary>高级</summary><div class="cap-fields">{advanced}</div></details>'
                if advanced else '')
        return (f'<form method="post" action="/capabilities/save" class="cap-form" data-cap-form>'
                f'<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="section" value="{section}">'
                f'{extra}<div class="cap-fields">{main}</div>{fold}'
                f'<footer><button type="submit">保存{title}设置</button><span data-cap-form-status aria-live="polite">'
                f'{"已保存。" if saved == section else ""}</span></footer></form>')

    def sub_switch(flag, on, parent_on, label, hint):
        return (f'<div class="cap-sub" data-cap-sub="{flag}">{switch(flag, on, label, parent_on=parent_on)}'
                f'<div><strong>{label}</strong><small>{_esc(hint)}</small></div></div>')

    vision, drawing, hearing, speech = (sections[name] for name in ("vision", "image", "voice_input", "voice_output"))
    providers = [("", "沿用聊天服务"), ("openai", "OpenAI 兼容"), ("zhipu", "智谱"), ("gemini", "Gemini")]
    deps = speech.get("dependencies") or {}
    bodies = {
        "vision": settings_form(
            "vision", "读图",
            field("vision", vision, "provider", "用哪个服务", choices=providers)
            + field("vision", vision, "model", "模型", placeholder=vision.get("effective_model") or "沿用该服务的首选模型",
                    hint="留空就沿用所选服务的首选模型；要选支持图片的模型。")
            + field("vision", vision, "daily_limit", "每天最多看几张", number=True),
            field("vision", vision, "input_mode", "图片怎么交给模型",
                  choices=[("download", "先下载到本机再发"), ("url", "直接发图片链接")])),
        "image": sub_switch("gif", drawing.get("gif_enabled", False), drawing.get("enabled", False),
                            "也画动图", "短循环动图，和静图共用额度。")
        + settings_form(
            "image", "画图",
            field("image", drawing, "model", "模型")
            + field("image", drawing, "api_key", "API Key", secret=True, placeholder="沿用 OpenAI 的 Key；要用别的就填这里")
            + field("image", drawing, "daily_limit", "每天最多画几张", number=True)
            + field("image", drawing, "user_daily_limit", "每人每天最多几张", number=True),
            field("image", drawing, "base_url", "接口地址", hint="换地址时要一起填那个服务的 Key。")
            + field("image", drawing, "quality", "画质") + field("image", drawing, "size", "静图尺寸")
            + field("image", drawing, "gif_size", "动图尺寸"))
        + ('' if judgment.get("key_configured") else
           '<p class="cap-note">画之前会请判断帮手看一眼题材；它还没配置，现在照常画。<a href="/decision">配置判断帮手</a></p>'),
        "voice_input": sub_switch("transcription", hearing.get("transcription_enabled", True), hearing.get("enabled", False),
                                  "QQ 没带文字时用模型转写", "关掉后只用 QQ 随消息带的转写，不额外花钱。")
        + settings_form("voice_input", "听语音", field("voice_input", hearing, "model", "转写模型")),
        "voice_output": settings_form(
            "voice_output", "发语音",
            field("voice_output", speech, "model", "模型") + field("voice_output", speech, "voice", "音色")
            + field("voice_output", speech, "daily_limit", "每天最多发几条", number=True),
            field("voice_output", speech, "node", "Node 路径") + field("voice_output", speech, "silk_entry", "SILK 编码器路径"),
            extra='<p class="cap-deps">' + "".join(
                f'<span class="{"ok" if ok else "todo"}">{label}：{"找到了" if ok else "没找到"}</span>'
                for label, ok in (("本机 Node", deps.get("node")), ("SILK 编码器", deps.get("silk")))) + '</p>'),
    }

    rows = []
    for name, title, summary in _CAPABILITIES:
        row = sections[name]
        state_tone, state = _cap_state(name, row)
        running = ""
        if reported:
            on = (reported.get("enabled") or {}).get(name)
            model = (reported.get("models") or {}).get(name)
            running = (f'<p class="cap-running">机器人正在用：{"开启" if on else "关闭"}'
                       f'{" · 模型 " + _esc(model) if isinstance(model, str) and model else ""}</p>')
        env = ('<p class="cap-note">启动环境覆盖了 ' + "、".join(_esc(k) for k in row.get("overridden") or ())
               + '，要在启动服务的地方改。</p>') if row.get("overridden") else ''
        missing = row.get("missing") or []
        need = (f'<p class="cap-note cap-need">还需要：{"、".join(_esc(m) for m in missing)}</p>'
                if row.get("enabled") and missing and state_tone == "todo" else '')
        # The whole row opens the settings; the switch sits beside it, outside <summary>.
        rows.append(
            f'<article class="cap-item" id="{name}" data-cap="{name}" data-tone="{state_tone}">'
            f'<details class="cap-more"{" open" if saved == name else ""}><summary class="cap-head">'
            f'<span class="cap-icon" aria-hidden="true"><svg viewBox="0 0 24 24">{_CAP_ICONS[name]}</svg></span>'
            f'<span class="cap-title"><strong>{title}</strong><small>{summary}</small></span>'
            f'<span class="cap-state" data-cap-state>{_esc(state)}</span><span class="cap-open">设置</span></summary>'
            f'<div class="cap-body">{need}{running}{env}{bodies[name]}</div></details>'
            f'{switch(name, row.get("enabled", False), "允许" + title)}</article>')

    voice_source = {"dedicated": "已配置专用 Key", "openai": "沿用 OpenAI 的 Key"}.get(hearing.get("key_source"), "还没有 Key")
    voice_form = settings_form("voice", "语音服务",
                               field("voice", hearing, "voice_api_key", "OpenAI 语音 Key", secret=True),
                               field("voice", hearing, "base_url", "接口地址", hint="只支持官方 OpenAI 接口。"))
    judged = judgment.get("key_configured")
    shared = (
        '<div class="cap-shared">'
        f'<a class="cap-chip" href="/setup#chat-model"><span>聊天模型</span><strong>{_esc(chat.get("primary_model") or "还没选")}</strong>'
        f'<small>{_esc(chat.get("active_provider") or "")} · 更换</small></a>'
        f'<a class="cap-chip{"" if judged else " todo"}" href="/decision"><span>判断帮手</span>'
        f'<strong>{_esc(judgment.get("model")) if judged else "还没配置"}</strong><small>接话判断、提醒审核、画前检查</small></a>'
        f'<details class="cap-chip cap-chip-voice{"" if hearing.get("key_configured") else " todo"}" id="voice"{" open" if saved == "voice" else ""}>'
        f'<summary><span>语音服务</span><strong>{voice_source}</strong><small>听语音、发语音共用 · 设置</small></summary>'
        f'<div class="cap-body">{voice_form}</div></details></div>')
    body = (
        '<header class="cap-page-head"><h1>能力与服务</h1><p>聊天之外，她还能看图、画图、听语音、发语音。开关点了就保存。</p></header>'
        f'<p class="cap-runtime" data-tone="{tone}" data-cap-runtime>{timing}</p>'
        f'<section class="cap-page" aria-label="能力">{shared}<div class="cap-list">{"".join(rows)}</div>'
        '<p class="cap-foot">保存不会自动重启机器人。桌宠的动作和特效在人物的右键菜单里。</p></section>')
    return _page("能力与服务", body, "capabilities")


def _runtime_controls(cfg, csrf, runner):
    settings = instance_settings.public_settings(cfg)
    managed = runner.snapshot()
    snapshot, state = _status(cfg)
    age = time.time() - float(snapshot.get("updated_at") or 0) if snapshot else None
    online = bool(managed.get("gateway_connected"))
    if managed.get("owned") and managed.get("running"):
        controls = f'<form method="post" action="/runtime/stop"><input type="hidden" name="csrf" value="{_esc(csrf)}"><button class="secondary">停止机器人</button></form>'
    elif managed.get("external"):
        controls = '<a class="button secondary" href="/capabilities">调整能力 →</a>'
    elif settings.get("ready"):
        controls = f'<form method="post" action="/runtime/start"><input type="hidden" name="csrf" value="{_esc(csrf)}"><button>启动机器人</button></form>'
    else:
        controls = '<a class="button" href="/setup">连接 QQ 与模型 →</a>'
    owner_path = getattr(cfg, "OWNER_FILE", str(Path(cfg.DATA_DIR) / "owner.txt"))
    owner = bool(_read_text(owner_path).strip())
    if online:
        headline = '已连上 QQ'
        detail = ('在群里叫她的名字，就能聊起来。' if owner
                  else '先给机器人发一条私聊，绑定你为维护者；再邀请她进群。')
    elif managed.get("running"):
        headline, detail = '等待 QQ 连接确认', '进程已启动。若迟迟没有连上，请检查 QQ 配置与运行日志。'
    else:
        headline, detail = '还没开始运行', '配置已保存，启动后就能在群里和她聊天。' if settings.get('ready') else '先填 QQ 和聊天模型，其余能力可以以后再开。'
    if age is not None and age > 120:
        detail = '运行状态已超过两分钟未更新，不能据此判断在线。请检查原进程或下方启动日志。'
    progress = ""
    if not (settings.get("ready") and online and owner):
        milestones = (("QQ 与模型", settings.get("ready")), ("QQ 连接", online), ("私聊认主", owner))
        progress = '<ol class="wx-progress" aria-label="首次连接进度">' + ''.join(
            f'<li class="{"wx-done" if done else ""}"><span>{"✓" if done else index}</span>{name}</li>'
            for index, (name, done) in enumerate(milestones, 1)) + '</ol>'
    external = ('<small class="wx-managed">由其他终端或常驻服务管理，需在原启动方式中启停。</small>'
                if managed.get("external") else "")
    return f'''<section class="wx-status" aria-label="QQ 连接状态"><div><span class="wx-live-dot {'wx-online' if online else ''}" aria-hidden="true"></span><h2>{headline}</h2><p>{detail}</p>{external}</div><div class="wx-status-action">{controls}</div>{progress}</section>'''


def _agent_brief(cfg):
    directory = instance_settings.public_settings(cfg)["instance_dir"]
    code_root = Path(__file__).resolve().parent
    command = shlex.join([sys.executable, "-m", "regular", "--directory", directory,
                          "checkup", "--day", "today"])
    return f'''请帮助我改进 The Regular · 老群友，让它更适应我的 QQ 群。
运行实例（私有配置和数据）：{directory}
当前安装代码位置：{code_root}

先在源码工作区阅读 AGENTS.md 和 .agents/skills/regular-iterate/SKILL.md。
如果当前只有安装包，先准备独立源码工作区，保留上面的实例目录，不把私有数据拷进公开仓库。
先运行只读体检：{command}
根据我指出的表现找到当轮输入和实际送达结果，区分代码问题与提示词问题，保存回归用例。
修改后在相同现场比较新旧版本，同时检查正常场景是否被改坏。
任何含真实群聊的外发评测，先用 --plan 告诉我会把哪些内容发给哪些模型，获得授权后再 --allow-paid。
源码 ZIP 没有 Git 历史时，用基线目录与候选目录比较，不假定 HEAD 存在。
把结论、未覆盖项和上线建议交给我；提交、发布、重启机器人都等我明确要求。

我观察到的问题：
（在这里补一句描述，或把截图交给 Agent；这份交接说明本身不包含群聊或密钥。）
'''


def _iterate_page(cfg):
    code_root = Path(__file__).resolve().parent
    source_ready = (code_root / "AGENTS.md").is_file() and (code_root / ".agents/skills/regular-iterate/SKILL.md").is_file()
    source_note = ('源码工作区已带上 AGENTS.md 与自迭代 skill。让你的编码 Agent 打开这个目录。'
                   if source_ready else '当前是运行安装包。要修改机器人，请先下载源码工作区；源码包自带 AGENTS.md 和自迭代 skill。实例数据继续用原目录。')
    body = f'''<header class="wx-heading"><div><h1>让 Agent 帮忙</h1><p>把接偏的那句话交给编码 Agent，找原因、改一版，再比较效果。</p></div></header>
<section class="wx-handoff"><div class="wx-handoff-main"><span class="wx-note">从你的一句反馈开始</span><h2>复制说明，带上截图或原话</h2><p>发给你常用的编码 Agent。说明里已写好实例位置和自迭代流程。</p><button type="button" data-copy-brief>复制给 Agent <span aria-hidden="true">↗</span></button><p data-copy-status aria-live="polite"></p></div>
<ol class="wx-handoff-steps"><li><strong>你说哪里不对</strong><span>一句描述或一张截图就够了</span></li><li><strong>Agent 找现场、做对比</strong><span>保留正常场景，检查有没有改坏</span></li><li><strong>你看结果，再决定上线</strong><span>付费评测和重启都等你确认</span></li></ol></section>
<p class="wx-source-note">{source_note}</p>
<details class="wx-disclosure"><summary>完整交接说明</summary><textarea class="wx-brief" readonly aria-label="交给 Agent 的任务说明" data-agent-brief>{_esc(_agent_brief(cfg))}</textarea></details>
<div class="wx-links"><a href="/iterate/brief">下载说明 .md ↓</a><a href="https://github.com/dubianche123/the-regular-qq-bot/releases/latest" target="_blank" rel="noreferrer">获取源码包 ↗</a><a href="/checkup">先看每日体检 →</a></div>
<p class="wx-footnote">面板不会自动上传聊天。交接说明只含本机路径与操作流程，不含群聊或密钥。</p>'''
    return _page("让 Agent 改进", body, "iterate")


def _chat_route_card(cfg, runner=None):
    settings = instance_settings.public_settings(cfg)
    configured = f"{settings['active_provider']} · {settings['primary_model'] or '还没选模型'}"
    snapshot, _state = _status(cfg)
    managed = runner.snapshot() if runner is not None else {}
    rows = (snapshot.get("providers") or {}).get("providers") or []
    live = bool(managed.get("running") and not managed.get("stale")
                and snapshot.get("pid") == managed.get("pid") and rows)
    if live:
        first = rows[0]
        models = first.get("models") or []
        active = f"{first.get('name') or first.get('label') or '已配置服务'} · {models[0] if models else '未报告模型'}"
        changed = first.get("name") != settings["active_provider"] or models != settings["model_options"]
        detail = (f"已保存的新选择：{configured}。由你重启机器人后生效。" if changed
                  else "QQ 回复和这里共用此首选；失败时才尝试已配置的备用。")
        label = "运行中的聊天首选"
    else:
        label, active = "已保存的聊天首选", configured
        detail = "还没有可核对的运行模型信息；启动后以进程报告为准。"
    return (f'<div class="chat-route-card"><span class="route-label">{label}</span><strong>{_esc(active)}</strong>'
            f'<a href="/setup#chat-model">更换 ↗</a><details><summary>模型与操作说明</summary><p>{_esc(detail)} '
            '发送消息才会调用模型。能力调整需点确认卡；修改代码可生成 Agent 交接说明。</p></details></div>')


def _companion_page(cfg, request, csrf, runner=None):
    pet = request.query.get("mode") == "pet"
    suggestions = ("今天你忙了些什么？", "先别画图了", "你现在心情怎么样？")
    body = f'''<section class="companion-room" data-chat-room><header class="room-heading"><div><h1>聊两句</h1><p>聊聊天、看看今日概况，或让她帮你开关能力。</p></div><a class="room-home-link" href="/">工作台 ↗</a></header>
<div class="room-layout"><aside class="room-card" aria-label="她现在的样子">
<div class="room-scene" data-room-scene><span class="scene-window" aria-hidden="true"></span><span class="scene-glow" aria-hidden="true"></span><p class="scene-bubble" data-scene-bubble aria-live="polite" hidden></p>{_pet_sprite('idle', size='stage')}<span class="scene-floor" aria-hidden="true"></span></div>
<div class="room-status"><ul class="status-chips" data-presence-chips><li class="chip"><span class="dot"></span>正在看看她在不在…</li></ul>
<div class="mood-meter" data-mood-meter><span>心情</span><span class="meter" aria-hidden="true"><i></i></span><b data-mood-word>平稳</b></div></div>
<div class="room-recent"><h2>刚才</h2><ol data-presence-feed><li class="empty">她在群里被@、接话、画图时，这里会记一笔。只记类型，不显示群聊原文。</li></ol></div>
<div class="room-actions"><button type="button" data-room-action="shy">摸摸头</button><button type="button" data-room-action="eat">投喂饼干</button><button type="button" data-room-action="wave">打个招呼</button></div></aside>
<div class="chat-panel"><div class="chat-head"><span class="chat-avatar" aria-hidden="true"></span><div><strong>老群友</strong><p data-chat-status aria-live="polite">正在检查本机连接…</p></div><button type="button" class="icon-button" data-chat-refresh title="刷新连接状态" aria-label="刷新连接状态">↻</button></div>
<div class="chat-history" role="log" aria-live="polite" aria-label="本机对话" data-chat-history><div class="chat-message system"><span class="chat-speaker">本机小窗</span><p>机器人运行后，就能在这里和她聊聊。打开页面不会发消息，也不会请求模型。</p></div></div>
<div class="chat-suggestions" data-chat-suggestions>{"".join(f'<button type="button" data-suggestion>{_esc(text)}</button>' for text in suggestions)}</div>
<form class="chat-compose" action="/companion/chat" method="post" data-chat-form><input type="hidden" name="csrf" value="{_esc(csrf)}"><label class="sr-only" for="companion-message">想和老群友说的话</label><textarea id="companion-message" name="text" rows="2" maxlength="2000" placeholder="今天有什么想聊的？Enter 发送，Shift+Enter 换行" required data-chat-input></textarea><div class="chat-compose-footer"><span data-chat-count>0 / 2000</span><button type="submit" disabled data-chat-submit>发送 <span aria-hidden="true">↑</span></button></div></form>
{_chat_route_card(cfg, runner)}</div></div>
<nav class="room-shortcuts" aria-label="工作台快捷入口"><a href="/setup">设置</a><a href="/checkup">体检</a><a href="/">运行状态</a><a href="/iterate">让 Agent 帮忙</a><button type="button" class="text-button" data-actions-toggle aria-pressed="true" title="关闭后角色只保留静止姿势">角色动作：开</button></nav></section>'''
    return _page("和老群友聊聊", body, "companion", pet=pet)


def _knowledge(cfg, request):
    names = _knowledge_files(cfg)
    name = request.query.get("file") or (names[0] if names else "all.md")
    path = _knowledge_path(cfg, name)
    content = _read_text(path, limit=MAX_EDIT_BYTES)
    library = knowledge_mod.KnowledgeBase(cfg.KNOWLEDGE_DIR)
    parsed = library.parse_file(path)
    examples = {
        "agreement": "## 群里的小约定\n关键词：群规、约定\n请替换成你们已经确认的群约定。\n",
        "names": "## 一个称呼的来历\n关键词：称呼、昵称\n请填写群里常用的称呼及适用范围，不要填写私人身份编号。\n",
        "routine": "## 固定活动\n关键词：活动、时间\n请填写已经确定的时间和参与方式。这里提供聊天参考，不会创建定时提醒。\n",
    }
    template = request.query.get("template")
    drafted = not content.strip() and template in examples
    if drafted:
        content = examples[template]
    entries = "".join(
        f'<article class="knowledge-entry"><h3>{_esc(entry["title"])}</h3>'
        f'<div class="knowledge-keywords">{"".join(f"<span>{_esc(word)}</span>" for word in entry["keywords"])}</div>'
        f'<p>{_esc(entry["content"])}</p></article>' for entry in parsed)
    preview = entries or '<div class="knowledge-blank"><h3>还没有保存的条目</h3><p>先从下面的示例开始，改成你们真实的约定后再保存。</p></div>'
    saved = ('<div class="notice good">已保存；下一条相关消息会使用新内容。</div>'
             if request.query.get("saved") else "")
    cards = []
    for item in names:
        try:
            item_path = _knowledge_path(cfg, item)
        except web.HTTPBadRequest:
            continue
        item_entries = library.parse_file(item_path)
        scope = "所有群共享" if item == "all.md" else "指定群使用"
        summary = " · ".join(entry["title"] for entry in item_entries[:2]) or "还没有可用条目"
        cards.append(f'<a class="knowledge-file {"selected" if item == name else ""}" href="/knowledge?file={quote(item, safe="")}" '
                     f'{"aria-current=page" if item == name else ""}><span>{scope}<small>{len(item_entries)} 条</small></span>'
                     f'<strong>{_esc(item)}</strong><p>{_esc(summary)}</p></a>')
    scope = "所有群都能参考" if name == "all.md" else "只在文件名对应的群里使用"
    draft_notice = '<div class="notice">这是未保存的格式示例。请改成真实内容；尚未写入知识库。</div>' if drafted else ""
    template_links = " · ".join(f'<a href="/knowledge?file={quote(name, safe="")}&amp;template={key}#knowledge-editor">{label} ↗</a>'
                               for key, label in (("agreement", "群约定"), ("names", "称呼来历"), ("routine", "固定活动")))
    body = f'''
<header class="knowledge-hero"><div><span class="eyebrow">THINGS YOUR GROUP KNOWS</span><h1>有些事，不用每次都解释。</h1><p>把群里的约定、称呼和梗的来历放在这里。<br>聊到相关关键词时，老群友才取少量内容来参考。</p></div>{_mascot('knowledge')}</header>
{saved}{draft_notice}
<div class="knowledge-workspace"><aside class="knowledge-library"><div class="section-heading"><h2>知识文件</h2><span class="tag">{len(names)} 份</span></div>
<nav aria-label="知识文件">{''.join(cards) or '<p class="field-hint">还没有文件。可以先写一份所有群共享的知识。</p>'}</nav>
<a class="knowledge-shared" href="/knowledge?file=all.md">所有群共享：all.md →</a>
<details class="knowledge-new"><summary>给某个群单独写一份</summary><form method="get" action="/knowledge"><label class="field"><span class="field-label">知识文件名</span><input type="text" name="file" placeholder="群号.md" required><span class="field-hint">使用对应群号作文件名；同名条目优先使用群内版本。</span></label><button type="submit">打开空白草稿</button></form></details>
<p class="knowledge-permission">只由维护者在本机编辑。群聊不能写入这里，也不会自动把聊天当成事实。</p></aside>
<section class="knowledge-main"><div class="knowledge-file-heading"><div><span class="eyebrow">{scope}</span><h2>{_esc(name)}</h2></div><span class="tag lilac">解析到 {len(parsed)} 条</span></div>
<div class="knowledge-preview" aria-label="已保存知识条目">{preview}</div>
<form method="post" action="/knowledge/save" class="knowledge-editor" id="knowledge-editor" data-knowledge-form>
<input type="hidden" name="file" value="{_esc(name)}">
<div class="section-heading"><div><h2>写下要记住的事</h2><p class="field-hint">一条一个标题，关键词写大家实际会说的词。保存后，下一轮相关聊天即可参考。</p></div></div>
<div class="knowledge-composer"><label class="field"><span class="field-label">条目标题</span><input type="text" maxlength="80" placeholder="例如：奶茶局怎么报名" data-knowledge-title></label><label class="field"><span class="field-label">聊到哪些词时用？</span><input type="text" maxlength="160" placeholder="例如：奶茶、报名" data-knowledge-keywords></label><label class="field field-wide"><span class="field-label">具体内容</span><textarea rows="3" maxlength="3000" placeholder="写已经确认的事实或约定，尽量简短。" data-knowledge-body></textarea></label><div class="field-wide row"><button type="button" class="secondary" data-knowledge-add>加入下方草稿</button><span class="field-hint" data-knowledge-status aria-live="polite">加入草稿后，仍需点击保存才生效。</span></div></div>
<label class="field knowledge-source"><span class="field-label">完整草稿<small>可直接修改已有条目</small></span><textarea name="content" spellcheck="false" rows="9" data-knowledge-source>{_esc(content)}</textarea></label>
<div class="knowledge-save-row"><button type="submit">保存这份知识 <span aria-hidden="true">→</span></button><span class="field-hint">保存不调用模型，也不需要重启。</span></div>
</form>
<details class="knowledge-help"><summary>不知道写什么？看格式和使用说明</summary><p>从一个示例开始：{template_links}。仅空白文件会载入示例，不覆盖已有内容，也不会自动保存。</p><pre>## 奶茶局怎么报名\n关键词：奶茶、报名\n请在活动当天的群公告下回复。\n</pre><p>每个 <code>## 标题</code> 是一条；没有关键词行时使用标题作关键词。命中后只取少量内容，不会每轮全部发给模型。敏感词条目会在解析时跳过，保存后可在上方核对有效条目。</p><p>适合写：共同约定、固定活动、已经确认的称呼。不适合写：密码、私人身份编号、未确认的传闻。这里的活动时间只是聊天参考，需要到点提醒时仍应在 QQ 明确设置提醒。</p></details></section></div>
'''
    return _page("知识库", body, "knowledge")


async def _knowledge_save(cfg, request):
    form = await request.post()
    name = str(form.get("file") or "")
    content = str(form.get("content") or "")
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        raise web.HTTPBadRequest(text="文件过大")
    path = _knowledge_path(cfg, name)
    storage.atomic_write_text(path, content)
    raise web.HTTPFound(f"/knowledge?file={quote(name, safe='')}&saved=1")


def _memory(cfg, request):
    files = _memory_files(cfg)
    name = request.query.get("file") or (files[0] if files else "")
    content = ""
    if name:
        if os.path.basename(name) != name or name not in files:
            raise web.HTTPBadRequest(text="非法记忆文件名")
        content = _read_text(os.path.join(cfg.MEMORY_DIR, name))
    options = "".join(
        f'<option value="{_esc(item)}"{" selected" if item == name else ""}>{_esc(item)}</option>'
        for item in files)
    picker = (f'<form method="get" action="/memory" class="row"><select name="file">'
              f'{options}</select><button type="submit">打开</button></form>') if files else ""
    memory_content = f"<pre>{_esc(content)}</pre>" if content else _empty_state(
        "群记忆还在慢慢攒", "聊过、整理过的片段会留在这里。现在还没有可显示的每日记忆。", state="sleeping")
    body = f"""
<h1>记忆</h1>
<p class="muted">这里只读。摘要由每日压缩生成；要纠正具体事实，请改知识库。</p>
{picker}
{memory_content}
"""
    return _page("记忆", body, "memory")


async def _search(cfg, request):
    query = (request.query.get("q") or "").strip()[:120]
    rows = ""
    notice = ""
    if query:
        if not cfg.SEARCH_ENABLED:
            notice = '<div class="notice bad">搜索已关闭（SEARCH_ENABLED=false）。</div>'
        else:
            results = await search_mod.search_web(query)
            if results is None:
                notice = '<div class="notice bad">搜索失败：网络或 DuckDuckGo 不可用。</div>'
            elif not results:
                notice = '<div class="notice bad">没有找到结果。</div>'
            else:
                rows = "<table><thead><tr><th>结果</th></tr></thead><tbody>" + "".join(
                    f'<tr><td><a href="{_esc(row["url"])}" target="_blank" '
                    f'rel="noreferrer noopener">{_esc(row["title"])}</a><br>'
                    f'<span class="muted">{_esc(row["snippet"])}</span></td></tr>'
                    for row in results) + "</tbody></table>"
    body = f"""
<h1>搜索调试</h1>
<p class="muted">这里只验证 DuckDuckGo 是否可用；群里的正式入口是 <span class=mono>搜索 关键词</span>。</p>
<form method="get" action="/search" class="row">
<input type="text" name="q" value="{_esc(query)}" placeholder="关键词">
<button type="submit">搜索</button>
</form>
{notice}{rows}
"""
    return _page("搜索", body, "search")


def _decision(cfg, request, csrf):
    """判断 API 配置页：只显示 endpoint/model 与「是否已配 key」，永不回显 key。"""
    settings = decision_settings.public_settings(cfg.DATA_DIR)
    saved_path = os.path.join(str(cfg.DATA_DIR), decision_settings.FILENAME)
    notice = ""
    if request.query.get("saved"):
        notice = '<div class="notice good">已保存。后续判断会读取这份配置；保存本身没有调用模型。</div>'
    elif not settings["key_configured"] and os.path.isfile(saved_path):
        # 文件在但读不出合法配置：只给通用提示，不回显文件内容
        notice = '<div class="notice bad">已保存的配置暂时无法读取，请重新填写并保存。</div>'
    state = ("已保存配置（下次保存仍需重新输入 Key）" if settings["key_configured"]
             else "尚未配置")
    body = f"""
<h1>判断 API</h1>
<p class="muted">给老群友接一个判断帮手：理解反馈、挑选记忆、拿捏介入。
这里只保存独立配置，不改当前聊天模型，也不重启机器人。需要验证时，
可在「让 Agent 改进」页取得交接说明，请自己的编码 Agent 先预览调用范围，再经你同意测试。</p>
{notice}
<div class="card">
<p><span class="label">接口形态</span>：OpenAI 原生 GPT Decisions
（POST /v1/decisions），<strong>不是</strong> /chat/completions。</p>
<form method="post" action="/decision/save" class="decision">
<input type="hidden" name="csrf" value="{_esc(csrf)}">
<p><label>Endpoint URL<br>
<input type="text" name="endpoint" value="{_esc(settings['endpoint'])}" required></label></p>
<p><label>模型<br>
<input type="text" name="model" value="{_esc(settings['model'])}" required></label></p>
<p><label>API Key（每次保存都重新输入，页面永不回显）<br>
<input type="password" name="api_key" value="" autocomplete="off" required></label></p>
<button type="submit">保存配置（暂不调用）</button>
</form>
<p class="muted">当前状态：{state}</p>
</div>
"""
    return _page("判断 API", body, "decision")


async def _decision_save(cfg, request, csrf):
    """保存判断 API 配置：CSRF + 字段校验都过才落盘，失败一律通用 400。"""
    form = await request.post()
    if not hmac.compare_digest(str(form.get("csrf") or ""), str(csrf)):
        raise web.HTTPBadRequest(text="请求无效")
    try:
        decision_settings.save_settings(
            cfg.DATA_DIR,
            endpoint=str(form.get("endpoint") or ""),
            model=str(form.get("model") or ""),
            api_key=str(form.get("api_key") or ""))
    except (ValueError, OSError):
        raise web.HTTPBadRequest(text="保存失败：字段不合法或无法写入") from None
    raise web.HTTPFound("/decision?saved=1")


def _checkup_dirs(cfg):
    root = Path(cfg.DATA_DIR)
    return {
        "checkup_dir": getattr(cfg, "CHECKUP_DIR", str(root / "checkups")),
        "interaction_dir": getattr(cfg, "INTERACTION_LOG_DIR", str(root / "interactions")),
        "turn_dir": getattr(cfg, "TURN_LOG_DIR", str(root / "turns")),
        "archive_dir": getattr(cfg, "ARCHIVE_DIR", str(root / "archive")),
        "state_file": getattr(cfg, "STATE_FILE", str(root / "state.json")),
    }


def _checkup_days(dirs):
    from regular import checkup as checkup_mod
    days = set()
    for key, suffix in (("checkup_dir", ".json"), ("interaction_dir", ".jsonl"),
                        ("turn_dir", ".jsonl"), ("turn_dir", ".jsonl.gz")):
        try:
            for name in os.listdir(dirs[key]):
                if name.endswith(suffix):
                    day = name[:-len(suffix)]
                    try:
                        checkup_mod.resolve_day(day)
                        days.add(day)
                    except ValueError:
                        pass
        except OSError:
            pass
    return sorted(days, reverse=True)


def _checkup(cfg, request, csrf):
    """每日体检页：列出可疑候选与前后上下文，支持直接在面板标 yes/no。"""
    from regular import checkup as checkup_mod
    dirs = _checkup_dirs(cfg)
    days = _checkup_days(dirs)
    raw_day = (request.query.get("day") or "").strip()
    try:
        day = checkup_mod.resolve_day(raw_day or (days[0] if days else None))
    except ValueError as err:
        raise web.HTTPBadRequest(text="非法日期") from err
    if day not in days:
        days = [day, *days]
    policy_error = False
    try:
        policy = checkup_mod.load_policy(getattr(cfg, "CHECKUP_POLICY_FILE", ""))
    except ValueError:
        policy = None
        policy_error = True
    out = checkup_mod.report(day, **dirs, policy=policy)
    if not policy_error:
        try:
            checkup_mod.write(out, dirs["checkup_dir"])
        except OSError:
            pass
    warning = ('<p class="bad">体检策略文件无效，本页使用默认策略，已有报告保持不变。'
               '请修复策略文件，或由维护者执行 <code>regular policy reset checkup</code>。</p>'
               if policy_error else "")
    options = "".join(
        f'<option value="{_esc(d)}"{" selected" if d == day else ""}>{_esc(d)}</option>'
        for d in days)
    sig_rows = []
    for sig, label in checkup_mod.LABELS.items():
        p = out["precision"].get(sig)
        rate = f"{p['precision']:.0%}（{p['yes']}/{p['yes'] + p['no']}）" if p else "—"
        sig_rows.append(
            f"<tr><td>{_esc(label)}</td><td>{out['counts'][sig]}</td><td>{_esc(rate)}</td></tr>")
    cards = []
    for f in out["findings"]:
        clock = time.strftime("%H:%M:%S", time.localtime(f["ts"]))
        verdict = f.get("verdict")
        badge = ('<span class="wx-verdict">已确认问题</span>' if verdict == "yes"
                 else '<span class="wx-verdict">已排除</span>' if verdict == "no"
                 else '<span class="wx-verdict wx-pending">待看</span>')
        ctx_html = f'<pre class="wx-context">{_esc(chr(10).join(f["context"]))}</pre>' if f.get("context") else ""
        raw_cmds = checkup_mod.case_add_commands(day, f)
        cmds = ""
        if raw_cmds:
            cmd_list = "；".join(f"<code>{_esc(cmd)}</code>" for cmd in raw_cmds)
            cmds = f'<details class="wx-case-commands"><summary>交给 Agent 的用例命令</summary><p>{cmd_list}</p></details>'
        cards.append(f"""
<details class="wx-finding">
<summary><span class="wx-finding-title"><strong>{_esc(f['label'])}</strong><span>{_esc(f['detail'])}</span></span><span class="wx-finding-meta"><time>{_esc(clock)}</time>{badge}</span></summary>
<div class="wx-finding-body"><p class="wx-finding-id">条目 {_esc(f['id'])}</p>
{ctx_html}
  <form method="post" action="/checkup/label" class="row">
    <input type="hidden" name="csrf" value="{_esc(csrf)}">
    <input type="hidden" name="day" value="{_esc(day)}">
    <input type="hidden" name="id" value="{_esc(f['id'])}">
    <button type="submit" name="verdict" value="yes">是个问题</button>
    <button type="submit" name="verdict" value="no" class="secondary">这次没问题</button>
  </form>
{cmds}</div>
</details>""")
    findings_html = "".join(cards) if cards else '<div class="wx-checkup-empty"><h2>这一天没有发现可疑的地方</h2><p>有哪句让你觉得不对，也可以直接把截图交给 Agent。</p><a href="/iterate">交给 Agent →</a></div>'
    i = out["inputs"]
    pending = sum(f.get("verdict") not in ("yes", "no") for f in out["findings"])
    body = f"""
<header class="wx-heading"><div><h1>每日体检</h1><p>看看哪些话可能接偏了，点开一条再看现场。</p></div><form method="get" action="/checkup" class="wx-date"><label class="sr-only" for="checkup-day">体检日期</label><select id="checkup-day" name="day">{options}</select><button class="secondary" type="submit">查看</button></form></header>
{warning}
<div class="wx-checkup-summary"><p><strong>{pending} 条待看</strong><span>共 {len(out['findings'])} 条候选 · 收到 {i['incoming']} 条消息 · 送达 {i['delivered']} 条回复</span></p><a href="/iterate">让 Agent 帮忙 →</a></div>
<section class="wx-findings" aria-label="体检候选">{findings_html}</section>
<details class="wx-disclosure"><summary>分类统计与扫描范围</summary><p class="muted">只读本机记录，不联网、不调模型。扫描每轮输入 {i['turns']} 轮、分诊 {i['triage']} 次，参考前 {i['baseline_days']} 天。</p><table><thead><tr><th>信号</th><th>条数</th><th>候选标注确认率</th></tr></thead><tbody>{"".join(sig_rows)}</tbody></table></details>
"""
    return _page("每日体检", body, "checkup")


async def _checkup_label(cfg, request, csrf):
    from regular import checkup as checkup_mod
    form = await request.post()
    if not hmac.compare_digest(str(form.get("csrf") or ""), str(csrf)):
        raise web.HTTPBadRequest(text="请求无效")
    finding_id = str(form.get("id") or "").strip()
    verdict = str(form.get("verdict") or "").strip()
    day = str(form.get("day") or "").strip()
    if verdict not in ("yes", "no") or not finding_id:
        raise web.HTTPBadRequest(text="标注参数不合法")
    dirs = _checkup_dirs(cfg)
    found = checkup_mod.find_finding(dirs["checkup_dir"], finding_id)
    if found is None:
        raise web.HTTPBadRequest(text="未找到该体检条目")
    checkup_mod.add_label(dirs["checkup_dir"], finding_id, verdict, signal=found.get("signal"))
    target_day = quote(day, safe="") if day else ""
    raise web.HTTPFound(f"/checkup?day={target_day}" if target_day else "/checkup")


def create_app(cfg, *, token):
    runner = PanelRuntime(cfg)

    @web.middleware
    async def auth(request, handler):
        supplied = (request.query.get("token") or request.headers.get(TOKEN_HEADER)
                    or request.cookies.get("panel_token"))
        if not supplied or not hmac.compare_digest(str(supplied), str(token)):
            raise web.HTTPUnauthorized(text="Panel token required.\n")
        if request.query.get("token"):
            query = {k: v for k, v in request.query.items() if k != "token"}
            target = request.rel_url.with_query(query)
            response = web.HTTPFound(str(target))
            response.set_cookie("panel_token", token, httponly=True,
                                samesite="Strict", path="/")
            raise response
        response = await handler(request)
        response.headers["X-Regular-Panel"] = "1"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'self'; img-src 'self'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        return response

    async def home(request):
        return _html(_dashboard(cfg, csrf=csrf, runner=runner))

    async def setup_page(request):
        return _html(_setup_page(cfg, request, csrf))

    async def capabilities_page(request):
        return _html(_capabilities_page(cfg, request, csrf, runner))

    async def checked_form(request):
        form = await request.post()
        if not hmac.compare_digest(str(form.get("csrf") or ""), csrf):
            raise web.HTTPBadRequest(text="请求无效，请刷新页面后重试")
        return form

    async def setup_save(request):
        form = await checked_form(request)
        try:
            instance_settings.save_settings(cfg, form)
        except (ValueError, OSError):
            raise web.HTTPBadRequest(text="保存失败：请检查接口地址、模型、每日上限与文件权限。") from None
        raise web.HTTPFound("/setup?saved=1")

    async def capabilities_save(request):
        form = await checked_form(request)
        from regular import capabilities
        try:
            capabilities.save_settings(cfg, form)
        except (ValueError, OSError):
            raise web.HTTPBadRequest(text="保存失败：请检查模型、接口、调用上限和本机依赖路径。") from None
        section = quote(str(form.get("section") or ""), safe="")
        raise web.HTTPFound(f"/capabilities?saved={section}#{section}")

    async def capabilities_toggle(request):
        """One switch, saved at once. JSON for the page script; a redirect without script."""
        form = await checked_form(request)
        from regular import capabilities
        flag = str(form.get("flag") or "")
        try:
            sections = capabilities.set_enabled(cfg, flag, form.get("enabled"))
        except (ValueError, OSError):
            raise web.HTTPBadRequest(text="没能保存这个开关，请刷新后重试。") from None
        section = {"gif": "image", "transcription": "voice_input"}.get(flag, flag)
        if "application/json" not in request.headers.get("Accept", ""):
            raise web.HTTPFound(f"/capabilities#{section}")
        return web.json_response({"ok": True, "flag": flag, "timing": _cap_runtime(cfg, runner)[1],
                                  **_cap_snapshot(sections)})

    async def runtime_start(request):
        await checked_form(request)
        try:
            runner.start()
        except (ValueError, OSError, RuntimeError):
            raise web.HTTPBadRequest(text="未能启动：请检查配置，或确认这个实例是否已由其他终端运行。") from None
        raise web.HTTPFound("/")

    async def runtime_stop(request):
        await checked_form(request)
        try:
            await runner.stop()
        except (ValueError, OSError, RuntimeError):
            raise web.HTTPConflict(text="此进程不是本面板启动的，或已由其他方式结束。请刷新状态。") from None
        raise web.HTTPFound("/")

    async def iterate_page(request):
        return _html(_iterate_page(cfg))

    async def iterate_brief(request):
        return web.Response(text=_agent_brief(cfg), content_type="text/plain", charset="utf-8",
                            headers={"Content-Disposition": 'attachment; filename="regular-agent-brief.md"'})

    async def companion_page(request):
        return _html(_companion_page(cfg, request, csrf, runner))

    def companion_state():
        from regular.local_chat import bridge_status
        status = bridge_status(cfg)
        managed = runner.snapshot()
        return {"available": bool(status.get("available")),
                "reason": str(status.get("reason") or ""),
                "qq_connected": bool(managed.get("gateway_connected"))}

    async def companion_status(request):
        return web.json_response(companion_state())

    async def companion_session(request):
        # Native clients use the same panel authentication and existing CSRF.
        # Reading a session never starts a process or sends a chat request.
        return web.json_response({"csrf": csrf, **companion_state()})

    cues = deque(maxlen=20)

    async def companion_presence(request):
        # Content-free agent state for the pet: no text, group or user identifiers.
        from regular import presence
        try:
            since = float(request.query.get("since", "nan"))
        except ValueError:
            since = float("nan")
        return web.json_response({**companion_state(), **presence.snapshot(cfg, since=since, cues=cues)})

    async def companion_cue(request):
        """Coding agents may ask the pet to act or say one short line; nothing reaches QQ."""
        form = await checked_form(request)
        from regular.pet_brain import CUE_ACTIONS
        action = str(form.get("action") or "")
        text = " ".join(str(form.get("text") or "").split())
        if action and action not in CUE_ACTIONS:
            return web.json_response({"error": "未知动作。", "actions": list(CUE_ACTIONS)}, status=400)
        if not action and not text:
            return web.json_response({"error": "请给出动作或一句话。"}, status=400)
        if len(text) > 120:
            return web.json_response({"error": "一句话最多 120 个字符。"}, status=400)
        cue = {"kind": "cue", "id": secrets.token_hex(6), "ts": round(time.time(), 3),
               "action": action, "text": text}
        cues.append(cue)
        return web.json_response({"queued": cue["id"]})

    async def pet_asset(request):
        name = request.match_info["name"]
        asset = files("regular").joinpath("assets", "pet", name)
        kind = {"webp": "image/webp", "json": "application/json"}.get(name.rsplit(".", 1)[-1])
        if "/" in name or "\\" in name or kind is None or not asset.is_file():
            raise web.HTTPNotFound()
        return web.Response(body=asset.read_bytes(), content_type=kind,
                            headers={"Cache-Control": "max-age=86400"})

    async def companion_chat(request):
        form = await checked_form(request)
        text = str(form.get("text") or "").strip()
        if not text or len(text) > 2000:
            return web.json_response({"error": "请发送 1–2000 个字符。"}, status=400)
        from regular.local_chat import LocalChatError, send_chat
        try:
            result = await send_chat(cfg, text)
        except LocalChatError as error:
            return web.json_response({"error": error.message}, status=error.status)
        # A proposal is only shown; confirming it calls /capabilities/toggle like any switch on the page.
        action = result.get("action")
        return web.json_response({"text": str(result.get("text") or ""), **({"action": action} if action else {})})

    async def panel_script(request):
        return web.Response(text=files("regular").joinpath("panel.js").read_text(encoding="utf-8"),
                            content_type="application/javascript", charset="utf-8")

    async def mascot(request):
        name = request.match_info["name"]
        if name not in MASCOT_ASSETS:
            raise web.HTTPNotFound()
        asset = files("regular").joinpath("assets", name)
        if not asset.is_file():
            raise web.HTTPNotFound()
        return web.Response(body=asset.read_bytes(), content_type="image/png")

    async def cleanup(app):
        await runner.close()

    async def knowledge(request):
        return _html(_knowledge(cfg, request))

    async def knowledge_save(request):
        return await _knowledge_save(cfg, request)

    async def memory(request):
        return _html(_memory(cfg, request))

    async def search(request):
        return _html(await _search(cfg, request))

    async def decision(request):
        return _html(_decision(cfg, request, csrf))

    async def decision_save(request):
        return await _decision_save(cfg, request, csrf)

    async def checkup_page(request):
        return _html(_checkup(cfg, request, csrf))

    async def checkup_label(request):
        return await _checkup_label(cfg, request, csrf)

    csrf = secrets.token_urlsafe(24)
    app = web.Application(middlewares=[auth])
    app.router.add_get("/assets/panel.js", panel_script)
    app.router.add_get("/assets/pet/{name}", pet_asset)
    app.router.add_get("/assets/{name}", mascot)
    app.router.add_get("/companion", companion_page)
    app.router.add_get("/companion/status", companion_status)
    app.router.add_get("/companion/session", companion_session)
    app.router.add_post("/companion/chat", companion_chat)
    app.router.add_get("/companion/presence", companion_presence)
    app.router.add_post("/companion/cue", companion_cue)
    app.router.add_get("/", home)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/setup/save", setup_save)
    app.router.add_get("/capabilities", capabilities_page)
    app.router.add_post("/capabilities/save", capabilities_save)
    app.router.add_post("/capabilities/toggle", capabilities_toggle)
    app.router.add_post("/runtime/start", runtime_start)
    app.router.add_post("/runtime/stop", runtime_stop)
    app.router.add_get("/iterate", iterate_page)
    app.router.add_get("/iterate/brief", iterate_brief)
    app.router.add_get("/checkup", checkup_page)
    app.router.add_post("/checkup/label", checkup_label)
    app.router.add_get("/knowledge", knowledge)
    app.router.add_post("/knowledge/save", knowledge_save)
    app.router.add_get("/memory", memory)
    app.router.add_get("/search", search)
    app.router.add_get("/decision", decision)
    app.router.add_post("/decision/save", decision_save)
    app.on_cleanup.append(cleanup)
    return app


def _html(response):
    return web.Response(text=response, content_type="text/html", charset="utf-8")


def run(cfg, *, host="127.0.0.1", port=8765, open_browser=False):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("panel only binds to loopback addresses")
    token = load_or_create_token(cfg)
    app = create_app(cfg, token=token)
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{int(port)}/?token={token}"
    print(f"Panel: {url}")
    print("Local only. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    web.run_app(app, host=host, port=int(port), print=None)
