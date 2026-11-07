# -*- coding: utf-8 -*-
"""每日体检：扫一天的交互日志和每轮输入，列出可疑的地方（docs/self-iteration.md ②）。

只读、不联网、不调模型（跟 regular calibration 一样）。每条信号都是从一次事故来的，
列出来的只是**候选**，不是结论 ——「？？」可能是在问别人。独立评审另行标
「是 / 不是」（`regular checkup --label ID yes|no`），报告里按信号统计候选确认率，
准的留下，不准的改或删。

| 信号          | 怎么认                                                          | 来自 |
| ------------- | --------------------------------------------------------------- | ---- |
| unanswered    | 叫到它（at=1）或 @ 了从未发言对象的消息两分钟内没有送达           | 11-03 09:00 |
| repeated      | 同一个人五分钟内两条以上高度重合、其中至少一条叫到了它              | 11-03 09:00–09:04 |
| corrected     | 分诊判出群友在纠正 / 叫停它（natural_feedback）                    | 分诊层已有 |
| bad_reaction  | 它说完一分钟内有人回「？？」「答非所问」「串了」……                 | 11-02 20:57 |
| promised      | 它说了「我去查」「记下了」「到点叫你」，那一轮却没实际记上提醒或起查询 | 11-01 02:49、11-03 09:05 |
| catchphrase   | 它当天自己写的话里同一个 4–6 字片段在三条以上出现，且比前七天明显多  | 「刚醒 / 开机」三天 27 句 |
| blind         | 它说自己看不见图（「只看到图片标记」「图没显示出来」）              | 11-01 12:31、11-04 11:39 / 11:43 |
| sensitive     | 它的输出碰了敏感词表，或被平台拒收换成了兜底话                     | |
| recall_empty  | 分诊说要翻旧账，检索却一条没挑到（kind=recall 的检索记录）          | 检索策略自迭代 |

阈值和词表集中在 DEFAULT_POLICY。体检本身也是可迭代对象：`regular tune checkup` 拿标过的候选
在记录过的日子上重放，只在「不丢任何一条已确认的候选」前提下收紧阈值、删掉只报错的词，
维护者 `regular policy apply` 之后写进 data/policies/checkup.json（见 docs/self-iteration.md）。
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import expression
import quotes
import turns as turns_mod
from interactions import SCHEMA as INTERACTION_SCHEMA

SCHEMA_OUT = "regular.checkup.v1"

UNANSWERED_SECONDS = 120
REPEAT_WINDOW_SECONDS = 300
REPEAT_SIMILARITY = 0.6
REPEAT_MIN_CHARS = 4
REACTION_SECONDS = 60
CATCHPHRASE_SIZES = (4, 5, 6)
CATCHPHRASE_MIN = 3
CATCHPHRASE_BASELINE_DAYS = 7
CATCHPHRASE_TOP = 8
CONTEXT_BEFORE = 5
CONTEXT_AFTER = 3

# 群里「没听懂 / 不对劲」的说法。这只是体检的候选筛子，不进任何线上判断。
# 「没问你」那几句是它接了别人之间的话、被人点破（11-04 11:49「没问你小王，问的罗老板」）：
# 带着它的名字，却不是在叫它，「叫了没回」那条不算它（NOT_ADDRESSED_MARKERS）。
NOT_ADDRESSED_MARKERS = ("没问你", "不是问你", "没叫你", "没跟你说", "不是跟你说")
CONFUSED_MARKERS = ("答非所问", "又似了", "串了", "没听懂", "听不懂", "啥意思", "什么意思",
                    "在说啥", "说的啥", "你在说什么", "牛头不对马嘴") + NOT_ADDRESSED_MARKERS
# 它说自己看不见图：读图没成、或者把自己刚画的图当成了别人发的（11-04 11:43「没真看见图」）
BLIND_MARKERS = ("看不到图", "看不见图", "没看见图", "没看到图", "图片标记", "没显示出来",
                 "图没显示", "图片没显示", "图打不开", "图片打不开")
# 它答应了要做事的说法：分成查询承诺与提醒承诺，各自核对实际执行证据
LOOKUP_PROMISE_MARKERS = ("我去查", "我去搜", "我查查", "我查一下", "我搜一下",
                          "找到再告诉你", "找到告诉你", "回头告诉你", "查到告诉你")
REMIND_PROMISE_MARKERS = ("记下了", "记住了", "到点叫你", "到点提醒",
                          "到时候叫你", "到时候提醒")
PROMISE_MARKERS = LOOKUP_PROMISE_MARKERS + REMIND_PROMISE_MARKERS
CORRECTION_ACTIONS = ("repair_briefly", "back_off")

# ── 体检策略：每个信号用到的数值和词表（默认值即上面那几行常量）──
DEFAULT_POLICY = {
    "unanswered_seconds": UNANSWERED_SECONDS,
    "repeat_window_seconds": REPEAT_WINDOW_SECONDS,
    "repeat_similarity": REPEAT_SIMILARITY,
    "repeat_min_chars": REPEAT_MIN_CHARS,
    "reaction_seconds": REACTION_SECONDS,
    "confused_min_qmarks": 2,
    "confused_markers": list(CONFUSED_MARKERS),
    "lookup_promise_markers": list(LOOKUP_PROMISE_MARKERS),
    "remind_promise_markers": list(REMIND_PROMISE_MARKERS),
    "lookup_window_seconds": 300,
    "catchphrase_min": CATCHPHRASE_MIN,
    "catchphrase_ratio": 2.0,
    "catchphrase_min_talks": 2,
    "blind_markers": list(BLIND_MARKERS),
}

# 调参器的搜索空间。数值给一组候选值，候选值的范围就是边界；
# 词表只能删、不能加（"subset"）—— 新说法要靠编码代理按事故补，不让自动搜索堆同义词。
POLICY_SPACE = {
    "unanswered_seconds": (60, 90, 120, 180, 300),
    "repeat_window_seconds": (120, 180, 300, 600),
    "repeat_similarity": (0.5, 0.6, 0.7, 0.8),
    "repeat_min_chars": (3, 4, 6, 8),
    "reaction_seconds": (20, 30, 45, 60, 90, 120),
    "confused_min_qmarks": (1, 2, 3),
    "confused_markers": "subset",
    "lookup_promise_markers": "subset",
    "remind_promise_markers": "subset",
    "lookup_window_seconds": (120, 300, 600),
    "catchphrase_min": (3, 4, 5, 6),
    "catchphrase_ratio": (1.5, 2.0, 3.0, 4.0),
    "catchphrase_min_talks": (2, 3),
    "blind_markers": "subset",
}

# 每个参数只影响哪些信号：调参按信号分开调，互不牵连。
PARAM_SIGNALS = {
    "unanswered_seconds": ("unanswered",),
    "repeat_window_seconds": ("repeated",),
    "repeat_similarity": ("repeated",),
    "repeat_min_chars": ("repeated",),
    "reaction_seconds": ("bad_reaction",),
    "confused_min_qmarks": ("bad_reaction",),
    "confused_markers": ("bad_reaction",),
    "lookup_promise_markers": ("promised",),
    "remind_promise_markers": ("promised",),
    "lookup_window_seconds": ("promised",),
    "catchphrase_min": ("catchphrase",),
    "catchphrase_ratio": ("catchphrase",),
    "catchphrase_min_talks": ("catchphrase",),
    "blind_markers": ("blind",),
}


def validate_policy(raw):
    """把一份策略规整成完整策略；未知键、越界数值、往词表里加新词都抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("checkup policy must be a JSON object")
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_POLICY.items()}
    for key, value in raw.items():
        if key not in DEFAULT_POLICY:
            raise ValueError(f"unknown checkup policy key: {key!r}")
        space = POLICY_SPACE[key]
        if space == "subset":
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"checkup policy {key} must be a list of strings")
            extra = [v for v in value if v not in DEFAULT_POLICY[key]]
            if extra:
                raise ValueError(f"checkup policy {key} may only drop markers, not add {extra}")
            out[key] = [v for v in DEFAULT_POLICY[key] if v in value]
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"checkup policy {key} must be a number")
        if not min(space) <= value <= max(space):
            raise ValueError(f"checkup policy {key}={value} outside [{min(space)}, {max(space)}]")
        out[key] = value
    return out


