"""桌宠看到的 agent 状态：只读、粗粒度、不含聊天内容。

小王在 QQ 里做的事本来就落在本机文件里（status.json、state.json 的心情、按天的交互日志）。
这里把它们压成几个桌宠能演的信号：在不在线、心情、作息、群里热不热闹、刚才被@/在回/在查/在画/在录/回了什么类型。
不返回任何文字、群号、openid 或消息 ID；不调用模型、不写文件、不碰运行中的机器人。
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import time

import activation
from archive import day_key
from regular.panel_runtime import _pid_alive

EVENT_WINDOW = 600.0      # 只看最近 10 分钟发生的事
CHATTER_WINDOW = 300.0    # 群里热闹程度：最近 5 分钟收到的消息数
STATUS_MAX_AGE = 120.0
TAIL_BYTES = 128 * 1024
STATE_MAX_BYTES = 8 * 1024 * 1024


def _read_json(path, limit=256 * 1024):
    try:
        path = Path(path)
        if path.stat().st_size > limit:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def _bot(cfg, now):
    status = _read_json(cfg.STATUS_FILE)
    if not status or status.get("schema_version") != 1 or not _pid_alive(status.get("pid")):
        return "offline", False
    try:
        age = now - float(status.get("updated_at"))
    except (TypeError, ValueError):
        return "offline", False
    fresh = math.isfinite(age) and -5 <= age <= STATUS_MAX_AGE
    ready = fresh and status.get("lifecycle") == "ready"
    return ("ready" if ready else "offline"), bool(ready and status.get("gateway_connected"))


def _mood(cfg, now):
    state = _read_json(cfg.STATE_FILE, STATE_MAX_BYTES)
    return activation.mood_from_state((state or {}).get("mood"), now)


def _tail_rows(path):
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - TAIL_BYTES))
            chunk = handle.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if len(chunk) >= TAIL_BYTES:
        lines = lines[1:]          # the first line may be cut in half
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _event(row):
    """Map one interaction row to a content-free pet signal, or None."""
    kind = row.get("kind")
    if kind == "incoming" and row.get("at") is True and not row.get("cmd"):
        # 被 @、被喊名字、被引用是三种叫法；旧版机器人不记 call，只知道「叫到了」。
        return {"at": "mention", "name": "named", "quote": "quoted"}.get(row.get("call"), "called")
    if kind == "decision" and row.get("selected") is True:
        return "replying"
    if kind == "lookup":
        return "searching"         # 当场没查完，先回了一句「我去查查」，转后台在查
    if kind == "media":
        # 开始画 / 开始录：过程中就拿起速写本或麦克风；没发成（failed / unconfirmed）就放下。
        if row.get("status") == "started":
            return {"image": "drawing", "animation": "drawing", "voice": "singing"}.get(row.get("medium"))
        return "media_end"
    if kind == "delivery" and row.get("status") == "delivered":
        route = row.get("route")
        if route == "image":
            return "drew"
        if row.get("medium") == "voice":
            return "voice"
        return {"proactive": "proactive", "banter": "chimed"}.get(route, "said")
    return None


def _activity(cfg, now, since):
    folder = Path(cfg.INTERACTION_LOG_DIR)
    days = {day_key(now - EVENT_WINDOW), day_key(now)}
    events, chatter = [], 0
    for day in sorted(days):
        for row in _tail_rows(folder / f"{day}.jsonl"):
            ts = row.get("ts")
            if type(ts) not in (int, float) or not math.isfinite(ts) or not now - EVENT_WINDOW <= ts <= now + 5:
                continue
            if row.get("kind") == "incoming" and ts >= now - CHATTER_WINDOW:
                chatter += 1
            signal = _event(row)
            if signal and ts > since:
                events.append({"kind": signal, "ts": round(float(ts), 3)})
    events.sort(key=lambda e: e["ts"])
    return events[-40:], chatter


def day_summary(cfg, *, now=None):
    """Today in numbers only: how often she was called, what she did. No text, no ids (only a group count)."""
    now = time.time() if now is None else now
    counts, groups = {}, set()
    try:
        with open(Path(cfg.INTERACTION_LOG_DIR) / f"{day_key(now)}.jsonl", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("kind")
                if kind == "incoming":
                    counts["messages"] = counts.get("messages", 0) + 1
                    groups.add(row.get("group"))
                elif kind == "decision" and row.get("selected") is False:
                    counts["passed"] = counts.get("passed", 0) + 1
                elif kind == "natural_feedback":
                    counts["feedback"] = counts.get("feedback", 0) + 1
                signal = _event(row)
                if signal:
                    counts[signal] = counts.get(signal, 0) + 1
    except OSError:
        pass
    return {"day": day_key(now), "groups": len(groups - {None}), **counts}


def render_day_summary(summary):
    """The numbers as one plain Chinese paragraph for her to talk from."""
    n = summary.get
    if not n("messages"):
        return f"今天（{n('day')}）本机还没有收到群消息记录。"
    called = sum(n(k, 0) for k in ("mention", "named", "quoted", "called"))
    parts = [f"{n('groups')} 个群一共收到 {n('messages')} 条消息"]
    if called:
        how = "、".join(f"{label} {n(k)}" for k, label in (("mention", "@"), ("named", "喊名字"), ("quoted", "引用"))
                       if n(k))
        parts.append(f"被叫到 {called} 次" + (f"（{how}）" if how else ""))
    for key, words in (("said", "回了 {} 句"), ("chimed", "没人叫也插了 {} 句"), ("proactive", "主动开了 {} 次话头"),
                       ("drew", "画了 {} 张图"), ("voice", "发了 {} 条语音"), ("searching", "转后台查资料 {} 次"),
                       ("media_end", "有 {} 次图或语音没发成"), ("passed", "判断后没接的有 {} 次"),
                       ("feedback", "群友对她的回应有反应 {} 次")):
        if n(key):
            parts.append(words.format(n(key)))
    return f"今天（{n('day')}）：" + "，".join(parts) + "。"


def snapshot(cfg, *, since=None, now=None, cues=()):
    """The whole presence payload. `since` filters events the pet has already seen."""
    now = time.time() if now is None else now
    since = now - 60 if since is None or not math.isfinite(since) else max(since, now - EVENT_WINDOW)
    bot, gateway = _bot(cfg, now)
    mood = _mood(cfg, now)
    _, circadian = activation.circadian(datetime.fromtimestamp(now).hour)
    events, chatter = _activity(cfg, now, since) if bot == "ready" else ([], 0)
    events += [dict(cue) for cue in cues if cue.get("ts", 0) > since]
    return {"bot": bot, "gateway_connected": gateway, "mood": round(mood, 3),
            "circadian": circadian, "chatter": chatter, "events": events, "now": round(now, 3)}
