# -*- coding: utf-8 -*-
"""体检与检索策略的自迭代（docs/self-iteration.md「体检与检索策略的自迭代」）。

体检信号和旧账检索都是确定性的本地计算，在冻结输入上重算，不调模型。
搜索只在有界候选空间内逐坐标改动，不保证全局最优；评测结论受标注与快照覆盖限制。

  - 只搜数值、只删词，不加词：新说法要靠编码代理按事故补（AGENTS.md：不要用正则硬凑自然语言）。
  - 尺子由独立标注提供；历史候选 yes/no 只提供局部确认率，不能测完整漏报。
  - 硬约束：已确认的体检候选一条都不能丢；现在过的检索用例一条都不能挂。
  - 完整基准只用 train 搜索，再评独立 validation；缺完整正负标注时只报候选，不称已验证。
  - 只出报告和提案，`regular policy apply` 才写进 data/policies/ —— 那是维护者的一条命令。

    regular tune checkup      拿标注调体检阈值和词表
    regular tune recall       拿检索用例调旧账检索的打分策略，并在真实检索记录上看新旧差在哪
    regular policy show|apply|reset
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import turns as turns_mod
from regular import cases as cases_mod
from regular import checkup as checkup_mod

SCHEMA_TUNE = "regular.tune.v1"
SCHEMA_POLICY = "regular.policy.v1"
MAX_PASSES = 3


def _now_stamp():
    return time.strftime("%Y-%m-%d_%H%M%S", time.localtime()) + f"_{time.time_ns() % 1_000_000_000:09d}"


def _changes(base, cand):
    return {k: {"from": base[k], "to": cand[k]} for k in cand if cand[k] != base.get(k)}


def _diff_from_default(policy, default):
    """策略文件只存跟默认值不同的那几项：默认值以后改了，没调过的项跟着走。"""
    return {k: v for k, v in policy.items() if v != default.get(k)}


def _coordinate_search(start, space, keys, score_fn):
    """坐标下降：逐个参数试候选值，分数（越小越好的元组）变好就收下，最多 MAX_PASSES 轮。

    space[key] == "subset" 的词表一次试删一个词。score_fn 返回 None 表示违反硬约束。
    """
    best = {k: (list(v) if isinstance(v, list) else v) for k, v in start.items()}
    best_score = score_fn(best)
    for _ in range(MAX_PASSES):
        improved = False
        for key in keys:
            options = space[key]
            if options == "subset":
                trials = [[m for m in best[key] if m != drop] for drop in best[key]]
            else:
                trials = [v for v in options if v != best[key]]
            for value in trials:
                trial = dict(best)
                trial[key] = value
                score = score_fn(trial)
                if score is not None and (best_score is None or score < best_score):
                    best, best_score, improved = trial, score, True
        if not improved:
            break
    return best, best_score


# ══════════════════ 体检 ══════════════════

def _label_days(checkup_dir, label_ids):
    """标过的候选分别出自哪一天（从写过的体检 json 里找）。"""
    out = {}
    try:
        files = sorted(Path(checkup_dir).glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for f in doc.get("findings") or ():
            if f.get("id") in label_ids:
                out.setdefault(f["id"], doc.get("day") or path.stem)
    return out


class _CheckupBench:
    """把要用的那几天原料读一遍，之后换策略只重跑检测。"""

    def __init__(self, ctxs, labels, *, now=None):
        self.ctxs = ctxs              # day -> prepare() 结果
        self.labels = labels          # finding id -> {"verdict", "signal"}
        self._memo = {}
        self.now = time.time() if now is None else now

    def found(self, policy, signal, days):
        key = (signal, tuple(days), json.dumps(policy, sort_keys=True, ensure_ascii=False))
        if key not in self._memo:
            out = {}
            for day in days:
                for f in checkup_mod.detect(self.ctxs[day], policy, signals=[signal], now=self.now):
                    out[f["id"]] = {**f, "day": day, "context": []}
            self._memo[key] = out
        return self._memo[key]

    def metrics(self, policy, signal, days, base_found=None):
        found = self.found(policy, signal, days)
        lab = {i: r for i, r in self.labels.items() if r.get("signal") == signal}
        tp = sum(1 for i in found if lab.get(i, {}).get("verdict") == "yes")
        fp = sum(1 for i in found if lab.get(i, {}).get("verdict") == "no")
        out = {"tp": tp, "fp": fp, "found": len(found),
               "unlabeled": sum(1 for i in found if i not in lab),
               "scope": "candidate_only", "fn": None, "tn": None,
               "evidence_complete": False, "f1": None,
               "precision": tp / (tp + fp) if tp + fp else None,
               "recall": None, "fpr": None, "fnr": None,
               "false_positive_rate": None, "false_negative_rate": None}
        if base_found is not None:
            out["lost_yes"] = sorted(i for i in base_found if i not in found
                                     and lab.get(i, {}).get("verdict") == "yes")
            out["dropped_no"] = sorted(i for i in base_found if i not in found
                                       and lab.get(i, {}).get("verdict") == "no")
            out["dropped_unlabeled"] = sorted(i for i in base_found if i not in found and i not in lab)
            out["new_unlabeled"] = sorted(i for i in found if i not in base_found and i not in lab)
        return out

    def tune_signal(self, start, signal, days):
        keys = [k for k, sigs in checkup_mod.PARAM_SIGNALS.items() if signal in sigs]
        base_found = self.found(start, signal, days)
        base_policy = start

        def score(policy):
            m = self.metrics(policy, signal, days, base_found)
            if m["lost_yes"]:
                return None                       # 硬约束：已确认的不许丢
            changed = sum(1 for k in keys if policy[k] != base_policy[k])
            # 先多认出已确认的，再少报已否定的，再少冒出没标过的，最后改得越少越好
            return (-m["tp"], m["fp"], len(m["new_unlabeled"]), changed)

        return _coordinate_search(start, checkup_mod.POLICY_SPACE, keys, score)


CHECKUP_BENCHMARK_SCHEMA = "regular.checkup-benchmark.v1"


def _confusion(tp, fp, fn, tn):
    """先汇总计数再算率；分母为零不伪造 0% 或 100%。"""
    def ratio(a, b):
        return a / b if b else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn),
            "fpr": ratio(fp, fp + tn), "fnr": ratio(fn, tp + fn),
            "false_positive_rate": ratio(fp, fp + tn),
            "false_negative_rate": ratio(fn, tp + fn)}


def _unit_anchors(sample):
    """从原始输入枚举评测单元，不从某套检测结果反推负例。"""
    data, signal, group = sample["data"], sample["signal"], sample["group"]
    def keys(rows, field):
        return {str(r.get(field) or r.get("ts") or 0.0)
                for r in rows if r.get("group") == group}
    if signal in ("unanswered", "repeated", "corrected", "bad_reaction"):
        return keys(data["incoming"], "message_id")
    if signal in ("promised", "sensitive"):
        anchors = keys(data["delivery"], "outgoing_id")
        if signal == "sensitive":
            delivered = {r.get("turn_id") for r in data["delivery"]}
            anchors |= keys([r for r in data["turns"] if r.get("kind") == "reply"
                             and r.get("turn_id") not in delivered], "turn_id")
        return anchors
    if signal == "recall_empty":
        return keys([r for r in data["turns"] if r.get("kind") == "recall"], "turn_id")
    return {f"{sample['day']}|{gram}" for r in data["delivery"]
            if r.get("group") == group for gram in checkup_mod._grams(r.get("text"))}


def load_checkup_benchmark(path):
    """加载独立标注的冻结现场；完整性声明还须满足单元覆盖，未知单元不会自动记 no。"""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ValueError(f"cannot read checkup benchmark: {err}") from None
    if not isinstance(doc, dict) or doc.get("schema") != CHECKUP_BENCHMARK_SCHEMA:
        raise ValueError(f"checkup benchmark requires schema {CHECKUP_BENCHMARK_SCHEMA}")
    if not isinstance(doc.get("samples"), list) or not doc["samples"]:
        raise ValueError("checkup benchmark requires nonempty samples")
    samples, ids, partitions, events = [], set(), {}, {}
    row_kinds = ("incoming", "delivery", "next_delivery", "feedback", "decision", "turns")
    for raw in doc["samples"]:
        if not isinstance(raw, dict):
            raise ValueError("benchmark sample must be an object")
        sid, split, signal = raw.get("id"), raw.get("split"), raw.get("signal")
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError("benchmark sample id must be unique and nonempty")
        if split not in ("train", "validation") or signal not in checkup_mod.LABELS:
            raise ValueError(f"sample {sid}: invalid split or signal")
        if raw.get("complete") is not True or not isinstance(raw.get("group"), str) or not raw["group"]:
            raise ValueError(f"sample {sid}: require complete=true and group")
        if not isinstance(raw.get("day"), str) or not raw["day"]:
            raise ValueError(f"sample {sid}: require day")
        now = raw.get("now")
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError(f"sample {sid}: require frozen numeric now")
        partition = (signal, raw["group"], raw["day"])
        if partition in partitions and partitions[partition] != split:
            raise ValueError(f"sample {sid}: same signal/group/day crosses train and validation")
        partitions[partition] = split
        if not isinstance(raw.get("data"), dict):
            raise ValueError(f"sample {sid}: require frozen data object")
        data = {"day": raw["day"]}
        for kind in row_kinds:
            rows = raw["data"].get(kind, [])
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError(f"sample {sid}: data.{kind} must be rows")
            for row in rows:
                ts = row.get("now") or row.get("ts") or 0
                if (isinstance(ts, bool) or not isinstance(ts, (int, float))
                        or not math.isfinite(ts) or ts > now):
                    raise ValueError(f"sample {sid}: future or invalid timestamp in {kind}")
                if row.get("group") not in (None, raw["group"]):
                    raise ValueError(f"sample {sid}: input contains another group")
            data[kind] = sorted(rows, key=lambda r: r.get("ts") or 0)
        speakers = raw["data"].get("speakers", {})
        if not isinstance(speakers, dict) or not all(isinstance(v, list) for v in speakers.values()):
            raise ValueError(f"sample {sid}: speakers must map groups to lists")
        data["speakers"] = {g: set(v) for g, v in speakers.items()}
        for row in data["incoming"]:
            if row.get("group") and row.get("user"):
                data["speakers"].setdefault(row["group"], set()).add(row["user"])
        baseline = raw.get("baseline") or {}
        if (not isinstance(baseline, dict) or any(not isinstance(day, str) or day >= raw["day"] for day in baseline)
                or not all(isinstance(texts, list) and all(isinstance(t, str) for t in texts)
                           for texts in baseline.values())):
            raise ValueError(f"sample {sid}: baseline must contain only earlier days")
        if raw.get("names") is not None and not isinstance(raw["names"], dict):
            raise ValueError(f"sample {sid}: names must be an object")
        labels = raw.get("labels")
        if not isinstance(labels, list) or not labels:
            raise ValueError(f"sample {sid}: require independently annotated labels")
        truth = {}
        for label in labels:
            if (not isinstance(label, dict) or not isinstance(label.get("anchor"), str)
                    or not isinstance(label.get("positive"), bool) or label["anchor"] in truth):
                raise ValueError(f"sample {sid}: labels need unique anchor and boolean positive")
            truth[label["anchor"]] = label["positive"]
            event = (signal, raw["group"], label["anchor"])
            if event in events and events[event] != split:
                raise ValueError(f"sample {sid}: event anchor crosses train and validation")
            events[event] = split
        sample = {**raw, "data": data, "truth": truth}
        sample["units"] = _unit_anchors(sample)
        sample["ctx"] = checkup_mod.prepare_data(data, baseline=baseline, names=raw.get("names"))
        samples.append(sample)
        ids.add(sid)
    return samples


def _benchmark_metrics(samples, policy, signal, split):
    counts = {k: 0 for k in ("tp", "fp", "fn", "tn")}
    errors, unknown, missing, tp_units = [], [], [], []
    selected = [s for s in samples if s["signal"] == signal and s["split"] == split]
    for sample in selected:
        found = {f["anchor"] for f in checkup_mod.detect(
            sample["ctx"], policy, signals=[signal], now=sample["now"])
                 if f.get("group") == sample["group"]}
        truth = sample["truth"]
        unknown += [f"{sample['id']}:{a}" for a in sorted(found - truth.keys())]
        missing += [f"{sample['id']}:{a}" for a in sorted(sample["units"] - truth.keys())]
        missing += [f"{sample['id']}:{a}:outside_input" for a in sorted(truth.keys() - sample["units"])]
        for anchor, positive in truth.items():
            predicted = anchor in found
            outcome = ("tp" if predicted else "fn") if positive else ("fp" if predicted else "tn")
            counts[outcome] += 1
            if outcome == "tp":
                tp_units.append(f"{sample['id']}:{anchor}")
            if outcome in ("fp", "fn"):
                errors.append({"sample": sample["id"], "anchor": anchor, "kind": outcome})
    complete = bool(selected) and not unknown and not missing
    out = _confusion(**counts)
    if not complete:
        out = {k: None for k in out}
    return {**out, "scope": "annotated_event_units", "evidence_complete": complete,
            "samples": len(selected), "labeled_counts": counts,
            "unknown_predictions": unknown, "unlabeled_units": missing,
            "errors": errors, "tp_units": tp_units}


def _benchmark_totals(rows):
    complete = bool(rows) and all(r["evidence_complete"] for r in rows)
    counts = {k: sum(r["labeled_counts"][k] for r in rows) for k in ("tp", "fp", "fn", "tn")}
    matrix = _confusion(**counts)
    return {**(matrix if complete else {k: None for k in matrix}),
            "evidence_complete": complete, "samples": sum(r["samples"] for r in rows),
            "unknown_predictions": [x for r in rows for x in r["unknown_predictions"]],
            "unlabeled_units": [x for r in rows for x in r["unlabeled_units"]]}


def _tune_checkup_benchmark(config, path, *, write=True):
    samples = load_checkup_benchmark(path)
    current = checkup_mod.load_policy(config.CHECKUP_POLICY_FILE)
    candidate, proposal, rows = dict(current), dict(current), []
    for signal in dict.fromkeys(s["signal"] for s in samples):
        base = {split: _benchmark_metrics(samples, current, signal, split)
                for split in ("train", "validation")}
        train = base["train"]
        keys = [k for k, sigs in checkup_mod.PARAM_SIGNALS.items() if signal in sigs]
        usable_train = (train["evidence_complete"] and train["tp"] + train["fn"] > 0
                        and train["fp"] + train["tn"] > 0)
        def score(policy):
            result = _benchmark_metrics(samples, policy, signal, "train")
            if (not result["evidence_complete"] or result["fp"] > train["fp"]
                    or set(train["tp_units"]) - set(result["tp_units"])):
                return None
            return (result["fn"], result["fp"], sum(policy[k] != current[k] for k in keys))
        cand = _coordinate_search(current, checkup_mod.POLICY_SPACE, keys, score)[0] if usable_train else dict(current)
        measured = {split: _benchmark_metrics(samples, cand, signal, split)
                    for split in ("train", "validation")}
        changes = _changes(current, cand)
        held, held_base = measured["validation"], base["validation"]
        valid = (usable_train and held["evidence_complete"] and held["tp"] + held["fn"] > 0
                 and held["fp"] + held["tn"] > 0)
        recommended = (bool(changes) and valid and held["fp"] <= held_base["fp"]
                       and not (set(held_base["tp_units"]) - set(held["tp_units"])))
        status = ("候选已通过独立完整留出对照" if recommended else
                  "证据不足：需要完整且各含正负例的 train/validation" if not valid else
                  "保持当前策略" if not changes else "不推荐：独立留出出现退步")
        rows.append({"signal": signal, "label": checkup_mod.LABELS[signal], "base": base,
                     "cand": measured, "changes": changes, "recommended": recommended, "status": status})
        candidate.update({k: v["to"] for k, v in changes.items()})
        if recommended:
            proposal.update({k: v["to"] for k, v in changes.items()})
    splits = {split: {version: _benchmark_totals([r[version][split] for r in rows])
                      for version in ("base", "cand")} for split in ("train", "validation")}
    changes = _changes(current, proposal)
    report = {"schema": SCHEMA_TUNE, "target": "checkup", "scope": "independent_benchmark",
              "stamp": _now_stamp(), "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
              "benchmark": {"path": str(path), "splits": splits,
                            "matching": "signal/group + exact original event anchor; repeated uses first message_id; catchphrase uses day|gram"},
              "signals": rows, "current": _diff_from_default(current, checkup_mod.DEFAULT_POLICY),
              "candidate": _diff_from_default(candidate, checkup_mod.DEFAULT_POLICY),
              "proposal": {"name": "checkup", "changes": changes,
                           "policy": _diff_from_default(proposal, checkup_mod.DEFAULT_POLICY)}
              if changes and all(not r["changes"] or r["recommended"] for r in rows) else None}
    if write:
        report["report_path"] = str(_write(config, report, render_checkup))
    return report


def _render_checkup_benchmark(report):
    lines = [f"# 体检策略独立基准对照 · {report['created_at']}", "",
             "- 只在 train 搜索；锁定候选后评 validation。缺完整正负标注不推荐。",
             f"- 匹配：{report['benchmark']['matching']}",
             "- 比率按总计数计算；零分母显示 —；未知事件或未标注输入单元令完整指标不可用。", "",
             "| 信号 | 数据集 | 版本 | TP | FP | FN | TN | Precision | Recall | FPR | FNR |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    def value(v):
        return "—" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
    details = []
    for row in report["signals"]:
        for split in ("train", "validation"):
            for version in ("base", "cand"):
                m = row[version][split]
                values = " | ".join(value(m[k]) for k in ("tp", "fp", "fn", "tn", "precision", "recall", "fpr", "fnr"))
                lines.append(f"| {row['label']} | {split} | {version} | {values} |")
                for error in m["errors"]:
                    details.append(f"- {split}/{version} {error['kind']}: `{error['sample']}:{error['anchor']}`")
                if not m["evidence_complete"]:
                    details.append(f"- {row['label']} {split}/{version} 覆盖不完整：未知预测 {m['unknown_predictions']}，未标注单元 {m['unlabeled_units']}。")
        details += ["", f"{row['label']}：{row['status']}。", ""]
    for split, versions in report["benchmark"]["splits"].items():
        for version, m in versions.items():
            values = " | ".join(value(m[k]) for k in ("tp", "fp", "fn", "tn", "precision", "recall", "fpr", "fnr"))
            lines.append(f"| 总计（micro） | {split} | {version} | {values} |")
    lines += ["", *details, ""]
    lines += ["只输出报告与候选提案，发布仍需维护者执行 `regular policy apply`。"]
    return "\n".join(lines) + "\n"


def tune_checkup(config, *, min_labels=6, min_no=2, write=True, now=None, benchmark=None):
    """拿标注调体检策略。返回报告 dict（含提案）；write=True 时写 data/evals/tune-checkup-*.md/json。"""
    if benchmark:
        return _tune_checkup_benchmark(config, benchmark, write=write)
    labels = checkup_mod.read_labels(config.CHECKUP_DIR)
    current = checkup_mod.load_policy(config.CHECKUP_POLICY_FILE)
    day_of = _label_days(config.CHECKUP_DIR, set(labels))
    days = sorted(set(day_of.values()))
    ctxs = {d: checkup_mod.prepare(d, interaction_dir=config.INTERACTION_LOG_DIR,
                                   turn_dir=config.TURN_LOG_DIR, archive_dir=config.ARCHIVE_DIR,
                                   state_file=config.STATE_FILE) for d in days}
    bench = _CheckupBench(ctxs, labels, now=now)
    tunable = list(dict.fromkeys(s for sigs in checkup_mod.PARAM_SIGNALS.values() for s in sigs))
    proposal = dict(current)
    rows = []
    for signal in checkup_mod.LABELS:
        lab = {i: r for i, r in labels.items() if r.get("signal") == signal and i in day_of}
        yes = sum(1 for r in lab.values() if r["verdict"] == "yes")
        no = len(lab) - yes
        sig_days = sorted({day_of[i] for i in lab})
        row = {"signal": signal, "label": checkup_mod.LABELS[signal], "labels": len(lab),
               "yes": yes, "no": no, "days": sig_days, "changes": {}, "status": ""}
        rows.append(row)
        if signal not in tunable:
            row["status"] = "没有可调参数（按事故改代码）"
            continue
        if len(lab) < min_labels or no < min_no:
            row["status"] = f"证据不足（要至少 {min_labels} 条标注、{min_no} 条 no）"
            continue
        base_found = bench.found(current, signal, sig_days)
        base_m = bench.metrics(current, signal, sig_days, base_found)
        cand, _ = bench.tune_signal(current, signal, sig_days)
        cand_m = bench.metrics(cand, signal, sig_days, base_found)
        row.update(base=base_m, cand=cand_m, changes=_changes(current, cand))
        if yes == 0 and no >= min_labels:
            row["delete_candidate"] = True       # 标了一堆全是 no：信号本身该改或删，交给人
        # 留一天验证：在其余几天上调，看留出来那天有没有丢已确认的
        holdout = {"days": 0, "lost_yes": 0, "dropped_no": 0}
        if len(sig_days) >= 2:
            for d in sig_days:
                train = [x for x in sig_days if x != d]
                fold_cand, _ = bench.tune_signal(current, signal, train)
                held_base = bench.found(current, signal, [d])
                m = bench.metrics(fold_cand, signal, [d], held_base)
                holdout["days"] += 1
                holdout["lost_yes"] += len(m["lost_yes"])
                holdout["dropped_no"] += len(m["dropped_no"])
        row["holdout"] = holdout
        if not row["changes"]:
            row["status"] = "本次有界搜索未找到更好的候选"
        elif holdout["lost_yes"]:
            row["status"] = "不推荐：留一天验证丢了已确认的候选（过拟合）"
        elif holdout["days"] == 0:
            row["status"] = "候选提案（仅已有候选标注，无独立完整验证，不代表漏报率）"
            proposal.update({k: v["to"] for k, v in row["changes"].items()})
        else:
            row["status"] = "候选提案（已检出标签留一天未丢 yes；完整漏报率未知）"
            proposal.update({k: v["to"] for k, v in row["changes"].items()})

    # 新口径下冒出来、还没标过的候选：下一轮要标的就是这些（主动学习）
    to_label = []
    for row in rows:
        for fid in (row.get("cand") or {}).get("new_unlabeled", ())[:10]:
            to_label.append({"signal": row["signal"], "id": fid})
    report = {
        "schema": SCHEMA_TUNE, "target": "checkup", "stamp": _now_stamp(),
        "scope": "candidate_only",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "days": days, "labels": len(labels), "signals": rows, "to_label": to_label,
        "current": _diff_from_default(current, checkup_mod.validate_policy({})),
        "proposal": None,
    }
    changes = _changes(current, proposal)
    if changes:
        report["proposal"] = {"name": "checkup", "changes": changes,
                              "policy": _diff_from_default(proposal, checkup_mod.validate_policy({}))}
    if write:
        report["report_path"] = str(_write(config, report, render_checkup))
    return report


def render_checkup(report):
    if report.get("scope") == "independent_benchmark":
        return _render_checkup_benchmark(report)
    lines = [f"# 体检策略调参 · {report['created_at']}", "",
             f"- 标注：{report['labels']} 条，涉及 {len(report['days'])} 天（{', '.join(report['days']) or '—'}）",
             "- 硬约束：已确认（yes）的候选一条都不能丢；词表只删不加；数值不出搜索范围。",
             "- 覆盖：仅历史已检出候选的局部标注；无法计算完整 FN/TN、召回率、误报率或漏报率。",
             "- 只出提案：`regular policy apply <本报告 json>` 由维护者执行。", "",
             "| 信号 | 标注 (yes/no) | 现在 TP/FP | 提案 TP/FP | 少报 no | 顺带不再报的未标注 | 留一天验证 | 结论 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in report["signals"]:
        b, c, h = r.get("base"), r.get("cand"), r.get("holdout") or {}
        ho = (f"{h['days']} 天，丢 yes {h['lost_yes']}，少报 no {h['dropped_no']}"
              if h.get("days") else "—")
        base_counts = f"{b['tp']}/{b['fp']}" if b else "—"
        cand_counts = f"{c['tp']}/{c['fp']}" if c else "—"
        lines.append(
            f"| {r['label']} | {r['labels']} ({r['yes']}/{r['no']}) | "
            f"{base_counts} | {cand_counts} | "
            f"{len(c['dropped_no']) if c else '—'} | {len(c['dropped_unlabeled']) if c else '—'} | "
            f"{ho} | {r['status']}{'；全是 no，考虑改或删这个信号' if r.get('delete_candidate') else ''} |")
    for r in report["signals"]:
        if not r["changes"]:
            continue
        lines += ["", f"## {r['label']} 的改法", ""]
        for k, v in r["changes"].items():
            lines.append(f"- `{k}`：`{json.dumps(v['from'], ensure_ascii=False)}` → "
                         f"`{json.dumps(v['to'], ensure_ascii=False)}`")
        dropped = (r.get("cand") or {}).get("dropped_unlabeled") or []
        if dropped:
            lines.append(f"- 顺带不再报、但没人标过的 {len(dropped)} 条（apply 前最好先标）："
                         + "、".join(f"`{i}`" for i in dropped[:10]))
    if report.get("to_label"):
        lines += ["", "## 新口径下冒出来、还没标的候选", "",
                  "这些候选尚未写入日体检报告，不能直接用旧报告的标注命令。"
                  "请还原完整现场、独立标注正负单元后纳入 benchmark，再运行 `regular tune checkup --benchmark <文件>`。", ""]
        lines += [f"- 候选 `{x['id']}`（{x['signal']}）"
                  for x in report["to_label"]]
    if report.get("proposal"):
        lines += ["", "## 提案（只含跟默认值不同的项）", "", "```json",
                  json.dumps(report["proposal"]["policy"], ensure_ascii=False, indent=1), "```"]
    else:
        lines += ["", "没有提案：本次有界搜索未找到改进，或者证据不足；不代表全局最优。"]
    return "\n".join(lines) + "\n"


# ══════════════════ 旧账检索 ══════════════════

def _retrieve_eval(config, cases, policy):
    from regular import replay_worker
    out = {}
    for case in cases:
        sample = replay_worker.run_retrieve(config, case["retrieve"], policy=policy)
        res = cases_mod.check_reply(sample, case.get("checks"))
        got = json.loads(sample)
        out[case["id"]] = {"passed": res["passed"], "picked": got["picked"],
                           "promise": got.get("promise"),
                           "metrics": cases_mod.retrieval_metrics(sample, case),
                           "reasons": [d["reason"] for d in res["details"] if not d["passed"]]}
    return out


def _retrieve_search(config, cases, start, base_results=None):
    """在给定用例上搜检索策略：现在过的不许挂；先多过几条，再少改几项，再少塞几条证据。"""
    import recall
    base_results = base_results or _retrieve_eval(config, cases, start)
    must_pass = {cid for cid, r in base_results.items() if r["passed"]}
    memo = {}

    def score(policy):
        key = json.dumps(policy, sort_keys=True, ensure_ascii=False)
        if key not in memo:
            res = _retrieve_eval(config, cases, policy)
            if any(not res[cid]["passed"] for cid in must_pass):
                memo[key] = None
            elif any(
                old.get("metrics", {}).get("evidence_complete") and (
                    not res[cid]["metrics"]["evidence_complete"]
                    or res[cid]["metrics"]["fp"] > old["metrics"]["fp"]
                    or res[cid]["metrics"]["fn"] > old["metrics"]["fn"])
                for cid, old in base_results.items()
            ):
                memo[key] = None
            else:
                passed = sum(1 for r in res.values() if r["passed"])
                full = [r["metrics"] for r in res.values() if r["metrics"]["evidence_complete"]]
                changed = sum(1 for k in policy if policy[k] != start[k])
                picked = sum(len(r["picked"]) for r in res.values())
                memo[key] = (sum(m["fn"] for m in full), sum(m["fp"] for m in full),
                             -passed, changed, picked)
        return memo[key]

    return _coordinate_search(start, recall.POLICY_SPACE, list(recall.POLICY_SPACE), score)


def _folds(items, k):
    k = max(2, min(k, len(items)))
    return [items[i::k] for i in range(k)]


def _load_archive_by_group(archive_dir, since_ts):
    """真实检索记录回放用：归档按群读进内存一次（只读）。"""
    by_group = defaultdict(list)
    try:
        files = sorted(Path(archive_dir).glob("*.jsonl"))
    except OSError:
        return by_group
    for path in files:
        if path.stem < time.strftime("%Y-%m-%d", time.localtime(since_ts)):
            continue
        for r in cases_mod._read_jsonl_rows(path):
            if r.get("group") and float(r.get("ts") or 0) >= since_ts:
                by_group[r["group"]].append(r)
    return by_group


def recall_drift(config, base, cand, *, days=7, limit=20, now=None):
    """在最近几天真实的检索记录上，按新旧两套策略各重挑一遍，列出挑得不一样的那几次。

    没有标准答案，只是告诉维护者「换了以后线上会有哪些不同」—— 不同的那几次正好拿去存成用例标注。
    归档只取那一刻之前的行，不让「未来」的消息混进当时的检索。
    """
    import recall
    now = time.time() if now is None else now
    all_days = turns_mod.days(config.TURN_LOG_DIR)
    recent = [d for d in all_days if d >= time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))]
    records = [t for d in recent for t in turns_mod.read_day(config.TURN_LOG_DIR, d)
               if t.get("kind") == "recall" and t.get("query")]
    if not records:
        return {"records": 0, "changed": 0, "examples": []}
    earliest = min(float(t.get("now") or t.get("ts") or now) for t in records)
    archive = _load_archive_by_group(config.ARCHIVE_DIR, earliest - cases_mod.SNAPSHOT_LOOKBACK_DAYS * 86400)
    changed, examples, partial_records = 0, [], 0
    for t in records:
        at = float(t.get("now") or t.get("ts") or now)
        rows = [r for r in archive.get(t.get("group"), ()) if float(r.get("ts") or 0) <= at]
        if isinstance(t.get("promises"), list) and t.get("promises_complete") is True:
            promises = [dict(p) for p in t["promises"] if isinstance(p, dict)]
        else:
            selected = t.get("promise_record")
            promises = ([dict(selected)] if isinstance(selected, dict) else
                        [{"who": "", "what": t["promise"]}] if t.get("promise") else [])
            partial_records += 1
        if not t.get("defaults"):
            partial_records += int(t.get("promises_complete") is True)
        picks, outcomes = {}, {}
        spec = {"query": t["query"], "rows": rows, "promises": promises,
                "names": list(t.get("names") or ()), "now": at,
                "exclude_text": t["query"], "defaults": dict(t.get("defaults") or {})}
        for name, policy in (("base", base), ("cand", cand)):
            from regular.replay_worker import run_retrieve
            outcomes[name] = json.loads(run_retrieve(config, spec, policy=policy))
            picks[name] = outcomes[name]["picked"]
        if (picks["base"] != picks["cand"]
                or outcomes["base"].get("promise") != outcomes["cand"].get("promise")):
            changed += 1
            if len(examples) < limit:
                examples.append({"turn_id": t.get("turn_id"), "query": t["query"][:60],
                                 "base": picks["base"], "cand": picks["cand"],
                                 "base_promise": outcomes["base"].get("promise"),
                                 "cand_promise": outcomes["cand"].get("promise"),
                                 "recorded": [str(p.get("message_id")) for p in t.get("picked") or ()]})
    return {"records": len(records), "changed": changed, "examples": examples,
            "partial_records": partial_records, "scope": "behavior_drift_without_quality_labels"}


def _retrieve_regressed(base, cand):
    if base["passed"] and not cand["passed"]:
        return True
    old, new = base.get("metrics", {}), cand.get("metrics", {})
    return bool(old.get("evidence_complete") and (
        not new.get("evidence_complete") or new["fp"] > old["fp"] or new["fn"] > old["fn"]))


def _retrieve_totals(results):
    full = [r["metrics"] for r in results.values() if r.get("metrics", {}).get("evidence_complete")]
    counts = {k: sum(m[k] for m in full) for k in ("tp", "fp", "fn", "tn")}
    subset = _confusion(**counts) if full else None
    complete = bool(results) and len(full) == len(results)
    matrix = _confusion(**counts)
    return {**(matrix if complete else {k: None for k in matrix}),
            "scope": "archive_messages", "evidence_complete": complete,
            "cases": len(results), "complete_cases": len(full), "complete_subset": subset,
            "coverage_gaps": [{"id": cid, "reason": r.get("metrics", {}).get("reason", "missing labels")}
                              for cid, r in results.items()
                              if not r.get("metrics", {}).get("evidence_complete")]}


def tune_recall(config, *, selector="retrieve", folds=5, drift_days=7, write=True):
    """拿检索用例调旧账检索策略。返回报告 dict（含提案）。"""
    import recall
    cases = [c for c in cases_mod.load_cases(config.CASES_DIR, selector=selector, include_seeds=True)
             if (c.get("depth") or "generate") == "retrieve"]
    if not cases:
        raise ValueError(f"no retrieve cases match {selector!r}")
    if any(c.get("split", "train") not in ("train", "validation") for c in cases):
        raise ValueError("retrieve case split must be train or validation")
    training = [c for c in cases if c.get("split", "train") == "train"]
    validation = [c for c in cases if c.get("split") == "validation"]
    if not training:
        raise ValueError("recall tuning requires train cases")
    current = recall.validate_policy(
        recall.active_policy(config.RECALL_POLICY_FILE))
    base_res = _retrieve_eval(config, cases, current)
    train_ids = {c["id"] for c in training}
    cand, _ = _retrieve_search(config, training, current,
                               {k: v for k, v in base_res.items() if k in train_ids})
    cand_res = _retrieve_eval(config, cases, cand)

    # 分折验证：在其余折上调，看留出来那一折有没有原来过、现在挂的
    holdout = {"folds": 0, "regressed": [], "fixed": []}
    if not validation and len(training) >= 4:
        for test in _folds(training, folds):
            test_ids = {c["id"] for c in test}
            train = [c for c in training if c["id"] not in test_ids]
            fold_cand, _ = _retrieve_search(config, train, current,
                                            {k: v for k, v in base_res.items() if k not in test_ids})
            fold_res = _retrieve_eval(config, test, fold_cand)
            holdout["folds"] += 1
            for cid in test_ids:
                if _retrieve_regressed(base_res[cid], fold_res[cid]):
                    holdout["regressed"].append(cid)
                if not base_res[cid]["passed"] and fold_res[cid]["passed"]:
                    holdout["fixed"].append(cid)

    changes = _changes(current, cand)
    base_pass = sum(1 for r in base_res.values() if r["passed"])
    cand_pass = sum(1 for r in cand_res.values() if r["passed"])
    metrics = {}
    for split, subset in (("train", training), ("validation", validation)):
        ids = {c["id"] for c in subset}
        metrics[split] = {version: _retrieve_totals({i: r for i, r in results.items() if i in ids})
                          for version, results in (("base", base_res), ("cand", cand_res))}
    held_regressed = [c["id"] for c in validation
                      if _retrieve_regressed(base_res[c["id"]], cand_res[c["id"]])]
    complete = all(metrics[split]["cand"]["evidence_complete"] for split in ("train", "validation"))
    held = metrics["validation"]["cand"]
    balanced = complete and all(metrics[split]["cand"]["tp"] + metrics[split]["cand"]["fn"] > 0
                                and metrics[split]["cand"]["fp"] + metrics[split]["cand"]["tn"] > 0
                                for split in ("train", "validation"))
    validated = bool(validation) and balanced and not held_regressed
    if not changes:
        status = "本次有界搜索未找到更好的候选（不代表全局最优）"
    elif holdout["regressed"] or held_regressed:
        status = "不推荐：留出用例出现退步"
    elif not validated:
        status = "候选提案（缺独立完整正负标注的留出验证；训练通过率不代表上线质量）"
    else:
        status = "候选已通过独立完整留出对照"
    recommended = bool(changes) and not holdout["regressed"] and not held_regressed
    report = {
        "schema": SCHEMA_TUNE, "target": "recall", "stamp": _now_stamp(),
        "scope": "complete_archive_labels" if complete else "partial_cases",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "cases": len(cases), "base_pass": base_pass, "cand_pass": cand_pass,
        "status": status, "changes": changes, "holdout": holdout,
        "metrics": metrics, "validation": {"cases": len(validation), "regressed": held_regressed,
                                             "validated": validated},
        "rows": [{"id": c["id"], "base": base_res[c["id"]], "cand": cand_res[c["id"]]} for c in cases],
        "current": _diff_from_default(current, recall.DEFAULT_POLICY),
        "drift": recall_drift(config, current, cand, days=drift_days) if changes else None,
        "proposal": ({"name": "recall", "changes": changes,
                      "policy": _diff_from_default(cand, recall.DEFAULT_POLICY)}
                     if recommended else None),
    }
    if write:
        report["report_path"] = str(_write(config, report, render_recall))
    return report


def render_recall(report):
    lines = [f"# 旧账检索策略调参 · {report['created_at']}", "",
             f"- 检索用例：{report['cases']} 条 | 通过：`{report['base_pass']}` → `{report['cand_pass']}`",
             "- 硬约束：现在过的用例一条都不能挂；数值不出搜索范围；敏感词 / 指令行 / 机器编号不在策略里。",
             f"- 结论：**{report['status']}**"]
    if report.get("metrics"):
        gaps = []
        lines += ["", "误报漏报按归档消息ID计数并做 micro 汇总；只有完整语料与独立完整相关集才有完整指标。",
                  "缺省 split=train；显式 validation 不参与搜索。K 折验证搜索流程不等于最终候选的独立留出成绩。", "",
                  "| 数据集 | 版本 | 完整用例/全部 | TP | FP | FN | TN | Precision | Recall | FPR | FNR |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for split, versions in report["metrics"].items():
            for version, m in versions.items():
                values = " | ".join("—" if m[k] is None else f"{m[k]:.3f}" if isinstance(m[k], float) else str(m[k])
                                    for k in ("tp", "fp", "fn", "tn", "precision", "recall", "fpr", "fnr"))
                lines.append(f"| {split} | {version} | {m['complete_cases']}/{m['cases']} | {values} |")
                for gap in m["coverage_gaps"]:
                    gaps.append(f"- {split}/{version} `{gap['id']}` 覆盖缺口：{gap['reason']}")
        lines += ["", *gaps, ""]
    h = report["holdout"]
    if h["folds"]:
        lines.append(f"- 分折验证：{h['folds']} 折，留出来修好 {len(h['fixed'])} 条、挂掉 {len(h['regressed'])} 条"
                     + (f"（{', '.join(h['regressed'])}）" if h["regressed"] else ""))
    if report["changes"]:
        lines += ["", "## 改法", ""]
        for k, v in report["changes"].items():
            lines.append(f"- `{k}`：`{json.dumps(v['from'])}` → `{json.dumps(v['to'])}`")
    changed_rows = [r for r in report["rows"] if r["base"]["passed"] != r["cand"]["passed"]
                    or r["base"]["picked"] != r["cand"]["picked"]]
    failing = [r for r in report["rows"] if not r["cand"]["passed"]]
    if changed_rows:
        lines += ["", "## 挑得不一样的用例", "", "| 用例 | 现在 | 提案 |", "| --- | --- | --- |"]
        for r in changed_rows:
            lines.append(f"| `{r['id']}` | {'过' if r['base']['passed'] else '挂'} {r['base']['picked']} | "
                         f"{'过' if r['cand']['passed'] else '挂'} {r['cand']['picked']} |")
    if failing:
        lines += ["", "## 提案下仍然挂着的用例（数值调不好，多半要改代码）", ""]
        lines += [f"- `{r['id']}`：{'；'.join(r['cand']['reasons'])}" for r in failing]
    drift = report.get("drift")
    if drift:
        lines += ["", f"## 真实检索记录上的差别（{drift['records']} 次里有 {drift['changed']} 次挑得不一样）", ""]
        for ex in drift["examples"]:
            lines.append(f"- 「{ex['query']}」现在 {ex['base']} → 提案 {ex['cand']}；"
                         f"存例：`regular case add --turn {ex['turn_id']} --id <id> --relevant <消息编号>`")
    if report.get("proposal"):
        lines += ["", "## 提案（只含跟默认值不同的项）", "", "```json",
                  json.dumps(report["proposal"]["policy"], ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines) + "\n"


# ══════════════════ 报告与策略文件 ══════════════════

def _write(config, report, render):
    base = Path(config.EVAL_DIR)
    base.mkdir(parents=True, exist_ok=True)
    stem = f"tune-{report['target']}-{report['stamp']}"
    md = base / f"{stem}.md"
    md.write_text(render(report), encoding="utf-8")
    (base / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return md


def _policy_spec(name):
    if name == "checkup":
        return checkup_mod.validate_policy, lambda: checkup_mod.validate_policy({})
    if name == "recall":
        import recall
        return recall.validate_policy, lambda: dict(recall.DEFAULT_POLICY)
    raise ValueError(f"unknown policy {name!r}; expected checkup or recall")


def policy_path(config, name):
    _policy_spec(name)
    return Path(config.CHECKUP_POLICY_FILE if name == "checkup" else config.RECALL_POLICY_FILE)


def _archive_current(config, name):
    path = policy_path(config, name)
    if not path.exists():
        return None
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    fd, archived = tempfile.mkstemp(prefix=f"{name}-{_now_stamp()}-", suffix=".json", dir=history)
    os.close(fd)
    target = Path(archived)
    shutil.move(str(path), str(target))
    return target


def apply_proposal(config, report_path):
    """原子发布提案，旧文件复制到 history/；新文件写失败时原策略仍在。维护者执行。"""
    try:
        doc = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ValueError(f"cannot read policy proposal: {err}") from None
    if not isinstance(doc, dict):
        raise ValueError("policy proposal must be a JSON object")
    if doc.get("schema") == SCHEMA_POLICY:          # 也认 history 里的旧策略文件：回滚就是再 apply 一次
        name, policy = doc.get("name"), doc.get("policy", {})
    else:
        proposal = doc.get("proposal")
        if not isinstance(proposal, dict) or not proposal:
            raise ValueError(f"{report_path} has no proposal to apply")
        name, policy = proposal.get("name"), proposal.get("policy", {})
    validate, _default = _policy_spec(name)
    validate(policy)                                  # 越界 / 加词在这里拒掉，不写半个文件
    path = policy_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({
        "schema": SCHEMA_POLICY, "name": name, "policy": policy,
        "source": str(report_path),
        "applied_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }, ensure_ascii=False, indent=1) + "\n"
    previous = None
    fd, temporary = tempfile.mkstemp(prefix=f".{name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            history = path.parent / "history"
            history.mkdir(parents=True, exist_ok=True)
            hfd, old = tempfile.mkstemp(prefix=f"{name}-{_now_stamp()}-", suffix=".json", dir=history)
            os.close(hfd)
            previous = Path(old)
            shutil.copy2(path, previous)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"name": name, "path": str(path), "previous": str(previous) if previous else None,
            "policy": policy}


def reset_policy(config, name):
    """回到代码默认值：把策略文件挪进 history/。"""
    return _archive_current(config, name)


def show_policy(config, name):
    validate, default = _policy_spec(name)
    path = policy_path(config, name)
    if name == "checkup":
        effective = checkup_mod.load_policy(str(path))
    else:
        import recall
        effective = recall.active_policy(str(path))
    meta = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            meta = {k: doc.get(k) for k in ("source", "applied_at")}
        except (OSError, ValueError):
            meta = {"error": "unreadable"}
    return {"name": name, "path": str(path), "file": path.exists(), **meta,
            "overrides": _diff_from_default(effective, default()), "effective": effective}