def policy_id(policy):
    """报告里写明用的哪套口径：默认值写 default，否则写指纹（换了口径的两天不能直接比条数）。"""
    if policy == validate_policy({}):
        return "default"
    blob = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def load_policy(path):
    """读 data/policies/checkup.json（维护者 apply 过的那份）；没有就是默认值。坏文件直接报错，
    不悄悄退回默认 —— 体检是给人看的，用错了口径比报错更难发现。"""
    if not path:
        return validate_policy({})
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return validate_policy({})
    except (OSError, ValueError) as err:
        raise ValueError(f"checkup policy file {path} unreadable: {err}") from None
    return validate_policy(doc.get("policy", doc) if isinstance(doc, dict) else doc)


LABELS = {
    "unanswered": "叫了没回",
    "repeated": "同一句问好几遍",
    "corrected": "被纠正",
    "bad_reaction": "群里的反应不对",
    "promised": "只说不做",
    "catchphrase": "口头禅",
    "sensitive": "碰了敏感词",
    "blind": "说看不见图",
    "recall_empty": "翻旧账落空",
}

_WORD = re.compile(r"[\w\u4e00-\u9fff]+")


# ── 读 ──

def _read_jsonl(path, schema=None):
    out = []
    try:
        with open(path, "rb") as stream:
            for raw in stream:
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict) and (schema is None or row.get("schema") == schema):
                    out.append(row)
    except OSError:
        pass
    return out


def _shift(day, delta):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=delta)).isoformat()


def load_day(day, *, interaction_dir, turn_dir, archive_dir=None):
    """一天的原料：incoming / delivery / next_delivery / natural_feedback / decision + 每轮输入 + 已知发言人。"""
    rows = _read_jsonl(Path(interaction_dir) / f"{day}.jsonl", INTERACTION_SCHEMA)
    rows.sort(key=lambda r: r.get("ts") or 0.0)
    data = {
        "day": day,
        "incoming": [],
        "delivery": [],
        "images": [],   # 它发的图：描述只作追溯证据，不当成说出口的话跑文字信号
        "next_delivery": [],
        "feedback": [],
        "decision": [],
        "speakers": defaultdict(set),
    }
    for row in rows:
        kind = row.get("kind")
        if kind == "incoming":
            data["incoming"].append(row)
            if row.get("group") and row.get("user"):
                data["speakers"][row["group"]].add(row["user"])
        elif (kind == "delivery" and row.get("status") == "delivered"
              and row.get("actor") == "assistant" and row.get("route") == "image"):
            data["images"].append(row)
        elif (kind == "delivery" and row.get("status") == "delivered"
              and row.get("actor") == "assistant" and row.get("text")):
            data["delivery"].append(row)
        elif kind == "natural_feedback":
            data["feedback"].append(row)
        elif kind == "decision":
            data["decision"].append(row)

    # 跨日送达窗口：23:59 进来的消息可能在次日 00:00~00:02 才送达
    next_rows = _read_jsonl(Path(interaction_dir) / f"{_shift(day, 1)}.jsonl", INTERACTION_SCHEMA)
    for row in next_rows:
        if (row.get("kind") == "delivery" and row.get("status") == "delivered"
                and row.get("actor") == "assistant" and row.get("route") != "image"
                and row.get("text")):
            data["next_delivery"].append(row)

    if archive_dir:
        arch_rows = _read_jsonl(Path(archive_dir) / f"{day}.jsonl")
        for row in arch_rows:
            if row.get("group") and row.get("sender"):
                data["speakers"][row["group"]].add(row["sender"])
        if not data["incoming"]:
            # 交互日志关着的部署：退回原始归档
            for row in arch_rows:
                data["incoming"].append({
                    "ts": row.get("ts"),
                    "group": row.get("group"),
                    "user": row.get("sender"),
                    "text": row.get("text"),
                    "message_id": row.get("message_id"),
                    "at": bool(row.get("at")),
                    "cmd": bool(row.get("cmd")),
                    "at_ids": list(row.get("at_ids") or ()),
                    "mentions": list(row.get("mentions") or ()),
                })
        else:
            # 交互日志开着时，把归档里的原始 at_ids / mentions 合并到同 message_id 的 incoming 行上
            by_mid = {r.get("message_id"): r for r in arch_rows if r.get("message_id")}
            for inc in data["incoming"]:
                arch = by_mid.get(inc.get("message_id"))
                if arch:
                    if arch.get("at_ids") and not inc.get("at_ids"):
                        inc["at_ids"] = list(arch["at_ids"])
                    if arch.get("mentions") and not inc.get("mentions"):
                        inc["mentions"] = list(arch["mentions"])

    # 往前扫 7 天已知发言人，用来判断「@ 了一个从未发言的对象」（如 11-03 之前的 D3D7）
    for back in range(1, CATCHPHRASE_BASELINE_DAYS + 1):
        prev_day = _shift(day, -back)
        for row in _read_jsonl(Path(interaction_dir) / f"{prev_day}.jsonl", INTERACTION_SCHEMA):
            if row.get("kind") == "incoming" and row.get("group") and row.get("user"):
                data["speakers"][row["group"]].add(row["user"])
        if archive_dir:
            for row in _read_jsonl(Path(archive_dir) / f"{prev_day}.jsonl"):
                if row.get("group") and row.get("sender"):
                    data["speakers"][row["group"]].add(row["sender"])

    data["turns"] = turns_mod.read_day(turn_dir, day)
    return data


