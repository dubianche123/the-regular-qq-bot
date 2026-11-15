# -*- coding: utf-8 -*-
"""影子回放：候选版本不上线，每晚拿真实轮次和基线并排重放，盲评跨天累计，序贯检验下结论。

### 为什么要有它

一次 `regular eval` 只有几条用例、每条抽 4 次，盲评十几到三十几对；群里一天又只有
二三十轮真实对话。2026-11 的 38 份报告里 24 份是「看不出新版更好」—— 不是候选都不行，
是样本量撑不起结论。影子回放拿时间换样本：候选登记一次，之后每个过完的日子都把当天
记下的每一轮（`data/turns`）交给新旧两版各生成一次、盲评一对，结果累计进同一本账。

### 怎么下结论

每天都会看一眼累计结果，所以不能用固定样本量的检验（天天看、看到显著就停，会把噪声
看成结论）。用 Wald 序贯概率比检验，本来就是为「边攒边看」设计的：

    分出胜负的对子里，新版赢的比例 p
    H0：p = 0.5（没差别）        H1：p = MIN_EFFECT（值得上线的最小差别）

对「新版更好」「新版更差」各做一次，错误率都取 `eval.REGRESSION_P`。四种结论：

    better      新版更好的证据够了
    worse       新版更差的证据够了，或有一次越线（越线不看次数）
    same        两个方向都排除了 MIN_EFFECT 那么大的差别 —— 可以停，但不是「一样好」的证明
    collecting  还不够，接着攒

### 必须先说清的边界

1. **序贯检验假设每一对互相独立。** 同一天的轮次共享话题和上下文，实际并不独立，
   所以结论偏乐观；它管得住「天天偷看」，管不住「同一个梗被数了五次」。
2. **裁判说更好 ≠ 群友觉得更好。** 所以另有 `judge_check`：拿真实被纠正 / 被接着玩的
   回复去问裁判，看它的偏好对不对得上群里的真实反应（见下）。
3. **只回放生成层。** 候选改的是输入怎么拼、消息怎么路由时，这里测不到，要用消息层用例。
4. **基线是登记时写死的版本。** 中途上线了别的版本，这本账比的就不再是「候选对线上」。
5. **不自动上线。** 结论只写进 `data/shadow/`，上不上线是人的一条命令。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path

import storage
import turns as turns_mod
from archive import day_key
from regular import eval as eval_mod
from regular import outcomes as outcomes_mod

SCHEMA = "regular.shadow.v1"
CHECK_SCHEMA = "regular.judge-check.v1"

# 值得上线的最小差别：分出胜负的对子里新版至少赢这么多。比它小的改动，这套裁判本来也量不准。
MIN_EFFECT = 0.65
ERROR_RATE = eval_mod.REGRESSION_P
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

CALLS = {
    "better": "新版更好，证据够了",
    "worse": "新版更差，证据够了（或有越线）",
    "same": f"两个方向都排除了 {MIN_EFFECT:.0%} 那么大的差别；可以停，但这不是「一样好」的证明",
    "collecting": "还不够，接着攒",
}


# ── 序贯检验 ──

def _llr(wins, losses):
    """对数似然比：这些胜负在 p=MIN_EFFECT 下比在 p=0.5 下可能多少倍。"""
    return wins * math.log(MIN_EFFECT / 0.5) + losses * math.log((1 - MIN_EFFECT) / 0.5)


def verdict(cand, base, crossed=0):
    """累计胜负 → 结论。平局不计；越线不看次数，一次就算更差。"""
    bound = math.log((1 - ERROR_RATE) / ERROR_RATE)
    up, down = _llr(cand, base), _llr(base, cand)
    if crossed or down >= bound:
        call = "worse"
    elif up >= bound:
        call = "better"
    elif up <= -bound and down <= -bound:
        call = "same"
    else:
        call = "collecting"
    return {"call": call, "llr_better": round(up, 3), "llr_worse": round(down, 3),
            "bound": round(bound, 3)}


def totals(state):
    out = {"days": 0, "turns": 0, "cand": 0, "base": 0, "tie": 0, "dropped": 0,
           "cand_crossed": 0, "base_crossed": 0}
    for entry in state["days"].values():
        out["days"] += 1
        for key in out:
            if key != "days":
                out[key] += entry.get(key, 0)
    return out


# ── 登记与读取 ──

def _dir(config):
    return Path(config.SHADOW_DIR)


def _path(config, name):
    if not _NAME.fullmatch(str(name)):
        raise ValueError("影子回放的名字只用小写字母、数字和连字符，40 字以内")
    return _dir(config) / f"{name}.json"


def load(config, name):
    path = _path(config, name)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError(f"没有登记过影子回放 {name!r}") from None
    if state.get("schema") != SCHEMA:
        raise ValueError(f"{path} 不是影子回放的记录")
    return state


def names(config):
    try:
        found = sorted(p.stem for p in _dir(config).glob("*.json"))
    except OSError:
        return []
    return [name for name in found if _NAME.fullmatch(name)]


def _save(config, state):
    path = _path(config, state["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_json(str(path), state)
    return path


def start(config, name, *, cand, base="HEAD", since=None, base_env=None, cand_env=None):
    """登记一个候选。since 给了就从那天起回放（补跑已经过去的日子），否则从今天起攒。"""
    if _path(config, name).exists():
        raise ValueError(f"影子回放 {name!r} 已经登记过；换个名字，或先 stop")
    state = {"schema": SCHEMA, "name": name, "base": base, "cand": cand,
             "base_env": dict(base_env or {}), "cand_env": dict(cand_env or {}),
             "since": since or day_key(), "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "status": "active", "days": {}}
    _save(config, state)
    return state


def stop(config, name):
    state = load(config, name)
    state["status"] = "stopped"
    _save(config, state)
    return state


# ── 每晚那一步 ──

def pending_days(config, state, *, today=None):
    """还没回放的、已经过完的日子：当天的记录还在追加，回放了就把半天冻进账里。"""
    today = today or day_key()
    return [day for day in turns_mod.days(config.TURN_LOG_DIR)
            if state["since"] <= day < today and day not in state["days"]]


def describe(config, state, days):
    """这一步会把多少条真实轮次发给谁；不调模型。"""
    lines = [f"影子回放 {state['name']}：{state['base']} → {state['cand']}，"
             f"待回放 {len(days)} 天（{'、'.join(days) or '无'}）。"]
    for day in days:
        cases = eval_mod.day_cases(config.TURN_LOG_DIR, day, judged=True)
        lines.append(f"── {day}：{len(cases)} 轮真实对话，新旧两版各生成 1 次、盲评 1 对")
        if cases:
            lines += ["   " + line for line in eval_mod.describe_traffic(
                config, cases, n=1, env={"base": state["base_env"], "cand": state["cand_env"]})]
    return lines


def run(config, name, *, days=None, judge_fn=None, worker_fn=None, allow_paid=False, today=None):
    """把还没回放的日子逐天跑完并记账。同一天不会记两次。"""
    state = load(config, name)
    if state["status"] != "active":
        raise ValueError(f"影子回放 {name!r} 已经停了")
    todo = pending_days(config, state, today=today)
    if days is not None:
        todo = [day for day in todo if day in days]
    for day in todo:
        cases = eval_mod.day_cases(config.TURN_LOG_DIR, day, judged=True)
        entry = {"turns": len(cases)}
        if cases:
            report = eval_mod.run_eval(
                config, base=state["base"], cand=state["cand"], cases=cases, n=1,
                judge_fn=judge_fn, worker_fn=worker_fn, allow_paid=allow_paid,
                base_env=state["base_env"], cand_env=state["cand_env"])
            t = report["totals"]
            entry.update(cand=t["judge_cand_wins"], base=t["judge_base_wins"], tie=t["judge_ties"],
                         dropped=t["dropped_pairs"], cand_crossed=t["cand_crossed"],
                         base_crossed=t["base_crossed"], report=report.get("stamp"))
        state["days"][day] = entry
        _save(config, state)       # 一天一存：后面的日子跑挂了，前面的不用重跑、不用重付
    return state, todo


def summary(state):
    t = totals(state)
    return {"name": state["name"], "base": state["base"], "cand": state["cand"],
            "status": state["status"], "since": state["since"], **t,
            "verdict": verdict(t["cand"], t["base"], t["cand_crossed"])}


def render(summaries):
    if not summaries:
        return "没有登记过影子回放。用 regular shadow start NAME --cand <版本> 登记一个候选。"
    lines = ["影子回放 —— 候选不上线，每晚用真实轮次和基线并排重放"]
    for s in summaries:
        v = s["verdict"]
        lines += [
            "",
            f"  {s['name']}  {s['base']} → {s['cand']}"
            + ("（已停）" if s["status"] != "active" else ""),
            f"      {s['since']} 起 {s['days']} 天 {s['turns']} 轮｜"
            f"盲评 新 {s['cand']} / 旧 {s['base']} / 平 {s['tie']}｜"
            f"没评成 {s['dropped']}｜越线 新 {s['cand_crossed']} / 旧 {s['base_crossed']}",
            f"      结论：{CALLS[v['call']]}"
            f"（更好 {v['llr_better']:+.2f}、更差 {v['llr_worse']:+.2f}，到 {v['bound']:.2f} 才算数）",
        ]
    lines += [
        "",
        "  边界：同一天的轮次共享话题，并不独立，结论偏乐观。裁判说更好不等于群友觉得更好",
        "  （regular shadow judge 核对裁判）。只回放生成层；不自动上线。",
    ]
    return "\n".join(lines)


# ── 裁判对不对得上群里的真实反应 ──
#
# 做法是「已知组」检验：反应账本里有一批真实回复，群友当场纠正 / 叫停了（bad），
# 另一批被接着玩了（good）。同一轮输入用当前代码再生成一条，把「当时真说出口的那条」和
# 「重新生成的这条」交给裁判盲评。裁判要是跟得上现实，bad 那批输给重新生成的比例
# 应该明显高于 good 那批。裁判只看得到当轮之前的现场，看不到群友后来怎么说。

def _check_path(config):
    return _dir(config) / "judge-check.json"


def load_check(config):
    try:
        data = json.loads(_check_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": CHECK_SCHEMA, "items": {}}
    if data.get("schema") != CHECK_SCHEMA:
        raise ValueError(f"{_check_path(config)} 不是裁判核对的记录")
    return data


def check_candidates(config, *, since=None, done=()):
    """有明确真实反应、又有每轮记录可以重放的回复。又被纠正又被接着玩的不算，图片不算。"""
    rows, _, _ = outcomes_mod.ledger(config.INTERACTION_LOG_DIR, config.TURN_LOG_DIR, since=since)
    out = []
    for row in rows:
        bad, good = row["corrected"] or row["told_off"], row["welcomed"]
        if (bad == good or not row["turn_id"] or row["outgoing_id"] in done
                or row["route"] == "image"):
            continue      # 画出来的图没法和一条重新生成的文字比
        turn = turns_mod.find(config.TURN_LOG_DIR, row["turn_id"], day=row["day"])
        if not turn or turn.get("kind") != "reply" or not turn.get("raw"):
            continue      # 每轮记录已过保留期，或那一轮没有模型原文（兜底话）
        out.append({"outgoing_id": row["outgoing_id"], "day": row["day"],
                    "outcome": "bad" if bad else "good", "turn": turn})
    return out


def describe_check(config, *, since=None, base="."):
    found = check_candidates(config, since=since, done=load_check(config)["items"])
    cases = [_check_case(item) for item in found]
    lines = [f"裁判核对：{len(found)} 条有明确真实反应的回复还没核对过"
             f"（被纠正 / 叫停 {sum(1 for i in found if i['outcome'] == 'bad')}，"
             f"被接着玩 {sum(1 for i in found if i['outcome'] == 'good')}）。",
             f"每条用 {base} 重新生成 1 次，再和当时真说出口的那条盲评 1 对。"]
    if cases:
        lines.append("（下面沿用评测预览的写法；其中「新旧两版」在这里只有重新生成的那一版。）")
        lines += eval_mod.describe_traffic(config, cases, n=1)
    return lines


def _check_case(item):
    return {"id": item["outgoing_id"], "depth": "generate", "turn": item["turn"],
            "checks": [{"judge": eval_mod.DEFAULT_FOCUS}]}


def judge_check(config, *, since=None, base=".", judge_fn=None, worker_fn=None, allow_paid=False):
    """核对新出现的那几条并记账。记过的不重跑，所以每晚跑一次就是在攒样本。"""
    data = load_check(config)
    found = check_candidates(config, since=since, done=data["items"])
    if found:
        if not allow_paid:
            raise eval_mod.PaidEvaluationRequired(
                "shadow judge regenerates replies and calls the judge; it requires --allow-paid")
        if judge_fn is None:
            eval_mod.preflight_judge(config)
        cases = [_check_case(item) for item in found]
        worker = worker_fn or eval_mod.run_worker_subprocess
        with eval_mod.checkout_version(base) as code_dir:
            marked = eval_mod.mark_frozen_prompts(cases, (code_dir,), turn_dir=config.TURN_LOG_DIR)
            regenerated = worker(code_dir, marked, n=1,
                                 concurrency=getattr(config, "EVAL_CONCURRENCY", 6))
        actual = [{"id": item["outgoing_id"], "samples": [item["turn"]["raw"]]} for item in found]
        rows, _ = asyncio.run(eval_mod.evaluate_cases(
            cases, actual, regenerated["results"], judge_fn=judge_fn,
            concurrency=getattr(config, "EVAL_CONCURRENCY", 6)))
        judged = {row["id"]: row["judge"] for row in rows}
        for item in found:
            tally = judged.get(item["outgoing_id"]) or {}
            # 「旧版」这一侧放的是当时真说出口的那条：它赢 = 真实回复赢了重新生成的
            result = ("won" if tally.get("base") else "lost" if tally.get("cand")
                      else "tie" if tally.get("tie") else "dropped")
            data["items"][item["outgoing_id"]] = {
                "day": item["day"], "outcome": item["outcome"], "result": result}
        path = _check_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        storage.atomic_write_json(str(path), data)
    return check_summary(data), len(found)


MIN_FOR_CHECK = 10      # 每组分出胜负的不到这么多条，不给结论，只报条数


def check_summary(data):
    groups = {name: {"won": 0, "lost": 0, "tie": 0, "dropped": 0} for name in ("bad", "good")}
    for item in data["items"].values():
        groups[item["outcome"]][item["result"]] += 1
    decisive = {name: g["won"] + g["lost"] for name, g in groups.items()}
    enough = all(count >= MIN_FOR_CHECK for count in decisive.values())
    out = {"schema": CHECK_SCHEMA, "groups": groups, "enough": enough, "min_per_group": MIN_FOR_CHECK,
           "p": None, "call": "collecting"}
    if enough:
        # 被纠正的那批赢得这么少，碰巧出现的概率（good 当基线、bad 当候选的单侧 Fisher 检验）
        out["p"] = round(eval_mod.drop_p_value(
            groups["good"]["won"], decisive["good"], groups["bad"]["won"], decisive["bad"]), 3)
        out["call"] = "tracks" if out["p"] <= ERROR_RATE else "no_evidence"
    return out


def render_check(out):
    g = out["groups"]

    def line(label, group):
        decisive = group["won"] + group["lost"]
        rate = f"，输掉 {group['lost'] / decisive:.0%}" if decisive >= out["min_per_group"] else ""
        return (f"  {label}：真实回复 赢 {group['won']} / 输 {group['lost']} / 平 {group['tie']}"
                f" / 没评成 {group['dropped']}{rate}")

    lines = ["裁判核对 —— 当时真说出口的那条 对 同一轮重新生成的一条",
             line("群友纠正 / 叫停过的", g["bad"]), line("群友接着玩的      ", g["good"]), ""]
    if not out["enough"]:
        lines.append(f"  每组分出胜负的还不到 {out['min_per_group']} 条：现在下不了结论，接着攒。")
    elif out["call"] == "tracks":
        lines.append(f"  被纠正的那批输得明显更多（p={out['p']}）：裁判的偏好和群里的真实反应方向一致。")
    else:
        lines.append(f"  两批输赢差不多（p={out['p']}）：没有证据说明裁判跟得上群里的真实反应，"
                     "拿它的胜率做上线依据要打折扣。")
    lines += ["",
              "  边界：「被纠正」是分诊模型判的，里面混着正常的补充和追问；",
              "  重新生成用的是现在的代码，不是当时那一版；方向一致也不等于裁判的每一票都对。"]
    return "\n".join(lines)

