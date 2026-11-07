"""Translate local coding-agent hook events to fixed, content-free pet cues."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys


def cue_for_event(tool, event):
    """Only interpret event names; never inspect or forward transcript content."""
    if not isinstance(event, dict):
        return None
    kind = event.get("hook_event_name")
    if tool == "codex" and (kind == "Stop" or event.get("type") == "agent-turn-complete"):
        return "cheer", "Codex 这轮回复结束啦。"
    if tool == "claude" and kind == "Stop":
        return "cheer", "Claude Code 这轮回复结束啦。"
    if tool == "claude" and kind == "Notification" and event.get("notification_type") == "permission_prompt":
        return "think", "Claude Code 需要你看一下。"
    return None


def send_cue(directory, port, action, text):
    from regular.cli import load_config
    config = load_config(directory)
    from regular.desktop import cue
    # Hook stdout must be valid JSON, not cue's human-facing message.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cue(config, action=action, text=text, port=port)


def main(argv=None, *, stdin=None, sender=send_cue):
    parser = argparse.ArgumentParser(description="Send a fixed local coding-agent cue to a running pet")
    parser.add_argument("--tool", choices=("codex", "claude"), required=True)
    parser.add_argument("--directory", default="~/.the-regular")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--status", choices=("finished", "attention"), help="explicit manual signal")
    parser.add_argument("payload", nargs="?", help="Codex notify JSON argument; hooks normally use stdin")
    args = parser.parse_args(argv)
    if args.status:
        event = {"hook_event_name": "Stop" if args.status == "finished" else "Notification",
                 "notification_type": "permission_prompt"}
    else:
        try:
            raw = args.payload if args.payload is not None else (stdin or sys.stdin).read(1048577)
            event = json.loads(raw) if len(raw) <= 1048576 else None
        except (ValueError, UnicodeError):
            event = None
    selected = cue_for_event(args.tool, event)
    if args.status == "attention" and args.tool == "codex":
        selected = ("think", "Codex 需要你看一下。")
    result = 0
    if selected:
        try:
            result = sender(args.directory, args.port, *selected)
        except Exception:
            result = 1
    # Never block an agent turn if the pet isn't running. Codex Stop requires JSON.
    if not args.status:
        print("{}")
        return 0
    if selected is None:
        print("未识别的通知。", file=sys.stderr)
        return 2
    if result:
        print("桌宠未收到提示；请检查本机面板是否运行。", file=sys.stderr)
    else:
        print("已交给桌宠。")
    return result