def load_baseline(day, *, interaction_dir, days=CATCHPHRASE_BASELINE_DAYS):
    """前 N 天它自己送达的话（算口头禅基线用），按天分组；没有记录的天不算进分母。"""
    out = {}
    for i in range(1, days + 1):
        d = _shift(day, -i)
        texts = [r.get("text") for r in _read_jsonl(Path(interaction_dir) / f"{d}.jsonl",
                                                    INTERACTION_SCHEMA)
                 if r.get("kind") == "delivery" and r.get("status") == "delivered"
                 and r.get("actor") == "assistant" and r.get("route") != "image"
                 and r.get("text")]
        if texts:
            out[d] = texts
    return out


# ── 时间线与上下文 ──

def _timeline(data):
    """按群把人和它的话排成一条时间线；每个元素 (ts, who, text, ref)。

    旧图片投递在交互日志里没有文字，按来源消息找回那一轮写的画面描述，记成「[图：…]」或
    「[动图：…]」（跟线上它自己会话里那行一样）；找不到就记「[图]」。
    不补的话，看前后文的人不知道群里那张图是它画的。
    """
    drawings = _drawings(data.get("turns") or ())
    lines = defaultdict(list)
    for row in data["incoming"]:
        lines[row.get("group")].append({"ts": row.get("ts") or 0.0, "bot": False,
                                        "user": row.get("user"), "text": row.get("text") or "",
                                        "message_id": row.get("message_id")})
    for row in data["delivery"]:
        lines[row.get("group")].append({"ts": row.get("ts") or 0.0, "bot": True,
                                        "user": row.get("user"), "text": row.get("text") or "",
                                        "outgoing_id": row.get("outgoing_id"),
                                        "turn_id": row.get("turn_id")})
    for row in data.get("images") or ():
        drawn = _drawn(drawings, row)
        label = "动图" if drawn and drawn[3] else "图"
        lines[row.get("group")].append({"ts": row.get("ts") or 0.0, "bot": True,
                                        "user": row.get("user"),
                                        "text": row.get("text") or (
                                            f"[{label}：{drawn[2]}]" if drawn else "[图]"),
                                        "outgoing_id": row.get("outgoing_id"),
                                        "turn_id": row.get("turn_id") or (drawn[1] if drawn else None)})
    for items in lines.values():
        items.sort(key=lambda r: r["ts"])
    return lines


def _drawings(turns):
    """写了〔画〕/〔动图〕的轮次，按群、请求人、来源消息建索引，再按时间排。"""
    out = defaultdict(list)
    for t in turns:
        if (t.get("kind") == "reply" and t.get("message_id")
                and t.get("group") and t.get("user")):
            said = expression.parse(t.get("raw") or "")
            if said.kind == "image":
                out[(t["group"], t["user"], t["message_id"])].append(
                    (t.get("ts") or 0.0, t.get("turn_id"), said.spec, said.animated))
    for items in out.values():
        items.sort(key=lambda d: d[0])
    return out


def _drawn(drawings, row):
    """优先认显式 turn_id；旧投递只回溯同群同人的来源消息，不猜别人的轮次。"""
    ts = row.get("ts") or 0.0
    key = (row.get("group"), row.get("user"), row.get("source_message_id"))
    before = [d for d in drawings.get(key) or () if d[0] <= ts]
    if row.get("turn_id"):
        before = [d for d in before if d[1] == row["turn_id"]]
    return before[-1] if before else None


def _context(timeline, group, ts, names):
    items = timeline.get(group) or []
    before = [r for r in items if r["ts"] < ts][-CONTEXT_BEFORE:]
    at = [r for r in items if r["ts"] == ts]
    after = [r for r in items if r["ts"] > ts][:CONTEXT_AFTER]
    return [_line(r, names, mark=r in at) for r in before + at + after]


def _line(row, names, mark=False):
    clock = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
    who = names.get("bot", "bot") if row["bot"] else names.get(row.get("user")) or _tail(row.get("user"))
    text = (row["text"] or "").replace("\n", " ⏎ ")
    return f"{'▶ ' if mark else ''}{clock} {who}：{text[:160]}"


