# -*- coding: utf-8 -*-
"""反应账本：每条送达之后群友实际怎么反应，只读。

守的还是**不许撒谎**那几条：
  · 没有下文是缺失，不是差评，也不是好评；
  · 说的是别人的话不算对它的反应；
  · 本地指令的固定输出不进分母；图片和主动发言照样会被纠正，要进；
  · 一条送达同时被纠正又被接着玩，两项都记，不强行归一类；
  · 样本不足时报条数，不报比例；
  · 行里只有本地事件编号，没有正文、群号和发言人。
"""
import json
import os
import tempfile
import unittest

import interactions as interactions_mod
import turns as turns_mod
from regular import outcomes

G = "GID_OUT"
A = "USER_OUT_A"
NOON = 1793419200.0     # 落在哪一天由 archive.day_key 决定，测试只用它算出来的那天


class _Journal:
    """按互动日志和每轮记录的真实形状各写一份临时 JSONL。"""

    def __init__(self, directory):
        self.log_dir = os.path.join(directory, "interactions")
        self.turn_dir = os.path.join(directory, "turns")
        os.makedirs(self.log_dir)
        os.makedirs(self.turn_dir)
        self.ts = NOON
        self.day = outcomes.day_key(NOON)

    def _append(self, directory, row):
        with open(os.path.join(directory, f"{self.day}.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write(self, kind, **fields):
        self.ts += 1.0
        self._append(self.log_dir, {
            "schema": interactions_mod.SCHEMA, "event_id": os.urandom(8).hex(),
            "ts": self.ts, "kind": kind, "group": G, "user": A, **fields})

    def delivered(self, outgoing_id, *, release=None, route="reply", eligible=True,
                  status="delivered", model="model-a"):
        """eligible 照线上的样子写进去；账本不该拿它筛（图片行就没带这个标记）。"""
        turn_id = None
        if release:
            turn_id = f"turn-{outgoing_id}"
            self._append(self.turn_dir, {
                "schema": turns_mod.SCHEMA, "turn_id": turn_id, "ts": self.ts,
                "kind": "reply", "release": release})
        self.write("delivery", actor="assistant", status=status, route=route,
                   outgoing_id=outgoing_id, source_message_id=f"m-{outgoing_id}",
                   feedback_eligible=eligible, turn_id=turn_id, model=model,
                   text="合成回复，不该出现在账本里")

    def said(self, outgoing_id, *, target="bot_response", action=None):
        self.write("natural_feedback", actor="system", source_reply_id=outgoing_id,
                   source_message_id=os.urandom(4).hex(), target=target,
                   suggested_move=action, action=action)


class OutcomeLedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.j = _Journal(self._tmp.name)

    def report(self, **kw):
        return outcomes.report(self.j.log_dir, self.j.turn_dir, **kw)

    def row(self, out, outgoing_id):
        return next(r for r in out["rows"] if r["outgoing_id"] == outgoing_id)

    def test_each_reaction_is_read_from_the_judgement_about_that_reply(self):
        self.j.delivered("fixed"); self.j.said("fixed", action="repair_briefly")
        self.j.delivered("stop"); self.j.said("stop", action="back_off")
        self.j.delivered("fun"); self.j.said("fun", action="keep_playful")
        self.j.delivered("plain"); self.j.said("plain", action="continue_normally")
        out = self.report()
        self.assertTrue(self.row(out, "fixed")["corrected"])
        self.assertTrue(self.row(out, "stop")["told_off"])
        self.assertTrue(self.row(out, "fun")["welcomed"])
        plain = self.row(out, "plain")
        self.assertTrue(plain["reacted"])
        self.assertFalse(plain["corrected"] or plain["told_off"] or plain["welcomed"])
        self.assertEqual(out["overall"]["reacted"], 4)

    def test_silence_and_talk_about_others_are_not_verdicts(self):
        self.j.delivered("quiet")
        self.j.delivered("passed")
        self.j.said("passed", target="another_person", action="repair_briefly")
        self.j.said("passed", target="not_feedback", action="no_adjustment")
        out = self.report()
        quiet, passed = self.row(out, "quiet"), self.row(out, "passed")
        self.assertTrue(quiet["silent"])
        self.assertTrue(passed["moved_on"])
        for row in (quiet, passed):
            self.assertFalse(row["reacted"] or row["corrected"] or row["welcomed"])
        # 纠正的是别人，不算它被纠正
        self.assertEqual(out["overall"]["corrected"], 0)

    def test_mixed_reactions_count_on_both_sides(self):
        self.j.delivered("both")
        self.j.said("both", action="repair_briefly")
        self.j.said("both", action="keep_playful")
        out = self.report()
        self.assertEqual((out["overall"]["corrected"], out["overall"]["welcomed"]), (1, 1))
        self.assertEqual(out["overall"]["reacted"], 1)

    def test_fixed_command_output_stays_out_of_the_denominator(self):
        self.j.delivered("local", route="local", eligible=False)
        self.j.delivered("queued", status="queued")
        self.j.delivered("real")
        out = self.report()
        self.assertEqual(out["counts"], {"delivered": 2, "fixed_lines": 1,
                                         "other_routes": 0, "in_ledger": 1})
        self.assertEqual([r["outgoing_id"] for r in out["rows"]], ["real"])

    def test_images_and_proactive_posts_are_judged_even_without_the_flag(self):
        # 线上实测：图片行没带 feedback_eligible，主动发言是 False，照样被纠正 / 被回应
        self.j.delivered("img", route="image", eligible=None)
        self.j.said("img", action="repair_briefly")
        self.j.delivered("pro", route="proactive", eligible=False)
        self.j.said("pro", action="continue_normally")
        out = self.report()
        self.assertTrue(self.row(out, "img")["corrected"])
        self.assertTrue(self.row(out, "pro")["reacted"])

    def test_routes_are_always_listed_and_can_be_isolated(self):
        self.j.delivered("txt", release="aaa1111")
        self.j.delivered("img", route="image", release="aaa1111")
        self.j.said("img", action="repair_briefly")
        out = self.report()
        routes = {g["key"]: g for g in out["routes"]}
        self.assertEqual((routes["reply"]["corrected"], routes["image"]["corrected"]), (0, 1))
        self.assertIn("各路径", outcomes.render(out))
        only = self.report(route="reply")
        self.assertEqual((only["overall"]["n"], only["overall"]["corrected"]), (1, 0))
        self.assertEqual(only["counts"]["other_routes"], 1)

    def test_groups_by_the_release_that_generated_the_turn(self):
        self.j.delivered("a1", release="aaa1111"); self.j.said("a1", action="repair_briefly")
        self.j.delivered("b1", release="bbb2222")
        self.j.delivered("b2", release="bbb2222"); self.j.said("b2", action="keep_playful")
        self.j.delivered("old")                     # 每轮记录上线之前的送达
        groups = {g["key"]: g for g in self.report()["groups"]}
        self.assertEqual(list(groups), ["aaa1111", "bbb2222", None])   # 按首次出现排
        self.assertEqual((groups["aaa1111"]["n"], groups["aaa1111"]["corrected"]), (1, 1))
        self.assertEqual((groups["bbb2222"]["n"], groups["bbb2222"]["welcomed"]), (2, 1))
        self.assertEqual(groups[None]["n"], 1)
        self.assertIn(outcomes.UNKNOWN, outcomes.render(self.report()))

    def test_small_groups_report_counts_not_rates(self):
        self.j.delivered("only"); self.j.said("only", action="repair_briefly")
        overall = self.report()["overall"]
        self.assertFalse(overall["enough_for_rate"])
        self.assertIsNone(overall["corrected_rate"])
        self.assertIsNone(overall["corrected_ci"])
        self.assertNotIn("%", outcomes.render(self.report()).split("方括号")[0])

    def test_enough_deliveries_give_a_rate_with_an_interval(self):
        for index in range(outcomes.MIN_FOR_RATE):
            self.j.delivered(f"r{index}")
            if index < 5:
                self.j.said(f"r{index}", action="repair_briefly")
        overall = self.report()["overall"]
        self.assertAlmostEqual(overall["corrected_rate"], 0.25)
        self.assertLess(overall["corrected_ci"]["low"], 0.25)
        self.assertGreater(overall["corrected_ci"]["high"], 0.25)
        # 一条都没有也不是「确定为零」
        self.assertEqual(overall["welcomed_rate"], 0.0)
        self.assertGreater(overall["welcomed_ci"]["high"], 0.0)

    def test_since_drops_earlier_days(self):
        self.j.delivered("today")
        later = (outcomes.day_key(NOON + 86400.0))
        self.assertIsNone(self.report(since=later)["overall"])
        self.assertEqual(self.report(since=self.j.day)["overall"]["n"], 1)

    def test_rows_carry_local_event_ids_only(self):
        self.j.delivered("x", release="aaa1111"); self.j.said("x", action="keep_playful")
        dumped = json.dumps(self.report(), ensure_ascii=False)
        for leaked in (G, A, "合成回复"):
            self.assertNotIn(leaked, dumped)

    def test_empty_journal_says_unavailable_not_zero(self):
        out = self.report()
        self.assertIsNone(out["overall"])
        self.assertIn("不是 0%", outcomes.render(out))

    def test_unknown_grouping_is_refused(self):
        with self.assertRaises(ValueError):
            self.report(by="user")


if __name__ == "__main__":
    unittest.main()
