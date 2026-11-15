# -*- coding: utf-8 -*-
"""反应账本：每条送达之后群友实际怎么反应，按版本 / 模型 / 路径 / 日期汇总。只读。

### 这是为了回答哪个问题

「这个版本上线以后，群里对它说的话反应变了没有」—— 评测报告比的是裁判更喜欢哪一条，
这里记的是真实群友随后做了什么。两样都要有，才知道裁判的偏好对不对得上现实。

拼法全部靠显式标识，不靠时间凑，也不另写词表：

    delivery(status=delivered)                           一条送达：回复、插嘴、图片、主动发言
      → outgoing_id
      → natural_feedback(source_reply_id=outgoing_id)    之后每句群友发言的分诊结论
      → target / action
    delivery.turn_id → turns(kind=reply).release          那一轮是哪个版本生成的

一条送达可以同时有几种反应（一个人纠正、另一个人接着玩），所以各项**分别计数**，
不强行归成一类：

| 项       | 怎么认                                                    |
| -------- | --------------------------------------------------------- |
| 有人回应 | 之后有发言被判成在回应这条（target=bot_response）          |
| 被纠正   | 其中有 `repair_briefly`                                   |
| 被叫停   | 其中有 `back_off`                                         |
| 被接着玩 | 其中有 `keep_playful`                                     |
| 话题走了 | 之后有人说话，但没有一句是冲着这条来的                    |
| 没有下文 | 这条之后没有任何发言进入判断（没人说话，或说的是指令）     |

### 必须先说清的边界

1. **标签是分诊模型判的，不是人标的。** 「被纠正」里混着正常的补充和追问，
   它的确认比例要看体检标注（`regular checkup` 的 corrected 信号）。
2. **这是观察记录，不是实验。** 不同版本对着不同的日子、话题和人；
   两个版本的比例不一样，不能直接读成「新版更好」。区间不重叠只说明值得去看现场。
3. **没有下文 ≠ 说得不好。** 群里大多数接话本来就不会引出反馈。
4. **反馈只挂在窗口内最近的一条回复上。** 很快被下一条盖过去的回复，被回应的机会本来就少。
5. **先按路径看，再比版本。** 图片被纠正的比例远高于文字（「再画一张」也算在里面），
   画图多的版本整体比例自然高；所以报告总是先列各路径，`--route` 可以只看一条路径。
6. 本地指令的固定输出（route=local）不是它「说」的话，也从不被反馈判断绑定，
   单独计数，不进分母。日志里的 `feedback_eligible` 不用来筛：实测图片和主动发言
   没带这个标记，照样收到了反馈。

样本不足 `MIN_FOR_RATE` 条时只报条数，不报比例。
"""
from __future__ import annotations

import math
from collections import defaultdict

import turns as turns_mod
from archive import day_key
from regular.calibration import UNWELCOME, WELCOME, read_rows

SCHEMA_OUT = "regular.outcomes.v1"

REPAIR = "repair_briefly"
GROUPINGS = ("release", "model", "route", "day")
# 各项反应：账本行里的字段名 → 给人读的名字。
REACTIONS = (("reacted", "有人回应"), ("corrected", "被纠正"), ("told_off", "被叫停"),
             ("welcomed", "被接着玩"), ("moved_on", "话题走了"), ("silent", "没有下文"))
# 报比例的两项：一项坏消息，一项好消息。其余只给条数。
RATED = ("corrected", "welcomed")
# 本地指令的固定输出：不是模型说的话，反馈判断也不绑定它。
FIXED_ROUTE = "local"
MIN_FOR_RATE = 20
UNKNOWN = "（无记录）"


def _text(row, key):
    value = row.get(key)
    return value if isinstance(value, str) and value else None


def _releases(turn_dir):
    """turn_id → 生成那一轮的版本。没有每轮记录的部署返回空表。"""
    out = {}
    if not turn_dir:
        return out
    for day in turns_mod.days(turn_dir):
        for row in turns_mod.read_day(turn_dir, day):
            if row.get("kind") == "reply" and _text(row, "release"):
                out[row["turn_id"]] = row["release"]
    return out


def ledger(log_dir, turn_dir=None, *, since=None, route=None):
    """一条送达一行。只读；行里只有本地事件编号，没有正文、群号和发言人。"""
    rows, diagnostics = read_rows(log_dir)
    releases = _releases(turn_dir)
    counts = {"delivered": 0, "fixed_lines": 0, "other_routes": 0, "in_ledger": 0}

    heard = defaultdict(list)       # outgoing_id → 之后进入判断的每句发言
    for row in rows:
        if row.get("kind") != "natural_feedback":
            continue
        reply = _text(row, "source_reply_id")
        if reply:
            heard[reply].append(row)

    out = []
    for row in rows:
        if (row.get("kind") != "delivery" or row.get("status") != "delivered"
                or row.get("actor") != "assistant"):
            continue
        day = day_key(row.get("ts"))
        if since and day < since:
            continue
        counts["delivered"] += 1
        outgoing = _text(row, "outgoing_id")
        if row.get("route") == FIXED_ROUTE or not outgoing:
            counts["fixed_lines"] += 1
            continue
        if route and row.get("route") != route:
            counts["other_routes"] += 1
            continue
        counts["in_ledger"] += 1
        after = heard.get(outgoing, ())
        about = {r.get("action") for r in after if r.get("target") == "bot_response"}
        reacted = any(r.get("target") == "bot_response" for r in after)
        turn_id = _text(row, "turn_id")
        out.append({
            "outgoing_id": outgoing, "turn_id": turn_id, "ts": row.get("ts"), "day": day,
            "route": _text(row, "route"), "model": _text(row, "model"),
            "release": releases.get(turn_id),
            "heard": len(after), "reacted": reacted,
            "corrected": REPAIR in about, "told_off": UNWELCOME in about,
            "welcomed": WELCOME in about,
            "moved_on": bool(after) and not reacted, "silent": not after,
        })
    return out, counts, diagnostics


