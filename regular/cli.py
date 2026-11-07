"""Small operator entry point; no QQ/model imports until a command needs them."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shlex
import sys
import time
import warnings

from . import __version__


def load_config(directory):
    os.environ["REGULAR_HOME"] = str(Path(directory).expanduser().resolve())
    return importlib.import_module("config")


def _initial_values():
    return {"QQ_APP_ID": "", "QQ_APP_SECRET": "", "BOT_NAMES": "",
            "AI_PROVIDER": "openai", "OPENAI_BASE_URL": "", "OPENAI_API_KEY": "",
            "OPENAI_MODELS": "", "ZHIPU_API_KEY": "", "GEMINI_API_KEY": "",
            "FALLBACK_PROVIDER": "", "SSL_VERIFY": "true", "VISION_ENABLED": "true"}


def _prompt_configuration(values, directory, *, missing_only=False, fresh=True):
    """Share init's small question flow; setup fills only absent effective values."""
    from regular.keys import read_api_keys

    blocked = []
    def effective(name):
        value = os.environ.get(name, values.get(name)) if missing_only else values.get(name)
        return value or ""

    def ask(name, label, *, secret=False):
        if missing_only and effective(name).strip():
            return
        if missing_only and name in os.environ:
            blocked.append(name)
            return
        while True:
            if secret:
                # Refuse getpass's echoing fallback if a terminal becomes unavailable.
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    value = getpass.getpass(label)
            else:
                value = input(label).strip()
            if value.strip():
                values[name] = value
                return
            print("This setting is required; enter a value or press Ctrl+C to cancel.")

    ask("QQ_APP_ID", "QQ App ID: ")
    ask("QQ_APP_SECRET", "QQ App secret (hidden): ", secret=True)
    if fresh and (not missing_only or not effective("BOT_NAMES")):
        if not missing_only or "BOT_NAMES" not in os.environ:
            values["BOT_NAMES"] = input("Bot names (optional, comma separated): ").strip()
    if not missing_only or (fresh and "AI_PROVIDER" not in os.environ):
        while True:
            provider = input("Provider [openai/zhipu/gemini, default openai]: ").strip().lower() or "openai"
            if provider in ("openai", "zhipu", "gemini"):
                values["AI_PROVIDER"] = provider
                break
            print("Choose openai, zhipu or gemini; Enter uses openai.")
    provider = effective("AI_PROVIDER").strip().lower() or "openai"
    if provider not in ("openai", "zhipu", "gemini"):
        raise ValueError("Provider must be openai, zhipu or gemini")
    if provider == "openai":
        ask("OPENAI_BASE_URL", "OpenAI-compatible base URL (include /v1 if required): ")
        ask("OPENAI_MODELS", "Chat model IDs (ordered, comma separated): ")
    key_name = f"{provider.upper()}_API_KEY"
    if not missing_only or not read_api_keys(directory / f"api_keys_{provider}.txt", effective(key_name).strip()):
        ask(key_name, "Provider API key (hidden): ", secret=True)
    return blocked


def _create_configuration(directory, values):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ".env"
    # JSON double-quoted strings are valid dotenv values, including escaped newlines.
    text = "# Created by regular init. Keep this file private.\n"
    text += "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in values.items()) + "\n"
    text += ("# Vision defaults to the first chat model of AI_PROVIDER. For a text-only chat model,\n"
             "# set VISION_MODEL to a vision-capable model; set VISION_PROVIDER too for another provider.\n"
             "# VISION_MODEL=\n# VISION_PROVIDER=\n"
             "# The first non-empty private message claims ownership; restrict QQ console access.\n")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Config already exists; kept unchanged: {target}. Complete missing settings with {_command(directory, 'setup')}.")
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
    (directory / "data").mkdir(exist_ok=True, mode=0o700)
    print(f"Created {target} (0600).")
    return True


def initialize(directory, *, interactive=False):
    """Create a private config once. Never overwrite it or display its secrets."""
    directory = Path(directory).expanduser().resolve()
    target = directory / ".env"
    if target.exists():
        print(f"Config already exists; kept unchanged: {target}. Complete missing settings with {_command(directory, 'setup')}.")
        return 0
    values = _initial_values()
    if interactive:
        _prompt_configuration(values, directory)
    if _create_configuration(directory, values):
        if interactive:
            print(f"Check configuration: {_command(directory, 'doctor')}.")
        else:
            print(f"Fill credentials in {target}, then run {_command(directory, 'doctor')}; or use {_command(directory, 'setup')}.")
    return 0


def _command(directory, command):
    return shlex.join(["regular", "--directory", str(directory), command])