def _tail(value):
    return f"群友{str(value or '')[-4:]}" if value else "群友"


def _finding(signal, group, ts, *, user=None, detail="", turn_ids=(), message_id=None,
             context=(), key="", marker=None):
    raw = f"{signal}|{group}|{key or ts}"
    out = {"id": hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10], "signal": signal,
           "anchor": str(key or ts),
           "label": LABELS[signal], "ts": ts, "group": group, "user": user, "detail": detail,
           "message_id": message_id, "turn_ids": [t for t in turn_ids if t],
           "context": list(context)}
    if marker:
        out["marker"] = marker   # 由哪个词认出来的：调参器做逐词消融用
    return out


def _unknown_at_targets(row, speakers_by_group):
    """返回这条消息里 @ 了但从未在本群发过言的对象 ID 列表（如 11-03 之前的机器人自身成员 openid）。"""
    group = row.get("group")
    known = (speakers_by_group or {}).get(group) or set()
    raw_ids = [*(row.get("at_ids") or ()), *(row.get("mentions") or ())]
    seen = []
    for oid in raw_ids:
        if oid and oid not in known and oid not in seen:
            seen.append(oid)
    return seen


# ── 信号 ──

def find_unanswered(data, timeline, names, turn_by_message, *, now=None, policy=None):
    p = policy or DEFAULT_POLICY
    limit = p["unanswered_seconds"]
    now = time.time() if now is None else float(now)
    all_deliveries = list(data["delivery"]) + list(data.get("next_delivery") or ())
    answered = {r.get("source_message_id") for r in all_deliveries
                if r.get("source_message_id")}
    late = {}
    for r in all_deliveries:
        late.setdefault(r.get("source_message_id"), r.get("ts") or 0.0)
    reasons = {}
    for r in data["decision"]:
        if r.get("source_message_id"):
            reasons.setdefault(r["source_message_id"], f"{r.get('route')}:{r.get('reason')}")
    speakers = data.get("speakers") or {}
    out = []
    for row in data["incoming"]:
        if row.get("cmd") or any(m in (row.get("text") or "") for m in NOT_ADDRESSED_MARKERS):
            continue   # 「没问你小王」带着它的名字，却是叫它别插嘴：算群里的反应，不算叫了没回
        is_at = bool(row.get("at"))
        unknown_ats = [] if is_at else _unknown_at_targets(row, speakers)
        if not is_at and not unknown_ats:
            continue
        mid = row.get("message_id")
        ts = row.get("ts") or 0.0
        if mid in answered and late.get(mid, 0) - ts <= limit:
            continue
        # 还没等满两分钟的不抢报未回复
        if mid not in answered and 0 <= now - ts < limit:
            continue
        if unknown_ats:
            who_tag = "、".join(str(x)[-4:] for x in unknown_ats[:2])
            why = (f"@了从没说过话的对象（{who_tag}），超过 {limit:g} 秒才回"
                   if mid in answered else f"@了从没说过话的对象（{who_tag}），没有送达")
        else:
            why = f"超过 {limit:g} 秒才回" if mid in answered else "没有送达"
        if mid in reasons:
            why += f"（决策：{reasons[mid]}）"
        out.append(_finding("unanswered", row.get("group"), ts, user=row.get("user"),
                            detail=why, message_id=mid,
                            turn_ids=[t["turn_id"] for t in turn_by_message.get(mid, ())],
                            context=_context(timeline, row.get("group"), ts, names), key=mid))
    return out


def _similar(a, b, threshold=REPEAT_SIMILARITY):
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def find_repeated(data, timeline, names, turn_by_message=None, policy=None):
    p = policy or DEFAULT_POLICY
    speakers = data.get("speakers") or {}
    by_user = defaultdict(list)
    for row in data["incoming"]:
        # 引用前缀（「[回复小王：「…」] 」）不算他说的话：两次引用同一张图提不同要求，前缀会把相似度撑高
        text = "".join(_WORD.findall(quotes.strip(row.get("text") or "")))
        if len(text) >= p["repeat_min_chars"] and not row.get("cmd"):
            by_user[(row.get("group"), row.get("user"))].append((row, text))
    out = []
    for (group, user), rows in by_user.items():
        used = set()
        for i, (row, text) in enumerate(rows):
            if i in used:
                continue
            cluster = [i]
            for j in range(i + 1, len(rows)):
                other, other_text = rows[j]
                if (other.get("ts") or 0) - (row.get("ts") or 0) > p["repeat_window_seconds"]:
                    break
                if j not in used and _similar(text, other_text, p["repeat_similarity"]):
                    cluster.append(j)
            called = any(
                rows[k][0].get("at") or _unknown_at_targets(rows[k][0], speakers)
                for k in cluster
            )
            if len(cluster) < 2 or not called:
                continue
            used.update(cluster)
            last = rows[cluster[-1]][0]
            turn_ids = [
                t["turn_id"]
                for k in cluster
                for t in (turn_by_message or {}).get(rows[k][0].get("message_id"), ())
                if t.get("turn_id")
            ]
            out.append(_finding("repeated", group, row.get("ts") or 0.0, user=user,
                                detail=f"{len(cluster)} 条：「{row.get('text', '')[:40]}」",
                                message_id=row.get("message_id"),
                                turn_ids=turn_ids,
                                context=_context(timeline, group, last.get("ts") or 0.0, names),
                                key=row.get("message_id")))
    return out