def _wilson(hits, total, z=1.96):
    """比例的 95% Wilson 区间：几十条样本上，裸比例会把噪声读成结论。"""
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def _summary(entries):
    total = len(entries)
    out = {"n": total, "first_day": entries[0]["day"], "last_day": entries[-1]["day"]}
    for field, _ in REACTIONS:
        out[field] = sum(1 for e in entries if e[field])
    out["enough_for_rate"] = total >= MIN_FOR_RATE
    for field in RATED:
        out[f"{field}_rate"] = out[field] / total if out["enough_for_rate"] else None
        out[f"{field}_ci"] = _wilson(out[field], total) if out["enough_for_rate"] else None
    return out


def day_summary(log_dir, turn_dir, day):
    """只看某一天的送达：整体一行加各路径。那天没有送达返回 None。"""
    entries, _, _ = ledger(log_dir, turn_dir, since=day)
    entries = [entry for entry in entries if entry["day"] == day]
    if not entries:
        return None
    return {"overall": _summary(entries), "routes": _grouped(entries, "route")}


def _grouped(entries, by):
    groups = defaultdict(list)
    for entry in entries:
        groups[entry[by]].append(entry)
    return [{"key": key, **_summary(members)} for key, members in groups.items()]


def report(log_dir, turn_dir=None, *, by="release", since=None, route=None):
    """只读报告：整体一行、各路径各一行，再按 `by` 分组；组按首次出现的时间排。"""
    if by not in GROUPINGS:
        raise ValueError("unknown grouping")
    entries, counts, diagnostics = ledger(log_dir, turn_dir, since=since, route=route)
    return {
        "schema": SCHEMA_OUT, "by": by, "since": since, "route": route,
        **diagnostics, "counts": counts, "min_for_rate": MIN_FOR_RATE,
        "overall": _summary(entries) if entries else None,
        "routes": _grouped(entries, "route"),
        "groups": _grouped(entries, by),
        "rows": entries,
    }


def _rate(summary, field):
    if summary[f"{field}_rate"] is None:
        return f"{summary[field]} 条"
    ci = summary[f"{field}_ci"]
    return (f"{summary[field]} 条 {summary[f'{field}_rate']:.0%}"
            f" [{ci['low']:.0%}–{ci['high']:.0%}]")


def _line(label, summary):
    span = (summary["first_day"][5:] if summary["first_day"] == summary["last_day"]
            else f"{summary['first_day'][5:]}–{summary['last_day'][5:]}")
    rest = "｜".join(f"{name} {summary[field]}" for field, name in REACTIONS
                     if field not in RATED)
    return (f"  {label}  （{span}，{summary['n']} 条）\n"
            f"      被纠正 {_rate(summary, 'corrected')}｜被接着玩 {_rate(summary, 'welcomed')}\n"
            f"      {rest}")


def render(out):
    """给人读的一页。每个数字后面都带上它**不**说明什么。"""
    counts = out["counts"]
    scope = f"，只看 {out['route']}" if out["route"] else ""
    lines = [f"反应账本（按 {out['by']} 分组{scope}）—— 只读本地互动日志",
             f"  确认送达 {counts['delivered']} 条｜进账本 {counts['in_ledger']}｜"
             f"本地指令的固定输出 {counts['fixed_lines']}（不进分母）"
             + (f"｜其他路径 {counts['other_routes']}" if out["route"] else "")]
    if not out["overall"]:
        lines.append("  还没有可对照的送达：比例不可得，不是 0%。")
        return "\n".join(lines)
    lines += ["", _line("全部", out["overall"])]
    if out["by"] != "route" and len(out["routes"]) > 1:
        lines += ["", "  各路径（先看这里：路径之间差得比版本之间大）"]
        lines += [_line(group["key"] or UNKNOWN, group) for group in out["routes"]]
    lines += ["", f"  按 {out['by']}"]
    lines += [_line(group["key"] or UNKNOWN, group) for group in out["groups"]]
    lines += [
        "",
        f"  方括号是 95% 区间；不足 {out['min_for_rate']} 条的组只报条数。",
        "  边界：标签是分诊模型判的，不是人标的；「被纠正」里混着正常的补充和追问。",
        "  这是观察记录，不是实验：各组对着不同的日子、话题和人，比例不同不等于谁更好。",
        "  没有下文是缺失，不是差评。",
    ]
    return "\n".join(lines)