def setup(directory):
    """Guide local configuration, then use the existing offline diagnostics."""
    from dotenv import dotenv_values, set_key

    directory = Path(directory).expanduser().resolve()
    target = directory / ".env"
    if not sys.stdin.isatty():
        print("Setup needs an interactive terminal; no credentials were read or changed.", file=sys.stderr)
        print(f"Create a template with {_command(directory, 'init')}, edit {target}, then run {_command(directory, 'doctor')}.", file=sys.stderr)
        return 1
    fresh = not target.exists()
    previous = _initial_values() if fresh else dict(dotenv_values(target, interpolate=False))
    values = dict(previous)
    try:
        blocked = _prompt_configuration(values, directory, missing_only=True, fresh=fresh)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("\nSetup cancelled; no configuration changes were saved.", file=sys.stderr)
        return 1
    if fresh:
        _create_configuration(directory, values)
    else:
        for name, value in values.items():
            if value != previous.get(name):
                set_key(target, name, value, quote_mode="always")
    for name in blocked:
        print(f"External environment variable {name} is empty and overrides .env; set it or unset it before retrying.", file=sys.stderr)
    config = load_config(directory)
    result = doctor(config)
    if result == 0:
        print(f"Ready to start: {_command(directory, 'run')}")
        print("Send the bot one private message first: ownership binds silently and that message is handled normally.")
        print("In the QQ console, enable C2C and the required group-message events for your bot.")
    else:
        print(f"Fix the reported settings, then retry {_command(directory, 'setup')}.", file=sys.stderr)
    return result


def _path_writable(path):
    path = Path(path)
    while not path.exists():
        parent = path.parent
        if parent == path:
            break
        path = parent
    return os.access(path, os.W_OK)


def _doctor_result(rows, network, as_json):
    result = {"ok": not any(r["state"] == "error" for r in rows), "network": network, "checks": rows}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"[{row['state'].upper()}] {row['check']}: {row['detail']}")
        print("No model inference was made.")
    return 0 if result["ok"] else 1


