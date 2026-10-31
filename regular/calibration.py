# -*- coding: utf-8 -*-
"""把判断的数值和随后真实发生的事拼成标签对，只读本地互动日志。

### 这是为了回答哪个问题

「这次插话受欢迎吗」—— 四个产品位置里唯一有真实结果可对的那个。
拼法是三跳，全部靠显式标识，不靠时间凑：

    decision_scores(site=banter, source_message_id=M)
      → delivery(source_message_id=M, route=banter, status=delivered)
      → outgoing_id
      → natural_feedback(source_reply_id=outgoing_id) → action

`action` 只认两个值当标签：`keep_playful` = 受欢迎，`back_off` = 不受欢迎。
其余结论谈的是内容对不对，不是打扰该不该，一律不计（和 receptive.py 同一口径）。

### 必须先说清的三条边界（这份报告最容易被误读的地方）

1. **只有「插了」的那些才可能有标签。** 判断说 `stay_quiet` 时机器人不开口，
   于是永远看不到结果 —— 这是**审查**（censoring），不是负例。
   所以这份报告只能校准「决定插话时，它说的概率准不准」，
   **不能**回答「那些它选择沉默的时刻，插进去会不会受欢迎」。
2. **没人反应 ≠ 不受欢迎。** 群里绝大多数接话不会引出任何明确反馈。
   那些是**缺失标签**，单独计数，绝不补成负例。
3. **这是观察记录，不是实验。** 没有随机分组；插话本身是产品机制决定的，
   所以这些标签对里混着「它更愿意在什么时候插话」的选择效应。
   用它学一个阈值是正当的；用它声称因果收益不是。

### 口径

ECE 用 10 个等宽箱（与 arXiv:2609.29429 附录 E 一致），并给出
Bernoulli(p) 抽 1000 次的零假设 —— 几百条样本上即便完美校准 ECE 也会偏正，
不给零假设就没法读那个数。Platt 重标在样本不足时**不拟合**，只报条数。
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from interactions import SCHEMA

SCHEMA_OUT = "regular.decision-calibration.v1"

# 只有这两个结论是关于「打扰该不该」的真实结果。
WELCOME = "keep_playful"
UNWELCOME = "back_off"

# 每个位置拿来校准的那道题，以及「真的做了动作」的那个选项。
SITES = {"banter": ("intervention", "join_briefly"),
         "callback": ("callback", "bring_it_up")}

ECE_BINS = 10
NULL_DRAWS = 1000
# 论文里 k=10 已拿到大部分收益、k=20 接近饱和；低于 10 条不拟合刻度。
MIN_FOR_FIT = 10


def _rows(log_dir):
    """逐行读本机互动日志。坏行跳过并计数，不让半行挡住整份报告。"""
    diagnostics = {"files_read": 0, "malformed_lines": 0, "read_errors": 0}
    try:
        files = sorted(p for p in Path(log_dir).iterdir() if p.suffix == ".jsonl")
    except FileNotFoundError:
        return [], diagnostics
    except OSError:
        diagnostics["read_errors"] += 1
        return [], diagnostics
    out = []
    for path in files:
        try:
            with path.open("rb") as stream:
                diagnostics["files_read"] += 1
                for raw in stream:
                    try:
                        row = json.loads(raw.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeError):
                        diagnostics["malformed_lines"] += 1
                        continue
                    if isinstance(row, dict) and row.get("schema") == SCHEMA:
                        out.append(row)
        except OSError:
            diagnostics["read_errors"] += 1
    out.sort(key=lambda r: r.get("ts") or 0.0)
    return out, diagnostics


def _text(row, key):
    value = row.get(key)
    return value if isinstance(value, str) and value else None


def pairs(log_dir, site="banter"):
    """拼出 (预测概率, 真实结果) 对，外加各级计数。只读，不改任何文件。"""
    question, acted = SITES[site]
    rows, diagnostics = _rows(log_dir)
    counts = {"scores": 0, "acted": 0, "with_probability": 0, "delivered": 0,
              "labelled": 0, "welcome": 0, "unwelcome": 0,
              "no_reaction": 0, "other_verdict": 0, "stayed_quiet": 0,
              "dropped_by_floor": 0}

    # delivery：(群, 触发消息) -> 已确认送达的 outgoing_id
    delivered = {}
    for row in rows:
        if (row.get("kind") != "delivery" or row.get("status") != "delivered"
                or row.get("actor") != "assistant"):
            continue
        group, source = _text(row, "group"), _text(row, "source_message_id")
        outgoing = _text(row, "outgoing_id")
        if group and source and outgoing:
            delivered.setdefault((group, source), outgoing)

    # natural_feedback：outgoing_id -> 对那条发言的结论（只认关于打扰的两种）
    verdict = {}
    for row in rows:
        if row.get("kind") != "natural_feedback":
            continue
        if row.get("target") != "bot_response":
            continue
        reply = _text(row, "source_reply_id")
        action = _text(row, "action")
        if reply and action and reply not in verdict:
            verdict[reply] = action

    out = []
    for row in rows:
        if row.get("kind") != "decision_scores" or row.get("site") != site:
            continue
        counts["scores"] += 1
        scores = row.get("scores")
        if not isinstance(scores, dict):
            continue          # 题数超限只记条数，没有明细可用
        entry = scores.get(question)
        if not isinstance(entry, dict):
            if question in (row.get("dropped") or []):
                counts["dropped_by_floor"] += 1
            continue
        if entry.get("choice") != acted:
            counts["stayed_quiet"] += 1
            continue          # 没开口 ⇒ 永远看不到结果，这是审查不是负例
        counts["acted"] += 1
        probability = entry.get("p")
        if (isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not 0.0 <= float(probability) <= 1.0
                or not math.isfinite(float(probability))):
            continue
        counts["with_probability"] += 1
        group, source = _text(row, "group"), _text(row, "source_message_id")
        outgoing = delivered.get((group, source)) if group and source else None
        if not outgoing:
            continue          # 生成成功不等于送达成功
        counts["delivered"] += 1
        action = verdict.get(outgoing)
        if action is None:
            counts["no_reaction"] += 1       # 缺失标签，不是负例
            continue
        if action == WELCOME:
            label = 1
        elif action == UNWELCOME:
            label = 0
        else:
            counts["other_verdict"] += 1     # 谈内容不谈打扰，不计
            continue
        counts["labelled"] += 1
        counts["welcome" if label else "unwelcome"] += 1
        out.append({"p": float(probability), "y": label,
                    "margin": entry.get("margin"), "ts": row.get("ts"),
                    "group": group})
    return out, counts, diagnostics


# ── 刻度 ──

def _brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def _ece(ps, ys, bins=ECE_BINS):
    total = 0.0
    for index in range(bins):
        lo, hi = index / bins, (index + 1) / bins
        chunk = [k for k, p in enumerate(ps)
                 if lo <= p < hi or (index == bins - 1 and p == 1.0)]
        if not chunk:
            continue
        mean_p = sum(ps[k] for k in chunk) / len(chunk)
        mean_y = sum(ys[k] for k in chunk) / len(chunk)
        total += len(chunk) / len(ps) * abs(mean_p - mean_y)
    return total


def _ece_null(ps, draws=NULL_DRAWS, seed=0):
    """完美校准下 ECE 的期望与 95 分位：从 Bernoulli(p) 反复抽标签。

    几百条样本、10 个箱时 ECE 本来就会偏正；不给这个参照就读不懂那个数。
    """
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        fake = [1 if rng.random() < p else 0 for p in ps]
        values.append(_ece(ps, fake))
    values.sort()
    return {"mean": sum(values) / len(values),
            "p95": values[min(len(values) - 1, int(0.95 * len(values)))]}


def _logit(p, eps=1e-3):
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def _platt_bootstrap(ps, ys, draws=200, seed=0):
    """斜率的 95% 自助区间。

    两参数拟合在几十条样本上很不稳 —— 只报点估计会把噪声当成结论。
    区间跨过 0 时**不存在可用的单调关系**，那不是「需要压缩」，
    是「这批标签还看不出刻度该怎么改」。
    """
    rng = random.Random(seed)
    n = len(ps)
    slopes = []
    for _ in range(draws):
        idx = [rng.randrange(n) for _ in range(n)]
        if len({ys[k] for k in idx}) < 2:
            continue          # 重抽到全同标签，拟合无意义
        fit = _platt([ps[k] for k in idx], [ys[k] for k in idx], epochs=600,
                     lr=0.5)
        slopes.append(fit["slope"])
    if len(slopes) < 20:
        return None
    slopes.sort()
    return {"low": slopes[int(0.025 * len(slopes))],
            "high": slopes[min(len(slopes) - 1, int(0.975 * len(slopes)))],
            "draws": len(slopes)}


def _platt(ps, ys, epochs=4000, lr=0.3, l2=1.0):
    """两参数重标：logit(p) 上的逻辑回归。斜率是「自信里有多少是真的」。"""
    xs = [_logit(p) for p in ps]
    a, b = 0.0, 1.0
    for _ in range(epochs):
        ga = gb = 0.0
        for x, y in zip(xs, ys):
            z = a + b * x
            q = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            error = q - y
            ga += error
            gb += error * x
        n = len(xs)
        a -= lr * ga / n
        b -= lr * (gb / n + l2 * b / n)
    return {"intercept": a, "slope": b}


def _reliability(ps, ys, bins=5):
    table = []
    for index in range(bins):
        lo, hi = index / bins, (index + 1) / bins
        chunk = [k for k, p in enumerate(ps)
                 if lo <= p < hi or (index == bins - 1 and p == 1.0)]
        table.append({"from": lo, "to": hi, "n": len(chunk),
                      "mean_predicted": (sum(ps[k] for k in chunk) / len(chunk)
                                         if chunk else None),
                      "observed": (sum(ys[k] for k in chunk) / len(chunk)
                                   if chunk else None)})
    return table


def report(log_dir, site="banter"):
    """只读报告。样本不足时报条数，不报编出来的比例。"""
    if site not in SITES:
        raise ValueError("unknown site")
    rowset, counts, diagnostics = pairs(log_dir, site=site)
    out = {"schema": SCHEMA_OUT, "site": site, **diagnostics, "counts": counts,
           "label_rate": (counts["labelled"] / counts["delivered"]
                          if counts["delivered"] else None),
           "enough_to_fit": counts["labelled"] >= MIN_FOR_FIT,
           "min_for_fit": MIN_FOR_FIT}
    if not rowset:
        out.update(base_rate=None, mean_predicted=None, prevalence_gap=None,
                   brier=None, ece=None, ece_null=None, platt=None,
                   reliability=None)
        return out
    ps = [r["p"] for r in rowset]
    ys = [r["y"] for r in rowset]
    base = sum(ys) / len(ys)
    mean_p = sum(ps) / len(ps)
    out.update(base_rate=base, mean_predicted=mean_p,
               # 这就是群主问的那个量：输出的「该插话」率对不对得上真实比例。
               prevalence_gap=mean_p - base,
               brier=_brier(ps, ys),
               ece=_ece(ps, ys), ece_null=_ece_null(ps),
               reliability=_reliability(ps, ys),
               platt=_platt(ps, ys) if out["enough_to_fit"] else None)
    if out["platt"]:
        out["platt"]["slope_ci"] = _platt_bootstrap(ps, ys)
    return out


def render(out):
    """给人读的一页。每个数字后面都带上它**不**说明什么。"""
    lines = []
    counts = out["counts"]
    lines.append(f"判断刻度核对（site={out['site']}）—— 只读本地互动日志")
    lines.append(
        f"  判断记录 {counts['scores']} 条｜选择开口 {counts['acted']}｜"
        f"确认送达 {counts['delivered']}｜拿到标签 {counts['labelled']}"
        f"（受欢迎 {counts['welcome']} / 被嫌烦 {counts['unwelcome']}）")
    lines.append(
        f"  选择沉默 {counts['stayed_quiet']}（审查，不是负例）｜"
        f"无人反应 {counts['no_reaction']}（缺失标签，不是负例）｜"
        f"只谈内容 {counts['other_verdict']}｜被弃答闸丢掉 "
        f"{counts['dropped_by_floor']}")
    if out["label_rate"] is not None:
        lines.append(f"  标签率 {out['label_rate']:.1%}"
                     "（送达里有多少引出了明确反馈）")
    if out["base_rate"] is None:
        lines.append("  还没有可用的标签对：比例不可得，不是 0%。")
        lines.append(f"  想学一个阈值大约需要 {out['min_for_fit']} 条"
                     "（论文 k=10 已拿到大部分收益）。")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"  实际受欢迎比例      {out['base_rate']:.3f}")
    lines.append(f"  它平均说的概率      {out['mean_predicted']:.3f}")
    lines.append(f"  边缘率偏差          {out['prevalence_gap']:+.3f}"
                 "   ← 正=比现实乐观")
    lines.append(f"  Brier               {out['brier']:.4f}")
    null = out["ece_null"]
    lines.append(f"  ECE                 {out['ece']:.4f}"
                 f"   零假设 {null['mean']:.4f}（95分位 {null['p95']:.4f}）")
    if out["ece"] <= null["p95"]:
        lines.append("    → 没有超过完美校准下的零假设：这个样本量说不出刻度有问题。")
    if out["platt"]:
        slope = out["platt"]["slope"]
        ci = out["platt"].get("slope_ci")
        if ci:
            lines.append(f"  Platt 斜率          {slope:.3f}"
                         f"   95%% 区间 [{ci['low']:.3f}, {ci['high']:.3f}]"
                         .replace("%%", "%"))
            if ci["low"] <= 0.0 <= ci["high"]:
                lines.append("    → 区间跨过 0：这批标签还看不出单调关系，"
                             "**先别重标刻度**，去攒标签。")
            elif ci["high"] < 1.0:
                lines.append("    → 整个区间小于 1：它的 logit 确实需要压缩"
                             "（和离线实测的方向一致）。")
            elif ci["low"] > 1.0:
                lines.append("    → 整个区间大于 1：它反而过于保守，"
                             "需要把 logit 放大（与 Choice 题的已知方向相反，"
                             "值得先核对题型和样本）。")
            else:
                lines.append("    → 区间横跨 1：还说不出该压还是该放。")
        else:
            lines.append(f"  Platt 斜率          {slope:.3f}"
                         "   （样本太少，给不出区间）")
    else:
        lines.append(f"  Platt               标签不足 {out['min_for_fit']} 条，不拟合")
    lines.append("")
    lines.append("  可靠性（预测 → 实际）")
    for row in out["reliability"]:
        if not row["n"]:
            continue
        lines.append(f"    {row['from']:.1f}–{row['to']:.1f}  n={row['n']:4d}"
                     f"   说 {row['mean_predicted']:.3f}"
                     f"   实际 {row['observed']:.3f}")
    lines.append("")
    lines.append("  边界：只覆盖**决定开口**的那些判断；沉默的时刻没有结果可看。")
    lines.append("  无人反应是缺失标签，不是不受欢迎。这是观察记录，不是随机实验，")
    lines.append("  用它学阈值正当，用它声称因果收益不正当。")
    return "\n".join(lines)
