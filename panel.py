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
from hashlib import sha256
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
        "setup": "连上 QQ，给她起个名字，再去群里打声招呼。",
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


# 侧栏按「你来这里做什么」分两组：天天用的四件事，加上配一次就好的设置。
# 判断、搜索检查在「模型与能力 → 高级」里；交给 Agent 和体检是同一件事（让她更好）；记忆是群知识的一页。
_NAV = (
    ("", (("/", "我的群友", "home"), ("/companion", "聊两句", "companion"),
          ("/checkup", "让她更好", "checkup"), ("/knowledge", "群知识", "knowledge"))),
    ("设置", (("/capabilities", "模型与能力", "capabilities"), ("/setup", "QQ 连接与身份", "setup"))),
)
_NAV_HOME = {"iterate": "checkup", "memory": "knowledge", "decision": "capabilities", "search": "capabilities"}


def _pending_findings(cfg):
    """体检里还没看的条数：只读最近一份已存的报告和标注，不在每次打开页面时重算体检。"""
    if cfg is None:
        return 0
    base = Path(_checkup_dirs(cfg)["checkup_dir"])
    try:
        latest = max(base.glob("*.json"), key=lambda path: path.name)
        findings = json.loads(latest.read_text(encoding="utf-8")).get("findings") or []
    except (ValueError, OSError, AttributeError):
        return 0
    labeled = set()
    try:
        for line in (base / "labels.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                labeled.add(json.loads(line).get("id"))
            except (ValueError, AttributeError):
                continue
    except OSError:
        pass
    return sum(1 for f in findings if isinstance(f, dict) and f.get("id") not in labeled
               and f.get("verdict") not in ("yes", "no"))


def _page(title, body, active="", refresh=0, *, pet=False, cfg=None):
    current = _NAV_HOME.get(active, active)
    pending = _pending_findings(cfg)
    badges = {"checkup": pending} if pending else {}

    def link(href, text, key):
        badge = (f'<span class="nav-badge" aria-label="{badges[key]} 条待看">{badges[key]}</span>'
                 if key in badges else '')
        return (f'<a class="nav-item {"active" if key == current else ""}" href="{href}"'
                f'{" aria-current=page" if key == current else ""}><span class="nav-icon">{_icon(key)}</span>'
                f'<span class="nav-text">{text}</span>{badge}</a>')

    links = "".join(('<span class="nav-label">' + label + '</span>' if label else '')
                    + "".join(link(*entry) for entry in entries) for label, entries in _NAV)
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


def _attention(cfg, settings, running):
    """首页只列需要你动手的例外：没配完的、缺东西的能力、体检里待看的。没有就什么都不列。"""
    items = []
    qq_ready = bool(settings.get("qq_app_id") and settings.get("qq_secret_configured"))
    if not qq_ready:
        items.append(("QQ 还没连接", "/setup"))
    elif not settings.get("ready"):
        items.append(("还没选聊天模型", "/capabilities#chat"))
    try:
        from regular import capabilities
        sections = capabilities.public_settings(cfg)
        for name, label in capabilities.NAMES:
            tone, words = capabilities.state_line(name, sections[name])
            if tone == "todo":
                items.append((f"{label}{words}", f"/capabilities#{name}"))
    except Exception:
        pass
    pending = _pending_findings(cfg)
    if pending:
        items.append((f"体检里有 {pending} 句可能接偏了", "/checkup"))
    return items


def _today(cfg):
    """今天的数字（只有计数，没有群聊原文）。"""
    from types import SimpleNamespace
    from regular import presence
    try:
        day = presence.day_summary(SimpleNamespace(INTERACTION_LOG_DIR=_checkup_dirs(cfg)["interaction_dir"]))
    except Exception:
        return {}
    return {"收到消息": day.get("messages", 0),
            "被叫到": sum(day.get(k, 0) for k in ("mention", "named", "quoted", "called")),
            "回了": day.get("said", 0) + day.get("chimed", 0) + day.get("proactive", 0),
            "画了": day.get("drew", 0), "发语音": day.get("voice", 0)}


def _home_status(cfg, csrf, runner, groups, budget):
    """她现在怎么样、你能做的那一个动作、首次使用时的进度，以及需要你看一眼的例外 —— 一张卡说完。"""
    settings = instance_settings.public_settings(cfg)
    managed = runner.snapshot() if runner is not None else {}
    snapshot, _ = _status(cfg)
    age = time.time() - float(snapshot.get("updated_at") or 0) if snapshot else None
    qq_ready = bool(settings.get("qq_app_id") and settings.get("qq_secret_configured"))
    online = bool(managed.get("gateway_connected") and not managed.get("stale"))
    running = bool(managed.get("running"))      # a fresh child may not have reported yet: still "connecting"
    owner = bool(_read_text(getattr(cfg, "OWNER_FILE", str(Path(cfg.DATA_DIR) / "owner.txt"))).strip())
    if online:
        tone, headline = "ok", "她在群里，正听着呢。"
        detail = "在群里叫她的名字就能聊起来。" if owner else "先由你发第一条 QQ 私聊认主，再邀请她进群。"
    elif running:
        tone, headline, detail = "todo", "正在连接 QQ…", "稍等一下；一直连不上的话，看看 QQ 连接和下方日志。"
    elif settings.get("ready"):
        tone, headline, detail = "idle", "都配好了，启动后就能进群。", "她不在运行时，群里的消息不会有人接。"
    elif qq_ready:
        tone, headline, detail = "todo", "还差一个聊天模型。", "选好模型，就可以启动了。"
    else:
        tone, headline, detail = "todo", "先让她连上 QQ。", "填好 QQ 机器人的 ID 和密钥，再选一个聊天模型。"
    if age is not None and age > 120 and running:
        detail = "暂时收不到运行状态，请看下方日志。"
    if managed.get("owned") and managed.get("running"):
        action = (f'<form method="post" action="/runtime/stop"><input type="hidden" name="csrf" value="{_esc(csrf)}">'
                  f'<button class="secondary">停止</button></form>')
    elif managed.get("external"):
        action = '<span class="wx-hero-note">由常驻服务运行，在原来的入口启停</span>'
    elif settings.get("ready") and runner is not None:
        action = (f'<form method="post" action="/runtime/start"><input type="hidden" name="csrf" value="{_esc(csrf)}">'
                  f'<button>启动她</button></form>')
    elif qq_ready:
        action = '<a class="button" href="/capabilities#chat">选聊天模型 →</a>'
    else:
        action = '<a class="button" href="/setup">连接 QQ →</a>'
    facts = ["QQ 已连接" if online else "QQ 未连接", f"认识 {groups} 个群"]
    if budget.get("limit"):
        facts.append(f"今天用了 {budget.get('used', 0)} / {budget.get('limit')} 次")
    progress = ""
    if not (settings.get("ready") and online and owner):
        steps = (("连上 QQ", qq_ready), ("选聊天模型", settings.get("ready")), ("启动并在线", online), ("你发第一条私聊", owner))
        progress = '<ol class="wx-progress" aria-label="第一次使用的进度">' + "".join(
            f'<li class="{"wx-done" if done else ""}"><span>{"✓" if done else i}</span>{name}</li>'
            for i, (name, done) in enumerate(steps, 1)) + '</ol>'
    items = _attention(cfg, settings, running)
    attention = ('<div class="wx-attention"><span>需要你看一眼</span><ul>' + "".join(
        f'<li><a href="{href}">{_esc(label)}</a></li>' for label, href in items) + '</ul></div>') if items else ''
    return (f'<section class="wx-hero" data-tone="{tone}" aria-label="她现在怎么样">'
            f'<div class="wx-hero-main"><span class="wx-hero-dot" aria-hidden="true"></span>'
            f'<div><h2>{headline}</h2><p>{_esc(detail)}</p><p class="wx-hero-facts">{" · ".join(_esc(f) for f in facts)}</p></div>'
            f'<div class="wx-hero-action">{action}</div></div>{progress}{attention}</section>')


def _dashboard(cfg, *, csrf="", runner=None):
    snapshot, state = _status(cfg)
    managed = runner.snapshot() if runner is not None else {}
    if runner is not None:
        snapshot = {**snapshot, "gateway_connected": managed.get("gateway_connected", False)}
    groups = _groups(state)
    rows = []
    for gid in sorted(groups):
        info = groups[gid]
        slot = info.get("slot") or {}
        name = str(slot.get("group_name") or slot.get("name") or slot.get("title") or "").strip()
        if not name:
            name = "群 · " + sha256(f"{gid}.md".encode("utf-8")).hexdigest()[:6].upper()
        last = slot.get("last_seen")
        last_text = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "—"
        kfile = f"{gid}.md"
        klink = (f'<a href="/knowledge?file={_esc(kfile)}">群知识</a>'
                 if kfile in _knowledge_files(cfg) else '<span class="muted">暂无群知识</span>')
        rows.append(
            f'<tr><td>{_esc(name)}</td>'
            f'<td>认识 {info.get("relations", 0)} 人<small>{info.get("promises", 0)} 项承诺</small></td>'
            f'<td>{_esc(last_text)}<small>{klink}</small></td></tr>')
    if not rows:
        group_table = '<p class="wx-empty">还没有群消息。连上 QQ 后，先由你发一条私聊，再邀请她进群。</p>'
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
    code = snapshot.get("code") or {}
    boot_log = managed.get("log_file")
    startup = (f'<h3>本面板启动日志</h3><pre>{_esc(_tail(boot_log) or "暂无本面板启动日志")}</pre>'
               if boot_log and boot_log != cfg.LOG_FILE else "")
    numbers = _today(cfg)
    today = ('<section class="wx-today" aria-label="今天"><h2>今天</h2><dl>' + "".join(
        f'<div><dt>{_esc(label)}</dt><dd>{value}</dd></div>' for label, value in numbers.items())
        + '</dl></section>') if numbers else ''
    body = f"""
<header class="wx-heading"><div><h1>我的群友</h1><p>她现在怎么样，有没有需要你处理的事。</p></div><a class="button secondary" href="/companion">聊两句 →</a></header>
{_home_status(cfg, csrf, runner, len(groups), budget)}
{today}
<section class="wx-section"><div class="wx-section-head"><h2>群里</h2><a href="/knowledge">群知识 →</a></div>{group_table}</section>
<details class="wx-disclosure wx-details-group"><summary>维护者才需要看的：日志、文件与版本</summary><h3>运行日志与版本</h3><p>当前版本：<code>{_esc(code.get('release') or '尚无运行记录')}</code></p><p>安装位置：<code>{_esc(code.get('source_root') or '—')}</code></p><h3>最近日志</h3><pre>{_esc(log or '暂无日志')}</pre>{startup}<h3>知识文件 · {len(files)} 份</h3>{knowledge_table}<a href="/knowledge">整理群知识 →</a></details>
"""
    return _page("概览", body, "home", cfg=cfg, refresh=5 if managed.get("owned") and managed.get("running")
                 and not managed.get("gateway_connected") else 0)


def _setup_page(cfg, request, csrf, runner=None):
    settings = instance_settings.public_settings(cfg)
    qq_ready = bool(settings.get("qq_app_id") and settings.get("qq_secret_configured"))
    managed = runner.snapshot() if runner is not None else {}
    connected = bool(managed.get("gateway_connected") and not managed.get("stale"))
    name = str(settings.get("bot_names") or "老群友").split(",")[0].strip() or "老群友"
    state = "已连接 QQ" if connected else "身份已保存" if qq_ready else "等待连接"
    notice = ('<p class="identity-notice" role="status">已保存，重新启动机器人后生效。</p>'
              if request.query.get("saved") else "")
    overrides = set(settings.get("overridden") or ()) & {"QQ_APP_ID", "QQ_APP_SECRET", "BOT_NAMES"}
    override_note = ('<p class="identity-notice">部分设置由后台服务管理，请让你的 Agent 帮忙修改。</p>'
                     if overrides else "")
    need_chat = not (settings.get("base_url") and settings.get("primary_model") and settings.get("key_configured"))
    next_step = ('<a class="button" href="/capabilities#chat">选择聊天模型 →</a>' if need_chat else
                 '<a class="text-link" href="/capabilities">管理模型与能力 →</a>')
    body = f'''<header class="wx-heading"><div><h1>QQ 连接与身份</h1><p>让她以你的机器人身份加入群聊。</p></div></header>
{notice}{override_note}
<section class="identity-panel" aria-label="机器人身份"><div class="identity-summary"><img src="/assets/app-icon.png" alt=""><div><h2>{_esc(name)}</h2><span class="identity-state{' connected' if connected else ''}">{state}</span></div></div>
<details class="identity-editor"{' open' if not qq_ready else ''}><summary><span>{'连接 QQ' if not qq_ready else '连接与名字'}</span><span>{'填写' if not qq_ready else '修改'} <span aria-hidden="true">⌄</span></span></summary>
<form method="post" action="/setup/save" class="identity-form">
<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="section" value="identity">
<label class="identity-name"><span>大家怎么叫她</span><input type="text" name="bot_names" value="{_esc(settings.get('bot_names'))}" placeholder="例如：小王" autocomplete="off"></label>
<div class="identity-fields"><label><span>QQ AppID</span><input type="text" name="qq_app_id" value="{_esc(settings.get('qq_app_id'))}" required autocomplete="off"></label>
<label><span>AppSecret</span><input type="password" name="qq_app_secret" value="" autocomplete="new-password" placeholder="{'已保存，留空保留' if settings.get('qq_secret_configured') else '粘贴机器人密钥'}"{' required' if not settings.get('qq_secret_configured') else ''}></label></div>
<button type="submit">保存身份</button></form></details></section>
<div class="identity-next">{next_step}<a href="/">返回我的群友 →</a></div>
<details class="identity-help"><summary>从哪里获取 QQ 凭据？</summary><ol><li>在 <a href="https://q.qq.com/" target="_blank" rel="noreferrer">QQ 开放平台 ↗</a> 创建机器人，复制 AppID 与 AppSecret。</li><li>在 QQ 开放平台启用私聊、群消息和使用范围。</li><li>连接成功后，先给她发一条 QQ 私聊，再邀请进群。</li></ol></details>'''
    return _page("QQ 连接与身份", body, "setup", cfg=cfg)


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
        return "ok", "开关对新消息生效；更换模型后需重启。", reported
    if managed.get("running"):
        return "todo", "机器人需要更新后才能使用这些开关。", {}
    return "off", "机器人尚未启动，保存后下次启动时使用。", {}


def _capabilities_page(cfg, request, csrf, runner=None):
    from regular import capabilities
    sections = capabilities.public_settings(cfg)
    try:
        chat = instance_settings.public_settings(cfg, request.query.get("provider"))
    except ValueError:
        raise web.HTTPBadRequest(text="请选择支持的模型服务。") from None
    judgment = decision_settings.public_settings(
        cfg.DATA_DIR, shared_ready=bool(chat["key_configured"]))
    tone, timing, reported = _cap_runtime(cfg, runner)
    saved = str(request.query.get("saved") or "")
    editing = str(request.query.get("edit") or "")
    overridden = {key for row in sections.values() for key in row.get("overridden") or ()}

    def switch(flag, on, label, *, parent_on=True):
        locked = capabilities.FLAGS[flag][0] in overridden
        title = ' title="由安装配置管理"' if locked else ""
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
                placeholder = "已保存，留空不改" if row.get("key_configured") else (placeholder or "粘贴 API Key")
            attrs = (' min="0" step="1"' if number else '') + (' autocomplete="new-password"' if secret else '')
            control = (f'<input id="{field_id}" type="{kind}" name="{name}" value="{_esc("" if secret else value)}"'
                       f'{attrs} placeholder="{_esc(placeholder)}">')
        note = f'<small>{_esc(hint)}</small>' if hint else ''
        return f'<label class="cap-field" for="{field_id}"><span>{label}</span>{control}{note}</label>'

    def settings_form(section, title, main, advanced="", extra=""):
        fold = (f'<details class="cap-advanced"><summary>更多设置</summary><div class="cap-fields">{advanced}</div></details>'
                if advanced else '')
        main_fields = f'<div class="cap-fields">{main}</div>' if main else ''
        return (f'<form method="post" action="/capabilities/save" class="cap-form" data-cap-form>'
                f'<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="section" value="{section}">'
                f'{extra}{main_fields}{fold}'
                f'<footer><button type="submit">保存{title}设置</button><span data-cap-form-status aria-live="polite">'
                f'{"已保存。" if saved == section else ""}</span></footer></form>')

    def sub_switch(flag, on, parent_on, label, hint):
        return (f'<div class="cap-sub" data-cap-sub="{flag}">{switch(flag, on, label, parent_on=parent_on)}'
                f'<div><strong>{label}</strong><small>{_esc(hint)}</small></div></div>')

    def model_row(name, title, description, state, form, *, configured=True, first=False):
        opened = saved == name or editing == name or (first and not configured)
        return (f'<article class="cap-item ms-model-item" id="{name}" data-tone="{"ok" if configured else "todo"}">'
                f'<details class="cap-more"{" open" if opened else ""}><summary class="cap-head">'
                f'<span class="cap-icon" aria-hidden="true">{_icon("companion" if name == "chat" else "decision")}</span>'
                f'<span class="cap-title"><strong>{title}</strong><small>{description}</small></span>'
                f'<span class="cap-state">{_esc(state)}</span><span class="cap-open">更换</span></summary>'
                f'<div class="cap-body">{form}</div></details></article>')

    provider_buttons = []
    for provider, label in (("openai", "OpenAI / 兼容服务"), ("zhipu", "智谱"), ("gemini", "Gemini")):
        option = instance_settings.public_settings(cfg, provider)
        provider_buttons.append(
            f'<button type="button" class="provider-option{" selected" if provider == chat["provider"] else ""}"'
            f' data-provider-option="{provider}" data-base-url="{_esc(option["base_url"])}"'
            f' data-models="{_esc(option["primary_model"])}" data-model-options="{_esc(json.dumps(option["model_options"]))}"'
            f' data-fallback-models="{_esc(option["fallback_models"])}"'
            f' data-key-placeholder="{"已保存，留空不改" if option["key_configured"] else "粘贴 API Key"}"'
            f' aria-pressed="{"true" if provider == chat["provider"] else "false"}">{label}</button>')
    model_options = chat["model_options"]
    model_choice = chat["primary_model"] if model_options else "__custom__"
    picker = (f'<label class="cap-field" for="chat-model-choice"><span>聊天模型</span>'
              f'<select id="chat-model-choice" name="model_choice" data-model-choice>'
              + ''.join(f'<option value="{_esc(model)}"{" selected" if model == model_choice else ""}>{_esc(model)}</option>'
                        for model in model_options)
              + f'<option value="__custom__"{" selected" if model_choice == "__custom__" else ""}>填写其他模型…</option></select></label>'
              + f'<label class="cap-field" for="chat-custom-model" data-custom-model-field{" hidden" if model_options else ""}>'
                f'<span>模型 ID</span><input id="chat-custom-model" type="text" name="models"'
                f' value="{_esc(chat["primary_model"])}" placeholder="服务提供的模型 ID" autocomplete="off"'
                f'{" required" if not model_options else ""}></label>')
    chat_form = (
        '<form method="post" action="/models/save" class="cap-form" data-setup-form data-cap-form>'
        f'<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="provider" value="{_esc(chat["provider"])}">'
        f'<div class="provider-choices" role="group" aria-label="聊天服务">{"".join(provider_buttons)}</div>'
        '<span class="ms-feedback" data-provider-status aria-live="polite"></span>'
        f'<div class="cap-fields">{picker}{field("chat", chat, "api_key", "API Key", secret=True)}</div>'
        '<details class="cap-advanced"><summary>接口地址、备用模型与用量</summary><div class="cap-fields">'
        + field("chat", chat, "base_url", "接口地址")
        + field("chat", chat, "fallback_models", "备用模型", placeholder="多个模型用逗号分隔")
        + field("chat", chat, "daily_budget", "每天最多调用几次", number=True)
        + '</div></details>'
        f'<footer><button type="submit">保存聊天设置</button><span data-cap-form-status aria-live="polite">'
        f'{"已保存，重启后生效。" if saved == "chat" else ""}</span></footer></form>')
    active_chat = instance_settings.public_settings(cfg)
    same_service = False
    if active_chat["key_configured"]:
        try:
            instance_settings.decision_key_from_chat(cfg, judgment["endpoint"])
            same_service = True
        except (ValueError, OSError):
            same_service = False
    if not active_chat["key_configured"]:
        shared_note = ('<p class="cap-note cap-need">先在「聊天」里填一次 OpenAI API Key，'
                       '判断、画图、语音都会用它。</p>')
    elif same_service:
        shared_note = ('<p class="cap-note cap-shared-note">连接凭据：共用聊天设置里那把 '
                       'OpenAI Key。换 Key 只改一处，这里会跟着变。</p>')
    else:
        shared_note = ('<p class="cap-note cap-need">这个判断接口和聊天服务不是同一家，'
                       '不能共用 Key；请改回同一接口，或在 .env 里单独配置。</p>')
    judgment_form = (
        '<form method="post" action="/decision/save" class="cap-form" data-cap-form>'
        f'<input type="hidden" name="csrf" value="{_esc(csrf)}">'
        '<input type="hidden" name="credential_source" value="shared">'
        f'<div class="cap-fields">{field("decision", judgment, "model", "判断模型")}'
        f'{shared_note}</div>'
        '<details class="cap-advanced"><summary>接口地址</summary><div class="cap-fields">'
        + field("decision", judgment, "endpoint", "判断接口地址") + '</div></details>'
        f'<footer><button type="submit">保存判断设置</button><span data-cap-form-status aria-live="polite">'
        f'{"已保存。" if saved == "decision" else ""}</span></footer></form>')
    chat_ready = bool(chat["key_configured"] and chat["base_url"] and chat["primary_model"])
    model_rows = [model_row("chat", "聊天", "和群友、和你聊天", chat["primary_model"] if chat_ready else "还没连接",
                            chat_form, configured=chat_ready, first=True),
                  model_row("decision", "判断", "拿捏接话分寸，审核提醒", judgment["model"] if judgment["key_configured"] else "还没连接",
                            judgment_form, configured=judgment["key_configured"])]

    vision, drawing, hearing, speech = (sections[name] for name in ("vision", "image", "voice_input", "voice_output"))
    providers = [("", "沿用聊天服务"), ("openai", "OpenAI 兼容"), ("zhipu", "智谱"), ("gemini", "Gemini")]
    deps = speech.get("dependencies") or {}
    bodies = {
        "vision": settings_form(
            "vision", "读图",
            field("vision", vision, "provider", "服务", choices=providers)
            + field("vision", vision, "model", "模型", placeholder=vision.get("effective_model") or "跟随聊天模型"),
            field("vision", vision, "daily_limit", "每天最多看几张", number=True)
            + field("vision", vision, "input_mode", "图片传送方式",
                  choices=[("download", "先下载到本机再发"), ("url", "直接发图片链接")])),
        "image": sub_switch("gif", drawing.get("gif_enabled", False), drawing.get("enabled", False),
                            "也画动图", "")
        + settings_form(
            "image", "画图",
            field("image", drawing, "model", "模型"),
            field("image", drawing, "base_url", "接口地址")
            + field("image", drawing, "daily_limit", "每天最多画几张", number=True)
            + field("image", drawing, "user_daily_limit", "每人每天最多几张", number=True)
            + field("image", drawing, "quality", "画质") + field("image", drawing, "size", "静图尺寸")
            + field("image", drawing, "gif_size", "动图尺寸"),
            extra='<p class="cap-note cap-shared-note">连接凭据：共用聊天设置里的 OpenAI Key，不用再填一次。</p>'),
        "voice_input": sub_switch("transcription", hearing.get("transcription_enabled", True), hearing.get("enabled", False),
                                  "QQ 没带文字时，交给模型转写", "")
        + settings_form("voice_input", "听语音", field("voice_input", hearing, "model", "转写模型"))
        + '<a class="ms-inline-link" href="/capabilities?edit=voice#voice">设置语音服务 →</a>',
        "voice_output": settings_form(
            "voice_output", "发语音",
            field("voice_output", speech, "model", "模型") + field("voice_output", speech, "voice", "音色"),
            field("voice_output", speech, "daily_limit", "每天最多发几条", number=True)
            + field("voice_output", speech, "node", "Node 路径") + field("voice_output", speech, "silk_entry", "音频编码器路径"))
        + ('' if all(deps.values()) else '<p class="cap-note">还需要安装音频组件。</p>')
        + '<a class="ms-inline-link" href="/capabilities?edit=voice#voice">设置语音服务 →</a>',
    }

    # 判断模型在实现上必须独立（另一条调用链），但平时不该在界面上占一行。
    # 概念保留，位置降级：只留聊天模型常驻，判断/搜索一起收进底部的高级区。
    rows = [model_rows[0]]
    for name, title, summary in _CAPABILITIES:
        row = sections[name]
        state_tone, state = _cap_state(name, row)
        running = ""
        if reported:
            model = (reported.get("models") or {}).get(name)
            if isinstance(model, str) and model and model != row.get("model"):
                running = f'<p class="cap-running">当前使用 {_esc(model)}，重启后使用新设置。</p>'
        env = '<p class="cap-note">这项连接由安装配置管理。</p>' if row.get("overridden") else ''
        missing = row.get("missing") or []
        if name == "voice_output":
            missing = [item for item in missing if item not in ("本机 Node", "SILK 编码器")]
        need = (f'<p class="cap-note cap-need">还需要：{"、".join(_esc(m) for m in missing)}</p>'
                if row.get("enabled") and missing and state_tone == "todo" else '')
        # The whole row opens the settings; the switch sits beside it, outside <summary>.
        rows.append(
            f'<article class="cap-item" id="{name}" data-cap="{name}" data-tone="{state_tone}">'
            f'<details class="cap-more"{" open" if saved == name or editing == name else ""}><summary class="cap-head">'
            f'<span class="cap-icon" aria-hidden="true"><svg viewBox="0 0 24 24">{_CAP_ICONS[name]}</svg></span>'
            f'<span class="cap-title"><strong>{title}</strong><small>{summary}</small></span>'
            f'<span class="cap-state" data-cap-state>{_esc(state)}</span><span class="cap-open">设置</span></summary>'
            f'<div class="cap-body">{need}{running}{env}{bodies[name]}</div></details>'
            f'{switch(name, row.get("enabled", False), "允许" + title)}</article>')

    rounds = hearing.get("desktop_daily_limit")
    voice_source = ((f"每天最多 {rounds} 轮" if rounds else "已停用") if hearing.get("key_configured")
                    else "还没连接 OpenAI")
    voice_form = settings_form("voice", "语音服务", "",
                               field("voice", hearing, "base_url", "接口地址")
                               + field("voice", hearing, "desktop_daily_limit", "桌宠每天最多聊几轮（0 停用）", number=True),
                               extra='<p class="cap-note cap-shared-note">连接凭据：共用聊天设置里的 OpenAI Key，不用再填一次。</p>')
    shared = (
        f'<details class="ms-voice-connection" id="voice"{" open" if saved == "voice" or editing == "voice" else ""}>'
        f'<summary><span>桌宠语音</span><span class="ms-voice-state">{voice_source}</span><span class="cap-open">设置</span></summary>'
        f'<div class="cap-body"><p class="cap-note">桌宠语音共用「发语音」的模型和音色；点一下开始连续对话，再点结束。QQ 里的发语音开关不影响它。</p>{voice_form}</div></details>')
    advanced_open = " open" if saved == "decision" or editing == "decision" or saved == "voice" else ""
    advanced = (
        f'<details class="ms-diagnostics ms-advanced"{advanced_open}><summary>高级：判断、搜索与连接检查</summary>'
        f'{model_rows[1]}<a href="/decision">判断帮手 →</a><a href="/search">检查联网搜索 →</a></details>')
    key_ready = bool(chat["key_configured"])
    key_card = (
        f'<section class="cap-key-card" data-tone="{"ok" if key_ready else "todo"}">'
        f'<div><strong>{"一把 OpenAI Key 已就位" if key_ready else "还没填 OpenAI API Key"}</strong>'
        f'<p>{"聊天、判断、读图、画图、语音都用它；换 Key 只改这一处。" if key_ready else "在下面「聊天」里填一次，其他能力自动共用，不用再填第二遍。"}</p></div>'
        '<a href="#chat">去设置 →</a></section>')
    body = (
        '<header class="cap-page-head"><h1>模型与能力</h1><p>她会什么、用哪个模型。开关点了就保存。</p></header>'
        f'<section class="cap-page" aria-label="模型与能力">{key_card}'
        f'<p class="cap-runtime" data-tone="{tone}" data-cap-runtime>{timing}</p>'
        f'<div class="cap-list">{"".join(rows)}</div>{shared}{advanced}</section>')
    return _page("模型与能力", body, "capabilities", cfg=cfg)


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


def _handoff_section(cfg):
    """交给编码 Agent：复制或下载一份交接说明。说明里只有本机路径和流程，不含群聊或密钥。"""
    code_root = Path(__file__).resolve().parent
    source_ready = (code_root / "AGENTS.md").is_file() and (code_root / ".agents/skills/regular-iterate/SKILL.md").is_file()
    source_note = ('源码工作区已带上 AGENTS.md 与自迭代 skill。让你的编码 Agent 打开这个目录。'
                   if source_ready else '当前是运行安装包。要修改机器人，请先下载源码工作区；源码包自带 AGENTS.md 和自迭代 skill。实例数据继续用原目录。')
    return (f'<section class="wx-handoff-bar" id="handoff" aria-label="交给编码 Agent">'
            f'<div class="wx-handoff-copy"><strong>交给编码 Agent</strong><span>哪句不对，复制交接说明，连同截图或原话发给 CodeX、Claude Code 这类 Agent。'
            f'它会找到当轮现场、对比新旧版本；上线和付费评测都等你点头。</span></div>'
            f'<div class="wx-handoff-actions"><button type="button" data-copy-brief>复制交接说明</button>'
            f'<a href="/iterate/brief">下载</a></div><p class="wx-handoff-status" data-copy-status aria-live="polite"></p>'
            f'<details class="wx-disclosure"><summary>看完整说明</summary><p class="wx-source-note">{source_note}</p>'
            f'<textarea class="wx-brief" readonly aria-label="交给 Agent 的任务说明" data-agent-brief>{_esc(_agent_brief(cfg))}</textarea>'
            f'<p class="wx-footnote">面板不会自动上传聊天。交接说明只含本机路径与操作流程，不含群聊或密钥。</p>'
            f'<a href="https://github.com/dubianche123/the-regular-qq-bot/releases/latest" target="_blank" rel="noreferrer">获取源码包 ↗</a></details></section>')


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
        label = "当前聊天模型"
    else:
        label, active = "已选聊天模型", configured
    return (f'<div class="chat-route-card"><span class="route-label">{label}</span><strong>{_esc(active)}</strong>'
            '<a href="/capabilities#chat">更换 ↗</a></div>')


def _companion_page(cfg, request, csrf, runner=None):
    pet = request.query.get("mode") == "pet"
    suggestions = ("今天你忙了些什么？", "先别画图了", "你现在心情怎么样？")
    body = f'''<section class="companion-room" data-chat-room><header class="room-heading"><div><h1>聊两句</h1><p>聊聊天、看看今日概况，或让她帮你开关能力。</p></div><a class="room-home-link" href="/">首页 ↗</a></header>
<div class="room-layout"><aside class="room-card" aria-label="她现在的样子">
<div class="room-scene" data-room-scene><span class="scene-window" aria-hidden="true"></span><span class="scene-glow" aria-hidden="true"></span><p class="scene-bubble" data-scene-bubble aria-live="polite" hidden></p>{_pet_sprite('idle', size='stage')}<span class="scene-floor" aria-hidden="true"></span></div>
<div class="room-status"><ul class="status-chips" data-presence-chips><li class="chip"><span class="dot"></span>正在连接…</li></ul>
<div class="mood-meter" data-mood-meter><span>心情</span><span class="meter" aria-hidden="true"><i></i></span><b data-mood-word>平稳</b></div></div>
<div class="room-recent"><h2>刚才</h2><ol data-presence-feed><li class="empty">她最近在群里的动态会留在这里。</li></ol></div>
<div class="room-actions"><button type="button" data-room-action="shy">摸摸头</button><button type="button" data-room-action="eat">投喂饼干</button><button type="button" data-room-action="wave">打个招呼</button></div></aside>
<div class="chat-panel"><div class="chat-head"><span class="chat-avatar" aria-hidden="true"></span><div><strong>老群友</strong><p data-chat-status aria-live="polite">正在连接…</p></div><button type="button" class="icon-button" data-chat-refresh title="刷新状态" aria-label="刷新状态">↻</button></div>
<div class="chat-history" role="log" aria-live="polite" aria-label="对话" data-chat-history><div class="chat-message system"><span class="chat-speaker">老群友</span><p>从这里打个招呼吧。</p></div></div>
<div class="chat-suggestions" data-chat-suggestions>{"".join(f'<button type="button" data-suggestion>{_esc(text)}</button>' for text in suggestions)}</div>
<form class="chat-compose" action="/companion/chat" method="post" data-chat-form><input type="hidden" name="csrf" value="{_esc(csrf)}"><label class="sr-only" for="companion-message">想和老群友说的话</label><textarea id="companion-message" name="text" rows="2" maxlength="2000" placeholder="今天有什么想聊的？Enter 发送，Shift+Enter 换行" required data-chat-input></textarea><div class="chat-compose-footer"><span data-chat-count>0 / 2000</span><button type="submit" disabled data-chat-submit>发送 <span aria-hidden="true">↑</span></button></div></form>
{_chat_route_card(cfg, runner)}</div></div>
<nav class="room-shortcuts" aria-label="快捷入口"><a href="/capabilities">模型与能力</a><a href="/checkup">每日体检</a><a href="/">首页</a><a href="/iterate">让 Agent 帮忙</a><button type="button" class="text-button" data-actions-toggle aria-pressed="true" title="关闭后角色只保留静止姿势">角色动作：开</button></nav></section>'''
    return _page("和老群友聊聊", body, "companion", pet=pet, cfg=cfg)


def _knowledge_revision(path):
    try:
        stat = Path(path).stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except FileNotFoundError:
        return "new"


def _knowledge_document(cfg, name):
    path = _knowledge_path(cfg, name)
    try:
        with open(path, encoding="utf-8") as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_size > MAX_EDIT_BYTES:
                raise web.HTTPBadRequest(text="这份内容过大，请先精简后再编辑。")
            content = stream.read()
            revision = f"{stat.st_mtime_ns}:{stat.st_size}"
    except FileNotFoundError:
        content, revision = "", "new"
    except (OSError, UnicodeError):
        raise web.HTTPBadRequest(text="暂时无法读取这份知识。") from None
    return path, content, revision


def _knowledge_sections(content):
    # Keep exact offsets in the original document, including every unparsed byte.
    comments = list(knowledge_mod.COMMENT_RE.finditer(content))
    matches = [match for match in knowledge_mod.SECTION_RE.finditer(content)
               if not any(comment.start() <= match.start() < comment.end() for comment in comments)]
    rows = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[match.end():end]
        words, lines = "", []
        for line in body.splitlines():
            keyword = knowledge_mod.KEYWORDS_RE.match(line)
            if keyword and not words:
                words = keyword.group(1)
            else:
                lines.append(line)
        rows.append({"index": index, "start": match.start(), "end": end,
                     "title": match.group(1).strip(), "keywords": words,
                     "body": "\n".join(lines).strip()})
    return rows


def _knowledge_scopes(cfg, selected):
    import hashlib
    state = _read_json(cfg.STATE_FILE) or {}
    groups = _groups(state)
    names = set(_knowledge_files(cfg)) | {selected, "all.md"}
    names.update(f"{gid}.md" for gid in groups if _safe_name(f"{gid}.md"))
    choices = []
    for name in ["all.md", *sorted(names - {"all.md"})]:
        group = groups.get(name[:-3]) or {}
        slot = group.get("slot") or {}
        label = str(slot.get("group_name") or slot.get("name") or slot.get("title") or "").strip()
        if name == "all.md":
            label = "所有群"
        elif not label:
            label = "群 · " + hashlib.sha256(name.encode("utf-8")).hexdigest()[:6].upper()
            if slot.get("last_seen"):
                label += " · " + time.strftime("%m-%d %H:%M", time.localtime(slot["last_seen"]))
            elif group.get("relations"):
                label += f" · 认识 {group['relations']} 人"
        choices.append((name, label))
    return choices


def _knowledge_hidden(values):
    return "".join(f'<input type="hidden" name="{_esc(key)}" value="{_esc(value)}">'
                   for key, value in values.items())


def _knowledge_editor(cfg, name, values, csrf, *, example=False):
    label = dict(_knowledge_scopes(cfg, name))[name]
    editing = values.get("action") == "edit"
    draft = '<p class="kb-draft">示例草稿 · 修改后再保存</p>' if example else ''
    return f'''<header class="kb-heading"><div><a class="kb-back" href="/knowledge?file={quote(name, safe='')}">← 群知识</a><h1>{'编辑知识' if editing else '新增知识'}</h1><p>{_esc(label)}</p></div></header>
<form class="kb-editor" action="/knowledge/entry/preview" method="post" data-kb-editor>
{_knowledge_hidden({'csrf': csrf, 'file': name, 'revision': values['revision'], 'action': values['action'], 'index': values.get('index', '')})}{draft}
<label for="kb-title">标题</label><input id="kb-title" name="title" value="{_esc(values.get('title', ''))}" maxlength="80" placeholder="例如：周五的奶茶局" required>
<label for="kb-body">要记住的事</label><textarea id="kb-body" name="body" rows="5" maxlength="3000" placeholder="写已经确认的事实、约定或梗的来历。" required>{_esc(values.get('body', ''))}</textarea>
<details class="kb-optional"{' open' if values.get('keywords') else ''}><summary>关联词</summary><label class="sr-only" for="kb-keywords">聊到哪些词时参考</label><input id="kb-keywords" name="keywords" value="{_esc(values.get('keywords', ''))}" maxlength="200" placeholder="例如：奶茶、报名；留空使用标题"></details>
<footer><button type="submit">预览并保存</button><a href="/knowledge?file={quote(name, safe='')}">取消</a></footer></form>'''


def _knowledge(cfg, request, csrf=""):
    name = str(request.query.get("file") or "all.md")
    path, content, revision = _knowledge_document(cfg, name)
    sections = _knowledge_sections(content)
    choices = _knowledge_scopes(cfg, name)
    label = dict(choices)[name]
    base = "/knowledge?file=" + quote(name, safe="")
    editing = request.query.get("edit")
    deleting = request.query.get("delete")
    examples = {
        "agreement": ("群里的小约定", "群规、约定", "把已经确认的约定写在这里。"),
        "names": ("一个称呼的来历", "称呼、昵称", "写下这个称呼的由来，以及适合怎么使用。"),
        "routine": ("固定活动", "活动、时间", "写下活动的时间与参加方式。"),
    }
    template = request.query.get("template")
    if request.query.get("new") or editing is not None or template in examples:
        values = {"action": "add", "revision": revision}
        if editing is not None:
            try:
                index = int(editing)
                if index < 0: raise ValueError
                values.update(sections[index], action="edit")
            except (ValueError, IndexError):
                raise web.HTTPBadRequest(text="没有找到这条知识。") from None
        elif template in examples:
            title, keywords, body = examples[template]
            values.update(title=title, keywords=keywords, body=body)
        return _page("群知识", _knowledge_editor(cfg, name, values, csrf, example=template in examples), "knowledge", cfg=cfg)
    if deleting is not None:
        try:
            index = int(deleting)
            if index < 0: raise ValueError
            row = sections[index]
        except (ValueError, IndexError):
            raise web.HTTPBadRequest(text="没有找到这条知识。") from None
        body = f'''<header class="kb-heading"><div><h1>删除这条知识？</h1><p>{_esc(label)}</p></div></header><section class="kb-confirm"><h2>{_esc(row['title'])}</h2><p>{_esc(row['body'])}</p><form method="post" action="/knowledge/entry/save">{_knowledge_hidden({'csrf': csrf, 'file': name, 'revision': revision, 'action': 'delete', 'index': index})}<footer><button type="submit" class="kb-danger">删除</button><a href="{base}">保留</a></footer></form></section>'''
        return _page("群知识", body, "knowledge", cfg=cfg)
    parsed = knowledge_mod.KnowledgeBase(cfg.KNOWLEDGE_DIR).parse_file(path)
    available = {entry["title"] for entry in parsed}
    seen = set()
    cards = []
    for row in sections:
        clean_title = knowledge_mod.COMMENT_RE.sub("", row["title"]).strip()[:40]
        usable = clean_title in available and clean_title not in seen
        seen.add(clean_title)
        status = '' if usable else '<span class="kb-pending">待整理</span>'
        cards.append(f'''<article class="kb-entry"><div class="kb-entry-copy"><h2><a href="{base}&amp;edit={row['index']}">{_esc(row['title'])}</a>{status}</h2><p>{_esc(row['body'])}</p></div><div class="kb-entry-actions"><a href="{base}&amp;edit={row['index']}">编辑</a><a href="{base}&amp;delete={row['index']}" aria-label="删除{_esc(row['title'])}">删除</a></div></article>''')
    empty = f'''<div class="kb-empty"><span class="kb-empty-icon" aria-hidden="true">{_icon('knowledge')}</span><h2>把你们知道的事，也告诉她</h2><p>群里的约定、称呼和梗的来历，都可以从一条开始。</p><a class="button" href="{base}&amp;new=1">写下第一条</a><div class="kb-examples"><span>也可以从这里开始</span><a href="{base}&amp;template=agreement">群里的约定</a><a href="{base}&amp;template=names">称呼的来历</a><a href="{base}&amp;template=routine">固定活动</a></div></div>'''
    intro = content[:sections[0]["start"]] if sections else content
    unorganized = (f'<details class="kb-unorganized"><summary>还有一段未整理的内容</summary><pre>{_esc(intro)}</pre><a href="{base}&amp;source=1">整理原文</a></details>'
                   if intro.strip() else '')
    saved = '<p class="kb-saved" role="status">已保存</p>' if request.query.get("saved") else ''
    options = ''.join(f'<option value="{_esc(value)}"{" selected" if value == name else ""}>{_esc(text)}</option>' for value, text in choices)
    advanced_open = ' open' if request.query.get('source') else ''
    body = f'''<header class="kb-heading"><div><h1>群知识</h1><p>让她记住你们已经确认的事。</p></div>{f'<a class="button" href="{base}&amp;new=1">新增知识</a>' if sections else ''}</header>{_knowledge_tabs("knowledge")}{saved}
<div class="kb-toolbar"><form method="get" action="/knowledge" data-kb-scope><label for="kb-scope">适用范围</label><select id="kb-scope" name="file">{options}</select><button type="submit" class="secondary">切换</button></form><span>{len(sections)} 条知识</span></div>
<section class="kb-list" aria-label="已保存知识条目">{''.join(cards) or empty}</section>{unorganized}
<details class="kb-source"{advanced_open}><summary>编辑完整原文</summary><form method="post" action="/knowledge/save">{_knowledge_hidden({'csrf': csrf, 'file': name, 'revision': revision})}<label class="sr-only" for="kb-source">完整原文</label><textarea id="kb-source" name="content" rows="12" spellcheck="false">{_esc(content)}</textarea><footer><button type="submit">保存原文</button></footer></form></details>'''
    return _page("群知识", body, "knowledge", cfg=cfg)


def _knowledge_entry_plan(cfg, form):
    name = str(form.get("file") or "")
    path, original, revision = _knowledge_document(cfg, name)
    if str(form.get("revision") or "") != revision:
        raise web.HTTPConflict(text="内容已有更新，请重新预览。")
    action = str(form.get("action") or "")
    if action not in ("add", "edit", "delete"):
        raise web.HTTPBadRequest(text="请选择要修改的知识。")
    rows = _knowledge_sections(original)
    index, current = "", None
    if action != "add":
        try:
            index = int(form.get("index"))
            if index < 0: raise ValueError
            current = rows[index]
        except (ValueError, TypeError, IndexError):
            raise web.HTTPBadRequest(text="没有找到这条知识。") from None
    values = {"file": name, "revision": revision, "action": action, "index": index}
    if action == "delete":
        values.update(title=current["title"], keywords=current["keywords"], body=current["body"])
        return path, original[:current["start"]] + original[current["end"]:], values
    title, keywords, body = (str(form.get(key) or "").strip() for key in ("title", "keywords", "body"))
    if (not title or not body or len(title) > 80 or len(keywords) > 200 or len(body) > 3000
            or any(char in title + keywords for char in ("\r", "\n", "\x00"))
            or "\x00" in body or knowledge_mod.SECTION_RE.search(body)):
        raise web.HTTPBadRequest(text="请为这一条填写简短的标题和内容。")
    if not knowledge_mod._keywords(keywords or title) or knowledge_mod.wordfilter.has_hit(title + " " + keywords + " " + body):
        raise web.HTTPBadRequest(text="这条内容暂时不能使用，请调整后再预览。")
    if any(knowledge_mod.COMMENT_RE.sub("", row["title"]).strip()[:40] == title[:40]
           and row["index"] != index for row in rows):
        raise web.HTTPBadRequest(text="已经有同名知识，请编辑原来的那条。")
    values.update(title=title, keywords=keywords, body=body)
    section = f"## {title}\n" + (f"关键词：{keywords}\n" if keywords else "") + body + "\n"
    if action == "add":
        updated = original + ("\n\n" if original and not original.endswith("\n\n") else "") + section
    else:
        updated = original[:current["start"]] + section + original[current["end"]:]
    if len(updated.encode("utf-8")) > MAX_EDIT_BYTES:
        raise web.HTTPBadRequest(text="内容过多，请精简后再保存。")
    return path, updated, values


def _knowledge_entry_preview(cfg, form, csrf=""):
    _path, _updated, values = _knowledge_entry_plan(cfg, form)
    if form.get("intent") == "edit":
        return _page("群知识", _knowledge_editor(cfg, values["file"], values, csrf), "knowledge", cfg=cfg)
    label = dict(_knowledge_scopes(cfg, values['file']))[values['file']]
    body = f'''<header class="kb-heading"><div><h1>看看这样写对不对</h1><p>{_esc(label)} · 尚未保存</p></div></header><section class="kb-confirm"><h2>{_esc(values['title'])}</h2><p class="kb-preview-body">{_esc(values['body'])}</p>{f'<p class="kb-preview-words">关联词：{_esc(values["keywords"])}</p>' if values['keywords'] else ''}<form method="post" action="/knowledge/entry/save">{_knowledge_hidden({'csrf': csrf, **values})}<footer><button type="submit">保存知识</button><button type="submit" name="intent" value="edit" formaction="/knowledge/entry/preview" class="secondary">返回修改</button><a href="/knowledge?file={quote(values['file'], safe='')}">取消</a></footer></form></section>'''
    return _page("群知识", body, "knowledge", cfg=cfg)


async def _knowledge_entry_save(cfg, form):
    path, updated, values = _knowledge_entry_plan(cfg, form)
    storage.atomic_write_text(path, updated)
    raise web.HTTPFound(f"/knowledge?file={quote(values['file'], safe='')}&saved=1")


async def _knowledge_save(cfg, request, form=None):
    form = await request.post() if form is None else form
    name = str(form.get("file") or "")
    content = str(form.get("content") or "")
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        raise web.HTTPBadRequest(text="内容过多，请精简后再保存。")
    path = _knowledge_path(cfg, name)
    if "revision" in form and str(form["revision"]) != _knowledge_revision(path):
        raise web.HTTPConflict(text="内容已有更新，请重新预览。")
    storage.atomic_write_text(path, content)
    raise web.HTTPFound(f"/knowledge?file={quote(name, safe='')}&saved=1")


def _knowledge_tabs(active):
    """群知识（你告诉她的）和群记忆（她自己攒的）是同一件事的两面，放在一个入口里切换。"""
    tabs = (("/knowledge", "你告诉她的", "knowledge"), ("/memory", "她自己记下的", "memory"))
    return '<nav class="wx-tabs" aria-label="群知识">' + "".join(
        f'<a href="{href}"{" aria-current=page" if key == active else ""}>{text}</a>' for href, text, key in tabs) + '</nav>'


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
        "群记忆还在慢慢攒", "有了新的相处片段，她会慢慢整理。", state="sleeping")
    body = f"""
<header class="kb-heading"><div><h1>群知识</h1><p>她每天自己整理的群记忆。想补充或纠正，写进「你告诉她的」。</p></div></header>
{_knowledge_tabs("memory")}
{picker}
{memory_content}
"""
    return _page("群记忆", body, "memory", cfg=cfg)