def doctor(config, *, network=False, as_json=False, dependency_error=None):
    """Inspect only config, dependencies and paths unless --network is explicit."""
    rows = []
    def add(name, state, detail):
        rows.append({"check": name, "state": state, "detail": detail})
    add("python", "ok" if sys.version_info >= (3, 10) else "error", sys.version.split()[0])
    dependencies_ready = True
    for module in ("dotenv", "botpy", "openai", "httpx", "aiohttp", "certifi"):
        present = importlib.util.find_spec(module) is not None
        dependencies_ready = dependencies_ready and present
        add(f"dependency.{module}", "ok" if present else "error", "installed" if present else "Install project dependencies: pip install .")
    if config is None:
        add("configuration", "error", f"Settings unavailable: missing dependency {dependency_error or 'unknown'}. Install project dependencies before retrying.")
        return _doctor_result(rows, False, as_json)
    for error in config.validate():
        add("configuration", "error", error)
    add("bot_names", "ok",
        ", ".join(config.BOT_NAMES) if config.BOT_NAMES else "No aliases; only platform nickname/mentions will trigger replies")
    add("owner_claim", "ok", "first non-empty private message; existing owner is retained; group messages cannot claim ownership")
    add("tls", "ok" if config.SSL_VERIFY else "warning", "verified" if config.SSL_VERIFY else "verification disabled")
    for name in ("STATE_FILE", "OWNER_FILE", "STATUS_FILE", "LOG_FILE", "ARCHIVE_DIR", "MEMORY_DIR"):
        path = getattr(config, name)
        add(f"path.{name}", "ok" if _path_writable(path) else "error", path)
    state = Path(config.STATE_FILE)
    if state.exists():
        try:
            value = json.loads(state.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("expected an object")
            add("state_json", "ok", "readable JSON object")
        except (OSError, ValueError) as error:
            add("state_json", "error", type(error).__name__)
    add("data_layout", "ok", config.DATA_DIR)
    capabilities = None
    if dependencies_ready:
        try:
            import providers
            capabilities = providers.configuration()
        except ImportError as error:
            dependencies_ready = False
            add("provider_dependencies", "error", f"Cannot import provider dependencies: {error.name or 'unknown'}")
    else:
        add("provider_capabilities", "warning", "Not inspected while dependencies are missing")
    if capabilities is not None:
        if capabilities.get("request_policy"):
            add("request_policy", "ok", json.dumps(capabilities["request_policy"], ensure_ascii=False))
        for name in capabilities["provider_chain"]:
            provider = capabilities["providers"][name]
            add(f"provider.{name}", "ok" if provider["key_count"] else "warning",
                f"{provider['key_count']} key(s); chat={','.join(provider['models'])}; "
                f"digest={','.join(provider['digest_models'])}; judge={','.join(provider['judge_models'])}; access unverified")
        vision = capabilities["vision"]
        enabled = config.VISION_ENABLED and vision["configured"]
        origin = "inherited from first chat model" if config.VISION_MODEL_INHERITED else "explicit vision model"
        add("vision", "ok" if enabled or not config.VISION_ENABLED else "warning",
            f"{vision['provider']}/{vision['model']}; {origin}; image capability and access unverified" if enabled else
            ("disabled by configuration" if not config.VISION_ENABLED else
             "unavailable: vision endpoint, model or key missing; text chat remains available"))
    add("qq_permissions", "manual", "QQ console: enable C2C and required group events; restrict the bot's usage scope to intended users before first private contact. Local checks cannot verify these switches.")
    if network and not dependencies_ready:
        add("network", "warning", "Skipped: dependencies must be installed first")
    if network and dependencies_ready:
        import httpx
        names = dict.fromkeys(n for n in (config.AI_PROVIDER, config.FALLBACK_PROVIDER,
                                         config.VISION_PROVIDER if config.VISION_ENABLED else "") if n)
        with httpx.Client(verify=config.SSL_VERIFY, timeout=10.0) as client:
            for name in names:
                preset = config.PROVIDER_PRESETS[name]
                keys = config.load_api_keys(name)
                if not preset["base_url"]:
                    add(f"network.{name}", "warning", "base URL missing; skipped")
                    continue
                if not keys:
                    add(f"network.{name}", "warning", "no configured key; skipped")
                    continue
                try:
                    response = client.get(preset["base_url"].rstrip("/") + "/models",
                                          headers={"Authorization": f"Bearer {keys[0]}"})
                    add(f"network.{name}", "ok" if response.is_success else "error",
                        f"metadata HTTP {response.status_code}; no inference; does not prove model quota/capabilities")
                except (httpx.HTTPError, httpx.InvalidURL) as error:
                    add(f"network.{name}", "error", type(error).__name__)
    return _doctor_result(rows, network and dependencies_ready, as_json)


def status(config, *, as_json=False):
    path = Path(config.STATUS_FILE)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema")
        age = max(0, time.time() - float(snapshot["updated_at"]))
    except FileNotFoundError:
        print(json.dumps({"available": False, "path": str(path)}) if as_json else
              f"No runtime snapshot at {path}. Start regular run to publish status.")
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        if as_json:
            print(json.dumps({"available": False, "path": str(path), "error": "invalid_snapshot"}))
        else:
            print("Invalid/unreadable runtime snapshot; run regular doctor.", file=sys.stderr)
        return 1
    result = {"available": True, "age_seconds": round(age, 1), "stale": age > 120, **snapshot}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Runtime: {snapshot.get('lifecycle', 'unknown')} | snapshot age {age:.0f}s" + (" (stale)" if age > 120 else ""))
        print(f"Gateway: {snapshot.get('gateway_connected', 'unknown')} | PID: {snapshot.get('pid', 'unknown')}")
        for name in ("providers", "budget", "tasks"):
            if name in snapshot:
                print(f"{name}: {json.dumps(snapshot[name], ensure_ascii=False)}")
    return 1 if age > 120 or snapshot.get("lifecycle") in ("stopped", "error") else 0


async def demo(messages):
    # The shared adapter owns routing/state/budget/sending; this CLI has no chat rules.
    from regular.demo import DemoSession
    identity = {"user_id": "alice", "group_id": "demo-group"}
    async with DemoSession() as session:
        help_text = "Just chat, e.g. 小王 在吗 / 叫我阿强; /user NAME and /group ID switch identity; /quit exits."
        print("Local demo: no QQ connection or model requests. " + help_text)

        async def dispatch(message):
            command, _, value = message.strip().partition(" ")
            if command in ("/quit", "/exit"):
                return True
            if command == "/help":
                print(help_text)
            elif command in ("/user", "/group"):
                value = value.strip()
                if not value:
                    print(f"Usage: {command} " + ("NAME" if command == "/user" else "ID"))
                else:
                    identity["user_id" if command == "/user" else "group_id"] = value
                    print(f"Now speaking as {identity['user_id']} in {identity['group_id']}.")
            elif message.strip():
                print(await session.reply(message, **identity))
            return False

        if messages:
            for message in messages:
                print(f"{identity['user_id']}@{identity['group_id']}> {message}")
                if await dispatch(message):
                    break
            return 0
        while True:
            try:
                message = input(f"{identity['user_id']}@{identity['group_id']}> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if await dispatch(message):
                break
    return 0


def coverage(config, *, as_json=False):
    from regular.observations import personal_memory_coverage
    report = personal_memory_coverage(config.INTERACTION_LOG_DIR)
    stats = report["overall"]
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        rate = stats["injection_rate"]
        label = "unavailable (no measured model replies)" if rate is None else f"{rate:.2%} (among traced replies)"
        print(f"Personal-quote injection coverage: {label}")
        print(f"Delivered model replies: {stats['delivered_model_replies']} | injected: {stats['injected']} | not injected: {stats['not_injected']} | unknown: {stats['unknown']}")
        print("Scope: observed confirmed model replies, not eligibility or personalization benefit.")
        if not report["files_read"]:
            print("No readable interaction journal files; unavailable is not 0% coverage.")
        if report["read_errors"]:
            print("Warning: journal read errors; results cover readable rows only.")
    return 1 if report["read_errors"] or not stats["measured"] else 0


def calibration(config, *, site="banter", as_json=False):
    """只读核对：判断说的概率和随后真实发生的事对不对得上。

    不连 QQ、不调模型、不改日志。样本不足时报条数而不是编出来的比例。
    """
    from regular.calibration import render, report
    out = report(config.INTERACTION_LOG_DIR, site=site)
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render(out))
    if out["read_errors"]:
        return 1
    return 0 if out["counts"]["labelled"] else 1


def checkup(config, *, day=None, as_json=False, write=True, label=None, note=""):
    """每日体检（docs/self-iteration.md ②）：只读、不联网、不调模型。

    --label ID yes|no 给一条候选标「是 / 不是」，体检报告据此统计每种信号的准确率。
    """
    from regular import checkup as checkup_mod
    if label:
        finding_id, verdict = label
        found = checkup_mod.find_finding(config.CHECKUP_DIR, finding_id)
        if found is None:
            print(f"No checkup finding {finding_id} in {config.CHECKUP_DIR}; run regular checkup first.",
                  file=sys.stderr)
            return 1
        checkup_mod.add_label(config.CHECKUP_DIR, finding_id, verdict,
                              signal=found["signal"], note=note)
        print(f"Labelled {finding_id} ({found['label']}) as {verdict}.")
        return 0
    out = checkup_mod.report(
        checkup_mod.resolve_day(day), interaction_dir=config.INTERACTION_LOG_DIR,
        turn_dir=config.TURN_LOG_DIR, archive_dir=config.ARCHIVE_DIR,
        state_file=config.STATE_FILE, checkup_dir=config.CHECKUP_DIR,
        policy=checkup_mod.load_policy(config.CHECKUP_POLICY_FILE))
    path = checkup_mod.write(out, config.CHECKUP_DIR) if write else None
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(checkup_mod.render(out))
        if path:
            print(f"Report written to {path}")
    return 0


def rules_command(*, as_json=False):
    """列出 prompts.py 里的规则账本和稳定头长度（只读，不调模型）。"""
    from regular import rules as rules_mod
    items = [
        {k: v for k, v in r.items() if k != "text"}
        for r in rules_mod.load_rules()
    ]
    head = rules_mod.head_stats()
    payload = {"head": head, "rules": items}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Stable head: {head['total_chars']} chars | tracked rules: {head['rules_count']} ({head['rules_chars']} chars)")
        for r in items:
            print(f"  - {r['id']} ({r['chars']}c, {r['date']}, {r['doc']}) cases={','.join(r['cases'])}")
    return 0


def case_command(config, args):
    """管理 data/cases 用例库：add（从 turn_id 存成用例）、seed（写入文档种子用例）、list。"""
    from regular import cases as cases_mod
    if args.case_action == "seed":
        written = cases_mod.seed_cases_into(config.CASES_DIR, overwrite=args.overwrite)
        print(f"Seeded {written} case(s) into {config.CASES_DIR} (total {len(cases_mod.SEED_CASES)} seed definitions).")
        return 0
    if args.case_action == "list":
        loaded = cases_mod.load_cases(config.CASES_DIR, selector=args.cases, include_seeds=True)
        if args.json:
            print(json.dumps([{"id": c["id"], "depth": c.get("depth", "generate"),
                               "incident": c.get("incident", ""), "tags": c.get("tags", [])}
                              for c in loaded], ensure_ascii=False, indent=2))
        else:
            for c in loaded:
                print(f"{c['id']}\t[{c.get('depth', 'generate')}]\t{','.join(c.get('tags') or ())}\t{c.get('incident', '')}")
            print(f"Total: {len(loaded)} case(s).")
        return 0
    if args.case_action == "add":
        checks = []
        for pat in args.must or ():
            checks.append({"must": pat})
        for pat in args.must_not or ():
            checks.append({"must_not": pat})
        for tag in args.tag_present or ():
            checks.append({"tag_present": tag})
        for tag in args.tag_absent or ():
            checks.append({"tag_absent": tag})
        for j in args.judge or ():
            checks.append({"judge": j})
        for c_item in args.choice or ():
            qname, sep, val = str(c_item).partition("=")
            if not sep or not qname.strip() or not val.strip():
                raise ValueError(f"invalid --choice {c_item!r}; expected QUESTION=VALUE")
            checks.append({"choice": {qname.strip(): val.strip()}})
        for c_item in args.choice_not or ():
            qname, sep, val = str(c_item).partition("=")
            if not sep or not qname.strip() or not val.strip():
                raise ValueError(f"invalid --choice-not {c_item!r}; expected QUESTION=VALUE")
            checks.append({"choice_not": {qname.strip(): val.strip()}})
        for mid in getattr(args, "relevant", None) or ():
            checks.append({"retrieved": mid.strip()})
        for mid in getattr(args, "irrelevant", None) or ():
            checks.append({"not_retrieved": mid.strip()})
        if getattr(args, "expect_none", False) and getattr(args, "relevant", None):
            raise ValueError("--expect-none cannot be combined with --relevant")
        if getattr(args, "expect_none", False):
            checks.append({"retrieved_none": True})
        if getattr(args, "expect_promise", False):
            checks.append({"promise": True})
        if getattr(args, "no_promise", False):
            checks.append({"promise": False})
        if getattr(args, "max_picked", None) is not None:
            if args.max_picked < 0:
                raise ValueError("--max-picked must be non-negative")
            checks.append({"max_picked": int(args.max_picked)})
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        try:
            if getattr(args, "turn", None):
                case = cases_mod.create_case_from_turn(
                    config.TURN_LOG_DIR, args.turn, args.id,
                    incident=args.incident or "", tags=tags, checks=checks, day=args.day,
                    expect_recorded=args.expect_recorded, archive_dir=config.ARCHIVE_DIR,
                    complete_relevance=getattr(args, "complete_relevance", False))
            else:
                case = cases_mod.create_case_from_message(
                    config.ARCHIVE_DIR, args.message, args.id,
                    interaction_dir=config.INTERACTION_LOG_DIR,
                    turn_dir=config.TURN_LOG_DIR, day=args.day,
                    context_messages=getattr(args, "context", 6),
                    incident=args.incident or "", tags=tags, checks=checks,
                    state_file=config.STATE_FILE)
        except (KeyError, ValueError) as err:
            print(str(err), file=sys.stderr)
            return 1
        path = cases_mod.save_case(config.CASES_DIR, case)
        print(f"Saved case {case['id']} -> {path}")
        return 0
    return 1


def eval_command(config, args):
    """跑用例评测、整天回放、规则消融或窄口子措辞搜索（docs/self-iteration.md ③④⑦）。"""
    from regular import eval as eval_mod
    if args.n < 1:
        raise ValueError("eval -n 必须至少为 1")
    if args.plan:
        # 必须先于任何付费分支；不支持的组合也只能报错，绝不能执行真实评测。
        if args.search_wording:
            print("--search-wording 暂不支持 --plan；未调用模型或写入报告。", file=sys.stderr)
            return 1
        if args.rejudge:
            for line in eval_mod.describe_rejudge_traffic(config, args.rejudge):
                print(line)
            return 0
        from regular import cases as cases_mod
        import turns as turns_mod
        selected = []
        if not args.day or args.cases not in ("all", "none"):
            selected = cases_mod.load_cases(config.CASES_DIR, selector=args.cases, include_seeds=True)
        day_turns = 0
        if args.day:
            from regular import checkup as checkup_mod
            day_turns = sum(1 for t in turns_mod.read_day(config.TURN_LOG_DIR, checkup_mod.resolve_day(args.day))
                            if t.get("kind") == "reply")
        for line in eval_mod.describe_traffic(config, selected, n=args.n, day_turns=day_turns):
            print(line)
        return 0
    if args.search_wording:
        if not args.allow_paid:
            raise eval_mod.PaidEvaluationRequired(
                "eval --search-wording requires --allow-paid; it makes real model requests")
        out = eval_mod.search_wording(
            config, target=args.search_wording, k=args.candidates, n=args.n,
            write=not args.no_write)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(eval_mod.render_search_report(out))
            if out.get("report_path"):
                print(f"Report written to {out['report_path']}")
        return 0

    patterns = {}
    for item in args.pattern or ():
        name, sep, rx = str(item).partition("=")
        if not sep or not name.strip() or not rx:
            raise ValueError(f"invalid --pattern {item!r}; expected NAME=REGEX")
        patterns[name.strip()] = rx

    try:
        if args.rejudge:
            out = eval_mod.rejudge_report(config, args.rejudge, write=not args.no_write,
                                          allow_paid=args.allow_paid)
        else:
            out = eval_mod.run_eval(
                config, base=args.base, cand=args.cand, cases_selector=args.cases,
                day=args.day, n=args.n, ablate=args.ablate or "",
                patterns=patterns or None, write=not args.no_write,
                allow_paid=args.allow_paid)
    except eval_mod.PaidEvaluationRequired:
        raise
    except RuntimeError as err:   # 盲评预检没过：原因原样说出来，别吞成一句泛泛的 doctor 提示
        print(str(err), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(eval_mod.render_report(out, samples=False))
        if out.get("report_path"):
            print(f"Report written to {out['report_path']}（末尾有逐条输出：新旧样本和盲评理由）")
    return 0


def tune_command(config, args):
    """离线调参：体检阈值/词表（靠标注）或旧账检索策略（靠检索用例）。只读、不联网、不调模型。"""
    from regular import tune as tune_mod
    if args.target == "checkup":
        out = tune_mod.tune_checkup(config, min_labels=args.min_labels, min_no=args.min_no,
                                    write=not args.no_write, benchmark=args.benchmark)
        render = tune_mod.render_checkup
    else:
        out = tune_mod.tune_recall(config, selector=args.cases, folds=args.folds,
                                   drift_days=args.drift_days, write=not args.no_write)
        render = tune_mod.render_recall
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render(out))
        if out.get("report_path"):
            print(f"Report written to {out['report_path']}")
    return 0


def policy_command(config, args):
    """查看、批准（apply）或重置 data/policies/ 下的体检 / 检索策略文件。"""
    from regular import tune as tune_mod
    if args.policy_action == "show":
        targets = ("checkup", "recall") if args.name == "all" else (args.name,)
        rows = [tune_mod.show_policy(config, name) for name in targets]
        if args.json:
            print(json.dumps(rows[0] if len(rows) == 1 else rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                src = f"applied {r.get('applied_at')} from {r.get('source')}" if r["file"] else "defaults"
                print(f"{r['name']}: {src} ({r['path']})")
                for k, v in r["overrides"].items():
                    print(f"  {k} = {json.dumps(v, ensure_ascii=False)}")
        return 0
    if args.policy_action == "apply":
        res = tune_mod.apply_proposal(config, args.report)
        print(f"Applied {res['name']} policy -> {res['path']}"
              + (f" (previous archived to {res['previous']})" if res.get("previous") else ""))
        return 0
    if args.policy_action == "reset":
        prev = tune_mod.reset_policy(config, args.name)
        print(f"Reset {args.name} policy to defaults"
              + (f" (archived {prev})" if prev else " (was already default)"))
        return 0
    return 1


def manage_command(config, args, parser):
    """The maintainer's controls as data: switches, one-line states, today's numbers. No model, no network."""
    from regular import capabilities, presence
    if args.verb == "set":
        if args.flag not in capabilities.FLAGS or args.value not in ("on", "off"):
            parser.error("usage: manage set {" + ",".join(capabilities.FLAGS) + "} {on,off}")
        sections = capabilities.set_enabled(config, args.flag, args.value == "on")
    else:
        if args.verb != "show" or args.flag or args.value is not None:
            parser.error("usage: manage [show] | manage set FLAG {on,off}")
        sections = capabilities.public_settings(config)
    summary = presence.day_summary(config)
    print(json.dumps({**capabilities.overview(sections), "today": summary,
                      "today_text": presence.render_day_summary(summary)}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="regular", description="The Regular: local demo, configuration, diagnostics and QQ runtime")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--directory", default=os.getcwd(), help="deployment directory containing .env (default: current directory)")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init", help="create a private .env without overwriting existing config")
    init.add_argument("--interactive", action="store_true", help="prompt for credentials with hidden secret input")
    sub.add_parser("setup", help="guide configuration and run local checks; no network or inference")
    diag = sub.add_parser("doctor", help="check local configuration; no network by default")
    diag.add_argument("--network", action="store_true", help="GET provider metadata, never generate model output")
    diag.add_argument("--json", action="store_true")
    stat = sub.add_parser("status", help="read runtime's structured snapshot")
    stat.add_argument("--json", action="store_true")
    cov = sub.add_parser("coverage", help="read-only personal-quote injection coverage; no network or inference")
    cov.add_argument("--json", action="store_true")
    cal = sub.add_parser("calibration", help="read-only check of native-judgement probabilities against what actually happened; no network or inference")
    cal.add_argument("--site", choices=("banter", "callback"), default="banter",
                     help="which judgement to check (default: banter)")
    cal.add_argument("--json", action="store_true")
    chk = sub.add_parser("checkup", help="read-only daily checkup: list suspicious moments from local journals; no network or inference")
    chk.add_argument("--day", help="YYYY-MM-DD, MM-DD, today or yesterday (default: today)")
    chk.add_argument("--json", action="store_true")
    chk.add_argument("--no-write", action="store_true", help="print only; do not write data/checkups")
    chk.add_argument("--label", nargs=2, metavar=("ID", "VERDICT"),
                     help="label a finding yes/no for per-signal precision")
    chk.add_argument("--note", default="", help="optional note stored with --label")
    rls = sub.add_parser("rules", help="read-only list of tracked prompt rules and stable-head length; no network or inference")
    rls.add_argument("--json", action="store_true")
    cs = sub.add_parser("case", help="manage replay cases in data/cases (add from turn, seed from docs, list)")
    cs_sub = cs.add_subparsers(dest="case_action", required=True)
    cs_add = cs_sub.add_parser("add", help="save a recorded turn or archived message as a replay case")
    cs_src = cs_add.add_mutually_exclusive_group(required=True)
    cs_src.add_argument("--turn", help="turn_id from data/turns (generate or triage case)")
    cs_src.add_argument("--message", help="message_id from data/archive or data/interactions (message-level case)")
    cs_add.add_argument("--context", type=int, default=6,
                        help="number of preceding group messages to include with --message (default: 6)")
    cs_add.add_argument("--id", required=True, help="case id")
    cs_add.add_argument("--day", help="optional day to look up turn_id or message_id")
    cs_add.add_argument("--incident", default="", help="incident doc path, e.g. docs/quotes.md")
    cs_add.add_argument("--tags", default="", help="comma-separated tags")
    cs_add.add_argument("--must", action="append", help="regex that must match")
    cs_add.add_argument("--must-not", action="append", help="regex that must not match")
    cs_add.add_argument("--tag-present", action="append", help="expression tag that must be present")
    cs_add.add_argument("--tag-absent", action="append", help="expression tag that must be absent")
    cs_add.add_argument("--choice", action="append", help="triage choice check QUESTION=VALUE")
    cs_add.add_argument("--choice-not", action="append", help="triage choice_not check QUESTION=VALUE")
    cs_add.add_argument("--judge", action="append", help="pairwise blind judge question")
    cs_add.add_argument("--relevant", action="append", help="retrieve case: message_id that must be picked")
    cs_add.add_argument("--irrelevant", action="append", help="retrieve case: message_id that must not be picked")
    cs_add.add_argument("--expect-none", action="store_true", help="retrieve case: nothing should be retrieved")
    promise_expectation = cs_add.add_mutually_exclusive_group()
    promise_expectation.add_argument("--expect-promise", action="store_true", help="retrieve case: promise line must be included")
    promise_expectation.add_argument("--no-promise", action="store_true", help="retrieve case: promise line must not be included")
    cs_add.add_argument("--max-picked", type=int, help="retrieve case: maximum number of picked rows")
    cs_add.add_argument("--complete-relevance", action="store_true",
                        help="retrieve case: --relevant lists the complete relevant set; empty set needs --expect-none")
    cs_add.add_argument("--expect-recorded", action="store_true",
                        help="triage or retrieve case: use the recorded live answer as the expected one "
                             "(default: you must give --choice/--choice-not or --relevant/--irrelevant)")
    cs_seed = cs_sub.add_parser("seed", help="write built-in seed cases into data/cases")
    cs_seed.add_argument("--overwrite", action="store_true")
    cs_list = cs_sub.add_parser("list", help="list available cases")
    cs_list.add_argument("--cases", default="all", help="selector: all, tag, or glob")
    cs_list.add_argument("--json", action="store_true")
    ev = sub.add_parser("eval", help="compare versions; pure local retrieve cases need no --allow-paid")
    ev.add_argument("--base", default=".", help="base git ref or directory (default: .)")
    ev.add_argument("--cand", default=".", help="candidate git ref or directory (default: .)")
    ev.add_argument("--cases", default="all", help="case selector: all, tag, glob, or none")
    ev.add_argument("-n", type=int, default=4, help="samples per case per version (default: 4)")
    ev.add_argument("--day", help="replay all recorded turns of a day (YYYY-MM-DD or MM-DD)")
    ev.add_argument("--ablate", help="rule id from prompts.py to ablate in candidate")
    ev.add_argument("--search-wording", choices=("remind", "banter"),
                    help="search better wording for a narrow deterministic rule")
    ev.add_argument("--candidates", type=int, default=6, help="number of wording candidates to propose (default: 6)")
    ev.add_argument("--pattern", action="append", help="custom whole-day regex NAME=REGEX")
    ev.add_argument("--no-write", action="store_true", help="do not write report files to data/evals")
    ev.add_argument("--json", action="store_true")
    ev.add_argument("--allow-paid", action="store_true", help="authorize real model requests and quota consumption")
    ev.add_argument("--plan", action="store_true",
                    help="print what this run would send where (cases, live vs synthetic, models, request counts); no model calls")
    ev.add_argument("--rejudge", metavar="REPORT",
                    help="reuse all samples of a saved report (.json path or data/evals stamp); rerun only failed judge pairs")
    tn = sub.add_parser("tune", help="read-only offline tuning for checkup thresholds or recall policy; no network or inference")
    tn.add_argument("target", choices=("checkup", "recall"), help="which local policy to tune")
    tn.add_argument("--cases", default="retrieve", help="recall: case selector (default: retrieve)")
    tn.add_argument("--folds", type=int, default=5, help="recall: holdout folds (default: 5)")
    tn.add_argument("--drift-days", type=int, default=7, help="recall: days of live recall turns to compare (default: 7)")
    tn.add_argument("--min-labels", type=int, default=6, help="checkup: minimum labels per signal before tuning (default: 6)")
    tn.add_argument("--min-no", type=int, default=2, help="checkup: minimum 'no' labels per signal before tuning (default: 2)")
    tn.add_argument("--benchmark", help="checkup: independently labeled frozen benchmark JSON")
    tn.add_argument("--no-write", action="store_true", help="do not write report files to data/evals")
    tn.add_argument("--json", action="store_true")
    pol = sub.add_parser("policy", help="inspect, apply, or reset local checkup / recall policies in data/policies")
    pol_sub = pol.add_subparsers(dest="policy_action", required=True)
    pol_show = pol_sub.add_parser("show", help="show effective policy and active overrides")
    pol_show.add_argument("name", nargs="?", default="all", choices=("all", "checkup", "recall"))
    pol_show.add_argument("--json", action="store_true")
    pol_apply = pol_sub.add_parser("apply", help="apply a tune proposal JSON into data/policies")
    pol_apply.add_argument("report", help="path to tune-*.json report (or archived policy JSON)")
    pol_reset = pol_sub.add_parser("reset", help="reset policy to code defaults (archives current file)")
    pol_reset.add_argument("name", choices=("checkup", "recall"))
    panel = sub.add_parser("panel", help="serve the local maintenance panel on loopback")
    panel.add_argument("--host", default="127.0.0.1", help="loopback address (default: 127.0.0.1)")
    panel.add_argument("--port", type=int, default=8765, help="local port (default: 8765)")
    panel.add_argument("--open", action="store_true", dest="open_browser",
                       help="open the panel in the default browser")
    pet = sub.add_parser("pet", help="open the desktop pet; prepare a local settings service if needed")
    pet.add_argument("--port", type=int, default=8765, help="local settings service port (default: 8765)")
    pet.add_argument("--browser", action="store_true", help="use a compact web page instead of a native floating window")
    pet_cue = sub.add_parser("pet-cue", help="ask the running desktop pet to act or say one line (for coding agents)")
    pet_cue.add_argument("action", nargs="?", default="", help="e.g. cheer, wave, laptop, think, sleep; omit to only speak")
    pet_cue.add_argument("--say", default="", help="one short line shown in her speech bubble (max 120 chars)")
    pet_cue.add_argument("--port", type=int, default=8765, help="local settings service port (default: 8765)")
    notify = sub.add_parser("pet-notify", help="receive a local Codex or Claude Code completion hook")
    notify.add_argument("--tool", choices=("codex", "claude"), required=True)
    notify.add_argument("--port", type=int, default=8765)
    notify.add_argument("--status", choices=("finished", "attention"), help="send an explicit manual signal")
    notify.add_argument("payload", nargs="?", help="notify JSON argument; hooks may use stdin")
    mg = sub.add_parser("manage", help="what she can do and what she did today, for coding agents (JSON); "
                                       "'manage set image off' flips one switch like the panel does")
    mg.add_argument("verb", nargs="?", choices=("show", "set"), default="show")
    mg.add_argument("flag", nargs="?", default="", help="vision, image, gif, voice_input, transcription, voice_output")
    mg.add_argument("value", nargs="?", choices=("on", "off"), help="with set")
    sub.add_parser("run", help="validate and connect the QQ runtime")
    svc = sub.add_parser("service", help="generate a launchd or systemd definition; never install or start it")
    svc.add_argument("kind", choices=("launchd", "systemd"))
    svc.add_argument("--output", required=True, help="path for the generated service definition")
    dm = sub.add_parser("demo", help="offline interactive demo through the production message handler")
    dm.add_argument("messages", nargs="*", help="optional messages for a noninteractive demo")
    probe = sub.add_parser("probe", help="run an explicitly authorized paid research probe")
    probe.add_argument("name", help="file stem in tests/probes, e.g. probe_cache")
    probe.add_argument("--allow-paid", action="store_true", help="authorize real model requests and quota consumption")
    args = parser.parse_args(argv)
    if args.command is None:
        args.command, args.port, args.browser = "pet", 8765, False
    try:
        if args.command == "init":
            return initialize(args.directory, interactive=args.interactive)
        if args.command == "setup":
            return setup(args.directory)
        if args.command == "demo":
            return asyncio.run(demo(args.messages))
        if args.command == "service":
            from regular.service import main as generate_service
            return generate_service([args.kind, "--directory", args.directory, "--output", args.output])
        try:
            config = load_config(args.directory)
        except ImportError as error:
            if args.command == "doctor":
                return doctor(None, network=args.network, as_json=args.json, dependency_error=error.name)
            raise
        if args.command == "doctor":
            return doctor(config, network=args.network, as_json=args.json)
        if args.command == "status":
            return status(config, as_json=args.json)
        if args.command == "coverage":
            return coverage(config, as_json=args.json)
        if args.command == "calibration":
            return calibration(config, site=args.site, as_json=args.json)
        if args.command == "checkup":
            if args.label and args.label[1] not in ("yes", "no"):
                parser.error("--label VERDICT must be yes or no")
            return checkup(config, day=args.day, as_json=args.json, write=not args.no_write,
                           label=args.label, note=args.note)
        if args.command == "rules":
            return rules_command(as_json=args.json)
        if args.command == "case":
            return case_command(config, args)
        if args.command == "eval":
            from regular import eval as eval_mod
            try:
                return eval_command(config, args)
            except eval_mod.PaidEvaluationRequired as error:
                parser.error(str(error))
        if args.command == "tune":
            if args.target != "checkup" and args.benchmark:
                parser.error("--benchmark is only supported by tune checkup")
            if args.target == "checkup" and (args.min_labels < 1 or args.min_no < 0):
                parser.error("--min-labels must be positive and --min-no must be non-negative")
            if args.target == "recall" and (args.folds < 2 or args.drift_days < 1):
                parser.error("--folds must be at least 2 and --drift-days must be positive")
            return tune_command(config, args)
        if args.command == "policy":
            return policy_command(config, args)
        if args.command == "panel":
            if args.host not in ("127.0.0.1", "localhost", "::1"):
                parser.error("panel only binds to loopback; use --host 127.0.0.1")
            import panel as panel_module
            return panel_module.run(config, host=args.host, port=args.port,
                                    open_browser=args.open_browser)
        if args.command == "pet":
            from regular import desktop
            return desktop.run(config, port=args.port, browser=args.browser)
        if args.command == "pet-cue":
            from regular import desktop
            return desktop.cue(config, action=args.action, text=args.say, port=args.port)
        if args.command == "pet-notify":
            from regular.pet_notifications import main as notify_main
            options = ["--tool", args.tool, "--directory", args.directory, "--port", str(args.port)]
            if args.status:
                options += ["--status", args.status]
            if args.payload is not None:
                options.append(args.payload)
            return notify_main(options)
        if args.command == "manage":
            return manage_command(config, args, parser)
        if args.command == "run":
            errors = config.validate()
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                print(f"Run {_command(Path(args.directory).expanduser().resolve(), 'setup')}.", file=sys.stderr)
                return 1
            import bot
            return bot.run_bot() or 0
        if not args.allow_paid:
            parser.error("probe requires --allow-paid; it makes real model requests")
        source_root = Path(__file__).resolve().parent.parent
        candidate = source_root / "tests" / "probes" / f"{args.name}.py"
        if Path(args.name).name != args.name or not candidate.is_file():
            parser.error("unknown probe; inspect tests/probes in the source checkout")
        previous_argv = sys.argv
        try:
            # Probe scripts have their own argv semantics (some treat every arg
            # as a model); never leak operator CLI flags into their requests.
            sys.argv = [str(candidate)]
            runpy.run_path(str(candidate), run_name="__main__")
        finally:
            sys.argv = previous_argv
        return 0
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("\nConfiguration cancelled; retry in an interactive terminal.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"{type(error).__name__}: command failed; check paths/configuration with {_command(Path(args.directory).expanduser().resolve(), 'doctor')}", file=sys.stderr)
        return 1
