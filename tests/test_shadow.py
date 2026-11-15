# -*- coding: utf-8 -*-
"""影子回放与裁判核对（regular/shadow.py）。全部用合成轮次和假的生成 / 裁判，不联网。

守的几条：
  · 同一天只记一次账，当天（还在追加）不回放；
  · 没授权不发任何模型请求；
  · 序贯检验：几胜几负不下结论，越线一次就算更差；
  · 裁判核对只收有明确真实反应的回复，样本不够只报条数。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import interactions as interactions_mod
import turns as turns_mod
from regular import eval as eval_mod
from regular import shadow

NOON = 1793419200.0
DAYS = [turns_mod.day_key(NOON + 86400.0 * i) for i in range(4)]     # 连续四天；最后一天当「今天」


class _Deployment:
    """一个临时部署目录：每轮记录、互动日志、影子账本都在里面。"""

    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.paths = {name: os.path.join(tmp.name, sub) for name, sub in (
            ("TURN_LOG_DIR", "turns"), ("INTERACTION_LOG_DIR", "interactions"),
            ("SHADOW_DIR", "shadow"), ("EVAL_DIR", "evals"), ("CASES_DIR", "cases"))}
        for name, path in self.paths.items():
            os.makedirs(path)
            patcher = patch.object(config, name, path)
            patcher.start()
            case.addCleanup(patcher.stop)
        checkout = patch.object(eval_mod, "checkout_version", lambda spec, repo_root=None: _Dir(spec))
        checkout.start()
        case.addCleanup(checkout.stop)

    def _append(self, key, day, row):
        with open(os.path.join(self.paths[key], f"{day}.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def turn(self, day_index, turn_id, raw="当时说的话"):
        ts = NOON + 86400.0 * day_index
        self._append("TURN_LOG_DIR", DAYS[day_index], {
            "schema": turns_mod.SCHEMA, "turn_id": turn_id, "ts": ts, "kind": "reply",
            "release": "aaa1111", "kwargs": {"user_text": f"合成：{turn_id}"}, "session": [],
            "raw": raw})
        return ts

    def reacted(self, day_index, turn_id, action, route="reply"):
        """这一轮送达了，随后群友的反应被分诊判成 action。"""
        ts = self.turn(day_index, turn_id)
        base = {"schema": interactions_mod.SCHEMA, "group": "G_SHADOW", "user": "U_SHADOW"}
        self._append("INTERACTION_LOG_DIR", DAYS[day_index], {
            **base, "event_id": f"d-{turn_id}", "ts": ts + 1, "kind": "delivery",
            "actor": "assistant", "status": "delivered", "route": route,
            "outgoing_id": f"out-{turn_id}", "turn_id": turn_id})
        self._append("INTERACTION_LOG_DIR", DAYS[day_index], {
            **base, "event_id": f"f-{turn_id}", "ts": ts + 2, "kind": "natural_feedback",
            "actor": "system", "source_reply_id": f"out-{turn_id}", "target": "bot_response",
            "action": action})


class _Dir:
    def __init__(self, spec):
        self.spec = spec

    def __enter__(self):
        return self.spec

    def __exit__(self, *exc):
        return False


def _worker(code_dir, items, *, n=1, concurrency=6, ablate="", override=None, env_extra=None):
    return {"head": {}, "results": [{"id": it["id"], "samples": [f"{code_dir} 版的回复"] * n,
                                     "errors": 0} for it in items]}


def _judge(winner_of):
    """winner_of(候选文本 A, 候选文本 B) → 'A' / 'B' / 'tie'。"""
    async def call(messages):
        text = messages[-1]["content"]
        a = text.split("【回复 A】\n")[1].split("\n\n【回复 B】")[0]
        b = text.split("【回复 B】\n")[1]
        return json.dumps({"winner": winner_of(a, b), "crossed": [], "reason": "合成"})
    return call


def _prefers(marker):
    return _judge(lambda a, b: "A" if marker in a else "B" if marker in b else "tie")


class SequentialVerdictTest(unittest.TestCase):
    def test_a_few_wins_are_not_a_verdict(self):
        self.assertEqual(shadow.verdict(3, 0)["call"], "collecting")
        self.assertEqual(shadow.verdict(6, 4)["call"], "collecting")

    def test_enough_one_sided_evidence_decides_either_way(self):
        self.assertEqual(shadow.verdict(9, 0)["call"], "better")
        self.assertEqual(shadow.verdict(0, 9)["call"], "worse")
        self.assertEqual(shadow.verdict(30, 14)["call"], "better")

    def test_a_long_even_run_rules_out_a_worthwhile_difference(self):
        self.assertEqual(shadow.verdict(20, 20)["call"], "collecting")
        self.assertEqual(shadow.verdict(30, 30)["call"], "same")

    def test_one_crossed_line_is_worse_whatever_the_score(self):
        self.assertEqual(shadow.verdict(9, 0, crossed=1)["call"], "worse")


class ShadowRunTest(unittest.TestCase):
    def setUp(self):
        self.dep = _Deployment(self)
        for day_index, count in ((0, 3), (1, 2), (3, 5)):          # 第 3 天没人说话；最后一天是今天
            for i in range(count):
                self.dep.turn(day_index, f"t{day_index}-{i}")
        shadow.start(config, "tone", cand="cand", base="base", since=DAYS[0])

    def run_shadow(self, judge=None, **kw):
        return shadow.run(config, "tone", judge_fn=judge or _prefers("cand"), worker_fn=_worker,
                          allow_paid=True, today=DAYS[3], **kw)

    def test_replays_each_finished_day_once_and_never_today(self):
        state, ran = self.run_shadow()
        self.assertEqual(ran, [DAYS[0], DAYS[1]])
        self.assertEqual(shadow.totals(state)["cand"], 5)
        self.assertEqual(state["days"][DAYS[0]]["turns"], 3)
        state, ran = self.run_shadow()                              # 第二晚：没有新过完的日子
        self.assertEqual(ran, [])
        self.assertEqual(shadow.totals(state)["cand"], 5)           # 没有重复记账
        self.assertNotIn(DAYS[3], state["days"])

    def test_tallies_accumulate_into_the_sequential_verdict(self):
        state, _ = self.run_shadow()
        out = shadow.summary(state)
        self.assertEqual((out["cand"], out["base"], out["turns"]), (5, 0, 5))
        self.assertEqual(out["verdict"]["call"], "collecting")      # 5:0 还不够
        self.assertIn("还不够", shadow.render([out]))

    def test_unpaid_run_sends_nothing_and_records_nothing(self):
        called = []

        def worker(*args, **kw):
            called.append(args)
            return _worker(*args, **kw)
        with self.assertRaises(eval_mod.PaidEvaluationRequired):
            shadow.run(config, "tone", judge_fn=_prefers("cand"), worker_fn=worker,
                       allow_paid=False, today=DAYS[3])
        self.assertEqual(called, [])
        self.assertEqual(shadow.load(config, "tone")["days"], {})

    def test_plan_lists_days_and_turn_counts_without_running(self):
        state = shadow.load(config, "tone")
        lines = shadow.describe(config, state, shadow.pending_days(config, state, today=DAYS[3]))
        self.assertIn("待回放 2 天", lines[0])
        self.assertTrue(any("3 轮真实对话" in line for line in lines))

    def test_stopped_shadow_refuses_to_run_and_names_are_checked(self):
        shadow.stop(config, "tone")
        with self.assertRaises(ValueError):
            self.run_shadow()
        with self.assertRaises(ValueError):
            shadow.start(config, "Bad Name", cand="cand")
        with self.assertRaises(ValueError):
            shadow.start(config, "tone", cand="other")              # 已登记过
        self.assertEqual(shadow.names(config), ["tone"])

    def test_candidate_env_reaches_only_the_candidate_replay(self):
        shadow.start(config, "model", cand=".", base=".", since=DAYS[0],
                     cand_env={"OPENAI_MODELS": "model-b"})
        seen = []

        def worker(code_dir, items, **kw):
            seen.append(kw.get("env_extra"))
            return _worker(code_dir, items, **{k: v for k, v in kw.items() if k != "env_extra"})
        shadow.run(config, "model", days=[DAYS[0]], judge_fn=_prefers("x"), worker_fn=worker,
                   allow_paid=True, today=DAYS[3])
        self.assertEqual(seen, [None, {"OPENAI_MODELS": "model-b"}])


class JudgeCheckTest(unittest.TestCase):
    def setUp(self):
        self.dep = _Deployment(self)

    def check(self, judge, **kw):
        return shadow.judge_check(config, judge_fn=judge, worker_fn=_worker, allow_paid=True, **kw)

    def test_only_replies_with_a_clear_real_reaction_are_checked(self):
        self.dep.reacted(0, "fixed", "repair_briefly")
        self.dep.reacted(0, "fun", "keep_playful")
        self.dep.reacted(0, "plain", "continue_normally")           # 有人回应，但说不上好坏
        self.dep.turn(0, "silent")                                   # 没送达记录
        self.dep.reacted(0, "drawn", "repair_briefly", route="image")  # 画的图：没法和文字比
        found = shadow.check_candidates(config)
        self.assertEqual({(i["outgoing_id"], i["outcome"]) for i in found},
                         {("out-fixed", "bad"), ("out-fun", "good")})

    def test_small_samples_report_counts_and_no_conclusion(self):
        self.dep.reacted(0, "fixed", "repair_briefly")
        self.dep.reacted(0, "fun", "keep_playful")
        out, added = self.check(_prefers("版的回复"))                 # 裁判总选重新生成的
        self.assertEqual(added, 2)
        self.assertEqual(out["groups"]["bad"]["lost"], 1)
        self.assertEqual(out["call"], "collecting")
        self.assertIsNone(out["p"])
        self.assertIn("下不了结论", shadow.render_check(out))

    def test_checked_replies_are_not_paid_for_twice(self):
        self.dep.reacted(0, "fixed", "repair_briefly")
        self.check(_prefers("版的回复"))
        out, added = self.check(_prefers("版的回复"))
        self.assertEqual(added, 0)
        self.assertEqual(out["groups"]["bad"]["lost"], 1)

    def test_enough_samples_say_whether_the_judge_tracks_reality(self):
        for i in range(shadow.MIN_FOR_CHECK):
            self.dep.reacted(0, f"bad{i}", "repair_briefly")
            self.dep.reacted(1, f"good{i}", "keep_playful")
        out, _ = self.check(_tracking_judge())
        self.assertEqual(out["groups"]["bad"]["lost"], shadow.MIN_FOR_CHECK)
        self.assertEqual(out["groups"]["good"]["won"], shadow.MIN_FOR_CHECK)
        self.assertEqual(out["call"], "tracks")

    def test_a_judge_blind_to_reality_is_reported_as_no_evidence(self):
        for i in range(shadow.MIN_FOR_CHECK):
            self.dep.reacted(0, f"bad{i}", "repair_briefly")
            self.dep.reacted(1, f"good{i}", "keep_playful")
        out, _ = self.check(_prefers("版的回复"))                    # 不管真实反应，一律选重新生成的
        self.assertEqual(out["call"], "no_evidence")
        self.assertIn("要打折扣", shadow.render_check(out))

    def test_unpaid_check_refuses_when_there_is_something_to_send(self):
        self.dep.reacted(0, "fixed", "repair_briefly")
        with self.assertRaises(eval_mod.PaidEvaluationRequired):
            shadow.judge_check(config, judge_fn=_prefers("x"), worker_fn=_worker, allow_paid=False)
        self.assertEqual(shadow.load_check(config)["items"], {})


def _tracking_judge():
    """裁判看得到【群友说】里的轮次名：bad 轮选重新生成的那条，good 轮选当时的原话。"""
    async def call(messages):
        text = messages[-1]["content"]
        a_is_actual = "当时说的话" in text.split("【回复 A】\n")[1].split("\n\n【回复 B】")[0]
        prefer_actual = "合成：good" in text
        winner = "A" if a_is_actual == prefer_actual else "B"
        return json.dumps({"winner": winner, "crossed": [], "reason": "合成"})
    return call


if __name__ == "__main__":
    unittest.main()
