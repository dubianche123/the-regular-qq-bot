# -*- coding: utf-8 -*-
"""用例评测与整天回放（docs/self-iteration.md ③④）：双版本子进程回放 + 确定性检查 + 盲评 + 整天套路对比。

核心设计：
  1. base 和 cand 各起一个 replay_worker 子进程（base 是 git commit 时用临时 git worktree 检出），
     同一份用例 / 同一天的 turns 喂给两边，不在同一进程里用 exec 换提示词。
  2. 能用代码判的检查算通过率（pass_rate）；带 judge 的用例把新旧回复打乱顺序交给不参赛的
     kimi-k3.1-pro 盲评，算胜率（win_rate）并标越线；网络错误自动重试一次，仍失败的那对剔除并单独计数，
     之后可以 --rejudge 只重评这几对。盲评跑之前先预检一次（preflight_judge），并发跑。
     下降和上升都要过同一道单侧检验，才算退步 / 改进；够不上的记「噪声内下降 / 上升」。
  2'. 用例里冻着录制时的提示词原文（某段注记就是 prompts 里某个常量的原文）：回放前认出来，
     每个版本换成自己的那份（mark_frozen_prompts → replay_worker），改了措辞才真的测得到。
  3. 整天回放（--day）对比整体分布：口头禅片段次数、〔不接〕比例、平均字数、碰敏感词次数、
     家务 / 假车 / 刚醒等套路正则。
  4. 报告写到 data/evals/<时间>.md：先一句结论，退步的用例排最前，稳定头长度每次必记，
     末尾逐条摆出新旧样本和盲评理由；同名 .json 存全部样本。
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import functools
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
from urllib.parse import urlsplit

import expression
import turns as turns_mod
import wordfilter
from regular import cases as cases_mod
from regular import checkup as checkup_mod
from regular import rules as rules_mod

SCHEMA_OUT = "regular.eval.v1"


class PaidEvaluationRequired(RuntimeError):
    """选中的评测包含模型请求，调用方尚未明确授权。"""


DEFAULT_DAY_PATTERNS = {
    "housework": r"洗衣|买菜|厨房|做饭|拖把|漏勺|家政|袜子",
    "fake_car": r"车刚|打火|熄火|早高峰|后备箱",
    "just_woke": r"刚醒|开机|转圈",
}

JUDGE_SYSTEM = """你是 QQ 群聊回复的盲评裁判（不参赛）。「小王」是群里的机器人，两条候选回复都是它这一轮可能说的话。
你会看到群聊背景、小王最近几轮对话、群友刚才说的话、本条用例的关注点，以及打乱顺序的两条候选 A 和 B。
先看哪一条更符合本条关注点；关注点上分不出高下，再比哪一条更像自然的真人群友。
候选是「〔不接〕」表示小王这一轮不说话：按关注点判断沉默合不合适，不要因为没有内容就判它输。
越线（涉政/涉黄/暴露机器编号/官方客服腔）的标进 crossed。
只输出一行 JSON：
{"winner": "A" 或 "B" 或 "tie", "crossed": [] 或 ["A"] 或 ["B"], "reason": "二十字以内原因"}"""

JUDGE_HISTORY_TURNS = 4   # 盲评看小王最近几轮对话：关注点常常是「别重复上一轮」，看不见上一轮就没法判


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


EXECUTION_EVIDENCE_VERSION = 1
_EVENT_KINDS = {"incoming", "generation_started", "generation_finished", "reply_waiting",
                "fixed_delivery", "first_released", "delivery", "decision", "lookup",
                "query", "proposal", "dropped", "media"}
_DELIVERY_STATES = {"delivered", "unconfirmed", "unknown", "queued", "failed", "rejected",
                    "expired", "superseded"}
_QUERY_STATES = {"proposed", "started", "running", "background", "done", "completed", "failed"}
_DROP_REASONS = {"stale", "said_more", "same_member_continued", "self_spoke", "superseded"}


def execution_evidence(trace):
    """唯一的外发投影：只保留有限状态/次数，不发送原始消息、编号或异常。"""
    if not isinstance(trace, dict):
        return None
    projected = trace.get("version") == EXECUTION_EVIDENCE_VERSION and isinstance(trace.get("event_order"), list)
    events = list(trace.get("event_order") if projected else trace.get("events") or ())
    mixed_streams = bool(trace.get("timeline_events"))
    if not any(isinstance(e, dict) and e.get("kind") == "delivery" for e in events):
        events += [dict(e, kind="delivery") for e in trace.get("deliveries") or () if isinstance(e, dict)]
    events += list(trace.get("timeline_events") or ())
    timed = events and all(isinstance(e, dict) and type(e.get("ts")) in (int, float) for e in events)
    if not projected and timed:
        events.sort(key=lambda e: e["ts"])
    ordered = bool(trace.get("event_order_recorded", True)) if projected else bool(timed or not mixed_streams)
    counts = {"deliveries": {}, "queries": {}, "withdrawals": {}}
    order = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if not isinstance(kind, str) or kind not in _EVENT_KINDS:
            continue
        status = event.get("status")
        status = status if isinstance(status, str) else None
        label = {"kind": kind}
        index = event.get("input", 0) - 1 if projected and type(event.get("input")) is int else event.get("message_index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < 100:
            label["input"] = index + 1
        if kind == "delivery" and status in _DELIVERY_STATES:
            counts["deliveries"][status] = counts["deliveries"].get(status, 0) + 1
            label["status"] = status
        medium = event.get("medium")
        if medium in ("text", "voice", "image", "animation"):
            label["medium"] = medium
        if kind in ("lookup", "query", "proposal") and status in _QUERY_STATES:
            status = "done" if status == "completed" else status
            counts["queries"][status] = counts["queries"].get(status, 0) + 1
            label["status"] = status
        reason = event.get("reason")
        if kind in ("decision", "dropped") and isinstance(reason, str) and reason in _DROP_REASONS:
            counts["withdrawals"][reason] = counts["withdrawals"].get(reason, 0) + 1
            label["reason"] = reason
        order.append(label)
    if projected:
        for section, allowed in (("deliveries", _DELIVERY_STATES), ("queries", _QUERY_STATES),
                                 ("withdrawals", _DROP_REASONS)):
            section_counts = trace.get(section)
            section_counts = section_counts if isinstance(section_counts, dict) else {}
            counts[section] = {k: v for k, v in section_counts.items()
                               if k in allowed and type(v) is int and 0 <= v <= 100000}
    mode = trace.get("query_mode")
    return {"version": EXECUTION_EVIDENCE_VERSION,
            "query_mode": mode if mode in ("live", "fixture", "disabled") else "unknown",
            **counts, "event_order_recorded": ordered, "event_order": order[:80] if ordered else []}


def _same_reply_and_evidence(base_reply, cand_reply, base_evidence=None, cand_evidence=None):
    return (base_reply is not None and cand_reply is not None
            and base_reply.strip() == cand_reply.strip() and base_evidence == cand_evidence)


def _judge_messages(case, base_reply, cand_reply, swap, base_evidence, cand_evidence):
    a_text, b_text = (cand_reply, base_reply) if swap else (base_reply, cand_reply)
    a_evidence, b_evidence = ((cand_evidence, base_evidence) if swap else
                              (base_evidence, cand_evidence))
    turn = case.get("turn") or {}
    turn_kw = turn.get("kwargs") or {}
    scene = case.get("judge_context")
    scene = scene if isinstance(scene, dict) else {}
    user_text = scene.get("user_text", turn_kw.get("user_text")) or ""
    if not user_text and case.get("messages"):
        user_text = (case["messages"][-1].get("text") or "")
    ctx = scene.get("context_hint", turn_kw.get("context_hint")) or ""
    criteria = "；".join(cases_mod.judge_prompts(case.get("checks"))) or "自然、贴题、不越线"
    history = _judge_history(scene.get("session", turn.get("session")))
    unprompted = scene.get("unprompted", turn_kw.get("unprompted"))
    said = f"【群友说】{'（没人叫小王，接不接由它自己定）' if unprompted else ''}\n{user_text}"
    observed = base_evidence is not None or cand_evidence is not None
    execution_note = ("【执行记录的来源】\n以下执行记录来自本次回放代码观察，只证明该次模拟如何执行，"
                      "不是外部事实真伪的证明。query_mode=fixture 表示测试资料，live 才表示实际联网；"
                      "unknown 表示未记录，不能当作没有执行。\n\n" if observed else "")
    a_note = "\n【A 的执行记录】\n" + json.dumps(a_evidence, ensure_ascii=False) if observed else ""
    b_note = "\n【B 的执行记录】\n" + json.dumps(b_evidence, ensure_ascii=False) if observed else ""
    prompt = (
        f"【群聊背景】（背景里的「你」就是小王）\n{ctx or '（无）'}\n\n"
        + (f"【小王最近几轮对话】\n{history}\n\n" if history else "")
        + f"{said}\n\n【本条关注点】\n{criteria}\n\n" + execution_note
        + f"【回复 A】\n{a_text}{a_note}\n\n【回复 B】\n{b_text}{b_note}"
    )
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}]


async def judge_pair(case, base_reply, cand_reply, *, judge_fn=None, rng=None, retries=1,
                     swap=None, base_evidence=None, cand_evidence=None, judge_input=None):
    """打乱顺序盲评一对新旧回复。返回 {"winner": "base"|"cand"|"tie"|None, "crossed": [...], "reason": ...}。

    swap 给了就照它摆 A/B（并发评时先统一抽好，结果才可复现），不给就现抽。
    """
    if base_reply is None or cand_reply is None:
        return {"winner": None, "crossed": [], "reason": "generation_error", "error": True}
    base_evidence, cand_evidence = execution_evidence(base_evidence), execution_evidence(cand_evidence)
    evidence = {"base": base_evidence, "cand": cand_evidence}
    if judge_input is None and _same_reply_and_evidence(base_reply, cand_reply, base_evidence, cand_evidence):
        return {"winner": "tie", "crossed": [], "reason": "identical", "error": False,
                "evidence": evidence, "judge_input": None}
    if swap is None:
        swap = bool((rng or random).getrandbits(1))
    messages = (json.loads(json.dumps(judge_input)) if judge_input is not None else
                _judge_messages(case, base_reply, cand_reply, swap, base_evidence, cand_evidence))
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
                "evidence": evidence, "judge_input": messages,
            }
    return {"winner": None, "crossed": [], "reason": "judge_error", "error": True,
            "evidence": evidence, "judge_input": messages}


def _judge_history(session, limit=JUDGE_HISTORY_TURNS):
    """会话里最近几句，按「群友 / 小王」排好；系统补的那行（（系统：…））去掉，裁判只看说出口的话。"""
    lines = []
    for msg in list(session or ())[-limit:]:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "\n".join(line for line in str(msg.get("content") or "").splitlines()
                         if not line.strip().startswith(("（系统：", "(系统："))).strip()
        if text:
            lines.append(f"{'小王' if role == 'assistant' else '群友'}：{text[:200]}")
    return "\n".join(lines)


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


# 改进和退步用同一道检验：退步要过 REGRESSION_P，改进也要过，否则「赢一场就算改进」会把噪声报成好消息。
# 2026-11-05 调语气那一圈：盲评新 18 旧 21 平 7，逐条看「改进」却有好几条 —— 都是 1:0、2:1 这种。
def rise_p_value(b_pass, b_total, c_pass, c_total):
    """新版通过数多到这个程度的单侧概率：drop_p_value 把新旧对调。"""
    return drop_p_value(c_pass, c_total, b_pass, b_total)


def judge_win_p_value(base_wins, cand_wins):
    """盲评里新版赢这么多场的单侧符号检验（平局不计）。"""
    return judge_loss_p_value(cand_wins, base_wins)


def _aligned_traces(result, samples):
    traces = (result or {}).get("traces", (result or {}).get("trace"))
    if traces is None:
        return [None] * len(samples)
    if not isinstance(traces, list) or len(traces) != len(samples):
        raise ValueError("回放 traces 必须与 samples 逐项对齐，失败样本也需保留占位")
    return traces


def _judge_protocol(case, base_traces, cand_traces):
    protocol = {"system": JUDGE_SYSTEM, "history_turns": JUDGE_HISTORY_TURNS}
    if isinstance(case.get("judge_context"), dict) or any(t is not None for t in base_traces + cand_traces):
        protocol["execution_evidence"] = EXECUTION_EVIDENCE_VERSION
    return protocol


async def evaluate_cases(cases, base_results, cand_results, *, judge_fn=None, rng=None,
                         concurrency=6, prior=None):
    """汇总每条用例的确定性通过率、盲评胜率，标出退步（regressed）、噪声内下降（wobbled）、
    改进（improved，同样要过 REGRESSION_P）与噪声内上升（lifted）。

    盲评先把每一对的 A/B 抽好，再并发跑（同时最多 concurrency 个）。
    prior 是上一份报告里每条用例逐对的盲评（{用例 id: [{"swap", "winner", ...}, ...]}）：
    评过的照用、连 A/B 摆法一起沿用，只重评失败的那几对（eval --rejudge）。
    """
    rng = rng or random
    prior = prior or {}
    base_map = {r["id"]: r for r in base_results}
    cand_map = {r["id"]: r for r in cand_results}
    plan = []
    for case in cases:
        cid = case["id"]
        b_samples = (base_map.get(cid) or {}).get("samples") or []
        c_samples = (cand_map.get(cid) or {}).get("samples") or []
        b_traces = _aligned_traces(base_map.get(cid), b_samples)
        c_traces = _aligned_traces(cand_map.get(cid), c_samples)
        b_evidence = [execution_evidence(t) for t in b_traces]
        c_evidence = [execution_evidence(t) for t in c_traces]
        pairs = list(zip(b_samples, c_samples))
        old = list(prior.get(cid) or ())
        old += [None] * (len(pairs) - len(old))
        swaps = [bool(o["swap"]) if o and o.get("swap") is not None else bool(rng.getrandbits(1))
                 for o in old[:len(pairs)]]
        plan.append((case, b_samples, c_samples, bool(cases_mod.judge_prompts(case.get("checks"))),
                     pairs, swaps, old[:len(pairs)], b_traces, c_traces, b_evidence, c_evidence))
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def judge_one(case, b_s, c_s, swap, old, b_e, c_e):
        if b_s is None or c_s is None:
            return None
        if old and not old.get("error") and old.get("winner") is not None:
            return {k: v for k, v in old.items() if k != "swap"}
        async with sem:
            verdict = await judge_pair(case, b_s, c_s, judge_fn=judge_fn, swap=swap,
                                       base_evidence=b_e, cand_evidence=c_e,
                                       judge_input=(old or {}).get("judge_input"))
            if old and isinstance(old.get("evidence"), dict):
                verdict["evidence"] = old["evidence"]
            return verdict

    verdicts = iter(await asyncio.gather(*(
        judge_one(case, b_s, c_s, swap, old, b_e, c_e)
        for case, _, _, has_judge, pairs, swaps, olds, _, _, b_es, c_es in plan if has_judge
        for (b_s, c_s), swap, old, b_e, c_e in zip(pairs, swaps, olds, b_es, c_es))))

    rows = []
    totals = {
        "cases": len(cases),
        "regressed": 0,
        "wobbled": 0,
        "improved": 0,
        "lifted": 0,
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
    for case, b_samples, c_samples, has_judge, pairs, swaps, _, b_traces, c_traces, _, _ in plan:
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
        pair_log = []   # 逐对的盲评：报告里逐条摆出来，--rejudge 也靠它只重评失败的
        c_crossed = b_crossed = 0
        for (b_s, c_s), swap in zip(pairs, swaps):
            verdict = next(verdicts) if has_judge else None
            if has_judge:
                pair_log.append({"swap": swap, **(verdict or {"winner": None, "crossed": [],
                                                              "reason": "generation_error",
                                                              "error": True})})
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
            and b_valid == c_valid
            and b_valid == len(b_samples)
            and c_valid == len(c_samples)
            and (not has_judge or (judged_pairs > 0 and j_wins["dropped"] == 0))
        )

        retrieval = None
        retrieval_regressed = retrieval_improved = False
        if case.get("depth") == "retrieve":
            retrieval = {
                "base": cases_mod.retrieval_metrics(next((s for s in b_samples if s is not None), None), case),
                "cand": cases_mod.retrieval_metrics(next((s for s in c_samples if s is not None), None), case),
            }
            b_ret, c_ret = retrieval["base"], retrieval["cand"]
            if b_ret["evidence_complete"] and c_ret["evidence_complete"]:
                retrieval_regressed = c_ret["fp"] > b_ret["fp"] or c_ret["fn"] > b_ret["fn"]
                retrieval_improved = (not retrieval_regressed) and (
                    c_ret["fp"] < b_ret["fp"] or c_ret["fn"] < b_ret["fn"])

        # 检索完整标注的误报/漏报直接比较；生成与盲评的下降仍需排除抽样噪声。
        gen_regressed = bool(b_valid > 0 and c_valid == 0)
        det_dropped = bool(b_checks and c_checks and c_rate < b_rate)
        det_p = drop_p_value(b_pass, b_valid, c_pass, c_valid) if det_dropped else 1.0
        if det_dropped and (case.get("depth") == "retrieve"):
            det_p = 0.0   # 检索是确定性的本地计算，没有抽样噪声：掉了就是掉了
        judge_lost = bool(has_judge and j_wins["base"] > j_wins["cand"])
        judge_p = judge_loss_p_value(j_wins["base"], j_wins["cand"]) if judge_lost else 1.0
        regressed = bool(gen_regressed or c_crossed > 0 or retrieval_regressed
                         or (det_dropped and det_p <= REGRESSION_P)
                         or (judge_lost and judge_p <= REGRESSION_P))
        wobbled = (not regressed) and (det_dropped or judge_lost)
        det_rose = bool(b_checks and c_checks and c_rate > b_rate)
        rise_p = rise_p_value(b_pass, b_valid, c_pass, c_valid) if det_rose else 1.0
        if det_rose and case.get("depth") == "retrieve":
            rise_p = 0.0
        judge_won = bool(has_judge and j_wins["cand"] > j_wins["base"] and c_crossed == 0)
        win_p = judge_win_p_value(j_wins["base"], j_wins["cand"]) if judge_won else 1.0
        clear = (not regressed) and (not wobbled)
        improved = clear and evidence_complete and bool(retrieval_improved or (det_rose and rise_p <= REGRESSION_P)
                                  or (judge_won and win_p <= REGRESSION_P))
        lifted = clear and not improved and (det_rose or judge_won)
        if regressed:
            totals["regressed"] += 1
        elif wobbled:
            totals["wobbled"] += 1
        elif improved:
            totals["improved"] += 1
        elif lifted:
            totals["lifted"] += 1
        failed_reasons = [d["reason"] for r in c_checks for d in r["details"]
                          if not d["passed"] and d.get("reason")]
        if gen_regressed:
            failed_reasons.append("新版生成失败")
        elif not evidence_complete:
            failed_reasons.append("样本或盲评不完整")
        if c_crossed > 0:
            failed_reasons.append(f"盲评判越线×{c_crossed}")
        if retrieval_regressed:
            for key, label in (("fp", "误报"), ("fn", "漏报")):
                if c_ret[key] > b_ret[key]:
                    failed_reasons.insert(0, f"检索{label}增加：{b_ret[key]} → {c_ret[key]}")
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
            "case_snapshot": json.loads(json.dumps(case)),
            "judge_protocol": _judge_protocol(case, b_traces, c_traces),
            "base_pass": f"{b_pass}/{len(b_checks)}",
            "cand_pass": f"{c_pass}/{len(c_checks)}",
            "base_pass_rate": round(b_rate, 3),
            "cand_pass_rate": round(c_rate, 3),
            "regressed": regressed,
            "wobbled": wobbled,
            "improved": improved,
            "lifted": lifted,
            "p_drop": round(det_p, 3) if det_dropped else None,
            "p_judge_loss": round(judge_p, 3) if judge_lost else None,
            "p_rise": round(rise_p, 3) if det_rose else None,
            "p_judge_win": round(win_p, 3) if judge_won else None,
            "evidence_complete": evidence_complete,
            "judge": j_wins if has_judge else None,
            "judge_reasons": j_reasons[:3],
            "failed_reasons": list(dict.fromkeys(failed_reasons))[:3],
            "base_sample": next((s for s in b_samples if s is not None), ""),
            "cand_sample": next((s for s in c_samples if s is not None), ""),
            # 全部样本和逐对盲评都留着：判输判赢之后得看原文（「先说原作没提再补一句玩笑」这种，分数看不出来）
            "base_samples": list(b_samples),
            "cand_samples": list(c_samples),
            "base_traces": json.loads(json.dumps(b_traces)),
            "cand_traces": json.loads(json.dumps(c_traces)),
            "pairs": pair_log if has_judge else None,
            "prompts_refreshed": _refreshed(base_map.get(cid), cand_map.get(cid)),
            "prompts_missing": sorted({*((base_map.get(cid) or {}).get("stale_prompts") or ()),
                                       *((cand_map.get(cid) or {}).get("stale_prompts") or ())}) or None,
            "retrieval": retrieval,
        })

    # 退步排最前，其次噪声内下降，再次改进、噪声内上升，其余按 id
    rows.sort(key=lambda r: (0 if r["regressed"] else 1 if r["wobbled"] else 2 if r["improved"]
                             else 3 if r["lifted"] else 4, r["id"]))
    totals["incomplete_cases"] = sum(1 for r in rows if not r.get("evidence_complete"))
    retrieval_rows = [r for r in rows if r.get("retrieval")]
    if retrieval_rows:
        totals["retrieval"] = _retrieval_totals(retrieval_rows)
    totals["base_pass_rate"] = round(
        totals["base_passed_samples"] / totals["base_total_samples"], 3
    ) if totals["base_total_samples"] else None
    totals["cand_pass_rate"] = round(
        totals["cand_passed_samples"] / totals["cand_total_samples"], 3
    ) if totals["cand_total_samples"] else None
    judged = totals["judge_cand_wins"] + totals["judge_base_wins"] + totals["judge_ties"]
    totals["cand_win_rate"] = round(totals["judge_cand_wins"] / judged, 3) if judged else None
    totals["verdict"] = overall_verdict(totals)
    return rows, totals


def _refreshed(base_res, cand_res):
    """回放时两边各自换成了自己措辞的提示词常量（见 mark_frozen_prompts）。"""
    out = {side: list((res or {}).get("refreshed") or ()) for side, res in (("base", base_res), ("cand", cand_res))}
    return out if any(out.values()) else None


def overall_verdict(totals):
    """整份评测一句话的结论：worse / better / unclear，外加两个合计检验的 p。

    better 要有证据：有用例过了改进检验，或者盲评合计新版赢得够多；
    「没退步」只说明没看出变差，不等于变好了（2026-11-05 调语气：新 18 旧 21 平 7，差点被当成「无退步可上线」）。
    """
    b_wins, c_wins = totals.get("judge_base_wins", 0), totals.get("judge_cand_wins", 0)
    out = {
        "p_judge_better": round(judge_win_p_value(b_wins, c_wins), 3) if c_wins + b_wins else None,
        "p_judge_worse": round(judge_loss_p_value(b_wins, c_wins), 3) if c_wins + b_wins else None,
        "incomplete": totals.get("incomplete_cases", 0),
    }
    if totals.get("regressed") or totals.get("cand_crossed"):
        out["call"] = "worse"
    elif out["p_judge_worse"] is not None and out["p_judge_worse"] <= REGRESSION_P:
        out["call"] = "worse"
    elif out["incomplete"]:
        out["call"] = "unclear"
    elif totals.get("improved") or (out["p_judge_better"] is not None
                                  and out["p_judge_better"] <= REGRESSION_P):
        out["call"] = "better" if not totals.get("wobbled") else "unclear"
    else:
        out["call"] = "unclear"
    return out


def _retrieval_totals(rows):
    """消息级矩阵每个确定性用例只计一次；两版都完整的用例使用同一个分母。"""
    complete = [r for r in rows if r["evidence_complete"]
                and all(r["retrieval"][side]["evidence_complete"] for side in ("base", "cand"))]
    out = {"scope": "archive_messages", "complete_cases": len(complete),
           "excluded_cases": len(rows) - len(complete)}
    for side in ("base", "cand"):
        if not complete:
            out[side] = None
            continue
        m = {key: sum(r["retrieval"][side][key] for r in complete) for key in ("tp", "fp", "fn", "tn")}
        tp, fp, fn, tn = (m[key] for key in ("tp", "fp", "fn", "tn"))
        m.update(precision=tp / (tp + fp) if tp + fp else None,
                 recall=tp / (tp + fn) if tp + fn else None,
                 f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                 false_positive_rate=fp / (fp + tn) if fp + tn else None,
                 false_negative_rate=fn / (tp + fn) if tp + fn else None)
        out[side] = m
    return out


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


VERDICT_LINES = {
    "worse": "新版更差：有退步、越线，或盲评合计明显输给旧版",
    "better": "新版更好，有证据：有用例过了改进检验，或盲评合计明显赢旧版",
    "unclear": "看不出新版更好：没退步不等于变好，不能拿这份报告当改进的证据",
}


def _cell(text, limit=140):
    if text is None:
        return "（生成失败）"
    flat = str(text).replace("|", "｜").replace("\n", " ⏎ ").strip()
    return (flat[:limit] + "…") if len(flat) > limit else (flat or "（空）")


def _samples_section(case_rows):
    """逐条输出：每条用例的新旧样本并排，盲评判了谁、为什么。分数说不清的（语气、较不较真）得看这里。"""
    lines = ["", "## 逐条输出", "",
             "盲评列是「谁赢 · 原因」；样本超过 140 字截断，完整原文在同名 .json 的 base_samples / cand_samples。"]
    for r in case_rows:
        b_s, c_s = r.get("base_samples"), r.get("cand_samples")
        if not b_s and not c_s:
            continue
        state = ("退步" if r["regressed"] else "噪声内下降" if r.get("wobbled") else "改进" if r["improved"]
                 else "噪声内上升" if r.get("lifted") else "持平")
        lines += ["", f"### `{r['id']}` · {state}", "", "| # | 旧 | 新 | 盲评 |", "| --- | --- | --- | --- |"]
        pairs = r.get("pairs") or []
        for i in range(max(len(b_s or ()), len(c_s or ()))):
            b = b_s[i] if b_s and i < len(b_s) else None
            c = c_s[i] if c_s and i < len(c_s) else None
            v = pairs[i] if i < len(pairs) else None
            if not v:
                judged = "—"
            elif v.get("error") or not v.get("winner"):
                judged = f"没评成（{v.get('reason') or '—'}）"
            else:
                who = {"cand": "新", "base": "旧", "tie": "平"}[v["winner"]]
                crossed = "；越线：" + "、".join({"cand": "新", "base": "旧"}[x] for x in v["crossed"]) \
                    if v.get("crossed") else ""
                judged = f"{who} · {_cell(v.get('reason'), 40)}{crossed}"
            lines.append(f"| {i + 1} | {_cell(b)} | {_cell(c)} | {judged} |")
    return lines


def render_report(report, samples=True):
    """渲染 Markdown 评测报告：先一句结论，退步排最前，包含稳定头长度对比、盲评胜率与整天指标。

    samples=True 时末尾附逐条输出（写进报告文件）；命令行直接打印时关掉，免得刷屏。
    """
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
    if report.get("rejudged_from"):
        lines.append(f"- 重评自 `{report['rejudged_from']}`：样本沿用那次的，只重跑了没评成的盲评")
    t = report.get("totals") or {}
    if t.get("cases"):
        v = t.get("verdict") or {}
        if v.get("call"):
            ps = [f"盲评合计新版更好 p={v['p_judge_better']}" if v.get("p_judge_better") is not None else "",
                  f"有 {v['incomplete']} 条证据不完整" if v.get("incomplete") else ""]
            extra = "；".join(x for x in ps if x)
            lines.insert(2, f"**结论：{VERDICT_LINES[v['call']]}**" + (f"（{extra}）" if extra else ""))
            lines.insert(3, "")
        lines.append(
            f"- 用例：{t['cases']} 条（每条抽 {report.get('n', 1)} 次）| "
            f"通过率：`{t.get('base_pass_rate')}` → `{t.get('cand_pass_rate')}` | "
            f"退步：**{t['regressed']}** 条 | 噪声内下降：{t.get('wobbled', 0)} 条 | "
            f"改进：{t['improved']} 条 | 噪声内上升：{t.get('lifted', 0)} 条"
        )
        if t.get("cand_win_rate") is not None:
            lines.append(
                f"- 盲评：新胜 {t['judge_cand_wins']} / 旧胜 {t['judge_base_wins']} / 平 {t['judge_ties']} "
                f"（新胜率 `{t['cand_win_rate']:.0%}`，越线 新 {t['cand_crossed']} / 旧 {t['base_crossed']}）"
            )
    case_rows_all = report.get("cases") or []
    if t.get("cases") and t.get("dropped_pairs"):
        rejudgeable = any("base_samples" in r for r in case_rows_all)
        hint = (f"，可以 `eval --rejudge {report.get('stamp', '<报告>')}` 只重评这几对" if rejudgeable else "")
        lines.append(f"- 网络/生成错误剔除对数：{t['dropped_pairs']}"
                     + (f"（其中盲评失败 {t['judge_errors']} 对{hint}）" if t.get("judge_errors") else ""))
    swapped = sorted({name for r in case_rows_all for side in (r.get("prompts_refreshed") or {}).values()
                      for name in side})
    stale = sorted({name for r in case_rows_all for name in (r.get("prompts_missing") or ())})
    if swapped:
        lines.append(f"- 用例里冻着的提示词原文，回放时各自换成了受测版本的措辞：{'、'.join(swapped)}")
    if stale:
        lines.append(f"- 注意：受测版本里已经没有 {'、'.join(stale)}，用到它的用例照录制时的原文回放，测不到这处改动")

    retrieval = t.get("retrieval")
    if retrieval:
        lines += ["", "## 检索误报与漏报（归档消息级）", "",
                  f"完整标注且两版执行完整：{retrieval['complete_cases']} 条用例；"
                  f"未纳入完整对照：{retrieval['excluded_cases']} 条。每条用例只计一次，不乘抽样数。"]
        if retrieval["complete_cases"]:
            lines += ["", "| 指标 | Base | Cand |", "| --- | --- | --- |"]
            for key, label in (("tp", "TP"), ("fp", "误报 FP"), ("fn", "漏报 FN"), ("tn", "TN"),
                               ("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"),
                               ("false_positive_rate", "误报率 FP / (FP + TN)"),
                               ("false_negative_rate", "漏报率 FN / (TP + FN)")):
                vals = []
                for side in ("base", "cand"):
                    value = retrieval[side][key]
                    vals.append("—" if value is None else str(value) if key in ("tp", "fp", "fn", "tn")
                                else f"{value:.1%}")
                lines.append(f"| {label} | {vals[0]} | {vals[1]} |")
        else:
            lines.append("现有部分判据仅用于通过/失败检查；缺少完整相关集或完整语料时不计算误报、漏报和召回率。")

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
                     else "IMPROVED" if r["improved"] else "LIFT" if r.get("lifted") else "OK")
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

    if samples and case_rows:
        lines += _samples_section(case_rows)
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


# ── 冻结在用例里的提示词原文 ──
# 生成层用例存的是当轮原样入参，其中好几段注记就是 prompts.py 里某个常量的原文（PROMPT_PLAYFUL_REQUEST、
# PROMPT_BANTER_RECENT、PROMPT_FEEDBACK_REPAIR……）。改了这些常量，回放却还喂录制时那份，测的就不是这次改动
# （2026-11-05 调语气：改了三版玩梗线索，回放一直用的是旧话术）。回放前把它们认出来，交给各版本换成自己的。

FROZEN_MIN_CHARS = 20   # 太短的常量（标签、单个词）容易在别的文字里碰巧出现，不认


def prompt_texts(source):
    """从 prompts.py 源码取出顶层字符串常量 {名字: 原文}。只认字面量，不执行代码。"""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            else node.target if isinstance(node, ast.AnnAssign) else None
        value = getattr(node, "value", None)
        if (isinstance(target, ast.Name) and target.id.isupper() and isinstance(value, ast.Constant)
                and isinstance(value.value, str) and len(value.value) >= FROZEN_MIN_CHARS):
            out[target.id] = value.value
    return out


@functools.lru_cache(maxsize=32)
def _release_prompt_texts(release, repo):
    proc = subprocess.run(["git", "show", f"{release}:prompts.py"], cwd=repo,
                          capture_output=True, text=True)
    return prompt_texts(proc.stdout) if proc.returncode == 0 else {}


def _dir_prompt_texts(code_dir):
    path = Path(code_dir) / "prompts.py"
    return prompt_texts(path.read_text(encoding="utf-8")) if path.is_file() else {}


def mark_frozen_prompts(cases, code_dirs, *, turn_dir=None, repo_root=None):
    """给生成层用例标出冻在入参里的提示词原文：turn.frozen_prompts = [{kwarg, name, text}]。

    认得出的原文来自：两个受测版本、驱动所在的工作区（种子用例多半是从这里存的）、
    以及录这一轮的那个版本（用例里的 turn.release；旧用例按 source_turn_id 回 data/turns 查）。
    """
    repo = str(Path(repo_root or Path(__file__).resolve().parent.parent).resolve())
    shared = [_dir_prompt_texts(d) for d in (*code_dirs, repo)]
    out = []
    for case in cases:
        turn = case.get("turn")
        if (case.get("depth") or "generate") != "generate" or not isinstance(turn, dict):
            out.append(case)
            continue
        release = turn.get("release")
        if not release and turn_dir and case.get("source_turn_id"):
            row = turns_mod.find(turn_dir, case["source_turn_id"])
            release = (row or {}).get("release")
        sources = shared + ([_release_prompt_texts(release, repo)] if release else [])
        known = {(name, text) for texts in sources for name, text in texts.items()}
        frozen = [{"kwarg": key, "name": name, "text": text}
                  for key, value in sorted((turn.get("kwargs") or {}).items())
                  if key != "user_text" and isinstance(value, str)
                  for name, text in sorted(known) if text in value]
        out.append({**case, "turn": {**turn, "frozen_prompts": frozen}} if frozen else case)
    return out


def _ablate_verdict(ablate, case_rows, totals, cases):
    """规则消融的结论：删规则宁严勿松，任何一条掉了（哪怕在噪声内）都不推荐删，只是说法不同。"""
    rule_obj = rules_mod.get_rule(ablate)
    has_linked = any(rules_mod.matches_case(rule_obj, r["id"], r.get("tags")) for r in case_rows)
    all_evidence_complete = bool(case_rows) and all(r.get("evidence_complete") for r in case_rows)
    per_case_judge_ok = all(r["judge"]["base"] <= r["judge"]["cand"] for r in case_rows if r.get("judge"))
    has_any_judge = any(cases_mod.judge_prompts(c.get("checks")) for c in cases)
    judged_total = (totals.get("judge_cand_wins", 0) + totals.get("judge_base_wins", 0)
                    + totals.get("judge_ties", 0))
    judge_ok = per_case_judge_ok and ((not has_any_judge) or (
        judged_total > 0 and totals.get("judge_base_wins", 0) <= totals.get("judge_cand_wins", 0)))
    if not has_linked:
        return "no_cases"
    if not all_evidence_complete:
        return "incomplete"
    if totals["regressed"] or totals.get("cand_crossed", 0) or not judge_ok:
        return "keep"
    if totals.get("wobbled", 0):
        return "unsure"
    return "delete"


def _set_ablate_verdict(ablate_info, verdict):
    ablate_info["verdict"] = verdict
    ablate_info["incomplete_evidence"] = verdict in ("no_cases", "incomplete")
    ablate_info["deletion_candidate"] = verdict == "delete"


def _traffic_host(url):
    """预览只显示目的主机，不回显 URL 中可能夹带的凭据或查询参数。"""
    try:
        parts = urlsplit(str(url or ""))
        name = parts.hostname or "—"
        return f"{name}:{parts.port}" if parts.port else name
    except ValueError:
        return "（无效地址）"


def _possibly_live_cases(cases):
    # 本地文件可能覆盖同名种子；只有内容也等于仓库种子的才认作公开合成用例。
    seeds = {c["id"]: c for c in cases_mod.SEED_CASES}
    return [c for c in cases if c.get("source_turn_id") or c.get("source_message_id")
            or c != seeds.get(c["id"])]


def _judge_destination(config):
    name = getattr(config, "EVAL_JUDGE_PROVIDER", "")
    preset = (getattr(config, "PROVIDER_PRESETS", {}).get(name) or {})
    return f"{getattr(config, 'EVAL_JUDGE_MODEL', '') or '—'} @ {name} · {_traffic_host(preset.get('base_url'))}"


def _completion_destinations(config, tier="chat"):
    presets = getattr(config, "PROVIDER_PRESETS", {})
    primary = (getattr(config, "TIER_PROVIDERS", {}).get(tier)
               or getattr(config, "AI_PROVIDER", "—"))
    names = list(dict.fromkeys(filter(None, (primary, getattr(config, "FALLBACK_PROVIDER", "")))))
    out = []
    for name in names:
        preset = presets.get(name) or (getattr(config, "PROVIDER", {}) if name == primary else {})
        models = preset.get("models" if tier == "chat" else f"models_{tier}") or preset.get("models")
        if not models and name == primary:
            models = getattr(config, "CANDIDATE_MODELS", ())
        out.append(f"{'、'.join(models or ()) or '—'} @ {name} · {_traffic_host(preset.get('base_url'))}")
    return "；备用 ".join(out)


def describe_traffic(config, cases, *, n, day_turns=0):
    """按实际回放深度列目的地和逻辑样本数；物理请求受路由、失败重放和候选上限影响。"""
    counts = Counter(c.get("depth") or "generate" for c in cases)
    judged = [c for c in cases if cases_mod.judge_prompts(c.get("checks"))]
    outbound = [c for c in cases if (c.get("depth") or "generate") != "retrieve"
                or cases_mod.judge_prompts(c.get("checks"))]
    live = _possibly_live_cases(outbound)
    lines = [
        f"用例 {len(cases)} 条：要调模型的 {len(outbound)} 条，其中本地用例 {len(live)} 条"
        "（可能含真实群聊、前文和称呼档案）；只有与仓库种子内容相同的才按公开合成数据计算。",
        f"深度：生成 {counts['generate']}，分诊 {counts['triage']}，消息 {counts['message']}，检索 {counts['retrieve']}。",
    ]
    if counts["generate"] or counts["message"] or day_turns:
        lines.append(
            f"回复模型候选：{_completion_destinations(config)}；生成层新旧两版各 {counts['generate']} × {n} 个样本"
            + (f"，整天回放另加 {day_turns} × 2 轮" if day_turns else "") + "。")
    if counts["triage"] or counts["message"]:
        from regular import decision_settings
        data_dir = getattr(config, "DATA_DIR", None)
        settings = decision_settings.effective_public_settings(config) if data_dir else None
        if not getattr(config, "DECISION_ENABLED", True):
            destination = "已禁用，不发原生分诊请求"
        elif settings and settings["key_configured"]:
            destination = f"{settings['model']} @ {_traffic_host(settings['endpoint'])}（无聊天供应商回退）"
        else:
            destination = "未配置，当前不会发原生分诊请求"
        lines.append(f"Decisions：{destination}；分诊层新旧两版各 {counts['triage']} × {n} 个样本。")
    if counts["message"]:
        message_cases = [c for c in cases if c.get("depth") == "message"]
        modes = Counter((c.get("tools") or {}).get("lookup", "disabled") for c in message_cases)
        timing = Counter((c.get("timeline") or {}).get("mode", "serial") for c in message_cases)
        lines.extend([
            f"消息层新旧两版各 {counts['message']} × {n} 次入口回放，回复和分诊次数取决于消息、路由及预置答案；不能按一条用例一次请求计算。",
            f"消息路由还可能使用审核候选：{_completion_destinations(config, 'judge')}；摘要候选：{_completion_destinations(config, 'digest')}。",
            f"消息层查询：禁用 {modes['disabled']}，固定测试资料 {modes['fixture']}，真实查询 {modes['live']}；"
            "默认不联网，固定资料不算实际搜索，DuckDuckGo 回退在回放中禁用。",
            f"消息时序：串行 {timing['serial']}，生成中补话 {timing['overlap']}，等待期间回复送达 {timing['late_delivery']}；"
            "每个消息层样本默认总计限时 120 秒、最多 6 次模型调用，以用例实际设置为准；工具调用仍另计。",
        ])
        if modes["live"]:
            provider = getattr(config, "RESEARCH_PROVIDER", "")
            preset = getattr(config, "PROVIDER_PRESETS", {}).get(provider) or {}
            lines.append(f"真实查询目的地：{getattr(config, 'RESEARCH_MODEL', '') or '未配置'} @ "
                         f"{provider or '未配置'} · {_traffic_host(preset.get('base_url'))}；"
                         "只在实际路由提出查询时调用，慢任务可继续查询进度与生成跟进，不能按一次入口一笔请求计费。")
    lines.append(
        f"盲评：{_judge_destination(config)}，{len(judged)} 条用例 × {n} 对"
        "（只发群友那句、背景、最近几轮和两条候选）；相同输出不发请求，失败可重试一次，另加 1 次合成预检。"
        if judged else "盲评：这批用例不用。")
    if counts["retrieve"]:
        lines.append("检索本身只在本地计算；带 judge 判据的检索用例仍会把评审输入外发。")
    lines.append(
        f"以上按当前部署配置预览；每个样本失败可再回放一次，单次回复最多尝试 {getattr(config, 'AI_MAX_ATTEMPTS', 3)} 个候选。"
        "受测版本若改变模型路由，需另行核对其配置；这不是精确账单。")
    return lines


def run_eval(config, *, base=".", cand=".", cases_selector="all", day=None,
             n=4, ablate="", patterns=None, judge_fn=None, worker_fn=None,
             write=True, allow_paid=True):
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
    if not allow_paid and (day or any(
            (case.get("depth") or "generate") != "retrieve"
            or cases_mod.judge_prompts(case.get("checks")) for case in cases)):
        raise PaidEvaluationRequired("eval requires --allow-paid for generation, triage, message, day replay or model judging; "
                                     "only pure local retrieve cases run without it")
    if judge_fn is None and any(cases_mod.judge_prompts(c.get("checks")) for c in cases):
        preflight_judge(config)

    with checkout_version(base) as base_dir, checkout_version(cand) as cand_dir:
        base_head = cand_head = None
        case_rows, totals = [], {"cases": 0, "regressed": 0, "improved": 0}
        if cases:
            cases = mark_frozen_prompts(cases, (base_dir, cand_dir),
                                        turn_dir=getattr(config, "TURN_LOG_DIR", None))
            b_out = worker(base_dir, cases, n=n, concurrency=concurrency)
            c_out = worker(cand_dir, cases, n=n, concurrency=concurrency, ablate=ablate)
            base_head, cand_head = b_out.get("head"), c_out.get("head")
            case_rows, totals = asyncio.run(
                evaluate_cases(cases, b_out["results"], c_out["results"], judge_fn=judge_fn,
                               concurrency=concurrency))
            if ablate_info is not None:
                _set_ablate_verdict(ablate_info, _ablate_verdict(ablate, case_rows, totals, cases))

        day_eval = None
        if day:
            resolved_day = checkup_mod.resolve_day(day)
            day_turns = [t for t in turns_mod.read_day(config.TURN_LOG_DIR, resolved_day)
                         if t.get("kind") == "reply"]
            # 整天回放也要刷新冻在 kwargs 里的注记；不能只让用例回放测到新措辞。
            day_cases = [{"id": t["turn_id"], "depth": "generate", "turn": t} for t in day_turns]
            marked = mark_frozen_prompts(day_cases, (base_dir, cand_dir),
                                         turn_dir=getattr(config, "TURN_LOG_DIR", None))
            day_turns = [c["turn"] for c in marked]
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


def load_report(config, ref):
    """按路径或时间戳（data/evals/<stamp>.json）读一份评测报告。"""
    path = Path(ref).expanduser()
    if not path.is_file():
        path = Path(config.EVAL_DIR) / (str(ref).removesuffix(".md").removesuffix(".json") + ".json")
    if not path.is_file():
        raise FileNotFoundError(f"找不到评测报告 {ref!r}（给 .json 路径，或 data/evals 里的时间戳）")
    return json.loads(path.read_text(encoding="utf-8"))


def _rejudge_inputs(config, ref):
    """只读恢复报告里的同一现场和判据；不把当前可变用例混进旧评审。"""
    old = load_report(config, ref)
    rows = old.get("cases") or []
    if any("base_samples" not in r or "cand_samples" not in r for r in rows):
        raise ValueError("这份报告是旧格式，没存全部样本，重评不了；重新跑一次 eval")
    for row in rows:
        case = row.get("case_snapshot")
        b_traces = _aligned_traces({"traces": row.get("base_traces")}, row["base_samples"])
        c_traces = _aligned_traces({"traces": row.get("cand_traces")}, row["cand_samples"])
        if not isinstance(case, dict) or row.get("judge_protocol") != _judge_protocol(case, b_traces, c_traces):
            raise ValueError("报告缺少原用例/评审协议快照，或评审协议已改变；不能混用新旧判据，请重新跑 eval")
    cases = [r["case_snapshot"] for r in rows]
    todo = 0
    pending = []
    for row, case in zip(rows, cases):
        if not cases_mod.judge_prompts(case.get("checks")):
            continue
        prior = row.get("pairs") or []
        b_traces = _aligned_traces({"traces": row.get("base_traces")}, row["base_samples"])
        c_traces = _aligned_traces({"traces": row.get("cand_traces")}, row["cand_samples"])
        needed = 0
        for i, (b, c) in enumerate(zip(row["base_samples"], row["cand_samples"])):
            prev = prior[i] if i < len(prior) else None
            evidence = (prev or {}).get("evidence") or {
                "base": execution_evidence(b_traces[i]), "cand": execution_evidence(c_traces[i])}
            if b is None or c is None or _same_reply_and_evidence(b, c, evidence.get("base"), evidence.get("cand")):
                continue
            if not prev or prev.get("error") or prev.get("winner") is None:
                if row["judge_protocol"].get("execution_evidence") and not (prev or {}).get("judge_input"):
                    raise ValueError("这份执行回放报告缺少逐对评审输入快照，不能重建或混用新证据")
                needed += 1
        if needed:
            pending.append(case)
            todo += needed
    return old, rows, cases, pending, todo


def describe_rejudge_traffic(config, ref):
    """补评预览只列出待补评样本，不重新生成、不读取当前用例。"""
    old, _, _, pending, todo = _rejudge_inputs(config, ref)
    live = _possibly_live_cases(pending)
    return [
        f"重评报告 {old.get('stamp') or ref}：沿用原样本、原现场和原判据，不重新生成。",
        f"待补评 {todo} 对，来自 {len(pending)} 条用例，其中本地用例 {len(live)} 条（可能含真实群聊及前文）。",
        f"盲评：{_judge_destination(config)}；最多 {todo} × 2 次样本请求（失败可重试一次）"
        + ("，另加 1 次合成预检。" if todo else "，无需预检或模型调用。"),
    ]


def rejudge_report(config, ref, *, judge_fn=None, write=True, allow_paid=True):
    """沿用报告冻结的全部样本、现场与判据，只补失败盲评；修改判据须新跑 eval。"""
    old, rows, cases, _, todo = _rejudge_inputs(config, ref)
    if todo and not allow_paid:
        raise PaidEvaluationRequired("eval --rejudge has judge pairs to run; it requires --allow-paid")
    if todo and judge_fn is None:
        preflight_judge(config)
    concurrency = getattr(config, "EVAL_CONCURRENCY", 6)
    case_rows, totals = asyncio.run(evaluate_cases(
        cases,
        [{"id": r["id"], "samples": r["base_samples"], "traces": r.get("base_traces")} for r in rows],
        [{"id": r["id"], "samples": r["cand_samples"], "traces": r.get("cand_traces")} for r in rows],
        judge_fn=judge_fn, concurrency=concurrency,
        prior={r["id"]: r.get("pairs") for r in rows}))
    for row, prev in zip(sorted(case_rows, key=lambda r: r["id"]), sorted(rows, key=lambda r: r["id"])):
        for key in ("prompts_refreshed", "prompts_missing"):
            if prev.get(key):
                row[key] = prev[key]
    ablate_info = dict(old["ablate"]) if old.get("ablate") else None
    if ablate_info:
        _set_ablate_verdict(ablate_info, _ablate_verdict(ablate_info["id"], case_rows, totals, cases))
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    report = {**old, "stamp": stamp, "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
              "rejudged_from": old.get("stamp"), "totals": totals, "cases": case_rows, "ablate": ablate_info}
    report.pop("report_path", None)
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

