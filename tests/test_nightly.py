# -*- coding: utf-8 -*-
"""每晚汇总（regular/nightly.py）与它的定时任务定义。合成数据，不联网。

守的几条：默认不调模型；付费那几步各自兜底；桌宠那一句只报数；
定时任务定义只有显式给了授权才带 --allow-paid，而且从不自己安装。
"""
import json
import os
import plistlib
import tempfile
import unittest
from unittest.mock import patch

import config
import interactions as interactions_mod
import turns as turns_mod
from regular import eval as eval_mod
from regular import nightly, service, shadow

NOON = 1793419200.0
DAY = turns_mod.day_key(NOON)


class NightlyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        for name, sub in (("TURN_LOG_DIR", "turns"), ("INTERACTION_LOG_DIR", "interactions"),
                          ("SHADOW_DIR", "shadow"), ("EVAL_DIR", "evals"), ("CASES_DIR", "cases"),
                          ("CHECKUP_DIR", "checkups"), ("NIGHTLY_DIR", "nightly"),
                          ("ARCHIVE_DIR", "archive")):
            path = os.path.join(tmp.name, sub)
            os.makedirs(path)
            patcher = patch.object(config, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(config, "STATE_FILE", os.path.join(tmp.name, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def spoke(self, turn_id, action=None):
        base = {"schema": interactions_mod.SCHEMA, "group": "G_NIGHT", "user": "U_NIGHT"}
        rows = [{**base, "event_id": f"d-{turn_id}", "ts": NOON + 1, "kind": "delivery",
                 "actor": "assistant", "status": "delivered", "route": "reply",
                 "outgoing_id": f"out-{turn_id}", "turn_id": turn_id, "text": "合成回复"}]
        if action:
            rows.append({**base, "event_id": f"f-{turn_id}", "ts": NOON + 2,
                         "kind": "natural_feedback", "actor": "system",
                         "source_reply_id": f"out-{turn_id}", "source_message_id": f"m-{turn_id}",
                         "target": "bot_response", "action": action})
        with open(os.path.join(config.INTERACTION_LOG_DIR, f"{DAY}.jsonl"), "a", encoding="utf-8") as fp:
            for row in rows:
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(os.path.join(config.TURN_LOG_DIR, f"{DAY}.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps({"schema": turns_mod.SCHEMA, "turn_id": turn_id, "ts": NOON,
                                 "kind": "reply", "release": "aaa1111", "raw": "合成回复",
                                 "kwargs": {"user_text": "合成"}, "session": []}) + "\n")

    def test_read_only_page_counts_reactions_and_findings(self):
        self.spoke("a", "repair_briefly")
        self.spoke("b")
        out = nightly.run(config, day=DAY)
        self.assertEqual(out["reactions"]["overall"]["n"], 2)
        self.assertEqual(out["reactions"]["overall"]["corrected"], 1)
        self.assertEqual(out["findings"], 1)                       # 被纠正那条进了体检
        self.assertEqual(out["line"], "昨天回了 2 句，被纠正 1 句；体检列出 1 处可疑")
        self.assertEqual(out["cue"], "checkup")
        page = open(out["path"], encoding="utf-8").read()
        self.assertIn("没有调用模型", page)
        self.assertTrue(os.path.exists(os.path.join(config.CHECKUP_DIR, f"{DAY}.json")))
        self.assertLessEqual(len(out["line"]), nightly.LINE_LIMIT)
        self.assertNotIn("合成回复", out["line"])

    def test_a_quiet_day_says_so(self):
        out = nightly.run(config, day=DAY, write=False)
        self.assertIsNone(out["reactions"])
        self.assertEqual(out["line"], "昨天没说话；体检没列出可疑的")
        self.assertEqual(out["cue"], "cheer")
        self.assertNotIn("path", out)

    def test_unpaid_run_never_touches_shadow_or_judge(self):
        self.spoke("a", "repair_briefly")
        shadow.start(config, "tone", cand="cand", base="base", since=DAY)
        with patch.object(shadow, "run") as run, patch.object(shadow, "judge_check") as check:
            out = nightly.run(config, day=DAY, write=False)
        run.assert_not_called()
        check.assert_not_called()
        self.assertEqual([s["name"] for s in out["shadows"]], ["tone"])   # 只读状态照样列出

    def test_a_failed_paid_step_is_noted_and_the_page_still_gets_written(self):
        self.spoke("a", "repair_briefly")
        shadow.start(config, "tone", cand="cand", base="base", since=DAY)
        with patch.object(shadow, "run", side_effect=RuntimeError("评审网关 503")), \
                patch.object(shadow, "judge_check", side_effect=eval_mod.PaidEvaluationRequired("x")):
            out = nightly.run(config, day=DAY, allow_paid=True)
        self.assertEqual(len(out["notes"]), 2)
        self.assertIn("评审网关 503", out["notes"][0])
        self.assertIn("没跑成的", open(out["path"], encoding="utf-8").read())

    def test_decided_shadow_makes_it_into_the_line(self):
        shadow.start(config, "tone", cand="cand", base="base", since=DAY)
        state = shadow.load(config, "tone")
        state["days"][DAY] = {"turns": 9, "cand": 9, "base": 0, "tie": 0}
        shadow._save(config, state)
        out = nightly.run(config, day=DAY, write=False)
        self.assertIn("影子回放 tone：新版更好", out["line"])


class NightlyServiceTest(unittest.TestCase):
    def test_definition_is_read_only_unless_paid_is_asked_for(self):
        plain = service.launchd_nightly("/tmp/deploy", "/usr/bin/python3")
        self.assertEqual(plain["ProgramArguments"][-2:], ["nightly", "--pet"])
        self.assertEqual(plain["StartCalendarInterval"], {"Hour": 9, "Minute": 0})
        self.assertNotIn("RunAtLoad", plain)
        self.assertNotEqual(plain["Label"], service.LABEL)       # 不会顶掉机器人自己的任务
        paid = service.launchd_nightly("/tmp/deploy", "/usr/bin/python3", hour=7, allow_paid=True)
        self.assertEqual(paid["ProgramArguments"][-1], "--allow-paid")
        self.assertEqual(paid["StartCalendarInterval"]["Hour"], 7)

    def test_generator_writes_a_plist_and_installs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "nightly.plist")
            code = service.main(["launchd", "--directory", tmp, "--output", output, "--job", "nightly"])
            self.assertEqual(code, 0)
            with open(output, "rb") as stream:
                self.assertEqual(plistlib.load(stream)["Label"], service.LABEL + ".nightly")
            with self.assertRaises(SystemExit):
                service.main(["systemd", "--directory", tmp, "--output", output, "--job", "nightly"])


if __name__ == "__main__":
    unittest.main()
