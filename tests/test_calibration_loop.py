# -*- coding: utf-8 -*-
"""标签回路：把判断的数值和随后真实发生的事拼起来，只读。

这组用例守的主要不是算术，而是**不许撒谎**：
  · 选择沉默的那些永远看不到结果 —— 审查，不是负例；
  · 没人反应 —— 缺失标签，不是负例；
  · 只谈内容的反馈结论不算「打扰该不该」的标签；
  · 样本不足时报条数，不报编出来的比例；
  · 以及：**测试永远不许写进部署目录的真实日志**。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import config
import interactions as interactions_mod
import naming
import runtime
from regular import calibration, decisions

G = "GID_CAL"
A = "USER_CAL_A"


class _Journal:
    """按互动日志的真实形状写一份临时 JSONL。"""

    def __init__(self, directory):
        self.dir = directory
        self.path = os.path.join(directory, "2026-10-31.jsonl")
        self.ts = 1_000_000.0
        self._fp = open(self.path, "a", encoding="utf-8")

    def close(self):
        self._fp.close()

    def write(self, kind, **fields):
        self.ts += 1.0
        row = {"schema": interactions_mod.SCHEMA, "event_id": os.urandom(8).hex(),
               "ts": self.ts, "kind": kind, "group": G, "user": A, **fields}
        self._fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fp.flush()
        return row

    # ── 一条完整的因果链 ──
    def judged(self, message_id, *, p, choice="join_briefly", site="banter",
               question="intervention", margin=0.6):
        self.write("decision_scores", site=site, actor="system",
                   source_message_id=message_id, questions=1, dropped=[],
                   min_margin=0.25,
                   scores={question: {"choice": choice, "p": p,
                                      "margin": margin, "confidence": margin}})

    def delivered(self, message_id, outgoing_id, route="banter"):
        self.write("delivery", actor="assistant", status="delivered",
                   route=route, outgoing_id=outgoing_id,
                   source_message_id=message_id, generation="model",
                   fallback=False, feedback_eligible=True)

    def reacted(self, outgoing_id, action, target="bot_response"):
        self.write("natural_feedback", actor="system", target=target,
                   action=action, source_reply_id=outgoing_id)

    def chain(self, tag, *, p, action=None, deliver=True,
              choice="join_briefly", site="banter",
              question="intervention"):
        self.judged(tag, p=p, choice=choice, site=site, question=question)
        if deliver:
            self.delivered(tag, "out-" + tag,
                           route="banter" if site == "banter" else "banter")
            if action:
                self.reacted("out-" + tag, action)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.journal = _Journal(self._tmp.name)
        self.addCleanup(self.journal.close)

    def report(self, site="banter"):
        return calibration.report(self._tmp.name, site=site)


class JoinTest(_Base):
    def test_three_hop_join_produces_a_label_pair(self):
        self.journal.chain("m1", p=0.8, action="keep_playful")
        rows, counts, _diag = calibration.pairs(self._tmp.name)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["p"], 0.8)
        self.assertEqual(rows[0]["y"], 1)
        self.assertEqual(counts["labelled"], 1)
        self.assertEqual(counts["welcome"], 1)

    def test_back_off_is_the_negative_label(self):
        self.journal.chain("m1", p=0.9, action="back_off")
        rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(rows[0]["y"], 0)
        self.assertEqual(counts["unwelcome"], 1)

    def test_join_is_by_identifier_not_by_time(self):
        """两条交错的链不许互相串台。"""
        self.journal.judged("m1", p=0.9)
        self.journal.judged("m2", p=0.2)
        self.journal.delivered("m2", "out-m2")
        self.journal.delivered("m1", "out-m1")
        self.journal.reacted("out-m2", "back_off")
        self.journal.reacted("out-m1", "keep_playful")
        rows, _counts, _ = calibration.pairs(self._tmp.name)
        got = {round(r["p"], 2): r["y"] for r in rows}
        self.assertEqual(got, {0.9: 1, 0.2: 0})

    def test_callback_site_has_its_own_question(self):
        self.journal.chain("m1", p=0.7, action="back_off", site="callback",
                           question="callback", choice="bring_it_up")
        rows, counts, _ = calibration.pairs(self._tmp.name, site="callback")
        self.assertEqual(len(rows), 1)
        self.assertEqual(counts["unwelcome"], 1)
        # 位置不混：banter 的报告里看不到它
        self.assertEqual(calibration.pairs(self._tmp.name)[1]["scores"], 0)


class NeverFabricateNegativesTest(_Base):
    """这组是这份工具的良心所在。"""

    def test_staying_quiet_is_censoring_not_a_negative(self):
        self.journal.judged("m1", p=0.3, choice="stay_quiet")
        rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(rows, [])
        self.assertEqual(counts["stayed_quiet"], 1)
        self.assertEqual(counts["labelled"], 0)
        self.assertEqual(counts["unwelcome"], 0)

    def test_no_reaction_is_a_missing_label_not_a_negative(self):
        self.journal.chain("m1", p=0.8, action=None)
        rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(rows, [])
        self.assertEqual(counts["delivered"], 1)
        self.assertEqual(counts["no_reaction"], 1)
        self.assertEqual(counts["unwelcome"], 0)

    def test_content_only_verdicts_are_not_intrusion_labels(self):
        for action in ("repair_briefly", "continue_normally", "no_adjustment"):
            with self.subTest(action=action):
                journal = _Journal(tempfile.mkdtemp())
                journal.chain("m1", p=0.8, action=action)
                journal.close()
                rows, counts, _ = calibration.pairs(journal.dir)
                self.assertEqual(rows, [])
                self.assertEqual(counts["other_verdict"], 1)

    def test_feedback_about_someone_else_is_not_a_label(self):
        self.journal.judged("m1", p=0.8)
        self.journal.delivered("m1", "out-m1")
        self.journal.write("natural_feedback", actor="system",
                           target="another_person", action="back_off",
                           source_reply_id="out-m1")
        rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(rows, [])
        self.assertEqual(counts["no_reaction"], 1)

    def test_undelivered_generation_is_not_an_exposure(self):
        """生成成功不等于送达成功。"""
        self.journal.judged("m1", p=0.8)
        self.journal.write("delivery", actor="assistant", status="queued",
                           route="banter", outgoing_id="out-m1",
                           source_message_id="m1", generation="model")
        rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(rows, [])
        self.assertEqual(counts["delivered"], 0)

    def test_dropped_by_the_floor_is_counted_separately(self):
        self.journal.write("decision_scores", site="banter", actor="system",
                           source_message_id="m1", questions=1,
                           dropped=["intervention"], min_margin=0.25,
                           scores={})
        _rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(counts["dropped_by_floor"], 1)


class StatisticsTest(_Base):
    def test_prevalence_gap_is_the_headline(self):
        """群主问的那个量：它平均说的，和实际发生的，差多少。"""
        for i in range(10):
            self.journal.chain(f"m{i}", p=0.9,
                               action="keep_playful" if i < 4 else "back_off")
        out = self.report()
        self.assertAlmostEqual(out["base_rate"], 0.4)
        self.assertAlmostEqual(out["mean_predicted"], 0.9)
        self.assertAlmostEqual(out["prevalence_gap"], 0.5)

    def test_ece_comes_with_its_null(self):
        """几百条上 ECE 即便完美校准也偏正，不给零假设读不懂。"""
        for i in range(12):
            self.journal.chain(f"m{i}", p=0.5,
                               action="keep_playful" if i % 2 else "back_off")
        out = self.report()
        self.assertIsNotNone(out["ece"])
        self.assertIn("mean", out["ece_null"])
        self.assertIn("p95", out["ece_null"])
        self.assertGreater(out["ece_null"]["p95"], 0.0)

    def test_platt_only_fits_with_enough_labels(self):
        for i in range(calibration.MIN_FOR_FIT - 1):
            self.journal.chain(f"m{i}", p=0.8, action="keep_playful")
        out = self.report()
        self.assertFalse(out["enough_to_fit"])
        self.assertIsNone(out["platt"])
        self.assertIn("标签不足", calibration.render(out))

    def test_platt_slope_below_one_means_overconfident(self):
        # 说得很高但只有一半成立 → 斜率应当被压下来
        for i in range(24):
            self.journal.chain(f"m{i}", p=0.95,
                               action="keep_playful" if i % 2 else "back_off")
        out = self.report()
        self.assertTrue(out["enough_to_fit"])
        self.assertLess(out["platt"]["slope"], 1.0)

    def test_empty_journal_reports_counts_not_zero_percent(self):
        out = self.report()
        self.assertIsNone(out["base_rate"])
        self.assertIsNone(out["prevalence_gap"])
        self.assertIsNone(out["label_rate"])
        text = calibration.render(out)
        self.assertIn("不可得，不是 0%", text)

    def test_label_rate_reports_how_much_is_missing(self):
        self.journal.chain("m1", p=0.8, action="keep_playful")
        for i in range(3):
            self.journal.chain(f"q{i}", p=0.8, action=None)
        out = self.report()
        self.assertAlmostEqual(out["label_rate"], 0.25)
        self.assertEqual(out["counts"]["no_reaction"], 3)

    def test_render_states_the_boundaries(self):
        self.journal.chain("m1", p=0.8, action="keep_playful")
        text = calibration.render(self.report())
        self.assertIn("审查", text)
        self.assertIn("缺失标签", text)
        self.assertIn("不是随机实验", text)


class RobustnessTest(_Base):
    def test_broken_lines_are_counted_not_fatal(self):
        with open(self.journal.path, "a", encoding="utf-8") as fp:
            fp.write("{not json\n")
        self.journal.chain("m1", p=0.8, action="keep_playful")
        out = self.report()
        self.assertEqual(out["malformed_lines"], 1)
        self.assertEqual(out["counts"]["labelled"], 1)

    def test_missing_directory_is_not_an_error(self):
        out = calibration.report(os.path.join(self._tmp.name, "nope"))
        self.assertEqual(out["files_read"], 0)
        self.assertEqual(out["read_errors"], 0)
        self.assertIsNone(out["base_rate"])

    def test_bad_probabilities_are_skipped(self):
        for bad in (None, "0.8", True, 1.5, -0.1, float("nan")):
            journal = _Journal(tempfile.mkdtemp())
            journal.judged("m1", p=bad)
            journal.delivered("m1", "out-m1")
            journal.reacted("out-m1", "keep_playful")
            journal.close()
            rows, counts, _ = calibration.pairs(journal.dir)
            self.assertEqual(rows, [], f"{bad!r} 不该被当成概率")
            self.assertEqual(counts["with_probability"], 0)

    def test_unknown_site_is_rejected(self):
        with self.assertRaises(ValueError):
            calibration.report(self._tmp.name, site="memory")

    def test_rows_without_score_details_are_skipped(self):
        """题数超限时只记条数，没有明细可拼。"""
        self.journal.write("decision_scores", site="banter", actor="system",
                           source_message_id="m1", questions=99, dropped=[],
                           min_margin=0.25)
        _rows, counts, _ = calibration.pairs(self._tmp.name)
        self.assertEqual(counts["scores"], 1)
        self.assertEqual(counts["acted"], 0)


class JournalStaysOutOfTheDeploymentTest(unittest.IsolatedAsyncioTestCase):
    """patch 掉 runtime.INTERACTIONS 必须真的改到判断的落盘去向。

    2026-10-31 实测到的 bug：runtime 把 INTERACTIONS **对象**在构造时传给了
    FeedbackTracker，于是用例 patch 掉 runtime.INTERACTIONS 也改不到那一份，
    测试的 decision_scores 行被写进了部署目录的真实日志（跑一轮攒 1,275 行）。
    修法是传一个惰性解析的 lambda。这条用例把它钉住。
    """

    async def test_patching_the_singleton_redirects_the_score_rows(self):
        naming.set_platform_name("小王")
        self.addCleanup(lambda: naming.set_platform_name(""))
        with tempfile.TemporaryDirectory() as tmp:
            log = interactions_mod.InteractionLog(tmp, enabled=True,
                                                  keep_days=0)
            self.addCleanup(log.close)
            body = {"answers": [{"name": "callback", "type": "choice",
                                 "choice": "hold_it", "confidence": 0.9,
                                 "probabilities": [
                                     {"value": "bring_it_up",
                                      "probability": 0.05},
                                     {"value": "hold_it",
                                      "probability": 0.95}]}]}
            with patch.object(runtime, "INTERACTIONS", log), \
                    patch.object(decisions.providers, "call_decision",
                                 AsyncMock(return_value=body)):
                verdict = await runtime.FEEDBACK.consider_callback(
                    G, A, quote="我只喝手冲", quote_ts=0.0,
                    history=[{"sender": A, "text": "在聊天",
                              "time": 1000.0}], now=1001.0)
            self.assertIs(verdict, False)
            rows = [json.loads(line) for name in os.listdir(tmp)
                    for line in open(os.path.join(tmp, name),
                                     encoding="utf-8")]
            scored = [r for r in rows if r["kind"] == "decision_scores"]
            self.assertTrue(scored, "数值没落到被 patch 的那份日志里")
            self.assertEqual(scored[0]["site"], "callback")

    def test_the_deployment_directory_is_not_the_test_target(self):
        """直接盯住部署目录：全量跑完它必须还是空的（见 conftest 的 fixture）。"""
        rows = []
        try:
            names = os.listdir(config.INTERACTION_LOG_DIR)
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            with open(os.path.join(config.INTERACTION_LOG_DIR, name),
                      encoding="utf-8") as fp:
                rows += [line for line in fp if line.strip()]
        self.assertEqual(
            rows, [],
            "部署目录的互动日志里出现了行 —— 有用例在往真实数据目录里写")


if __name__ == "__main__":
    unittest.main()
