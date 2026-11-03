# -*- coding: utf-8 -*-
"""规则账本（docs/self-iteration.md ④）：解析 prompts.py 里的 # rule: 标记，支持拿掉单条重跑。

标记写法（挂在 prompts.py 里那段规则的字符串字面量前面）：
    # rule: deflect-in-place | 2026-11-03 | docs/deflection.md | cases: deflect-*

日期写这段规则核心措辞第一次进仓库的那天（`git log -S"<措辞>"` 能查到），不凭印象估；
cases 至少要匹配上一条用例，不然拿掉它重跑时没有东西可比，消融只能报「判断不了」。

为什么直接挂在相邻字符串字面量上：
    Python 编译期就把相邻字面量拼成同一条 str，运行时零开销、稳定头逐字节不变；
    tokenize 扫一遍源码就能拿到每条规则的精确正文，--ablate 时从 prompts.* 里把它抽掉即可。
"""
from __future__ import annotations

import ast
import fnmatch
import re
import tokenize
from pathlib import Path

_RULE_COMMENT = re.compile(
    r"^#\s*rule:\s*([A-Za-z0-9_.-]+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*cases:\s*(.+?)\s*$"
)


def _prompts_path(path=None):
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().parent.parent / "prompts.py"


def load_rules(path=None):
    """从 prompts.py 扫出所有 # rule: 条目及紧随其后的那段字符串正文。"""
    src = _prompts_path(path)
    rules = []
    pending = None
    with src.open("rb") as stream:
        for tok in tokenize.tokenize(stream.readline):
            if tok.type == tokenize.COMMENT:
                m = _RULE_COMMENT.match(tok.string.strip())
                if m:
                    cases = [c.strip() for c in m.group(4).split(",") if c.strip()]
                    pending = {
                        "id": m.group(1),
                        "date": m.group(2).strip(),
                        "doc": m.group(3).strip(),
                        "cases": cases,
                        "line": tok.start[0],
                    }
            elif pending is not None and tok.type == tokenize.STRING:
                text = ast.literal_eval(tok.string)
                if isinstance(text, str):
                    pending["text"] = text
                    pending["chars"] = len(text.strip())
                    rules.append(pending)
                    pending = None
    return rules


def get_rule(rule_id, path=None):
    for rule in load_rules(path):
        if rule["id"] == rule_id:
            return rule
    return None


def matches_case(rule, case_id, tags=()):
    """这条用例属不属于该规则挂的 cases 模式（按用例 id 或标签匹配）。"""
    patterns = rule.get("cases") or ()
    candidates = [str(case_id or ""), *(str(t) for t in (tags or ()))]
    return any(fnmatch.fnmatchcase(c, pat) for pat in patterns for c in candidates if c)


def ablate(rule_id, prompts_mod=None, path=None):
    """从 prompts 模块的字符串常量里拿掉指定规则的正文。返回被拿掉的规则 dict。"""
    if prompts_mod is None:
        import prompts as prompts_mod
    rule = get_rule(rule_id, path=path)
    if rule is None and path is not None:
        rule = get_rule(rule_id, path=None)
    if rule is None:
        raise KeyError(f"unknown rule: {rule_id}")
    target = rule["text"]
    replaced = 0
    for name in dir(prompts_mod):
        val = getattr(prompts_mod, name)
        if isinstance(val, str) and target in val:
            setattr(prompts_mod, name, val.replace(target, ""))
            replaced += 1
    if not replaced:
        raise ValueError(f"rule {rule_id!r} text not found in prompts module")
    return rule


def head_stats(prompts_mod=None, path=None):
    """稳定头及各段的长度，写进每次评测报告，盯住提示词是不是只增不减。"""
    if prompts_mod is None:
        import prompts as prompts_mod
    parts = {
        "normal": len(getattr(prompts_mod, "PROMPT_NORMAL", "")),
        "shared": len(getattr(prompts_mod, "PROMPT_SHARED_RULES", "")),
        "output": len(getattr(prompts_mod, "PROMPT_OUTPUT_RULES", "")),
        "draw": len(getattr(prompts_mod, "PROMPT_DRAW_RULES", "")),
        "voice": len(getattr(prompts_mod, "PROMPT_VOICE_RULES", "")),
        "media_record": len(getattr(prompts_mod, "PROMPT_MEDIA_RECORD", "")),
        "remind": len(getattr(prompts_mod, "PROMPT_REMIND_RULES", "")),
    }
    rules = load_rules(path)
    return {
        "parts": parts,
        "total_chars": sum(parts.values()),
        "rules_count": len(rules),
        "rules_chars": sum(r["chars"] for r in rules),
    }