async def _search(cfg, request):
    query = (request.query.get("q") or "").strip()[:120]
    rows = ""
    notice = ""
    if query:
        if not cfg.SEARCH_ENABLED:
            notice = '<div class="notice bad">联网搜索还没开启。</div>'
        else:
            results = await search_mod.search_web(query)
            if results is None:
                notice = '<div class="notice bad">暂时连不上搜索服务，请稍后再试。</div>'
            elif not results:
                notice = '<div class="notice bad">没有找到结果。</div>'
            else:
                rows = "<table><thead><tr><th>结果</th></tr></thead><tbody>" + "".join(
                    f'<tr><td><a href="{_esc(row["url"])}" target="_blank" '
                    f'rel="noreferrer noopener">{_esc(row["title"])}</a><br>'
                    f'<span class="muted">{_esc(row["snippet"])}</span></td></tr>'
                    for row in results) + "</tbody></table>"
    body = f"""
<h1>联网检查</h1>
<p class="muted">输入关键词，看看能否找到网页。群里也可以说“搜索 关键词”。</p>
<form method="get" action="/search" class="row">
<input type="text" name="q" value="{_esc(query)}" placeholder="关键词">
<button type="submit">搜索</button>
</form>
{notice}{rows}
"""
    return _page("联网检查", body, "search", cfg=cfg)


