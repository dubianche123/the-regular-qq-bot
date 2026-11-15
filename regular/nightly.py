# -*- coding: utf-8 -*-
"""每晚汇总：把昨天的体检、群友反应、影子回放拼成一页，早上让桌宠说一句。

这些命令原本都要人想起来才跑（2026-11-11 之后体检三天没人跑）。这里只做编排，
不新增判断：体检还是 `regular checkup`，反应还是 `regular outcomes`，影子回放还是
`regular shadow` —— 各自的口径和边界不变，这一页只是把它们放在一起。

默认只读、不联网、不调模型。给了 allow_paid 才会顺带跑影子回放和裁判核对
（真实群聊会发给回复模型和裁判，授权由生成定时任务的那个人显式给）。
不改提示词，不上线，不重启机器人。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import storage
from regular import checkup as checkup_mod
from regular import outcomes as outcomes_mod
from regular import shadow as shadow_mod

LINE_LIMIT = 120        # 桌宠气泡一句话的上限（desktop.cue）


def _paid_step(label, fn, notes):
    """付费那几步各自兜底：评审网关挂了，不该连体检和反应账本那一页也写不出来。"""
    try:
        return fn()
    except Exception as error:      # noqa: BLE001 —— 原因原样写进那一页
        notes.append(f"{label}没跑成：{type(error).__name__} {str(error)[:120]}")
        return None


def run(config, *, day=None, allow_paid=False, judge_fn=None, worker_fn=None, write=True):
    day = day or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    report = checkup_mod.report(
        day, interaction_dir=config.INTERACTION_LOG_DIR, turn_dir=config.TURN_LOG_DIR,
        archive_dir=config.ARCHIVE_DIR, state_file=config.STATE_FILE,
        checkup_dir=config.CHECKUP_DIR)
    if write:
        checkup_mod.write(report, config.CHECKUP_DIR)
    reactions = outcomes_mod.day_summary(config.INTERACTION_LOG_DIR, config.TURN_LOG_DIR, day)

    notes, judge = [], None
    active = [name for name in shadow_mod.names(config)
              if shadow_mod.load(config, name)["status"] == "active"]
    if allow_paid:
        for name in active:
            _paid_step(f"影子回放 {name} ", lambda name=name: shadow_mod.run(
                config, name, judge_fn=judge_fn, worker_fn=worker_fn, allow_paid=True), notes)
        judge = _paid_step("裁判核对", lambda: shadow_mod.judge_check(
            config, judge_fn=judge_fn, worker_fn=worker_fn, allow_paid=True)[0], notes)
    shadows = [shadow_mod.summary(shadow_mod.load(config, name)) for name in active]

    out = {"schema": "regular.nightly.v1", "day": day, "paid": bool(allow_paid),
           "findings": len(report["findings"]), "counts": report["counts"],
           "reactions": reactions, "shadows": shadows, "judge_check": judge, "notes": notes}
    out["line"] = one_line(out)
    # 桌宠配这句话的动作：有要看的就摆出体检的样子，没有就高兴一下
    out["cue"] = "checkup" if out["findings"] or any(
        s["verdict"]["call"] == "worse" for s in shadows) else "cheer"
    if write:
        path = Path(config.NIGHTLY_DIR) / f"{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        storage.atomic_write_text(str(path), render(out, report))
        out["path"] = str(path)
    return out


def one_line(out):
    """桌宠早上说的那一句：只报数，不引原话。"""
    overall = (out["reactions"] or {}).get("overall")
    parts = [f"昨天回了 {overall['n']} 句，被纠正 {overall['corrected']} 句" if overall
             else "昨天没说话"]
    parts.append(f"体检列出 {out['findings']} 处可疑" if out["findings"] else "体检没列出可疑的")
    decided = [f"影子回放 {s['name']}：{shadow_mod.CALLS[s['verdict']['call']].split('，')[0]}"
               for s in out["shadows"] if s["verdict"]["call"] != "collecting"]
    line = "；".join(parts + decided)
    return line if len(line) <= LINE_LIMIT else "；".join(parts)


def render(out, checkup_report):
    lines = [f"# 每晚汇总 · {out['day']}", "", out["line"], ""]
    if out["reactions"]:
        lines += ["## 群友的反应", "", "```",
                  *[outcomes_mod._line(group["key"] or outcomes_mod.UNKNOWN, group)
                    for group in out["reactions"]["routes"]], "```", ""]
    lines += ["## 体检", "", "```", checkup_mod.render(checkup_report), "```", ""]
    if out["shadows"]:
        lines += ["## 影子回放", "", "```", shadow_mod.render(out["shadows"]), "```", ""]
    if out["judge_check"]:
        lines += ["## 裁判核对", "", "```", shadow_mod.render_check(out["judge_check"]), "```", ""]
    if out["notes"]:
        lines += ["## 没跑成的", "", *[f"- {note}" for note in out["notes"]], ""]
    if not out["paid"]:
        lines += ["这一页只读了本地日志，没有调用模型；影子回放和裁判核对要显式授权才会跑。", ""]
    return "\n".join(lines)
