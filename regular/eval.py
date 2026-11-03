# -*- coding: utf-8 -*-
"""用例评测与整天回放（docs/self-iteration.md ③④）：双版本子进程回放 + 确定性检查 + 盲评 + 整天套路对比。

核心设计：
  1. base 和 cand 各起一个 replay_worker 子进程（base 是 git commit 时用临时 git worktree 检出），
     同一份用例 / 同一天的 turns 喂给两边，不在同一进程里用 exec 换提示词。
  2. 能用代码判的检查算通过率（pass_rate）；带 judge 的用例把新旧回复打乱顺序交给不参赛的
     kimi-k3.1-pro 盲评，算胜率（win_rate）并标越线；网络错误自动重试一次，仍失败的那对剔除并单独计数。
     盲评跑之前先预检一次（preflight_judge），并发跑。下降要过单侧检验才算退步，够不上的记「噪声内下降」。
  3. 整天回放（--day）对比整体分布：口头禅片段次数、〔不接〕比例、平均字数、碰敏感词次数、
     家务 / 假车 / 刚醒等套路正则。
  4. 报告写到 data/evals/<时间>.md：退步的用例排最前，稳定头长度每次必记。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from math import comb
from pathlib import Path

import expression
import turns as turns_mod
import wordfilter
from regular import cases as cases_mod
from regular import checkup as checkup_mod
from regular import rules as rules_mod

SCHEMA_OUT = "regular.eval.v1"

DEFAULT_DAY_PATTERNS = {
    "housework": r"洗衣|买菜|厨房|做饭|拖把|漏勺|家政|袜子",
    "fake_car": r"车刚|打火|熄火|早高峰|后备箱",
    "just_woke": r"刚醒|开机|转圈",
}

JUDGE_SYSTEM = """你是 QQ 群聊回复的盲评裁判（不参赛）。
你会看到群友刚才说的话、群聊背景、本条用例的关注点，以及打乱顺序的两条候选回复 A 和 B。
比较哪一条更像自然的真人群友、更贴合关注点，且没有越线（涉政/涉黄/暴露机器编号/官方客服腔）。
只输出一行 JSON：
{"winner": "A" 或 "B" 或 "tie", "crossed": [] 或 ["A"] 或 ["B"], "reason": "二十字以内原因"}"""


@contextlib.contextmanager
def checkout_version(spec, repo_root=None):
    """把版本标识变成代码目录：已经是目录就直接用，否则用 git worktree 临时检出。"""
    repo = Path(repo_root or Path(__file__).resolve().parent.parent).resolve()
    target = Path(spec).expanduser()
    if target.is_dir():
        yield str(target.resolve())
        return
    with tempfile.TemporaryDirectory(prefix="regular-eval-wt-") as tmp:
        wt_dir = os.path.join(tmp, "tree")
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", wt_dir, str(spec)],
            cwd=str(repo), capture_output=True, text=True)
        if proc.returncode != 0:
            raise ValueError(f"git worktree failed for {spec!r}: {proc.stderr.strip()}")
        try:
            yield wt_dir
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", wt_dir],
                           cwd=str(repo), capture_output=True, text=True)


def run_worker_subprocess(code_dir, items, *, n=1, concurrency=6, ablate="",
                          override=None, env_extra=None):
    """起一个子进程跑 regular/replay_worker.py，返回 {"head": ..., "ablated": ..., "results": [...]}。"""
    worker_script = Path(__file__).resolve().parent / "replay_worker.py"
    with tempfile.TemporaryDirectory(prefix="regular-eval-io-") as tmp:
        in_path = os.path.join(tmp, "in.json")
        out_path = os.path.join(tmp, "out.json")
        Path(in_path).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        cmd = [
            sys.executable, str(worker_script),
            "--code-dir", str(code_dir),
            "--n", str(max(1, int(n))),
            "--concurrency", str(max(1, int(concurrency))),
            "--input", in_path,
            "--output", out_path,
        ]
        if ablate:
            cmd += ["--ablate", str(ablate)]
        if override:
            cmd += ["--override-json", json.dumps(override, ensure_ascii=False)]
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(
                f"replay_worker exited with {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
        return json.loads(Path(out_path).read_text(encoding="utf-8"))


# ── 盲评（kimi-k3.1-pro，打乱顺序）──

def _parse_judge_json(raw):
    if not raw:
        return None
    m = re.search(r"\{.*\}", str(raw), flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    winner = str(data.get("winner") or "").strip()
    if winner not in ("A", "B", "tie"):
        return None
    crossed = [x for x in (data.get("crossed") or ()) if x in ("A", "B")]
    return {"winner": winner, "crossed": crossed, "reason": str(data.get("reason") or "")[:80]}


def _judge_endpoint(config):
    """盲评走哪一家：(base_url, key, model)。没配好抛 ValueError，说清楚缺什么。

    只用 EVAL_JUDGE_PROVIDER 自己那一家的地址和凭据，不借别家的 —— 借了就是把一家的
    密钥发到另一家的地址上。
    """
    provider = config.EVAL_JUDGE_PROVIDER
    preset = config.PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise ValueError(f"EVAL_JUDGE_PROVIDER={provider!r} 不是已知的供应商")
    keys = config.load_api_keys(provider)
    if not keys or not preset.get("base_url"):
        raise ValueError(f"盲评供应商 {provider} 没有地址或凭据")
    return preset["base_url"], keys[0], config.EVAL_JUDGE_MODEL


async def _default_judge_call(messages):
    """直接调配置的盲评模型（默认网关上的 cn:kimi-k3.1-pro）。"""
    import config
    import httpx
    from openai import AsyncOpenAI

    base_url, key, model = _judge_endpoint(config)
    async with httpx.AsyncClient(verify=config.SSL_VERIFY,
                                 mounts={"all://127.0.0.1": None, "all://localhost": None},
                                 timeout=config.AI_TIMEOUT_SECONDS) as http:
        client = AsyncOpenAI(api_key=key, base_url=base_url,
                             http_client=http, max_retries=0)
        res = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=400, temperature=0.0)
        if res and res.choices:
            return (res.choices[0].message.content or "").strip()
    return None


def preflight_judge(config, judge_fn=None):
    """跑之前先让盲评答一道送分题：配错了（比如模型名在那一家根本没有）当场报错，
    不要等几百次生成跑完才发现盲评整批被剔掉。"""
    probe = [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": "【群友说】\n在吗\n\n【本条关注点】\n自然\n\n"
                                         "【回复 A】\n在呢\n\n【回复 B】\n您好，请问有什么可以帮您"}]
    call = judge_fn or _default_judge_call
    try:
        raw = asyncio.run(call(probe))
    except Exception as err:   # noqa: BLE001 —— 什么错都要原样告诉跑评测的人
        raise RuntimeError(
            f"盲评调不通（{config.EVAL_JUDGE_PROVIDER} / {config.EVAL_JUDGE_MODEL}）："
            f"{type(err).__name__} {err}") from None
    if _parse_judge_json(raw) is None:
        raise RuntimeError(
            f"盲评答的不是要的 JSON（{config.EVAL_JUDGE_PROVIDER} / {config.EVAL_JUDGE_MODEL}）："
            f"{str(raw)[:80]!r}")


async def judge_pair(case, base_reply, cand_reply, *, judge_fn=None, rng=None, retries=1,
                     swap=None):
    """打乱顺序盲评一对新旧回复。返回 {"winner": "base"|"cand"|"tie"|None, "crossed": [...], "reason": ...}。

    swap 给了就照它摆 A/B（并发评时先统一抽好，结果才可复现），不给就现抽。
    """
    if base_reply is None or cand_reply is None:
        return {"winner": None, "crossed": [], "reason": "generation_error", "error": True}
    if base_reply.strip() == cand_reply.strip():
        return {"winner": "tie", "crossed": [], "reason": "identical", "error": False}
    if swap is None:
        swap = bool((rng or random).getrandbits(1))
    a_text, b_text = (cand_reply, base_reply) if swap else (base_reply, cand_reply)
    turn_kw = (case.get("turn") or {}).get("kwargs") or {}
    user_text = turn_kw.get("user_text") or ""
    if not user_text and case.get("messages"):
        user_text = (case["messages"][-1].get("text") or "")
    ctx = turn_kw.get("context_hint") or ""
    criteria = "；".join(cases_mod.judge_prompts(case.get("checks"))) or "自然、贴题、不越线"
    prompt = (
        f"【群聊背景】\n{ctx or '（无）'}\n\n"
        f"【群友说】\n{user_text}\n\n"
        f"【本条关注点】\n{criteria}\n\n"
        f"【回复 A】\n{a_text}\n\n"
        f"【回复 B】\n{b_text}"
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    call = judge_fn or _default_judge_call
    for _ in range(retries + 1):
        try:
            raw = await call(messages)
        except Exception:
            raw = None
        parsed = _parse_judge_json(raw)
        if parsed is not None:
            map_side = {"A": "cand" if swap else "base",
                        "B": "base" if swap else "cand",
                        "tie": "tie"}
            return {
                "winner": map_side[parsed["winner"]],
                "crossed": [map_side[c] for c in parsed["crossed"]],
                "reason": parsed["reason"],
                "error": False,
            }
    return {"winner": None, "crossed": [], "reason": "judge_error", "error": True}


# ── 用例对比与整天指标 ──

# 退步要过一道单侧检验：每条只抽几次，同一版本跑两遍也会这次 4:0、那次 3:1。
# 0.1 的意思：每条抽 4 次时，至少掉 3 次（4→1、3→0）才算退步；盲评旧版 4:0（平局不计）才算判输。
# 2026-11-04 在 Pro 上拿同一版本对同一版本跑（-n 2），盲评 2:0 就出现了 —— 0.25 会把它报成退步。
# 够不上的下降不藏起来，单列成「噪声内下降」，排在退步后面。越线不看次数，一次就算退步。
REGRESSION_P = 0.1


def drop_p_value(b_pass, b_total, c_pass, c_total):
    """新版通过数少到这个程度的单侧概率（Fisher 精确检验）：两版其实一样好时，碰巧抽成这样的可能性。"""
    if not b_total or not c_total:
        return 1.0
    passed, total = b_pass + c_pass, b_total + c_total
    return sum(comb(passed, k) * comb(total - passed, c_total - k)
               for k in range(c_pass + 1)) / comb(total, c_total)


def judge_loss_p_value(base_wins, cand_wins):
    """盲评里旧版赢这么多场的单侧符号检验（平局不计）。"""
    n = base_wins + cand_wins
    if not n:
        return 1.0
    return sum(comb(n, k) for k in range(base_wins, n + 1)) / 2 ** n


async def evaluate_cases(cases, base_results, cand_results, *, judge_fn=None, rng=None,
                         concurrency=6):
    """汇总每条用例的确定性通过率、盲评胜率，标出退步（regressed）、噪声内下降（wobbled）与改进。

    盲评先把每一对的 A/B 抽好，再并发跑（同时最多 concurrency 个）。
    """
    rng = rng or random
    base_map = {r["id"]: r for r in base_results}
    cand_map = {r["id"]: r for r in cand_results}
    plan = []
    for case in cases:
        cid = case["id"]
        b_samples = (base_map.get(cid) or {}).get("samples") or []
        c_samples = (cand_map.get(cid) or {}).get("samples") or []
        pairs = list(zip(b_samples, c_samples))
        plan.append((case, b_samples, c_samples, bool(cases_mod.judge_prompts(case.get("checks"))),
                     pairs, [bool(rng.getrandbits(1)) for _ in pairs]))
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def judge_one(case, b_s, c_s, swap):
        if b_s is None or c_s is None:
            return None
        async with sem:
            return await judge_pair(case, b_s, c_s, judge_fn=judge_fn, swap=swap)

    verdicts = iter(await asyncio.gather(*(
        judge_one(case, b_s, c_s, swap)
        for case, _, _, has_judge, pairs, swaps in plan if has_judge
        for (b_s, c_s), swap in zip(pairs, swaps))))

    rows = []
    totals = {
        "cases": len(cases),
        "regressed": 0,
        "wobbled": 0,
        "improved": 0,
        "base_passed_samples": 0,
        "base_total_samples": 0,
        "cand_passed_samples": 0,
        "cand_total_samples": 0,
        "judge_cand_wins": 0,
        "judge_base_wins": 0,
        "judge_ties": 0,
        "judge_errors": 0,
        "dropped_pairs": 0,
        "cand_crossed": 0,
        "base_crossed": 0,
    }
    for case, b_samples, c_samples, has_judge, pairs, _ in plan:
        cid = case["id"]
        checks = case.get("checks") or []
        b_checks = [cases_mod.check_reply(s, checks) for s in b_samples if s is not None]
        c_checks = [cases_mod.check_reply(s, checks) for s in c_samples if s is not None]
        b_pass = sum(1 for r in b_checks if r["passed"])
        c_pass = sum(1 for r in c_checks if r["passed"])
        b_rate = (b_pass / len(b_checks)) if b_checks else 0.0
        c_rate = (c_pass / len(c_checks)) if c_checks else 0.0
        totals["base_passed_samples"] += b_pass
        totals["base_total_samples"] += len(b_checks)
        totals["cand_passed_samples"] += c_pass
        totals["cand_total_samples"] += len(c_checks)

        j_wins = {"cand": 0, "base": 0, "tie": 0, "dropped": 0}
        j_reasons = []
        c_crossed = b_crossed = 0
        for b_s, c_s in pairs:
            verdict = next(verdicts) if has_judge else None
            if b_s is None or c_s is None:
                j_wins["dropped"] += 1
                totals["dropped_pairs"] += 1
                continue
            if not has_judge:
                continue
            if verdict is None or verdict.get("error") or verdict["winner"] is None:
                j_wins["dropped"] += 1
                totals["dropped_pairs"] += 1
                totals["judge_errors"] += 1
                continue
            j_wins[verdict["winner"]] += 1
            if verdict["winner"] == "cand":
                totals["judge_cand_wins"] += 1
            elif verdict["winner"] == "base":
                totals["judge_base_wins"] += 1
            else:
                totals["judge_ties"] += 1
            if "cand" in verdict["crossed"]:
                c_crossed += 1
                totals["cand_crossed"] += 1
            if "base" in verdict["crossed"]:
                b_crossed += 1
                totals["base_crossed"] += 1
            if verdict.get("reason"):
                j_reasons.append(verdict["reason"])

        # 逐条核对有效样本与评审是否完整（任一侧 0 样本、出现 None 样本、或盲评未完整完成都不算证据完整）
        b_valid = len(b_checks)
        c_valid = len(c_checks)
        judged_pairs = j_wins["cand"] + j_wins["base"] + j_wins["tie"]
        evidence_complete = bool(
            b_valid > 0
            and c_valid > 0
            and b_valid == len(b_samples)
            and c_valid == len(c_samples)
            and (not has_judge or (judged_pairs > 0 and j_wins["dropped"] == 0))
        )

        # 退步：新版全部生成失败、盲评判越线、或者下降过了单侧检验；够不上检验的下降记成噪声内下降
        gen_regressed = bool(b_valid > 0 and c_valid == 0)
        det_dropped = bool(b_checks and c_checks and c_rate < b_rate)
        det_p = drop_p_value(b_pass, b_valid, c_pass, c_valid) if det_dropped else 1.0
        judge_lost = bool(has_judge and j_wins["base"] > j_wins["cand"])
        judge_p = judge_loss_p_value(j_wins["base"], j_wins["cand"]) if judge_lost else 1.0
        regressed = bool(gen_regressed or c_crossed > 0
                         or (det_dropped and det_p <= REGRESSION_P)
                         or (judge_lost and judge_p <= REGRESSION_P))
        wobbled = (not regressed) and (det_dropped or judge_lost)
        det_improved = bool(c_checks and c_rate > b_rate)
        judge_improved = bool(has_judge and j_wins["cand"] > j_wins["base"] and c_crossed == 0)
        improved = (not regressed) and (not wobbled) and (det_improved or judge_improved)
        if regressed:
            totals["regressed"] += 1
        elif wobbled:
            totals["wobbled"] += 1
        elif improved:
            totals["improved"] += 1
        failed_reasons = [d["reason"] for r in c_checks for d in r["details"]
                          if not d["passed"] and d.get("reason")]
        if gen_regressed:
            failed_reasons.append("新版生成失败")
        elif not evidence_complete:
            failed_reasons.append("样本或盲评不完整")
        if c_crossed > 0:
            failed_reasons.append(f"盲评判越线×{c_crossed}")
        if judge_lost:
            failed_reasons.append(f"盲评判输 ({j_wins['cand']}/{j_wins['base']}/{j_wins['tie']})")
            failed_reasons.extend(j_reasons)
        if wobbled:
            failed_reasons.insert(0, f"抽样噪声内（p={min(det_p, judge_p):.2f}）")
        rows.append({
            "id": cid,
            "incident": case.get("incident") or "",
            "tags": case.get("tags") or [],
            "depth": case.get("depth") or "generate",
            "base_pass": f"{b_pass}/{len(b_checks)}",
            "cand_pass": f"{c_pass}/{len(c_checks)}",
            "base_pass_rate": round(b_rate, 3),
            "cand_pass_rate": round(c_rate, 3),
            "regressed": regressed,
            "wobbled": wobbled,
            "improved": improved,
            "p_drop": round(det_p, 3) if det_dropped else None,
            "p_judge_loss": round(judge_p, 3) if judge_lost else None,
            "evidence_complete": evidence_complete,
            "judge": j_wins if has_judge else None,
            "judge_reasons": j_reasons[:3],
            "failed_reasons": list(dict.fromkeys(failed_reasons))[:3],
            "base_sample": next((s for s in b_samples if s is not None), ""),
            "cand_sample": next((s for s in c_samples if s is not None), ""),
        })

    # 退步排最前，其次噪声内下降，再次改进，其余按 id
    rows.sort(key=lambda r: (0 if r["regressed"] else 1 if r["wobbled"] else 2 if r["improved"] else 3,
                             r["id"]))
    totals["incomplete_cases"] = sum(1 for r in rows if not r.get("evidence_complete"))
    totals["base_pass_rate"] = round(
        totals["base_passed_samples"] / totals["base_total_samples"], 3
    ) if totals["base_total_samples"] else None
    totals["cand_pass_rate"] = round(
        totals["cand_passed_samples"] / totals["cand_total_samples"], 3
    ) if totals["cand_total_samples"] else None
    judged = totals["judge_cand_wins"] + totals["judge_base_wins"] + totals["judge_ties"]
    totals["cand_win_rate"] = round(totals["judge_cand_wins"] / judged, 3) if judged else None
    return rows, totals


def day_metrics(samples, *, patterns=None):
    """整天回放的整体指标：口头禅片段、〔不接〕比例、平均长度、敏感词次数、套路正则命中数。"""
    pats = dict(DEFAULT_DAY_PATTERNS)
    if patterns:
        pats.update(patterns)
    valid = [str(s) for s in samples if s is not None]
    if not valid:
        return {"total": 0, "errors": len(samples), "pass_count": 0, "pass_rate": 0.0,
                "avg_chars": 0.0, "sensitive_hits": 0, "patterns": {k: 0 for k in pats},
                "catchphrases": []}
    pass_count = 0
    spoken_lengths = []
    sensitive_hits = 0
    pat_counts = {k: 0 for k in pats}
    gram_counter = Counter()
    for raw in valid:
        said = expression.parse(raw)
        if said.kind == "pass":
            pass_count += 1
            continue
        text = said.text or raw
        spoken_lengths.append(len(text))
        if wordfilter.has_hit(raw):
            sensitive_hits += 1
        for name, rx in pats.items():
            if re.search(rx, raw, flags=re.S):
                pat_counts[name] += 1
        for g in checkup_mod._grams(text):
            gram_counter[g] += 1

    # 3 次以上的 4~6 字片段，去掉被长片段覆盖的子串
    raw_grams = [(g, c) for g, c in gram_counter.items() if c >= 3]
    raw_grams.sort(key=lambda x: (-len(x[0]), -x[1]))
    kept = []
    for g, c in raw_grams:
        if any(g in k[0] and k[1] >= 0.8 * c for k in kept):
            continue
        kept.append((g, c))
    kept.sort(key=lambda x: -x[1])
    return {
        "total": len(valid),
        "errors": len(samples) - len(valid),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(valid), 3),
        "avg_chars": round(sum(spoken_lengths) / len(spoken_lengths), 1) if spoken_lengths else 0.0,
        "sensitive_hits": sensitive_hits,
        "patterns": pat_counts,
        "catchphrases": [{"gram": g, "count": c} for g, c in kept[:8]],
    }


def render_report(report):
    """渲染 Markdown 评测报告：退步排最前，包含稳定头长度对比、盲评胜率与整天指标。"""
    b_head = (report.get("base_head") or {}).get("total_chars", "?")
    c_head = (report.get("cand_head") or {}).get("total_chars", "?")
    delta = (c_head - b_head) if isinstance(b_head, int) and isinstance(c_head, int) else 0
    delta_str = f"（{delta:+d} 字）" if isinstance(b_head, int) and isinstance(c_head, int) else ""
    lines = [
        f"# 评测报告 · {report['base']} → {report['cand']}",
        "",
        f"- 时间：{report['created_at']}",
        f"- 稳定头长度：`{b_head}` → `{c_head}` 字{delta_str}（规则数：{(report.get('cand_head') or {}).get('rules_count', '?')}）",
    ]
    if report.get("ablate"):
        ab = report["ablate"]
        verdict = {
            "delete": "可删除候选（拿掉后挂的用例和全量用例都没变差）",
            "keep": "仍需保留（拿掉后有退步）",
            "unsure": "说不准（拿掉后有下降，但都在抽样噪声内；加大 -n 再跑）",
            "no_cases": f"判断不了（评测集里没有这条规则挂的用例：{', '.join(ab.get('cases') or ()) or '—'}）",
            "incomplete": "判断不了（有用例没生成出来，或盲评没跑完）",
        }.get(ab.get("verdict"), "—")
        lines.append(f"- 规则消融：`{ab['id']}`（-{ab.get('chars', '?')} 字）→ **{verdict}**")
    t = report.get("totals") or {}
    if t.get("cases"):
        lines.append(
            f"- 用例：{t['cases']} 条（每条抽 {report.get('n', 1)} 次）| "
            f"通过率：`{t.get('base_pass_rate')}` → `{t.get('cand_pass_rate')}` | "
            f"退步：**{t['regressed']}** 条 | 噪声内下降：{t.get('wobbled', 0)} 条 | 改进：{t['improved']} 条"
        )
        if t.get("cand_win_rate") is not None:
            lines.append(
                f"- 盲评：新胜 {t['judge_cand_wins']} / 旧胜 {t['judge_base_wins']} / 平 {t['judge_ties']} "
                f"（新胜率 `{t['cand_win_rate']:.0%}`，越线 新 {t['cand_crossed']} / 旧 {t['base_crossed']}）"
            )
        if t.get("dropped_pairs"):
            lines.append(f"- 网络/生成错误剔除对数：{t['dropped_pairs']}"
                         + (f"（其中盲评失败 {t['judge_errors']} 对）" if t.get("judge_errors") else ""))

    case_rows = report.get("cases") or []
    for flag, title in (
            ("regressed", "退步用例（旧版过、新版不过，上线前必须交代）"),
            ("wobbled", f"噪声内下降（掉了，但每条只抽 {report.get('n', 1)} 次，分不清是不是抽样噪声；"
                        "要定论就加大 -n 只跑这几条）")):
        picked = [r for r in case_rows if r.get(flag)]
        if not picked:
            continue
        lines += ["", f"## {title}", "",
                  "| 用例 | 事故出处 | 旧通过 | 新通过 | 失败原因 |",
                  "| --- | --- | --- | --- | --- |"]
        for r in picked:
            why = "；".join(r["failed_reasons"]) or "—"
            lines.append(f"| `{r['id']}` | {r['incident'] or '—'} | {r['base_pass']} | {r['cand_pass']} | {why} |")

    if case_rows:
        lines += ["", "## 全部用例", "",
                  "| 状态 | 用例 | 旧通过 | 新通过 | 盲评 (新/旧/平) | 备注 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in case_rows:
            state = ("REGRESSED" if r["regressed"] else "WOBBLE" if r.get("wobbled")
                     else "IMPROVED" if r["improved"] else "OK")
            j = r["judge"]
            j_str = f"{j['cand']}/{j['base']}/{j['tie']}" if j else "—"
            note = "；".join(r["failed_reasons"] or r["judge_reasons"]) or "—"
            lines.append(f"| {state} | `{r['id']}` | {r['base_pass']} | {r['cand_pass']} | {j_str} | {note} |")

    day = report.get("day_eval")
    if day:
        bm, cm = day["base"], day["cand"]
        lines += [
            "",
            f"## 整天回放（{day['day']}，共 {bm['total']} 轮）",
            "",
            "| 指标 | Base | Cand |",
            "| --- | --- | --- |",
            f"| 〔不接〕比例 | {bm['pass_rate']:.1%} ({bm['pass_count']}) | {cm['pass_rate']:.1%} ({cm['pass_count']}) |",
            f"| 平均字数 | {bm['avg_chars']} | {cm['avg_chars']} |",
            f"| 碰敏感词次数 | {bm['sensitive_hits']} | {cm['sensitive_hits']} |",
        ]
        for pat_name in bm["patterns"]:
            lines.append(f"| 套路：{pat_name} | {bm['patterns'][pat_name]} | {cm['patterns'].get(pat_name, 0)} |")
        b_grams = "、".join(f"「{x['gram']}」×{x['count']}" for x in bm["catchphrases"][:5]) or "无"
        c_grams = "、".join(f"「{x['gram']}」×{x['count']}" for x in cm["catchphrases"][:5]) or "无"
        lines.append(f"| 高频口头禅 | {b_grams} | {c_grams} |")

    return "\n".join(lines) + "\n"


def write_report(report, eval_dir):
    base = Path(eval_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = report.get("stamp") or time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    md_path = base / f"{stamp}.md"
    json_path = base / f"{stamp}.json"
    md_path.write_text(render_report(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path


def run_eval(config, *, base=".", cand=".", cases_selector="all", day=None,
             n=4, ablate="", patterns=None, judge_fn=None, worker_fn=None,
             write=True):
    """执行一次完整评测（用例集 和/或 整天回放），返回 report dict。"""
    worker = worker_fn or run_worker_subprocess
    concurrency = getattr(config, "EVAL_CONCURRENCY", 6)
    ablate_info = None
    if ablate:
        rule = rules_mod.get_rule(ablate)
        if rule is None:
            raise KeyError(f"unknown rule id for --ablate: {ablate!r}")
        ablate_info = {"id": rule["id"], "chars": rule["chars"], "cases": rule["cases"]}

    cases = []
    if not day or cases_selector != "none":
        if not day or cases_selector != "all":
            cases = cases_mod.load_cases(config.CASES_DIR, selector=cases_selector,
                                         include_seeds=True)
    if judge_fn is None and any(cases_mod.judge_prompts(c.get("checks")) for c in cases):
        preflight_judge(config)

    with checkout_version(base) as base_dir, checkout_version(cand) as cand_dir:
        base_head = cand_head = None
        case_rows, totals = [], {"cases": 0, "regressed": 0, "improved": 0}
        if cases:
            b_out = worker(base_dir, cases, n=n, concurrency=concurrency)
            c_out = worker(cand_dir, cases, n=n, concurrency=concurrency, ablate=ablate)
            base_head, cand_head = b_out.get("head"), c_out.get("head")
            case_rows, totals = asyncio.run(
                evaluate_cases(cases, b_out["results"], c_out["results"], judge_fn=judge_fn,
                               concurrency=getattr(config, "EVAL_JUDGE_CONCURRENCY", 2)))
            if ablate_info is not None:
                rule_obj = rules_mod.get_rule(ablate)
                has_linked = any(rules_mod.matches_case(rule_obj, r["id"], r.get("tags"))
                                 for r in case_rows)
                all_evidence_complete = bool(case_rows) and all(
                    r.get("evidence_complete") for r in case_rows
                )
                per_case_judge_ok = all(
                    r["judge"]["base"] <= r["judge"]["cand"]
                    for r in case_rows if r.get("judge")
                )
                has_any_judge = any(cases_mod.judge_prompts(c.get("checks")) for c in cases)
                judged_total = (totals.get("judge_cand_wins", 0)
                                + totals.get("judge_base_wins", 0)
                                + totals.get("judge_ties", 0))
                judge_ok = per_case_judge_ok and ((not has_any_judge) or (
                    judged_total > 0
                    and totals.get("judge_base_wins", 0) <= totals.get("judge_cand_wins", 0)
                ))
                # 删规则宁严勿松：任何一条掉了（哪怕在噪声内）都不推荐删，只是说法不同
                if not has_linked:
                    verdict = "no_cases"
                elif not all_evidence_complete:
                    verdict = "incomplete"
                elif totals["regressed"] or totals.get("cand_crossed", 0) or not judge_ok:
                    verdict = "keep"
                elif totals.get("wobbled", 0):
                    verdict = "unsure"
                else:
                    verdict = "delete"
                ablate_info["verdict"] = verdict
                ablate_info["incomplete_evidence"] = verdict in ("no_cases", "incomplete")
                ablate_info["deletion_candidate"] = verdict == "delete"

        day_eval = None
        if day:
            resolved_day = checkup_mod.resolve_day(day)
            day_turns = [t for t in turns_mod.read_day(config.TURN_LOG_DIR, resolved_day)
                         if t.get("kind") == "reply"]
            b_day = worker(base_dir, day_turns, n=1, concurrency=concurrency)
            c_day = worker(cand_dir, day_turns, n=1, concurrency=concurrency, ablate=ablate)
            base_head = base_head or b_day.get("head")
            cand_head = cand_head or c_day.get("head")
            b_samples = [r["samples"][0] if r.get("samples") else None for r in b_day["results"]]
            c_samples = [r["samples"][0] if r.get("samples") else None for r in c_day["results"]]
            day_eval = {
                "day": resolved_day,
                "base": day_metrics(b_samples, patterns=patterns),
                "cand": day_metrics(c_samples, patterns=patterns),
            }

    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    report = {
        "schema": SCHEMA_OUT,
        "stamp": stamp,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "base": base,
        "cand": f"{cand} (ablate:{ablate})" if ablate else cand,
        "n": int(n),
        "base_head": base_head,
        "cand_head": cand_head,
        "ablate": ablate_info,
        "totals": totals,
        "cases": case_rows,
        "day_eval": day_eval,
    }
    if write:
        report["report_path"] = str(write_report(report, config.EVAL_DIR))
    return report


# ── 窄口子措辞自动搜索（docs/self-iteration.md 第 6、7 节）──

NARROW_TARGETS = {
    "remind": {
        "attr": "PROMPT_REMIND_RULES",
        "cases": "remind",
        "must_keep": ("〔提醒", "〔取消提醒〕", "{bot}"),
        "description": "到点提醒的时间格式与一人一条规则（代码直接判时间与标记）",
    },
    "banter": {
        "attr": "PROMPT_BANTER_NOTE",
        "cases": "banter",
        "must_keep": ("〔不接〕",),
        "description": "没叫它时接不接的判断说明（代码直接判 20 条带标签回放）",
    },
}

REWRITE_SYSTEM = """你是提示词精炼助手。把给定的一小段规则改写成更清楚、更紧凑的版本。
硬约束：
1. 必须原样保留所有机器协议标记（如 〔提醒 …〕、〔取消提醒〕、〔不接〕、{bot} 占位符）。
2. 不增加新规矩，不写客套话，尽量比原版更短或持平。
只输出改写后的那一段正文，不要加解释或代码块围栏。"""


async def _default_propose_variants(original, must_keep, k):
    import config
    import providers
    variants = []
    for i in range(k):
        msg = [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": (
                f"必须保留的标记：{'、'.join(must_keep)}\n"
                f"变体编号：{i + 1}/{k}\n\n原规则段落：\n{original}"
            )},
        ]
        out = await providers.call_model(msg, config.AI_MAX_TOKENS, tier="chat")
        if out and all(tok in out for tok in must_keep):
            variants.append(out.strip())
    return variants


def search_wording(config, target="remind", *, k=6, n=2, propose_fn=None,
                   worker_fn=None, write=True):
    """只对有确定性代码检查的窄口子（remind / banter）搜索更优措辞，输出报告交给人批准。"""
    if target not in NARROW_TARGETS:
        raise ValueError(
            f"search_wording only supports narrow deterministic targets {tuple(NARROW_TARGETS)}, got {target!r}")
    spec = NARROW_TARGETS[target]
    import prompts
    original = getattr(prompts, spec["attr"])
    cases = cases_mod.load_cases(config.CASES_DIR, selector=spec["cases"], include_seeds=True)
    # 校验每一条都有代码能判对错的确定性检查（不靠纯盲评搜措辞）
    deterministic = [
        c for c in cases
        if any(any(k in chk for k in ("must", "must_not", "tag_present", "tag_absent",
                                      "remind_time", "replied", "max_chars"))
               for chk in (c.get("checks") or ()))
    ]
    if not deterministic:
        raise ValueError(f"target {target!r} has no deterministic cases")

    worker = worker_fn or run_worker_subprocess
    concurrency = getattr(config, "EVAL_CONCURRENCY", 6)
    proposer = propose_fn or (lambda orig, keep, count: asyncio.run(
        _default_propose_variants(orig, keep, count)))
    proposed = proposer(original, spec["must_keep"], int(k))
    candidates = [("baseline", original)]
    for idx, text in enumerate(proposed, 1):
        if text and all(tok in text for tok in spec["must_keep"]):
            candidates.append((f"cand-{idx:02d}", "\n\n" + text.strip()))

    code_dir = str(Path(__file__).resolve().parent.parent)
    scored = []
    for name, text in candidates:
        override = None if name == "baseline" else {spec["attr"]: text}
        out = worker(code_dir, deterministic, n=n, concurrency=concurrency, override=override)
        passed = total = 0
        for case, res in zip(deterministic, out["results"]):
            for sample in res.get("samples") or ():
                if sample is None:
                    continue
                total += 1
                if cases_mod.check_reply(sample, case.get("checks"))["passed"]:
                    passed += 1
        rate = round(passed / total, 4) if total else 0.0
        scored.append({
            "name": name,
            "pass_rate": rate,
            "passed": f"{passed}/{total}",
            "chars": len(text.strip()),
            "text": text.strip(),
        })

    # 先比确定性通过率，打平挑字数更短的（抑制稳定头膨胀）
    scored.sort(key=lambda r: (-r["pass_rate"], r["chars"], r["name"] != "baseline"))
    best = scored[0]
    base_row = next(r for r in scored if r["name"] == "baseline")
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    report = {
        "schema": "regular.prompt-search.v1",
        "target": target,
        "attr": spec["attr"],
        "cases_count": len(deterministic),
        "n": int(n),
        "baseline": base_row,
        "best": best,
        "candidates": scored,
    }
    if write:
        base_dir = Path(config.EVAL_DIR)
        base_dir.mkdir(parents=True, exist_ok=True)
        md_path = base_dir / f"search-{target}-{stamp}.md"
        md_path.write_text(render_search_report(report), encoding="utf-8")
        (base_dir / f"search-{target}-{stamp}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(md_path)
    return report


def render_search_report(report):
    lines = [
        f"# 窄口子措辞搜索 · {report['target']} (`{report['attr']}`)",
        "",
        f"- 确定性用例：{report['cases_count']} 条（每条抽 {report['n']} 次）",
        f"- 基线通过率：`{report['baseline']['pass_rate']:.1%}`（{report['baseline']['chars']} 字）",
        f"- 最优候选：`{report['best']['name']}` · 通过率 `{report['best']['pass_rate']:.1%}`（{report['best']['chars']} 字）",
        "- 说明：仅输出候选供人批准，不会自动写入 `prompts.py`。",
        "",
        "| 排名 | 候选 | 通过率 | 通过样本 | 字数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(report["candidates"], 1):
        lines.append(
            f"| {idx} | `{row['name']}` | {row['pass_rate']:.1%} | {row['passed']} | {row['chars']} |"
        )
    lines += ["", "## 最优候选正文", "", "```", report["best"]["text"], "```", ""]
    return "\n".join(lines)