def find_corrected(data, timeline, names):
    by_outgoing = {(r.get("group"), r["outgoing_id"]): r
                   for r in [*data["delivery"], *(data.get("images") or ())]
                   if r.get("outgoing_id")}
    incoming = {(r.get("group"), r["message_id"]): r for r in data["incoming"]
                if r.get("message_id")}
    drawings = _drawings(data.get("turns") or ())
    out = []
    for row in data["feedback"]:
        if row.get("target") != "bot_response" or row.get("action") not in CORRECTION_ACTIONS:
            continue
        group = row.get("group")
        reply = by_outgoing.get((group, row.get("source_reply_id"))) or {}
        drawn = _drawn(drawings, reply) if reply.get("route") == "image" else None
        target = reply.get("text") or (
            f"[{'动图' if drawn[3] else '图'}：{drawn[2]}]" if drawn else "")
        turn_id = reply.get("turn_id") or (drawn[1] if drawn else None)
        said = incoming.get((group, row.get("source_message_id"))) or {}
        ts = said.get("ts") or row.get("ts") or 0.0
        out.append(_finding("corrected", row.get("group"), ts, user=row.get("user"),
                            detail=f"{row.get('action')}：对「{target[:40]}」",
                            message_id=row.get("source_message_id"),
                            turn_ids=[turn_id],
                            context=_context(timeline, row.get("group"), ts, names),
                            key=row.get("source_message_id") or row.get("event_id")))
    return out


def _confused(text, policy=None):
    p = policy or DEFAULT_POLICY
    t = (text or "").replace("?", "？").strip()
    if t.count("？") >= p["confused_min_qmarks"] and not _WORD.search(t):
        return True
    return any(m in t for m in p["confused_markers"])


def _confused_marker(text, policy):
    """它是因为哪个词被认成「没听懂」的（调参器拿来做逐词消融）；问号判出来的返回 None。"""
    t = (text or "").replace("?", "？").strip()
    return next((m for m in policy["confused_markers"] if m in t), None)


def find_bad_reaction(data, timeline, names, policy=None):
    p = policy or DEFAULT_POLICY
    out = []
    for group, items in timeline.items():
        last_bot = None
        for item in items:
            if item["bot"]:
                last_bot = item
                continue
            if (last_bot and item["ts"] - last_bot["ts"] <= p["reaction_seconds"]
                    and _confused(item["text"], p)):
                out.append(_finding("bad_reaction", group, item["ts"], user=item.get("user"),
                                    detail=f"「{item['text'][:30]}」← 它说「{last_bot['text'][:40]}」",
                                    message_id=item.get("message_id"),
                                    turn_ids=[last_bot.get("turn_id")],
                                    context=_context(timeline, group, item["ts"], names),
                                    key=item.get("message_id"),
                                    marker=_confused_marker(item["text"], p)))
                last_bot = None   # 同一句只报一次
    return out


def _remind_executed(row, turn):
    """核对提醒是不是真的记上了（不能只看模型写了〔提醒〕，可能被开关/插嘴挡住或时间被拒）。"""
    text = row.get("text") or ""
    if "（这个提醒没记上" in text or row.get("remind_applied") is False:
        return False
    if row.get("remind_applied") is True or "（记下了：" in text or "提醒取消了" in text:
        return True
    return False


def _lookup_executed(row, turn, data, window=300):
    """核对查询是不是真的执行了（不能只看分诊或门控写了 selected=True，查询可能还没跑完就失败）。"""
    if row.get("search_supplied"):
        return True
    kw = (turn or {}).get("kwargs") or {}
    if kw.get("search_note") or kw.get("lookup_note"):
        return True
    mid = row.get("source_message_id")
    user = row.get("user")
    group = row.get("group")
    if mid:
        for d in list(data.get("delivery") or ()) + list(data.get("next_delivery") or ()):
            if d.get("source_message_id") == mid and d.get("search_supplied"):
                return True
    ts = row.get("ts") or 0.0
    for t in data.get("turns") or ():
        if t.get("kind") != "reply" or t.get("group") != group:
            continue
        t_kw = t.get("kwargs") or {}
        if not (t.get("route") == "followup" or t_kw.get("lookup_note") or t_kw.get("search_note")):
            continue
        if not (0 <= (t.get("ts") or 0.0) - ts <= window):
            continue
        t_mid = t.get("message_id")
        t_user = t.get("user")
        t_sid = t.get("session_id")
        if mid and t_mid and t_mid != mid:
            continue
        if user and t_user and t_user != user:
            continue
        if user and group and t_sid and t_sid != f"{group}_{user}":
            continue
        if (mid and t_mid == mid) or (user and (t_user == user or t_sid == f"{group}_{user}")):
            return True
    return False


def _did_something(row, marker, turn, data, policy=None):
    """按承诺类型核对实际执行记录：提醒看 remind_applied/（记下了：…），查询看 search_supplied/decision/followup。"""
    p = policy or DEFAULT_POLICY
    window = p["lookup_window_seconds"]
    if marker in p["remind_promise_markers"]:
        return _remind_executed(row, turn)
    if marker in p["lookup_promise_markers"]:
        return _lookup_executed(row, turn, data, window)
    return _remind_executed(row, turn) or _lookup_executed(row, turn, data, window)


def find_promised(data, timeline, names, turn_by_id, triage_by_message=None, policy=None):
    p = policy or DEFAULT_POLICY
    markers = [*p["lookup_promise_markers"], *p["remind_promise_markers"]]
    out = []
    for row in data["delivery"]:
        if row.get("generation") not in (None, "model"):
            continue        # 本地话术「我去查查，等我一会儿」是真起了查询才说的
        text = row.get("text") or ""
        marker = next((m for m in markers if m in text), None)
        if not marker:
            continue
        turn = turn_by_id.get(row.get("turn_id"))
        if _did_something(row, marker, turn, data, p):
            continue
        ts = row.get("ts") or 0.0
        out.append(_finding("promised", row.get("group"), ts, user=row.get("user"),
                            detail=f"说了「{marker}」，但这一轮没有实际记上提醒或执行查询",
                            message_id=row.get("source_message_id"),
                            turn_ids=[row.get("turn_id")],
                            context=_context(timeline, row.get("group"), ts, names),
                            key=row.get("outgoing_id"), marker=marker))
    return out


def _grams(text):
    out = set()
    for run in _WORD.findall(text or ""):
        for n in CATCHPHRASE_SIZES:
            for i in range(len(run) - n + 1):
                gram = run[i:i + n]
                if not gram.isdigit() and not gram.isascii():
                    out.add(gram)
    return out


