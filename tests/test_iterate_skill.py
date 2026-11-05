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
        self.assertEqual(set(meta), {"name", "description"})   # CodeX 拒收多出来的键
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
        spec = importlib.util.spec_from_file_location("find_turn", FIND_TURN)
        find_turn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(find_turn)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            log = turns_mod.TurnLog(str(data / "turns"), enabled=True)
            row = log.append("reply", message_id="m1",
                             kwargs={"user_text": "那她别的地方臭吗"}, session=[], raw="〔不接〕")
            log.close()
            day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
            (data / "interactions").mkdir(parents=True)
            (data / "interactions" / f"{day}.jsonl").write_text(json.dumps(
                {"schema": INTERACTION_SCHEMA, "kind": "incoming", "ts": row["ts"], "message_id": "m1",
                 "text": "那她别的地方臭吗"}, ensure_ascii=False) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = find_turn.main(["别的地方臭", "--directory", tmp, "--day", day])
            self.assertEqual(code, 0)
            self.assertIn(row["turn_id"], out.getvalue())
            self.assertIn("incoming", out.getvalue())
            self.assertIn("小王：〔不接〕", out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(find_turn.main(["没人说过", "--directory", tmp, "--day", day]), 1)


if __name__ == "__main__":
    unittest.main()
