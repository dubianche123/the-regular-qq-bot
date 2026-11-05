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

import html
import hmac
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


def _page(title, body, active="", refresh=0):
    nav = [
        ("/setup", "开始使用", "setup"),
        ("/", "概览", "home"),
        ("/checkup", "体检", "checkup"),
        ("/iterate", "让 Agent 改进", "iterate"),
        ("/knowledge", "知识库", "knowledge"),
        ("/memory", "记忆", "memory"),
        ("/search", "搜索", "search"),
        ("/decision", "判断 API", "decision"),
    ]
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for href, label, key in nav
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} · The Regular</title>
{f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ''}
<style>
:root {{ color-scheme: light; --bg:#f4f1ea; --card:#fffdf8; --ink:#25231f;
  --muted:#77716a; --line:#ded7cc; --accent:#9a5b35; --bad:#a33b32; --good:#2f6b4f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
header {{ background:var(--card); border-bottom:1px solid var(--line); }}
.wrap {{ max-width:1040px; margin:0 auto; padding:0 20px; }}
header .wrap {{ display:flex; align-items:center; gap:22px; min-height:56px; padding-top:12px; padding-bottom:12px; flex-wrap:wrap; }}
.brand {{ font-weight:700; letter-spacing:.02em; }}
nav {{ display:flex; gap:14px; flex-wrap:wrap; }}
nav a {{ color:var(--muted); text-decoration:none; padding:4px 0; }}
nav a.active {{ color:var(--accent); border-bottom:2px solid var(--accent); }}
main {{ padding:26px 0 60px; }}
h1 {{ font-size:22px; margin:0 0 6px; }}
h2 {{ font-size:16px; margin:24px 0 10px; }}
p {{ margin:6px 0; }}
.muted {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
.card .label {{ color:var(--muted); font-size:12px; }}
.card .value {{ font-size:19px; font-weight:650; margin-top:2px; }}
.good {{ color:var(--good); }} .bad {{ color:var(--bad); }}
table {{ width:100%; border-collapse:collapse; background:var(--card);
  border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ color:var(--muted); font-size:12px; font-weight:600; }}
tr:last-child td {{ border-bottom:0; }}
code,.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
pre {{ background:#292721; color:#eee8dc; padding:14px; border-radius:10px;
  overflow:auto; white-space:pre-wrap; word-break:break-word; }}
textarea {{ width:100%; min-height:420px; padding:12px; border:1px solid var(--line);
  border-radius:10px; font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  background:var(--card); color:var(--ink); }}
input[type=text],input[type=password] {{ padding:9px 11px; border:1px solid var(--line);
  border-radius:8px; background:var(--card); color:var(--ink); min-width:260px; }}
form.decision input {{ width:100%; max-width:560px; min-width:0; }}
form.setup input {{ width:100%; min-width:0; }}
form.setup label {{ display:block; margin:14px 0; }}
input[type=number] {{ padding:9px 11px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); }}
.hero {{ padding:24px; margin-bottom:18px; background:var(--card); border:1px solid var(--line); border-radius:14px; }}
.hero h1 {{ font-size:28px; }}
.steps {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin:18px 0; }}
.step-no {{ color:var(--accent); font-size:12px; font-weight:700; }}
.button {{ display:inline-block; padding:9px 16px; border-radius:8px; background:var(--accent); color:white; text-decoration:none; font-weight:600; }}
.secondary {{ background:transparent; color:var(--accent); border:1px solid var(--line); }}
details {{ margin:16px 0; }} summary {{ cursor:pointer; font-weight:600; }}
textarea.brief {{ min-height:240px; }}
small {{ color:var(--muted); }}
button {{ padding:9px 16px; border:0; border-radius:8px; background:var(--accent);
  color:white; font-weight:600; cursor:pointer; }}
.row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.notice {{ padding:10px 14px; border-radius:9px; border:1px solid var(--line);
  background:var(--card); margin:12px 0; }}
.notice.bad {{ border-color:#d9b3ae; color:var(--bad); }}
.notice.good {{ border-color:#b8d3c5; color:var(--good); }}
.links a {{ color:var(--accent); }}
</style></head><body>
<header><div class="wrap"><div class="brand">The Regular · 维护面板</div>
<nav>{links}</nav></div></header>
<main><div class="wrap">{body}</div></main>
</body></html>"""


def _status_cards(snapshot, state):
    budget = snapshot.get("budget") or {}
    providers = snapshot.get("providers") or {}
    main = (providers.get("providers") or [{}])[0]
    age = time.time() - float(snapshot.get("updated_at") or 0) if snapshot else None
    age_text = "无快照" if age is None else f"{age:.0f}s 前"
    lifecycle = {"ready": "运行中", "starting": "启动中", "stopped": "已停止",
                 "error": "运行出错"}.get(snapshot.get("lifecycle"), "尚无运行记录")
    if age is not None and age > 120:
        lifecycle = "状态已过期"
    gateway = snapshot.get("gateway_connected") and age is not None and age <= 120
    budget_text = f"{budget.get('used', '?')} / {budget.get('limit', '?')}"
    model = (main.get("models") or ["?"])[0]
    vision = providers.get("vision") or {}
    groups = len(_groups(state))
    cards = [
        ("运行状态", lifecycle + (" · QQ 在线" if gateway else " · QQ 待确认")),
        ("快照", age_text),
        ("今日预算", budget_text),
        ("主模型", f"{main.get('label') or main.get('name') or '?'} · {model}"),
        ("读图", f"{vision.get('model') or '已关闭'}"),
        ("群数量", str(groups)),
    ]
    return "".join(
        f'<div class="card"><div class="label">{_esc(label)}</div>'
        f'<div class="value">{_esc(value)}</div></div>'
        for label, value in cards)


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
        digest = (state.get("digests") or {}).get(gid) or {}
        updated = (digest.get("summary") or {}).get("updated") if isinstance(digest, dict) else None
        updated_text = time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "—"
        kfile = f"{gid}.md"
        klink = (f'<a href="/knowledge?file={_esc(kfile)}">{_esc(kfile)}</a>'
                 if kfile in _knowledge_files(cfg) else '<span class="muted">未创建</span>')
        rows.append(
            f"<tr><td class=mono>{_esc(gid)}</td><td>{_esc(info.get('mode') or '—')}</td>"
            f"<td>{info.get('relations', 0)}</td><td>{info.get('promises', 0)}</td>"
            f"<td>{_esc(last_text)}</td><td>{_esc(updated_text)}</td><td>{klink}</td></tr>")
    if not rows:
        group_table = '<p class="muted">还没有群数据。</p>'
    else:
        group_table = ("<table><thead><tr><th>群号</th><th>模式</th><th>关系档案</th>"
                       "<th>承诺</th><th>最近活动</th><th>摘要更新</th><th>知识库</th></tr></thead>"
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
    body = f"""
<h1>你的老群友</h1>
<p class="muted">看它是否连上 QQ、今天的消耗，以及下一步可以做什么。</p>
{_runtime_controls(cfg, csrf, runner) if runner is not None else ''}
<div class="grid">{_status_cards(snapshot, state)}</div>
<h2>群</h2>{group_table}
<h2>知识库</h2>{knowledge_table}
<h2>最近日志</h2><pre>{_esc(log or "暂无日志")}</pre>
"""
    return _page("概览", body, "home", refresh=5 if managed.get("owned") and managed.get("running")
                 and not managed.get("gateway_connected") else 0)


def _setup_page(cfg, request, csrf):
    try:
        settings = instance_settings.public_settings(cfg, request.query.get("provider"))
    except ValueError:
        raise web.HTTPBadRequest(text="请选择页面提供的模型供应商") from None
    provider = settings["provider"]
    choices = " ".join(f'<a class="button {"" if key == provider else "secondary"}" '
                       f'href="/setup?provider={key}">{label}</a>' for key, label in
                       (("openai", "OpenAI 兼容接口"), ("zhipu", "智谱"), ("gemini", "Gemini")))
    notice = '<div class="notice good">配置已保存。运行中的机器人在下次启动时读取新配置。</div>' if request.query.get("saved") else ""
    if settings.get("overridden"):
        notice += '<div class="notice">这些项目由启动环境设置，环境变量优先生效：' + _esc(
            "、".join(settings["overridden"])) + '。请在启动面板的环境中调整。</div>'
    missing = "、".join(settings.get("missing") or ())
    readiness = f"启动前还需填写：{_esc(missing)}" if missing else "必填配置齐了，可以到概览启动。"
    body = f'''<div class="hero"><div class="step-no">第一次使用 · 1 / 3</div>
<h1>先让它走进你的 QQ 群</h1>
<p>填好 QQ 机器人和一个回复模型，启动后先和它私聊，再邀请进群。之后可以让自己的 Agent 根据群聊反馈继续改进。</p></div>
{notice}<p class="notice">{readiness}</p>
<form method="post" action="/setup/save" class="setup">
<input type="hidden" name="csrf" value="{_esc(csrf)}"><input type="hidden" name="provider" value="{_esc(provider)}">
<div class="grid"><section class="card"><h2>1. QQ 机器人</h2>
<p>在 <a href="https://q.qq.com/" target="_blank" rel="noreferrer">QQ 开放平台</a>创建机器人后取得这两项。</p>
<label>AppID<input type="text" name="qq_app_id" value="{_esc(settings.get('qq_app_id'))}" autocomplete="off"></label>
<label>AppSecret<input type="password" name="qq_app_secret" value="" autocomplete="new-password"
placeholder="{'已配置，留空保留' if settings.get('qq_secret_configured') else '填写 QQ AppSecret'}"></label>
<label>它的名字（可选，多个名字用逗号分隔）<input type="text" name="bot_names" value="{_esc(settings.get('bot_names'))}" placeholder="小王,老群友"></label>
<p><small>QQ 控制台还需启用私聊与所需群消息事件，并配置测试或使用范围。平台授权需要你本人完成。</small></p></section>
<section class="card"><h2>2. 回复模型</h2><div class="row">{choices}</div>
<p><small>选择你已开通的服务；使用兼容网关也选 OpenAI 兼容接口。</small></p>
<label>接口地址<input type="text" name="base_url" value="{_esc(settings.get('base_url'))}" placeholder="供应商提供的完整 API 基础地址"></label>
<label>模型 ID<input type="text" name="models" value="{_esc(settings.get('models'))}" placeholder="供应商提供的聊天模型 ID"></label>
<label>API Key<input type="password" name="api_key" value="" autocomplete="new-password"
placeholder="{'已配置，留空保留' if settings.get('key_configured') else '填写这个服务的 Key'}"></label>
<label>每日 AI 调用次数上限<input type="number" name="daily_budget" min="0" value="{_esc(settings.get('daily_budget', 3000))}"></label>
<p><small>这是调用次数，不是金额。保存配置不会请求模型或消耗调用额度；Key 不在页面回显。</small></p></section></div>
<div class="row" style="margin-top:18px"><button type="submit">保存配置</button><a class="button secondary" href="/">去启动和检查连接 →</a></div></form>
<section class="card" style="margin-top:22px"><h2>3. 先完成基本聊天，再加能力</h2>
<p>判断 API、画图、语音等可以稍后配置。判断 API 同时用于提醒的语义审核，未配置时不会保存新的提醒。</p>
<p><a href="/decision">配置判断 API</a> · <a href="/iterate">看看怎样让你的 Agent 帮忙改进</a></p></section>
<details><summary>配置和数据保存在哪里？</summary><p>实例：<code>{_esc(settings.get('instance_dir'))}</code></p>
<p>配置：<code>{_esc(settings.get('config_file'))}</code></p><p>聊天与私有数据：<code>{_esc(settings.get('data_dir'))}</code></p>
<p>源码可以升级，实例目录请保留。不要把配置、群聊记录或密钥推送到公开仓库。</p></details>'''
    return _page("开始使用", body, "setup")


def _runtime_controls(cfg, csrf, runner):
    settings = instance_settings.public_settings(cfg)
    managed = runner.snapshot()
    snapshot, state = _status(cfg)
    age = time.time() - float(snapshot.get("updated_at") or 0) if snapshot else None
    online = bool(managed.get("gateway_connected"))
    if managed.get("owned") and managed.get("running"):
        controls = f'<form method="post" action="/runtime/stop"><input type="hidden" name="csrf" value="{_esc(csrf)}"><button class="secondary">停止本面板启动的机器人</button></form>'
    elif managed.get("external"):
        controls = '<p class="muted">机器人由其他终端或常驻服务管理，请在原启动方式中操作；本面板不会停止它。</p>'
    elif settings.get("ready"):
        controls = f'<form method="post" action="/runtime/start"><input type="hidden" name="csrf" value="{_esc(csrf)}"><button>启动机器人</button></form>'
    else:
        controls = '<a class="button" href="/setup">完成首次配置 →</a>'
    if online:
        headline, detail = '已连上 QQ', '先给机器人发一条私聊，绑定你为维护者；再在群里叫它的名字试一句。'
    elif managed.get("running"):
        headline, detail = '进程已启动，等待 QQ 连接确认', '启动成功以真实 QQ 网关就绪为准。若迟迟没有连上，请检查 AppID、AppSecret 和下方日志。'
    else:
        headline, detail = '准备好后，启动你的老群友', '运行需要保持本机和启动面板的终端开启。常驻运行的设置可以稍后再做。'
    if age is not None and age > 120:
        detail = '运行状态已超过两分钟未更新，不能据此判断在线。请检查原进程或下方启动日志。'
    owner_path = getattr(cfg, "OWNER_FILE", str(Path(cfg.DATA_DIR) / "owner.txt"))
    owner = bool(_read_text(owner_path).strip())
    code = snapshot.get("code") or {}
    boot_log = managed.get("log_file") or str(Path(cfg.DATA_DIR) / "panel-start.log")
    return f'''<section class="hero"><div class="step-no">运行与连接</div><h2>{headline}</h2><p>{detail}</p>
<div class="row">{controls}<a href="/">刷新状态</a> · <a href="/setup">查看或修改配置</a></div></section>
<div class="steps"><div class="card"><div class="step-no">1 · 服务配置</div><h2>{'已填齐' if settings.get('ready') else '尚待填写'}</h2><a href="/setup">QQ 与回复模型</a></div>
<div class="card"><div class="step-no">2 · QQ 连接</div><h2>{'已确认' if online else '等待确认'}</h2><p>以运行进程的实际状态为准</p></div>
<div class="card"><div class="step-no">3 · 你的群</div><h2>{len(_groups(state))} 个群 · {'已认主' if owner else '待私聊认主'}</h2><p>首次非空私聊会静默绑定维护者</p></div>
<div class="card"><div class="step-no">下一步 · 适应你的群</div><h2>从反馈开始</h2><a href="/checkup">看今日体检</a> · <a href="/iterate">交给自己的 Agent</a></div></div>
<details><summary>当前版本与启动日志</summary><p>运行代码：<code>{_esc(code.get('release') or '尚无运行记录')}</code></p>
<p>代码位置：<code>{_esc(code.get('source_root') or '—')}</code></p><pre>{_esc(_tail(str(boot_log)) or '暂无本面板启动日志')}</pre></details>'''


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
    body = f'''<div class="hero"><div class="step-no">让它更懂你们的群</div><h1>把反馈交给你的 Agent</h1>
<p>你指出哪里不对，Agent 找现场、提出改动，用新旧回放检验。你决定哪些改动上线。</p></div>
<div class="steps"><div class="card"><div class="step-no">1 · 看现场</div><h2>截图或每日体检</h2><p>记下哪句话让你觉得不对，而不必先猜该改哪条提示词。</p><a href="/checkup">打开体检 →</a></div>
<div class="card"><div class="step-no">2 · 让 Agent 修改</div><h2>带上目录和流程</h2><p>{source_note}</p><a href="https://github.com/dubianche123/the-regular-qq-bot/releases/latest" target="_blank" rel="noreferrer">获取源码包 →</a></div>
<div class="card"><div class="step-no">3 · 比较后再发布</div><h2>看修好，也看改坏</h2><p>体检只在本机读日志。付费回放与真实群聊外发先预览并征得你的同意；面板不会自动上传聊天或自动改代码。</p></div></div>
<h2>可直接交给 Agent 的说明</h2><p>下载或复制下面这段，在编码 Agent 中打开源码目录后发送。它不会自动连接或控制你的 Agent。</p>
<p><a class="button" href="/iterate/brief">下载交接说明</a></p><textarea class="brief" readonly aria-label="交给 Agent 的任务说明">{_esc(_agent_brief(cfg))}</textarea>
<h2>日常资料</h2><div class="row"><a href="/knowledge">维护群知识</a> · <a href="/memory">查看已生成的记忆</a> · <a href="/decision">配置判断帮手</a></div>'''
    return _page("让 Agent 改进", body, "iterate")


def _knowledge(cfg, request):
    files = _knowledge_files(cfg)
    name = request.query.get("file") or (files[0] if files else "all.md")
    if not _safe_name(name):
        raise web.HTTPBadRequest(text="非法知识库文件名")
    path = _knowledge_path(cfg, name)
    content = _read_text(path, limit=MAX_EDIT_BYTES)
    parsed = knowledge_mod.KnowledgeBase(cfg.KNOWLEDGE_DIR).parse_file(path)
    entries = "".join(
        f"<li><b>{_esc(entry['title'])}</b> "
        f"<span class=mono>{_esc('、'.join(entry['keywords']))}</span><br>"
        f"{_esc(entry['content'])}</li>" for entry in parsed)
    preview = (f"<ul>{entries}</ul>" if entries
               else '<p class="muted">当前文件没有可解析条目。</p>')
    saved = ('<div class="notice good">已保存；下一条相关消息会使用新内容。</div>'
             if request.query.get("saved") else "")
    options = "".join(
        f'<option value="{_esc(item)}"{" selected" if item == name else ""}>{_esc(item)}</option>'
        for item in files)
    body = f"""
<h1>知识库</h1>
<p class="muted">all.md 全群共享；&lt;群号&gt;.md 只对该群生效。每个 ## 标题一条，
关键词行写成 <span class=mono>关键词：奶茶、请客</span>。保存后下一轮生效，不需要重启。</p>
{saved}
<div class="row" style="margin:14px 0">
<form method="get" action="/knowledge" class="row">
<select name="file">{options}</select>
<button type="submit">打开</button>
</form>
<form method="get" action="/knowledge" class="row">
<input type="text" name="file" placeholder="新建：群号.md">
<button type="submit">新建</button>
</form>
</div>
<form method="post" action="/knowledge/save">
<input type="hidden" name="file" value="{_esc(name)}">
<textarea name="content" spellcheck="false">{_esc(content)}</textarea>
<div class="row" style="margin-top:10px">
<button type="submit">保存 {_esc(name)}</button>
<span class="muted">解析到 {len(parsed)} 条；敏感词条目会被跳过。</span>
</div>
</form>
<h2>解析预览</h2>{preview}
"""
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
    body = f"""
<h1>记忆</h1>
<p class="muted">这里只读。摘要由每日压缩生成；要纠正具体事实，请改知识库。</p>
{picker}
<pre>{_esc(content or "暂无可显示的每日记忆。")}</pre>
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
        badge = (f'<span class="good">已标 yes</span>' if verdict == "yes"
                 else '<span class="bad">已标 no</span>' if verdict == "no"
                 else '<span class="muted">未标注</span>')
        ctx_html = f"<pre>{_esc(chr(10).join(f['context']))}</pre>" if f.get("context") else ""
        raw_cmds = checkup_mod.case_add_commands(day, f)
        cmds = ""
        if raw_cmds:
            cmd_list = "；".join(f"<code>{_esc(cmd)}</code>" for cmd in raw_cmds)
            cmds = f'<p class="muted">存成用例：{cmd_list}</p>'
        cards.append(f"""
<div class="card" style="margin:12px 0">
<div class="row" style="justify-content:space-between">
  <div><strong>[{_esc(f['id'])}] {_esc(f['label'])}</strong> · <span class="mono">{_esc(clock)}</span> · {badge}</div>
  <form method="post" action="/checkup/label" class="row">
    <input type="hidden" name="csrf" value="{_esc(csrf)}">
    <input type="hidden" name="day" value="{_esc(day)}">
    <input type="hidden" name="id" value="{_esc(f['id'])}">
    <button type="submit" name="verdict" value="yes">是问题 (yes)</button>
    <button type="submit" name="verdict" value="no" style="background:var(--muted)">误报 (no)</button>
  </form>
</div>
<p>{_esc(f['detail'])}</p>
{ctx_html}{cmds}
</div>""")
    findings_html = "".join(cards) if cards else '<p class="muted">今天没有可疑的地方。</p>'
    i = out["inputs"]
    body = f"""
<h1>每日体检</h1>
{warning}
<p class="muted">只读扫交互日志与每轮输入，不联网、不调模型。原料：消息 {i['incoming']} 条，送达 {i['delivered']} 条，
每轮输入 {i['turns']} 轮 / 分诊 {i['triage']} 次；基线 {i['baseline_days']} 天。</p>
<form method="get" action="/checkup" class="row" style="margin:12px 0">
<select name="day">{options}</select>
<button type="submit">查看</button>
</form>
<table><thead><tr><th>信号</th><th>条数</th><th>候选标注确认率</th></tr></thead>
<tbody>{"".join(sig_rows)}</tbody></table>
<h2>候选明细（{len(out['findings'])} 条）</h2>
{findings_html}
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
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        return response

    async def home(request):
        settings = instance_settings.public_settings(cfg)
        snapshot, state = _status(cfg)
        if not settings.get("ready") and not snapshot and not state:
            return _html(_setup_page(cfg, request, csrf))
        return _html(_dashboard(cfg, csrf=csrf, runner=runner))

    async def setup_page(request):
        return _html(_setup_page(cfg, request, csrf))

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
    app.router.add_get("/", home)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/setup/save", setup_save)
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
