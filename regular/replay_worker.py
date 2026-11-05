# -*- coding: utf-8 -*-
"""回放子进程（docs/self-iteration.md ③⑤）：在指定代码版本下重跑生成层、分诊层、消息层或检索用例。

为什么要单独起子进程：
    `regular eval --base <ref> --cand .` 对比两个版本时，各自的 prompts.py / bot.py
    可能都已经改过。子进程通过 --code-dir 把目标版本放到 sys.path 最前面，直接走那个版本
    自己的 `bot.get_ai_reply`、`decisions.usable_choices` 或 `bot.GroupBot.handle_group_msg`。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextvars
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import time
import uuid
import weakref
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_QUOTE_PREFIX = re.compile(r"^\[回复[^\]]*：「[^」]*」\]\s*")
_LOOP_GATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_MSG_CTX: contextvars.ContextVar = contextvars.ContextVar("_MSG_CTX", default=None)


class _ReplayGate:
    """单事件循环的读写门控：
    - 生成层（generate）与分诊层（triage）走 shared()，允许并发等待网络 I/O；
      其中生成层仅在同步组装提示词的极短窗口内额外持 patch_lock，进 call_model 前立即释放。
    - 消息层（message）需要在整条 handle_group_msg 期间改写 STATE / SENDER / call_decision 等全局对象，
      走 exclusive() 独占运行，确保混合批次里绝不会污染并发的分诊或生成任务。
    """

    def __init__(self) -> None:
        self.patch_lock = asyncio.Lock()
        self._cond = asyncio.Condition()
        self._active_shared = 0
        self._active_exclusive = False
        self._waiting_exclusive = 0

    @asynccontextmanager
    async def shared(self):
        async with self._cond:
            while self._active_exclusive or self._waiting_exclusive > 0:
                await self._cond.wait()
            self._active_shared += 1
        try:
            yield
        finally:
            async with self._cond:
                self._active_shared -= 1
                if self._active_shared == 0:
                    self._cond.notify_all()

    @asynccontextmanager
    async def exclusive(self):
        async with self._cond:
            self._waiting_exclusive += 1
            try:
                while self._active_exclusive or self._active_shared > 0:
                    await self._cond.wait()
                self._active_exclusive = True
            finally:
                self._waiting_exclusive -= 1
        try:
            yield
        finally:
            async with self._cond:
                self._active_exclusive = False
                self._cond.notify_all()


def _gate() -> _ReplayGate:
    loop = asyncio.get_running_loop()
    gate = _LOOP_GATES.get(loop)
    if gate is None:
        gate = _ReplayGate()
        _LOOP_GATES[loop] = gate
    return gate


def _load_harness_rules():
    """直接按文件路径加载评测驱动自带的 rules.py，不从受测代码的 regular 包导入（兼容旧 commit）。"""
    rules_path = Path(__file__).resolve().with_name("rules.py")
    spec = importlib.util.spec_from_file_location("_regular_harness_rules", str(rules_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reconstruct_raw_content(row):
    """从归档行还原传给 handle_group_msg 的原始 content（补回 <@openid>，去掉已拼过的 [回复…] 前缀）。"""
    if row.get("raw_content"):
        return str(row["raw_content"])
    text = str(row.get("text") or "")
    if row.get("ref_msg_idx"):
        text = _QUOTE_PREFIX.sub("", text, count=1)
    at_ids = row.get("at_ids") or ()
    prefix = [f"<@{oid}>" for oid in at_ids
              if oid and f"<@{oid}>" not in text and f"<@!{oid}>" not in text]
    return (" ".join(prefix) + (" " if prefix and text else "") + text).strip()


def archive_row_to_message(row, group_id, reply_cb):
    """把一条归档记录转成 handle_group_msg 认识的消息对象。"""
    content = reconstruct_raw_content(row)
    mentions = [SimpleNamespace(member_openid=m) for m in (row.get("mentions") or ()) if m]
    scene = {k: row[k] for k in ("msg_idx", "ref_msg_idx", "quoted", "bots") if row.get(k)}
    return SimpleNamespace(
        id=str(row.get("message_id") or f"msg-{uuid.uuid4().hex[:8]}"),
        content=content,
        group_openid=group_id,
        author=SimpleNamespace(member_openid=str(row.get("sender") or "REPLAY_USER")),
        timestamp=None,
        attachments=[],
        mentions=mentions,
        _scene=scene,
        reply=reply_cb,
    )


async def _run_generate_once(bot, runtime, config, turn, retries=1):
    kw = dict(turn.get("kwargs") or {})
    user_text = kw.pop("user_text", "")
    sig = inspect.signature(bot.get_ai_reply)
    if "playful_request" in sig.parameters and kw.get("playful_note"):
        # 旧记录冻结了提示词正文；支持语义标记的版本要使用自身当版措辞。
        kw["playful_request"] = True
        kw["playful_note"] = ""
    elif ("playful_request" not in sig.parameters and "playful_note" in sig.parameters
          and kw.get("playful_request") and not kw.get("playful_note")):
        # 新记录回放旧签名时，用受测版本自己的措辞还原同一个语义结论。
        kw["playful_note"] = bot.prompts.PROMPT_PLAYFUL_REQUEST
    if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        kw = {k: v for k, v in kw.items() if k in sig.parameters}
    media = dict(turn.get("media") or {"draw": True, "voice": True, "remind": True})
    sid = f"replay_{uuid.uuid4().hex[:10]}"
    runtime.SESSIONS._sessions[sid] = {
        "messages": [dict(m) for m in (turn.get("session") or ())],
        "last_time": time.time(),
    }
    gate = _gate()
    try:
        for _ in range(retries + 1):
            async with gate.shared():
                await gate.patch_lock.acquire()
                stack = ExitStack()
                released = False

                def _release_sync(st=stack):
                    nonlocal released
                    if not released:
                        released = True
                        st.close()
                        gate.patch_lock.release()

                try:
                    real_call_model = bot.call_model

                    async def _release_and_call(*c_args, _rel=_release_sync, _fn=real_call_model, **c_kw):
                        # get_ai_reply 进入 call_model 前已经同步拼完稳定头与 messages；
                        # 在首次 await 让出事件循环前立即恢复所有全局开关并释放锁，绝不跨协程串配置。
                        _rel()
                        return await _fn(*c_args, **c_kw)

                    if hasattr(bot, "media_flags"):
                        stack.enter_context(patch.object(bot, "media_flags", lambda m=media: dict(m)))
                    stack.enter_context(patch.object(bot.image_generation, "available",
                                                     lambda m=media: bool(m.get("draw", True))))
                    stack.enter_context(patch.object(bot._VOICE_OUT, "ready",
                                                     lambda m=media: bool(media.get("voice", True))))
                    stack.enter_context(patch.object(config, "REMINDER_ENABLED",
                                                     bool(media.get("remind", True))))
                    stack.enter_context(patch.object(bot, "call_model", _release_and_call))
                    body, _ = await bot.get_ai_reply(sid, user_text, allow_fallback=False, **kw)
                finally:
                    _release_sync()
            if body is not None:
                return body
        return None
    finally:
        runtime.SESSIONS._sessions.pop(sid, None)


def _questions_for_asked(decision_prompts, asked):
    """按分诊记录里的 asked 题名列表，从当前代码版本的 decision_prompts 取出题目定义。"""
    pool = {}
    for fn_name in ("feedback_questions", "addressed_questions", "recall_questions",
                    "lookup_questions", "request_kind_questions", "nickname_questions",
                    "callback_questions", "draw_questions"):
        fn = getattr(decision_prompts, fn_name, None)
        if callable(fn):
            for q in fn():
                pool[q["name"]] = q
    if not asked:
        return list(pool.values())
    return [pool[name] for name in asked if name in pool]


def _choices_to_decision_body(choices_map, questions=None):
    """把测试或用例预置的 {题名: 选项} 转成 usable_choices 能认的原生 {"answers": [...]} 结构。"""
    q_by_name = {q["name"]: q for q in (questions or ()) if isinstance(q, dict) and q.get("name")}
    answers = []
    for name, val in (choices_map or {}).items():
        if val is None or (q_by_name and name not in q_by_name):
            continue
        probs = [{"value": val, "probability": 0.95}]
        if name in q_by_name:
            others = [c["value"] for c in (q_by_name[name].get("choices") or ())
                      if isinstance(c, dict) and c.get("value") and c.get("value") != val]
            if others:
                probs.extend({"value": other, "probability": 0.05 / len(others)}
                             for other in others)
            else:
                probs[0]["probability"] = 1.0
        answers.append({
            "name": name,
            "type": "choice",
            "choice": val,
            "confidence": 0.95,
            "probabilities": probs,
        })
    return {"answers": answers}


async def _run_triage_once(runtime, config, triage_spec, retries=1):
    """重跑一条分诊用例（depth=triage），返回 JSON 字符串形式的 usable_choices 结果。"""
    from regular import decision_prompts
    from regular import decisions

    payload = triage_spec.get("payload") or {}
    asked = list(triage_spec.get("asked") or ())
    questions = _questions_for_asked(decision_prompts, asked)
    if not questions:
        return "{}"
    payload_json = json.dumps(payload, ensure_ascii=False)
    fake_env = os.environ.get("REGULAR_FAKE_DECISION_BODY")
    gate = _gate()
    for _ in range(retries + 1):
        async with gate.shared():
            if fake_env:
                body = json.loads(fake_env)
            else:
                body = await decisions.providers.call_decision(payload_json, questions)
        if body is None:
            continue
        choices, _ = decisions.usable_choices(body, questions)
        if choices is None:
            continue
        return json.dumps(choices, ensure_ascii=False, sort_keys=True)
    return None


async def _no_voice(*args, **kwargs):
    return None, None


async def _no_image(*args, **kwargs):
    return False


async def _run_message_once(bot, runtime, config, messages, retries=1, *, preset_quotes=(),
                            self_openid=None):
    """preset_quotes / self_openid 见 regular/cases.create_case_from_message：窗口外被引用的原句、
    线上已认出的小王的 @，回放开始前预置进这份临时状态。"""
    import archive as archive_mod
    import delivery
    import providers as providers_mod
    import storage
    from regular import decisions

    # 消息层回放会改写 runtime.STATE / bot.SENDER / bot.call_model / call_decision，
    # 走 gate.exclusive() 独占门控，并额外绑定 ContextVar，确保与分诊层/生成层混合回放时绝不互相串扰。
    gate = _gate()
    async with gate.exclusive():
        for _ in range(retries + 1):
            with tempfile.TemporaryDirectory(prefix="regular-msg-replay-") as tmp:
                state = storage.StateStore(os.path.join(tmp, "state.json"))
                arch = archive_mod.MessageArchive(tmp, keep_days=0)
                gid = f"REPLAY_GRP_{uuid.uuid4().hex[:6]}"
                sent_last = []
                failed = {"err": False}
                real_call = bot.call_model
                real_decision = decisions.providers.call_decision
                # 判断当前是不是离线单元测试（call_model 被替换成假函数且 call_decision 没被替换）
                model_is_mocked = real_call is not getattr(providers_mod, "call_model", None)
                decision_is_mocked = real_decision is not getattr(providers_mod, "call_decision", None)
                active_row = {"row": {}}

                async def tracked_model(msgs, max_tokens, **kw):
                    out = await real_call(msgs, max_tokens, **kw)
                    if out is None and _MSG_CTX.get() is active_row:
                        failed["err"] = True
                    return out

                async def tracked_decision(text, questions, **kw):
                    if _MSG_CTX.get() is not active_row:
                        return await real_decision(text, questions, **kw)
                    row_now = active_row["row"] or {}
                    if isinstance(row_now.get("decision_body"), dict):
                        return row_now["decision_body"]
                    if isinstance(row_now.get("triage_choices"), dict):
                        return _choices_to_decision_body(row_now["triage_choices"], questions)
                    fake_env = os.environ.get("REGULAR_FAKE_DECISION_BODY")
                    if fake_env:
                        return json.loads(fake_env)
                    if decision_is_mocked or not model_is_mocked:
                        return await real_decision(text, questions, **kw)
                    return None

                token = _MSG_CTX.set(active_row)
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(runtime, "STATE", state))
                        stack.enter_context(patch.object(
                            runtime, "SESSIONS",
                            storage.SessionStore(
                                max_turns=config.CONTEXT_MAX_TURNS,
                                max_chars=config.CONTEXT_MAX_CHARS,
                                ttl=config.CONTEXT_TTL_SECONDS,
                            ),
                        ))
                        if hasattr(bot, "processed_msg_ids"):
                            stack.enter_context(patch.object(
                                bot, "processed_msg_ids",
                                collections.deque(maxlen=getattr(config, "DEDUP_MAX", 500)),
                            ))
                        if hasattr(bot, "_recent_at_ids"):
                            stack.enter_context(patch.object(
                                bot, "_recent_at_ids", collections.OrderedDict()
                            ))
                        if hasattr(runtime, "INTERACTIONS"):
                            import interactions as interactions_mod
                            stack.enter_context(patch.object(
                                runtime, "INTERACTIONS",
                                interactions_mod.InteractionLog(os.path.join(tmp, "interactions"), enabled=False),
                            ))
                        if hasattr(runtime, "TURNS"):
                            stack.enter_context(patch.object(
                                runtime, "TURNS",
                                SimpleNamespace(append=lambda *a, **k: None, close=lambda: None),
                            ))
                        if hasattr(runtime, "QUOTES"):
                            import quotes
                            stack.enter_context(patch.object(runtime, "QUOTES", quotes.QuoteBook(state)))
                        stack.enter_context(patch.object(runtime, "ARCHIVE", arch))
                        stack.enter_context(patch.object(runtime, "FEEDBACK",
                                                         decisions.FeedbackTracker(120.0, 180.0)))
                        stack.enter_context(patch.object(decisions.providers, "call_decision",
                                                         tracked_decision))
                        stack.enter_context(patch.object(bot, "SENDER",
                                                         delivery.Sender(fallback=bot._blocked_reply)))
                        stack.enter_context(patch.object(runtime, "GROUP_BUCKET",
                                                         storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                                                             config.GROUP_RATE_REFILL_SECONDS)))
                        stack.enter_context(patch.object(runtime.KNOWLEDGE, "render", lambda *a, **k: ""))
                        stack.enter_context(patch.object(bot, "call_model", tracked_model))
                        # 它写了〔语音〕〔画〕也不真合成、不真画：那会花钱，语音还会记进部署目录里
                        # 线上那份语音额度（voice-outbound-budget.sqlite3）。回放里一律当没发成。
                        if hasattr(bot, "_VOICE_OUT"):
                            stack.enter_context(patch.object(
                                bot._VOICE_OUT, "attempt", _no_voice))
                        if hasattr(bot.image_generation, "generate_and_send"):
                            stack.enter_context(patch.object(
                                bot.image_generation, "generate_and_send", _no_image))
                        if self_openid:
                            state.data.setdefault("groups", {}).setdefault(gid, {})[
                                "self_openid"] = self_openid
                        if hasattr(runtime, "QUOTES"):
                            for q in preset_quotes or ():
                                runtime.QUOTES.remember(gid, q.get("ref"), q.get("who"),
                                                        q.get("text") or "")
                        try:
                            fake_self = SimpleNamespace(
                                robot=SimpleNamespace(id="REPLAY_BOT_ID", name="regular"))
                            for idx, row in enumerate(messages):
                                is_last = idx == len(messages) - 1
                                if row.get("bot"):
                                    text = str(row.get("text") or "")
                                    ref = row.get("ref_idx") or f"REF_BOT_{idx}"
                                    if hasattr(bot, "_remember_sent"):
                                        bot._remember_sent(gid, {"id": f"b{idx}", "ext_info": {"ref_idx": ref}}, text)
                                    if hasattr(bot, "_note_own_line"):
                                        bot._note_own_line(gid, text)
                                    continue
                                replies = []

                                async def reply_cb(replies=replies, idx=idx, **payload):
                                    replies.append(payload.get("content") or "")
                                    return {"id": f"rcpt-{idx}", "ext_info": {"ref_idx": f"REF_OUT_{idx}"}}

                                active_row["row"] = row
                                msg = archive_row_to_message(row, gid, reply_cb)
                                is_at = bool(row["is_at"]) if "is_at" in row else bool(row.get("at"))
                                await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
                                if is_last:
                                    sent_last = replies
                        finally:
                            arch.close()
                            bot.group_buffers.pop(gid, None)
                            bot.group_last_random_reply.pop(gid, None)
                            if hasattr(bot, "own_lines"):
                                bot.own_lines.pop(gid, None)
                finally:
                    _MSG_CTX.reset(token)
                if failed["err"] and not sent_last:
                    continue
                return "\n".join(sent_last) if sent_last else "〔不接〕"
        return None


class _PromiseList:
    """检索用例里存的承诺账本快照，装成 recall 认的 promises.list(group) 接口。"""

    def __init__(self, rows):
        self._rows = [dict(r) for r in rows or ()]

    def list(self, group_id):
        return list(self._rows)


class _RowArchive:
    """检索用例里存的归档快照，装成 archive.search 接口（给还没有 recall.select 的旧版本用）。"""

    def __init__(self, rows):
        self._rows = list(rows or ())

    def search(self, group_id=None, keyword="", since_ts=None, limit=50):
        return [r for r in self._rows if not since_ts or float(r.get("ts") or 0) >= since_ts][:limit]


async def _run_retrieve_once(config, spec, policy=None):
    return run_retrieve(config, spec, policy=policy)


def run_retrieve(config, spec, policy=None):
    """重跑一条检索用例（depth=retrieve）：纯本地计算，返回 {"picked", "promise", "skipped"} 的 JSON。

    调参器（regular/tune.py）在本进程里直接调它，回放子进程走 _run_retrieve_once —— 同一份逻辑。
    """
    import recall as recall_mod

    rows = [dict(r) for r in spec.get("rows") or ()]
    kwargs = dict(names=tuple(spec.get("names") or ()), now=float(spec.get("now") or time.time()),
                  max_items=spec.get("max_items"), lookback_days=spec.get("lookback_days"),
                  exclude_text=spec.get("exclude_text") or "")
    promises = _PromiseList(spec.get("promises"))
    with ExitStack() as stack:
        # 环境默认值也是现场的一部分；策略内的显式 max_items/lookback_days 仍可覆盖它们。
        defaults = spec.get("defaults") or {}
        for key, attr in (("max_items", "MEMORY_RECALL_MAX_ITEMS"),
                          ("lookback_days", "MEMORY_RECALL_LOOKBACK_DAYS")):
            if key in defaults:
                stack.enter_context(patch.object(recall_mod.config, attr, defaults[key]))
        if hasattr(recall_mod, "select"):
            if policy is not None:
                kwargs["policy"] = policy
            sel = recall_mod.select("REPLAY_GROUP", spec.get("query") or "", rows=rows,
                                    promises=promises, **kwargs)
            out = {"picked": [str(i["row"].get("message_id")) for i in sel["picked"]],
                   "promise": (sel.get("promise") or {}).get("what"),
                   "skipped": sel.get("skipped") or ""}
        else:
            # 走旧版自己的 _pick，只观察被选行。正文可能重复、截断或脱敏，不能逆推 ID。
            picked = []
            original_pick = recall_mod._pick
            def capture_pick(*args, **kw):
                result = original_pick(*args, **kw)
                picked.extend(str(row.get("message_id")) for row in result)
                return result
            stack.enter_context(patch.object(recall_mod, "_pick", capture_pick))
            stack.enter_context(patch.object(recall_mod.config, "MEMORY_RECALL_ENABLED", True))
            note = recall_mod.build_note("REPLAY_GROUP", spec.get("query") or "",
                                         archive=_RowArchive(rows), promises=promises, **kwargs)
            has_promise = any(line.startswith("- 承诺：") for line in note.splitlines())
            out = {"picked": picked, "promise": has_promise or None, "skipped": ""}
    return json.dumps(out, ensure_ascii=False)


async def replay_item(item, *, n=1, retries=1, bot=None, runtime=None, config=None):
    """对单条用例（或单轮输入）抽样 n 次，返回 {"id": ..., "samples": [...], "errors": int}。"""
    depth = item.get("depth") or ("triage" if item.get("kind") == "triage" else "generate")
    if depth == "retrieve":
        if config is None:
            import config as _config
            config = _config
        out = await _run_retrieve_once(config, item.get("retrieve") or {})
        return {"id": item.get("id"), "samples": [out] * max(1, int(n)), "errors": 0}
    if bot is None:
        import bot as _bot
        import config as _config
        import runtime as _runtime
        bot, runtime, config = _bot, _runtime, _config
    samples = []
    errors = 0
    for _ in range(max(1, int(n))):
        if depth == "message":
            out = await _run_message_once(bot, runtime, config, item.get("messages") or (),
                                          retries=retries, preset_quotes=item.get("quotes") or (),
                                          self_openid=item.get("self_openid"))
        elif depth == "triage":
            spec = item.get("triage") or item.get("turn") or item
            out = await _run_triage_once(runtime, config, spec, retries=retries)
        else:
            turn = item.get("turn") if "turn" in item else item
            out = await _run_generate_once(bot, runtime, config, turn, retries=retries)
        if out is None:
            errors += 1
        samples.append(out)
    return {"id": item.get("id") or item.get("turn_id"), "samples": samples, "errors": errors}


async def replay_batch(items, *, n=1, concurrency=6, retries=1):
    import config
    bot = runtime = None
    if any(item.get("depth") != "retrieve" for item in items):
        import bot as bot_mod
        import runtime as runtime_mod
        bot, runtime = bot_mod, runtime_mod
        if hasattr(runtime, "TURNS"):
            runtime.TURNS.enabled = False
        if hasattr(runtime, "INTERACTIONS"):
            runtime.INTERACTIONS.enabled = False
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(item):
        async with sem:
            return await replay_item(item, n=n, retries=retries,
                                     bot=bot, runtime=runtime, config=config)

    return await asyncio.gather(*(one(item) for item in items))


def _activate_code_dir(code_dir: str):
    """把受测代码目录设为唯一优先代码源，与当前评测驱动的工作区隔离。"""
    target = str(Path(code_dir).resolve())
    harness_pkg = str(Path(__file__).resolve().parent)
    harness_root = str(Path(__file__).resolve().parent.parent)
    cleaned = []
    for entry in sys.path:
        resolved = str(Path(entry or ".").resolve())
        if resolved == harness_pkg:
            continue
        if resolved == harness_root and target != harness_root:
            continue
        cleaned.append(entry)
    sys.path[:] = [target, *cleaned]
    for mod_name in list(sys.modules):
        if mod_name in ("bot", "prompts", "config", "runtime", "regular") or mod_name.startswith("regular."):
            sys.modules.pop(mod_name, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Subprocess worker for regular eval")
    parser.add_argument("--code-dir", required=True, help="target code checkout for sys.path[0]")
    parser.add_argument("--ablate", default="", help="rule id to ablate from prompts")
    parser.add_argument("--override-json", default="", help="JSON dict of prompts attributes to override")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--input", required=True, help="input JSON file with items list")
    parser.add_argument("--output", required=True, help="output JSON file")
    args = parser.parse_args(argv)

    rules_mod = _load_harness_rules()
    code_dir = str(Path(args.code_dir).resolve())
    _activate_code_dir(code_dir)
    import prompts
    items = json.loads(Path(args.input).read_text(encoding="utf-8"))
    needs_model_path = any(item.get("depth") != "retrieve" for item in items)
    if needs_model_path:
        import bot

    ablated = None
    prompts_file = Path(code_dir) / "prompts.py"
    if args.ablate:
        rule = rules_mod.ablate(
            args.ablate, prompts_mod=prompts,
            path=prompts_file if prompts_file.is_file() else None)
        ablated = {"id": rule["id"], "chars": rule["chars"]}
    if args.override_json:
        for attr, val in json.loads(args.override_json).items():
            setattr(prompts, attr, val)
    fake_reply = os.environ.get("REGULAR_FAKE_MODEL_REPLY")
    if fake_reply is not None and needs_model_path:
        async def _fake_call(messages, max_tokens, **kw):
            return fake_reply
        bot.call_model = _fake_call

    head = rules_mod.head_stats(
        prompts_mod=prompts, path=prompts_file if prompts_file.is_file() else None)
    results = asyncio.run(replay_batch(items, n=args.n, concurrency=args.concurrency))
    payload = {"head": head, "ablated": ablated, "results": results}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