def _common_span(texts, gram):
    """这批句子共有的最长一段（得含着 gram）：同一句被切成几段时，报出整句。"""
    common = texts[0] if texts else gram
    for other in texts[1:]:
        m = difflib.SequenceMatcher(None, common, other, autojunk=False).find_longest_match(
            0, len(common), 0, len(other))
        common = common[m.a:m.a + m.size]
    common = common.strip("，。！？、 ")
    return common[:30] if gram in common else gram


def find_catchphrases(data, names, baseline, policy=None):
    p = policy or DEFAULT_POLICY
    hits = defaultdict(list)
    for row in data["delivery"]:
        # 只看它自己写的：本地固定话术（「这张被图片审核拦下来了」）和平台拒收后的兜底话不算口头禅
        if row.get("generation") != "model" or row.get("fallback"):
            continue
        for gram in _grams(row.get("text")):
            hits[gram].append(row)
    base_days = max(1, len(baseline))
    base = defaultdict(int)
    for texts in baseline.values():
        for text in texts:
            for gram in _grams(text):
                base[gram] += 1
    candidates = []
    for gram, rows in hits.items():
        count = len(rows)
        talks = {r.get("user") or r.get("source_message_id") for r in rows}
        avg = base[gram] / base_days
        if (count >= p["catchphrase_min"] and len(talks) >= p["catchphrase_min_talks"]
                and count >= p["catchphrase_ratio"] * avg + 1):
            candidates.append((gram, count, avg, rows))
    # 同一句话里的长片段吃掉它的短子串（「刚醒没多久」在，就不再单列「刚醒没多」）
    candidates.sort(key=lambda c: (-len(c[0]), -c[1]))
    kept = []
    for gram, count, avg, rows in candidates:
        if any(gram in k[0] and k[1] >= 0.8 * count for k in kept):
            continue
        kept.append((gram, count, avg, rows))
    # 同一句话被切成几段 6 字片段，出现在同一批句子里：只报一条，片段换成这批句子的公共部分
    merged, by_rows = [], {}
    for gram, count, avg, rows in kept:
        sig = frozenset(id(r) for r in rows)
        if sig in by_rows:
            continue
        by_rows[sig] = True
        merged.append((_common_span([r.get("text") or "" for r in rows], gram), count, avg, rows))
    kept = merged
    kept.sort(key=lambda c: -c[1])
    out = []
    day_prefix = data.get("day") or ""
    for gram, count, avg, rows in kept[:CATCHPHRASE_TOP]:
        group = rows[0].get("group")
        out.append(_finding(
            "catchphrase", group, rows[0].get("ts") or 0.0,
            detail=f"「{gram}」今天 {count} 条，前 {len(baseline)} 天日均 {avg:.1f}",
            turn_ids=[r.get("turn_id") for r in rows],
            context=[_line({"ts": r.get("ts") or 0.0, "bot": True, "text": r.get("text") or ""},
                           names) for r in rows[:6]],
            key=f"{day_prefix}|{gram}"))
    return out


def find_sensitive(data, timeline, names, turn_by_id):
    import wordfilter
    out = []
    seen = set()
    for row in data["delivery"]:
        turn = turn_by_id.get(row.get("turn_id")) or {}
        hit = wordfilter.has_hit(row.get("text")) or wordfilter.has_hit(turn.get("raw"))
        if not (row.get("fallback") or hit):
            continue
        seen.add(row.get("turn_id"))
        ts = row.get("ts") or 0.0
        out.append(_finding("sensitive", row.get("group"), ts, user=row.get("user"),
                            detail="被平台拒收，换成了兜底话" if row.get("fallback") else "输出碰了敏感词表",
                            message_id=row.get("source_message_id"),
                            turn_ids=[row.get("turn_id")],
                            context=_context(timeline, row.get("group"), ts, names),
                            key=row.get("outgoing_id")))
    # 没送达的那几轮：模型原文碰了词表（比如被本地拦了），在 delivery 里看不见
    for turn in data["turns"]:
        if (turn.get("kind") == "reply" and turn.get("turn_id") not in seen
                and wordfilter.has_hit(turn.get("raw"))):
            ts = turn.get("ts") or 0.0
            out.append(_finding("sensitive", turn.get("group"), ts, user=turn.get("user"),
                                detail="模型原文碰了敏感词表（这一轮没有送达记录）",
                                message_id=turn.get("message_id"), turn_ids=[turn["turn_id"]],
                                context=_context(timeline, turn.get("group"), ts, names),
                                key=turn["turn_id"]))
    return out


def find_blind(data, timeline, names, policy=None):
    p = policy or DEFAULT_POLICY
    out = []
    for row in data["delivery"]:
        text = row.get("text") or ""
        marker = next((m for m in p["blind_markers"] if m in text), None)
        if row.get("generation") != "model" or not marker:
            continue
        ts = row.get("ts") or 0.0
        out.append(_finding("blind", row.get("group"), ts, user=row.get("user"),
                            detail=f"它说「{text[:40]}」", message_id=row.get("source_message_id"),
                            turn_ids=[row.get("turn_id")],
                            context=_context(timeline, row.get("group"), ts, names),
                            key=row.get("outgoing_id"), marker=marker))
    return out


def find_recall_empty(data, timeline, names):
    """分诊判了要翻旧账（才会有 kind=recall 的记录），检索却一条没挑到。

    可能是归档里本来就没有，也可能是检索策略切词 / 打分漏了 —— 后者标 yes 以后
    用 `regular case add --turn <检索记录> --relevant <消息编号>` 存成检索用例，交给 `regular tune recall`。
    """
    out = []
    for turn in data.get("turns") or ():
        if turn.get("kind") != "recall" or turn.get("picked") or turn.get("promise"):
            continue
        ts = turn.get("now") or turn.get("ts") or 0.0
        why = turn.get("skipped") or "没有打分过线的旧消息"
        out.append(_finding("recall_empty", turn.get("group"), ts, user=turn.get("user"),
                            detail=f"「{str(turn.get('query') or '')[:40]}」一条都没翻到（{why}）",
                            message_id=turn.get("message_id"), turn_ids=[turn.get("turn_id")],
                            context=_context(timeline, turn.get("group"), ts, names),
                            key=turn.get("turn_id")))
    return out


