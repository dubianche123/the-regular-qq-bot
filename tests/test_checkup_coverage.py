# -*- coding: utf-8 -*-
"""体检覆盖：用例库里取自真实现场的事故，现在的体检能列出几条。合成数据，只读。"""
import json
import os
import tempfile
import time
import unittest

import interactions as interactions_mod
import turns as turns_mod
from regular import checkup

NOON = 1793419200.0
DAY = time.strftime("%Y-%m-%d", time.localtime(NOON))


class IncidentCoverageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_dir = os.path.join(tmp.name, "interactions")
        self.turn_dir = os.path.join(tmp.name, "turns")
        os.makedirs(self.log_dir)
        os.makedirs(self.turn_dir)
        self.ts = NOON

    def _append(self, directory, row):
        with open(os.path.join(directory, f"{DAY}.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def spoke(self, turn_id, *, corrected):
        """一轮回复送达了；corrected 时下一句群友的话被分诊判成在纠正它。"""
        self.ts += 10.0
        self._append(self.turn_dir, {"schema": turns_mod.SCHEMA, "turn_id": turn_id, "ts": self.ts,
                                     "kind": "reply", "message_id": f"m-{turn_id}"})
        base = {"schema": interactions_mod.SCHEMA, "group": "G_COV", "user": "U_COV"}
        self._append(self.log_dir, {**base, "event_id": f"d-{turn_id}", "ts": self.ts + 1,
                                    "kind": "delivery", "actor": "assistant", "status": "delivered",
                                    "route": "reply", "outgoing_id": f"out-{turn_id}",
                                    "turn_id": turn_id, "text": "合成回复"})
        if corrected:
            self._append(self.log_dir, {**base, "event_id": f"f-{turn_id}", "ts": self.ts + 2,
                                        "kind": "natural_feedback", "actor": "system",
                                        "source_reply_id": f"out-{turn_id}",
                                        "source_message_id": f"next-{turn_id}",
                                        "target": "bot_response", "action": "repair_briefly"})

    def coverage(self, cases):
        return checkup.incident_coverage(cases, interaction_dir=self.log_dir, turn_dir=self.turn_dir)

    def test_listed_missed_and_expired_are_counted_apart(self):
        self.spoke("seen", corrected=True)
        self.spoke("unseen", corrected=False)
        out = self.coverage([
            {"id": "case-seen", "source_turn_id": "seen"},
            {"id": "case-unseen", "source_turn_id": "unseen"},
            {"id": "case-gone", "source_turn_id": "pruned-long-ago"},
            {"id": "case-synthetic"},                      # 合成用例没有现场，不算
        ])
        self.assertEqual((out["cases"], out["replayable"], out["covered"], out["expired"]),
                         (3, 2, 1, 1))
        rows = {r["case"]: r for r in out["rows"]}
        self.assertEqual(rows["case-seen"]["signals"], ["corrected"])
        self.assertIs(rows["case-unseen"]["covered"], False)
        self.assertIsNone(rows["case-gone"]["covered"])    # 回不到现场 ≠ 没列出
        text = checkup.render_coverage(out)
        self.assertIn("case-unseen", text.split("列得出的")[0])
        self.assertIn("被纠正", text)

    def test_no_real_cases_is_an_empty_report_not_a_zero_rate(self):
        out = self.coverage([{"id": "case-synthetic"}])
        self.assertEqual((out["cases"], out["covered"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
