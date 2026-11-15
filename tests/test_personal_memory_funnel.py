# -*- coding: utf-8 -*-
"""个人原话漏斗：去留判断的结论和在库条数，只读、只出计数。合成数据。"""
import json
import os
import tempfile
import unittest

import interactions as interactions_mod
from regular import observations

NOW = 1793419200.0


class PersonalMemoryFunnelTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_dir = os.path.join(tmp.name, "interactions")
        os.makedirs(self.log_dir)
        self.state_file = os.path.join(tmp.name, "state.json")

    def judged(self, scores, dropped=()):
        row = {"schema": interactions_mod.SCHEMA, "event_id": os.urandom(8).hex(), "ts": NOW,
               "kind": "decision_scores", "group": "G_FUN", "user": None, "site": "memory",
               "scores": {name: {"choice": choice} for name, choice in scores.items()},
               "dropped": list(dropped)}
        with open(os.path.join(self.log_dir, "2026-10-31.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def entry(self, message_id, age_days, decision=None):
        source = {"group": "G_FUN", "sender": "U_FUN", "message_id": message_id,
                  "ts": NOW - age_days * 86400.0, "quote": f"合成原话第 {age_days} 天"}
        row = {"who": "阿合成", "quote": source["quote"], "keywords": ["合成"], "evidence": source,
               "ts": source["ts"]}
        if decision:
            row["decision"] = {**decision, "message_id": message_id}
        return row

    def funnel(self, people=None):
        if people is not None:
            with open(self.state_file, "w", encoding="utf-8") as fp:
                json.dump({"digests": {"G_FUN": {"summary": {"data": {"people": people}}}}}, fp)
        return observations.personal_memory_funnel(
            self.log_dir, self.state_file if people is not None else None, now=NOW)

    def test_each_proposed_quote_lands_in_exactly_one_verdict(self):
        self.judged({"c0_memory_kind": "preference", "c0_memory_action": "retain_quote",
                     "c1_memory_kind": "temporary", "c1_memory_action": "keep_briefly",
                     "c2_memory_kind": "discard", "c2_memory_action": "discard",
                     "c3_memory_kind": "fact"}, dropped=["c3_memory_action"])
        self.judged({"c0_memory_kind": "fact", "c0_memory_action": "discard"})
        out = self.funnel()
        self.assertEqual(out["judgement_calls"], 2)
        self.assertEqual(out["judged"], 5)
        self.assertEqual(out["verdicts"], {"durable": 1, "brief": 1, "discard": 2, "abstained": 1})
        self.assertIsNone(out["store"])            # 没给状态文件：在库不可知，不是 0

    def test_other_judgement_sites_are_not_counted(self):
        self.judged({"c0_memory_kind": "discard", "c0_memory_action": "discard"})
        row = {"schema": interactions_mod.SCHEMA, "event_id": "x", "ts": NOW, "kind": "decision_scores",
               "group": "G_FUN", "user": None, "site": "triage",
               "scores": {"addressed": {"choice": "answer"}}, "dropped": []}
        with open(os.path.join(self.log_dir, "2026-10-31.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row) + "\n")
        self.assertEqual(self.funnel()["judgement_calls"], 1)

    def test_store_counts_fresh_and_long_term_entries(self):
        keep = {"kind": "preference", "action": "retain_quote"}
        out = self.funnel([
            self.entry("m_new", 1),                       # 没判过：短时效，还新鲜
            self.entry("m_old", 5),                       # 没判过：过了短时效
            self.entry("m_keep", 30, keep),               # 判过长期留：一个月还在
            {"who": "没有来源", "quote": "不该算"},
        ])
        self.assertEqual(out["store"], {"entries": 3, "fresh": 2, "durable": 1})
        dumped = json.dumps(out, ensure_ascii=False)
        for leaked in ("合成原话", "U_FUN", "G_FUN"):
            self.assertNotIn(leaked, dumped)


if __name__ == "__main__":
    unittest.main()