# ── 汇总 ──

def labels_path(checkup_dir):
    return Path(checkup_dir) / "labels.jsonl"


def read_labels(checkup_dir):
    """外部评审标注：finding id → 最后一次 yes/no（可更新，以最新为准）。"""
    out = {}
    for row in _read_jsonl(labels_path(checkup_dir)):
        if row.get("id") and row.get("verdict") in ("yes", "no"):
            out[row["id"]] = row
    return out


def add_label(checkup_dir, finding_id, verdict, *, signal=None, note=""):
    if verdict not in ("yes", "no"):
        raise ValueError("verdict must be yes or no")
    path = labels_path(checkup_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"id": finding_id, "verdict": verdict, "signal": signal, "note": note,
           "ts": time.time()}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def precision(checkup_dir):
    """已标候选确认率：yes / 已标候选；不包含未检出样本，不能推出召回率。"""
    stats = defaultdict(lambda: {"yes": 0, "no": 0})
    for row in read_labels(checkup_dir).values():
        if row.get("signal"):
            stats[row["signal"]][row["verdict"]] += 1
    return {k: dict(v, precision=round(v["yes"] / (v["yes"] + v["no"]), 2))
            for k, v in stats.items() if v["yes"] + v["no"]}


def _names(state_file, groups, speakers=None):
    """群友的称呼（state.json 关系档案里认领的那个）；同时把关系档案里的老群友并入已知发言人。"""
    names = {}
    try:
        import naming
        names["bot"] = naming.bot_name()
    except Exception:
        names["bot"] = "bot"
    try:
        records = json.loads(Path(state_file).read_text(encoding="utf-8")).get("relations") or {}
    except (OSError, ValueError, AttributeError):
        return names
    for key, rec in records.items():
        group, _, user = str(key).partition("|")
        if group in groups and user:
            if speakers is not None:
                speakers[group].add(user)
            if isinstance(rec, dict) and rec.get("nick"):
                names.setdefault(user, rec["nick"])
    return names


def prepare(day, *, interaction_dir, turn_dir, archive_dir=None, state_file=None):
    """读一天的原料并排好索引（只读）。调参器对同一天反复换策略重跑，原料只读这一遍。"""
    data = load_day(day, interaction_dir=interaction_dir, turn_dir=turn_dir,
                    archive_dir=archive_dir)
    baseline = load_baseline(day, interaction_dir=interaction_dir)
    groups = {r.get("group") for r in data["incoming"] + data["delivery"]}
    names = _names(state_file, groups, speakers=data["speakers"]) if state_file else {"bot": "bot"}
    return prepare_data(data, baseline=baseline, names=names)


def prepare_data(data, *, baseline=None, names=None):
    """为已冻结的本地输入建立索引；供日体检和独立策略基准共用，不读文件或调用模型。"""
    names = names or {"bot": "bot"}
    timeline = _timeline(data)
    turn_by_id, turn_by_message, triage_by_message = {}, defaultdict(list), {}
    for t in data["turns"]:
        if t.get("kind") == "reply":
            turn_by_id[t.get("turn_id")] = t
            if t.get("message_id"):
                turn_by_message[t["message_id"]].append(t)
        elif t.get("kind") == "triage" and t.get("message_id"):
            # 同一条消息先有分诊、后可能有画图前那一问（draw_check）：留先到的分诊
            triage_by_message.setdefault(t["message_id"], t)
    # 只有分诊、没有生成回复的消息（如 unanswered / let_it_pass），也把分诊 turn_id 挂上供一键存例
    for mid, tr in triage_by_message.items():
        if not turn_by_message.get(mid) and tr.get("turn_id"):
            turn_by_message[mid].append(tr)
    return {"day": data["day"], "data": data, "baseline": baseline or {}, "names": names,
            "timeline": timeline, "turn_by_id": turn_by_id,
            "turn_by_message": turn_by_message, "triage_by_message": triage_by_message}


_DETECTORS = {
    "unanswered": lambda c, p, now: find_unanswered(
        c["data"], c["timeline"], c["names"], c["turn_by_message"], now=now, policy=p),
    "repeated": lambda c, p, now: find_repeated(
        c["data"], c["timeline"], c["names"], c["turn_by_message"], policy=p),
    "corrected": lambda c, p, now: find_corrected(c["data"], c["timeline"], c["names"]),
    "bad_reaction": lambda c, p, now: find_bad_reaction(
        c["data"], c["timeline"], c["names"], policy=p),
    "promised": lambda c, p, now: find_promised(
        c["data"], c["timeline"], c["names"], c["turn_by_id"], c["triage_by_message"], policy=p),
    "catchphrase": lambda c, p, now: find_catchphrases(
        c["data"], c["names"], c["baseline"], policy=p),
    "sensitive": lambda c, p, now: find_sensitive(
        c["data"], c["timeline"], c["names"], c["turn_by_id"]),
    "recall_empty": lambda c, p, now: find_recall_empty(c["data"], c["timeline"], c["names"]),
    "blind": lambda c, p, now: find_blind(c["data"], c["timeline"], c["names"], policy=p),
}


def detect(ctx, policy=None, *, now=None, signals=None):
    """在 prepare 好的原料上按给定策略跑信号；signals 给了就只跑这几种（调参时按信号分开跑）。"""
    p = validate_policy(policy or {})
    findings = []
    for signal in (signals or LABELS):
        findings += _DETECTORS[signal](ctx, p, now)
    order = list(LABELS)
    findings.sort(key=lambda f: (order.index(f["signal"]), f["ts"]))
    return findings