def _decision(cfg, request, csrf):
    # Keep old bookmarks pointing at the unified model settings.
    raise web.HTTPFound("/capabilities#decision")


async def _decision_save(cfg, request, csrf):
    """保存判断 API 配置：CSRF + 字段校验都过才落盘，失败一律通用 400。"""
    form = await request.post()
    if not hmac.compare_digest(str(form.get("csrf") or ""), str(csrf)):
        raise web.HTTPBadRequest(text="请求无效")
    endpoint = str(form.get("endpoint") or "").strip()
    key = str(form.get("api_key") or "").strip()
    try:
        # The new form posts "shared" explicitly. Old clients that omit the
        # field keep their previous dedicated-key behaviour.
        source = str(form.get("credential_source") or ("new" if key else "existing"))
        model = str(form.get("model") or "")
        if source == "shared":
            # Dynamic binding: no second copy of the key is ever written.
            # The endpoint must be the same service the shared key belongs to.
            instance_settings.decision_key_from_chat(cfg, endpoint)
            decision_settings.save_settings(cfg.DATA_DIR, endpoint=endpoint,
                                            model=model, credential="shared")
        elif source == "chat":
            decision_settings.save_settings(
                cfg.DATA_DIR, endpoint=endpoint, model=model,
                api_key=instance_settings.decision_key_from_chat(cfg, endpoint))
        elif source == "new":
            if not key:
                raise ValueError("invalid credentials")
            decision_settings.save_settings(cfg.DATA_DIR, endpoint=endpoint,
                                            model=model, api_key=key)
        elif source == "existing":
            previous = decision_settings.load_settings(cfg.DATA_DIR)
            if (previous is None or endpoint != previous["endpoint"]
                    or not previous["api_key"]):
                raise ValueError("invalid credentials")
            decision_settings.save_settings(cfg.DATA_DIR, endpoint=endpoint,
                                            model=model, api_key=previous["api_key"])
        else:
            raise ValueError("invalid credentials")
    except (ValueError, OSError):
        raise web.HTTPBadRequest(text="保存失败：字段不合法或无法写入") from None
    raise web.HTTPFound("/capabilities?saved=decision#decision")


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
    warning = ('<p class="bad">体检设置暂时无法读取，本次使用默认设置。</p>'
               '<details class="wx-disclosure"><summary>查看处理方法</summary><p>修复体检策略文件，或由维护者执行 '
               '<code>regular policy reset checkup</code>。已有报告保持不变。</p></details>'
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
    findings_html = "".join(cards) if cards else '<div class="wx-checkup-empty"><h2>这一天没挑出明显接偏的话</h2><p>你自己觉得哪句不对，就用上面的交接说明交给 Agent。</p></div>'
    i = out["inputs"]
    pending = sum(f.get("verdict") not in ("yes", "no") for f in out["findings"])
    body = f"""
<header class="wx-heading"><div><h1>让她更好</h1><p>体检会挑出可能接偏的话；确认是问题，就交给编码 Agent 去改。</p></div></header>
{warning}
{_handoff_section(cfg)}
<div class="wx-checkup-summary"><p><strong>每日体检 · {pending} 句待看</strong><span>{_esc(day)} · 共 {len(out['findings'])} 条记录 · 收到 {i['incoming']} 条消息 · 回复 {i['delivered']} 条</span></p><form method="get" action="/checkup" class="wx-date"><label class="sr-only" for="checkup-day">体检日期</label><select id="checkup-day" name="day">{options}</select><button class="secondary" type="submit">换一天</button></form></div>
<section class="wx-findings" aria-label="待看记录">{findings_html}</section>
<details class="wx-disclosure"><summary>分类统计与查看范围</summary><p class="muted">查看 {i['turns']} 轮对话、{i['triage']} 次接话判断，参考前 {i['baseline_days']} 天的记录。</p><table><thead><tr><th>问题类型</th><th>数量</th><th>确认比例</th></tr></thead><tbody>{"".join(sig_rows)}</tbody></table></details>
"""
    return _page("让她更好", body, "checkup", cfg=cfg)


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
        if request.query.get("provider"):
            raise web.HTTPFound("/capabilities?provider=" + quote(str(request.query["provider"]), safe="") + "#chat")
        return _html(_setup_page(cfg, request, csrf, runner))

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
            if form.get("section") == "identity" or not any(name in form for name in
                    ("provider", "models", "model_choice", "base_url", "api_key", "fallback_models")):
                instance_settings.save_identity(cfg, form)
            else:
                # Existing local clients can still submit the previous combined setup form.
                instance_settings.save_settings(cfg, form)
        except (ValueError, OSError):
            raise web.HTTPBadRequest(text="没能保存，请检查填写内容后重试。") from None
        raise web.HTTPFound("/setup?saved=1")

    async def models_save(request):
        form = await checked_form(request)
        try:
            values = {name: form[name] for name in ("provider", "models", "model_choice", "api_key",
                      "base_url", "fallback_models", "daily_budget") if name in form}
            instance_settings.save_settings(cfg, values)
        except (ValueError, OSError):
            raise web.HTTPBadRequest(text="没能保存，请检查模型和服务连接。") from None
        raise web.HTTPFound("/capabilities?saved=chat#chat")

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
        # 「让 Agent 帮忙」并进了「让她更好」：旧链接、桌宠菜单照样能用，落在同一页的交接说明上。
        return _html(_checkup(cfg, request, csrf))

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
        from regular.pet_brain import CUE_ACTIONS, STATE_CLIPS
        action = str(form.get("action") or "")
        text = " ".join(str(form.get("text") or "").split())
        if action and action not in CUE_ACTIONS:
            return web.json_response({"error": "未知动作。", "actions": list(CUE_ACTIONS)}, status=400)
        if not action and not text:
            return web.json_response({"error": "请给出动作或一句话。"}, status=400)
        if len(text) > 120:
            return web.json_response({"error": "一句话最多 120 个字符。"}, status=400)
        # 状态动作（交给 Agent / 体检 / 没配好）是一次性事件，不是「让她演个动作」的 cue。
        cue = {"kind": "state" if action in STATE_CLIPS else "cue",
               "id": secrets.token_hex(6), "ts": round(time.time(), 3),
               "action": action, "state": action, "text": text}
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
        return _html(_knowledge(cfg, request, csrf=csrf))

    async def knowledge_save(request):
        form = await checked_form(request)
        return await _knowledge_save(cfg, request, form=form)

    async def knowledge_entry_preview(request):
        form = await checked_form(request)
        return _html(_knowledge_entry_preview(cfg, form, csrf=csrf))

    async def knowledge_entry_save(request):
        form = await checked_form(request)
        return await _knowledge_entry_save(cfg, form)

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
    from regular.panel_voice import install_voice_routes
    install_voice_routes(app, cfg, csrf, checked_form,
                         token_check=lambda value: hmac.compare_digest(value.encode(), str(token).encode()))
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
    app.router.add_post("/models/save", models_save)
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
    app.router.add_post("/knowledge/entry/preview", knowledge_entry_preview)
    app.router.add_post("/knowledge/entry/save", knowledge_entry_save)
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
