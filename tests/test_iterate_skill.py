"""自迭代 skill（.agents/skills/regular-iterate）：外接的代理照着它干活，它写错了就是把人带进沟里。

命令要真能被 regular 认、提到的文件要真在、Claude Code 那份入口要跟正本对得上、找当轮的脚本要能跑。
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import turns as turns_mod
from interactions import SCHEMA as INTERACTION_SCHEMA
from regular import cli

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".agents/skills/regular-iterate/SKILL.md"
CLAUDE_ENTRY = REPO / ".claude/skills/regular-iterate/SKILL.md"
FIND_TURN = REPO / ".agents/skills/regular-iterate/scripts/find_turn.py"


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    meta = dict(line.split(":", 1) for line in front.strip().splitlines())
    return {k.strip(): v.strip() for k, v in meta.items()}, body


def _find_turn_module():
    spec = importlib.util.spec_from_file_location("find_turn", FIND_TURN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _commands(body):
    """代码块里每条 `-m regular` 命令（续行接上、注释去掉、<占位> 换成 X）。"""
    out = []
    for block in re.findall(r"```bash\n(.*?)```", body, re.S):
        for line in block.replace("\\\n", " ").splitlines():
            if "-m regular" not in line:
                continue
            line = re.sub(r"<[^>]+>", "X", line.split("  #")[0].split(" # ")[0])
            argv = shlex.split(line.replace('"$DEPLOY"', "/tmp/deploy"))
            out.append(argv[argv.index("regular") + 1:])
    return out


class _Parsed(Exception):
    pass


class IterateSkillTest(unittest.TestCase):
    def test_frontmatter_follows_the_agent_skills_format(self):
        meta, _ = _frontmatter(SKILL)
        self.assertEqual(set(meta), {"name", "description"})   # 此入口只需最小的两项元数据
        self.assertEqual(meta["name"], SKILL.parent.name)
        self.assertRegex(meta["name"], r"^[a-z0-9-]{1,64}$")
        self.assertLessEqual(len(meta["description"]), 1024)

    def test_claude_entry_matches_and_points_to_the_one_body(self):
        self.assertEqual(_frontmatter(CLAUDE_ENTRY)[0], _frontmatter(SKILL)[0])
        self.assertIn(".agents/skills/regular-iterate/SKILL.md", _frontmatter(CLAUDE_ENTRY)[1])

    def test_agents_md_and_gemini_point_to_it(self):
        self.assertIn(".agents/skills/regular-iterate/SKILL.md", (REPO / "AGENTS.md").read_text(encoding="utf-8"))
        settings = json.loads((REPO / ".gemini/settings.json").read_text(encoding="utf-8"))
        self.assertIn("AGENTS.md", settings["context"]["fileName"])

    def test_every_regular_command_parses(self):
        commands = _commands(_frontmatter(SKILL)[1])
        self.assertGreaterEqual(len(commands), 10)
        for argv in commands:
            with self.subTest(argv=" ".join(argv)), \
                    patch.object(cli, "load_config", side_effect=_Parsed), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    cli.main(argv)
                except _Parsed:
                    pass
                except SystemExit:
                    self.fail(f"regular 不认这条命令：{' '.join(argv)}\n{err.getvalue()}")

    def test_files_it_names_exist(self):
        body = _frontmatter(SKILL)[1]
        named = {m for m in re.findall(r"`([\w./-]+\.(?:md|py|json))`", body)
                 if not m.startswith("data/") and "xxx" not in m}
        self.assertIn("AGENTS.md", named)
        for rel in sorted(named):
            with self.subTest(path=rel):
                self.assertTrue((REPO / rel).exists(), rel)

    def test_find_turn_finds_the_reply_and_the_incoming_message(self):
        find_turn = _find_turn_module()
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            log = turns_mod.TurnLog(str(data / "turns"), enabled=True)
            row = log.append("reply", message_id="m1",
                             kwargs={"user_text": "那只龙周一会装睡吗"}, session=[], raw="〔不接〕")
            log.close()
            day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
            (data / "interactions").mkdir(parents=True)
            (data / "interactions" / f"{day}.jsonl").write_text(json.dumps(
                {"schema": INTERACTION_SCHEMA, "kind": "incoming", "ts": row["ts"], "message_id": "m1",
                 "text": "那只龙周一会装睡吗"}, ensure_ascii=False) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = find_turn.main(["龙周一", "--directory", tmp, "--day", day])
            self.assertEqual(code, 0)
            self.assertIn(row["turn_id"], out.getvalue())
            self.assertIn("incoming", out.getvalue())
            self.assertIn("小王：〔不接〕", out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(find_turn.main(["没人说过", "--directory", tmp, "--day", day]), 1)

    def test_find_turn_links_question_reply_and_delivery_without_crossing_groups(self):
        find_turn = _find_turn_module()
        day = "2026-11-05"
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            turns = [
                {"kind": "triage", "turn_id": "triage-1", "group": "G1", "message_id": "m1",
                 "ts": 1, "payload": {"message": "合成茶杯问题"}, "asked": ["addressed"],
                 "choices": {"addressed": "answer"}},
                {"kind": "reply", "turn_id": "reply-1", "group": "G1", "message_id": "m1",
                 "ts": 2, "kwargs": {"user_text": "合成茶杯问题"}, "raw": "合成生成正文"},
                {"kind": "reply", "turn_id": "other-group", "group": "G2", "message_id": "m1",
                 "ts": 2, "kwargs": {"user_text": "无关群消息"}, "raw": "不应关联的另一群"},
            ]
            interactions = [
                {"kind": "incoming", "group": "G1", "message_id": "m1", "ts": 0,
                 "text": "合成茶杯问题"},
                {"kind": "delivery", "group": "G1", "source_message_id": "m1", "ts": 3,
                 "text": "合成送达正文", "status": "delivered"},
                {"kind": "delivery", "group": "G1", "turn_id": "reply-1", "ts": 4,
                 "status": "blocked", "reason": "合成拦截原因"},
            ]
            for name, rows in (("turns", turns), ("interactions", interactions)):
                path = data / name / f"{day}.jsonl"
                path.parent.mkdir(parents=True)
                schema = turns_mod.SCHEMA if name == "turns" else INTERACTION_SCHEMA
                path.write_text("".join(json.dumps({"schema": schema, **row}, ensure_ascii=False)
                                        + "\n" for row in rows), encoding="utf-8")
            for needle in ("合成茶杯问题", "合成生成正文", "合成送达正文"):
                with self.subTest(needle=needle):
                    hits = find_turn.search(data, needle, day)
                    text = "\n".join(line for _, line in hits)
                    self.assertEqual(len(hits), 5)
                    for expected in ("triage-1", "reply-1", "incoming", "合成送达正文",
                                     "status=blocked", "reason=合成拦截原因"):
                        self.assertIn(expected, text)
                    self.assertNotIn("不应关联的另一群", text)


if __name__ == "__main__":
    unittest.main()