def report(day, *, interaction_dir, turn_dir, archive_dir=None, state_file=None,
           checkup_dir=None, now=None, policy=None):
    ctx = prepare(day, interaction_dir=interaction_dir, turn_dir=turn_dir,
                  archive_dir=archive_dir, state_file=state_file)
    data, baseline = ctx["data"], ctx["baseline"]
    policy = validate_policy(policy or {})
    findings = detect(ctx, policy, now=now)
    labelled = read_labels(checkup_dir) if checkup_dir else {}
    for f in findings:
        if f["id"] in labelled:
            f["verdict"] = labelled[f["id"]]["verdict"]
    counts = {s: sum(1 for f in findings if f["signal"] == s) for s in LABELS}
    return {"schema": SCHEMA_OUT, "day": day,
            "inputs": {"incoming": len(data["incoming"]), "delivered": len(data["delivery"]),
                       "feedback": len(data["feedback"]),
                       "turns": sum(1 for t in data["turns"] if t.get("kind") == "reply"),
                       "triage": sum(1 for t in data["turns"] if t.get("kind") == "triage"),
                       "baseline_days": len(baseline)},
            "counts": counts, "findings": findings,
            "policy": policy_id(policy),
            "precision": precision(checkup_dir) if checkup_dir else {}}


def case_add_commands(day, finding):
    """生成一条或多条不撞名的 `regular case add` 命令（供 Markdown 报告与 Web 面板共用）。

    有 turn_id 时按 `--turn` 存成生成层/分诊层用例；无生成、无分诊（仅 message_id）时按 `--message`
    从原始归档或交互日志存成消息层用例。
    """
    base_id = f"{day}-{finding['signal']}-{finding['id'][:4]}"
    tids = list(dict.fromkeys(t for t in (finding.get("turn_ids") or ()) if t))[:3]
    if not tids:
        mids = list(dict.fromkeys(
            m for m in (finding.get("message_ids") or [finding.get("message_id")]) if m
        ))[:3]
        if not mids:
            return []
        if len(mids) == 1:
            return [f"regular case add --message {mids[0]} --id {base_id}"]
        short_heads = [str(m)[:6] for m in mids]
        distinct_heads = len(set(short_heads)) == len(mids)
        return [
            f"regular case add --message {m} --id {base_id}"
            f"{f'-{str(m)[:6]}' if distinct_heads else f'-{idx}-{str(m)[:6]}'}"
            for idx, m in enumerate(mids, 1)
        ]
    if len(tids) == 1:
        return [f"regular case add --turn {tids[0]} --id {base_id}"]
    short_heads = [str(t)[:6] for t in tids]
    distinct_heads = len(set(short_heads)) == len(tids)
    cmds = []
    for idx, t in enumerate(tids, 1):
        suffix = f"-{str(t)[:6]}" if distinct_heads else f"-{idx}-{str(t)[:6]}"
        cmds.append(f"regular case add --turn {t} --id {base_id}{suffix}")
    return cmds


def _case_add_commands(day, finding):
    return [f"`{cmd}`" for cmd in case_add_commands(day, finding)]


def render(out):
    lines = [f"# 体检 {out['day']}", ""]
    i = out["inputs"]
    lines.append(f"原料：群友消息 {i['incoming']} 条，送达 {i['delivered']} 条，分诊反馈 {i['feedback']} 条，"
                 f"每轮输入 {i['turns']} 轮 / 分诊 {i['triage']} 次；口头禅基线 {i['baseline_days']} 天。")
    lines.append("")
    lines.append("这些是候选，不是结论。标注：`regular checkup --label <id> yes|no`。")
    if out.get("policy") and out["policy"] != "default":
        lines.append(f"体检口径：`{out['policy']}`（data/policies/checkup.json，`regular policy show checkup` 查看）。")
    lines.append("")
    lines.append("| 信号 | 条数 | 已标候选确认率 |")
    lines.append("| --- | --- | --- |")
    for signal, label in LABELS.items():
        p = out["precision"].get(signal)
        rate = f"{p['precision']:.0%}（{p['yes']}/{p['yes'] + p['no']}）" if p else "—"
        lines.append(f"| {label} | {out['counts'][signal]} | {rate} |")
    for f in out["findings"]:
        clock = time.strftime("%H:%M:%S", time.localtime(f["ts"]))
        verdict = f" · 已标 {f['verdict']}" if f.get("verdict") else ""
        lines += ["", f"## [{f['id']}] {f['label']} · {clock}{verdict}", "", f["detail"], ""]
        if f["context"]:
            lines.append("```")
            lines += f["context"]
            lines.append("```")
        cmds = _case_add_commands(out["day"], f)
        if cmds:
            lines.append("")
            lines.append("存成用例：" + "；".join(cmds))
    if not out["findings"]:
        lines += ["", "今天没有可疑的地方（或者原料是空的）。"]
    return "\n".join(lines) + "\n"


def write(out, checkup_dir):
    """报告落 data/checkups/日期.md + 日期.json（面板和代理读 json）。"""
    base = Path(checkup_dir)
    base.mkdir(parents=True, exist_ok=True)
    md = base / f"{out['day']}.md"
    md.write_text(render(out), encoding="utf-8")
    (base / f"{out['day']}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return md


def find_finding(checkup_dir, finding_id):
    """在已写过的体检 json 里找一条（标注时带上它的信号名，算准确率要用）。"""
    try:
        files = sorted(Path(checkup_dir).glob("*.json"), reverse=True)
    except OSError:
        return None
    for path in files:
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for f in out.get("findings") or ():
            if f.get("id") == finding_id:
                return f
    return None


def yesterday():
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def resolve_day(value):
    """--day 认 2026-11-03 / 11-03 / today / yesterday；默认今天。"""
    if not value or value == "today":
        return dt.date.today().isoformat()
    if value == "yesterday":
        return yesterday()
    if re.fullmatch(r"\d{2}-\d{2}", value):
        return f"{dt.date.today().year}-{value}"
    dt.date.fromisoformat(value)
    return value
