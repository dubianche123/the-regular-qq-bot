# -*- coding: utf-8 -*-
"""判断数值：解析、落盘、弃答闸，以及「丢掉永远比猜一个安全」。

### 为什么有这一层（2026-10-31 实测，见 docs/decision-calibration.md）

原来 `parse_choices` 只取 argmax，数值验完就丢。用带客观标签的真实任务量过之后
知道这样不行也不够：

  · 概率的**刻度不能用** —— ECE 0.22、Platt 斜率 0.21（logit 需压缩约五倍），
    所以绝不能按绝对概率设阈值；**边际**是无标度的，可以。
  · 弃掉边际 < 0.3 的那 41% 样本，它们的挑对率是 **0.512**（掷硬币）；
    留下的从 0.575 升到 0.619。
  · 但**越自信不等于越准**：阈值 0.70 时挑对率 0.559，反而低于不弃答。
    所以这是一道**地板**，不是越高越好的曲线，更不是「它很确定所以放手干」的许可。
  · 官方的 `confidence` **逐位等于** p_choice − p_second（200/200 完全相同），
    不携带概率之外的信息。

所以这组用例盯三件事：数值真的留下来了、边际太小就整题丢掉、
而四个调用点对「丢掉」的反应全都是保守的。
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

import config
import digest
import naming
from regular import decision_prompts, decisions

GID, A = "GID_MARGIN", "USER_MARGIN_A"


def answer(name, choice, probs, confidence=None):
    """probs: {选项: 概率}。confidence 不给就按官方公式 (K·p_max−1)/(K−1) 填。"""
    ordered = [{"value": v, "probability": p} for v, p in probs.items()]
    if confidence is None and choice in probs and len(probs) > 1:
        k = len(probs)
        confidence = (k * max(probs.values()) - 1) / (k - 1)
    row = {"name": name, "type": "choice", "choice": choice,
           "probabilities": ordered}
    if confidence is not None:
        row["confidence"] = confidence
    return row


def body(*answers):
    return {"answers": list(answers)}


SIMPLE_Q = [{"name": "q", "type": "choice",
             "choices": [{"value": "x"}, {"value": "y"}, {"value": "z"}]}]


class ParseAnswersTest(unittest.TestCase):
    def test_numbers_survive_parsing(self):
        rows = decisions.parse_answers(
            body(answer("q", "x", {"x": 0.7, "y": 0.2, "z": 0.1})), SIMPLE_Q)
        self.assertEqual(rows["q"]["choice"], "x")
        self.assertAlmostEqual(rows["q"]["margin"], 0.5)
        self.assertAlmostEqual(rows["q"]["probabilities"]["y"], 0.2)

    def test_parse_choices_keeps_its_old_contract(self):
        payload = body(answer("q", "x", {"x": 0.7, "y": 0.3}))
        self.assertEqual(decisions.parse_choices(payload, SIMPLE_Q), {"q": "x"})

    def test_margin_is_against_the_runner_up_not_the_worst(self):
        rows = decisions.parse_answers(
            body(answer("q", "x", {"x": 0.4, "y": 0.39, "z": 0.21})), SIMPLE_Q)
        self.assertAlmostEqual(rows["q"]["margin"], 0.01)

    def test_margin_is_unknown_without_comparable_numbers(self):
        """没给 probabilities、或只列了一项 —— 量不出来就是 None，不编造。"""
        plain = {"answers": [{"name": "q", "type": "choice", "choice": "x"}]}
        self.assertIsNone(
            decisions.parse_answers(plain, SIMPLE_Q)["q"]["margin"])
        one = body(answer("q", "x", {"x": 1.0}, confidence=1.0))
        self.assertIsNone(decisions.parse_answers(one, SIMPLE_Q)["q"]["margin"])

    def test_bad_bodies_still_rejected_wholesale(self):
        for bad in (None, {}, {"answers": "x"},
                    body(answer("q", "nope", {"x": 1.0})),
                    body({"name": "q", "type": "refusal"}),
                    body(answer("other", "x", {"x": 1.0}))):
            self.assertIsNone(decisions.parse_answers(bad, SIMPLE_Q))
            self.assertIsNone(decisions.parse_choices(bad, SIMPLE_Q))


class ConfidenceFormulaTest(unittest.TestCase):
    """官方 `confidence` 到底是什么 —— 以及为什么闸不建在它上面。

    arXiv:2609.29429 附录 E 给出 `confidence = (K·p_max − 1)/(K−1)`，
    只依赖最大概率。下面四条是**真实返回**（2026-10-31，归档 803–808 那一轮），
    逐条吻合：

        K=4  p_max 0.67  conf 0.56   前二之差 0.36
        K=5  p_max 0.78  conf 0.73   前二之差 0.64
        K=3  p_max 0.63  conf 0.45   前二之差 0.31
        K=2  p_max 0.98  conf 0.96   前二之差 0.96   ← 只有这里重合

    ⚠️ 早先拿 200 条**二选一**样本得出「confidence 就是前二之差」是过度推广：
    K=2 时 (2·p_max−1) 恰好等于前二之差，K≥3 就不是了。
    """

    REAL = [("feedback_target", 4, 0.67, 0.56, 0.36),
            ("next_move", 5, 0.78, 0.73, 0.64),
            ("continuation", 3, 0.63, 0.45, 0.31),
            ("intervention", 2, 0.98, 0.96, 0.96)]

    def test_official_formula_matches_real_answers(self):
        for name, k, p_max, conf, _margin in self.REAL:
            self.assertAlmostEqual((k * p_max - 1) / (k - 1), conf, places=2,
                                   msg=f"{name}: 官方公式对不上真实 confidence")

    def test_margin_differs_from_confidence_above_two_choices(self):
        for name, k, _p, conf, margin in self.REAL:
            if k == 2:
                self.assertAlmostEqual(conf, margin, places=2,
                                       msg="二选一时两者必须重合")
            else:
                self.assertNotAlmostEqual(
                    conf, margin, places=2,
                    msg=f"{name}: K={k} 时两者不该相等（别再把它们混为一谈）")

    def test_we_gate_on_the_relative_quantity(self):
        """前二之差是相对量；confidence 是 p_max 的单调变换。

        按 confidence 设阈值 = 按绝对概率设阈值，而绝对概率的刻度正是不能用的
        那一样。所以闸建在前二之差上 —— 代价是 K≥3 时两者谁更会挑错没测过。
        """
        probs = {"a": 0.5, "b": 0.45, "c": 0.05}
        row = decisions.parse_answers(
            body(answer("q", "a", probs)),
            [{"name": "q", "type": "choice",
              "choices": [{"value": v} for v in probs]}])["q"]
        self.assertAlmostEqual(row["margin"], 0.05)          # 前二之差：很小
        self.assertAlmostEqual(row["confidence"], 0.25)      # 官方：看着不小
        # 三选一里 0.5 对 0.45 几乎是平手，闸应该拦住它
        choices, _ = decisions.usable_choices(
            body(answer("q", "a", probs)),
            [{"name": "q", "type": "choice",
              "choices": [{"value": v} for v in probs]}], min_margin=0.25)
        self.assertEqual(choices, {})

    def test_margin_is_computed_without_the_field(self):
        """字段缺了也要能算 —— 闸不该依赖它出不出现。"""
        questions = [{"name": "q", "type": "choice",
                      "choices": [{"value": "x"}, {"value": "y"}]}]
        stripped = {"answers": [{"name": "q", "type": "choice", "choice": "x",
                                 "probabilities": [
                                     {"value": "x", "probability": 0.68},
                                     {"value": "y", "probability": 0.32}]}]}
        row = decisions.parse_answers(stripped, questions)["q"]
        self.assertAlmostEqual(row["margin"], 0.36, places=6)
        self.assertIsNone(row["confidence"])


class UsableChoicesTest(unittest.TestCase):
    def test_low_margin_questions_are_dropped(self):
        choices, scores = decisions.usable_choices(
            body(answer("q", "x", {"x": 0.4, "y": 0.39, "z": 0.21})),
            SIMPLE_Q, min_margin=0.25)
        self.assertNotIn("q", choices)
        self.assertTrue(scores["q"]["dropped"])
        self.assertAlmostEqual(scores["q"]["margin"], 0.01)

    def test_comfortable_margins_pass(self):
        choices, scores = decisions.usable_choices(
            body(answer("q", "x", {"x": 0.8, "y": 0.15, "z": 0.05})),
            SIMPLE_Q, min_margin=0.25)
        self.assertEqual(choices, {"q": "x"})
        self.assertNotIn("dropped", scores["q"])

    def test_unmeasurable_margin_is_not_dropped(self):
        """量不出边际时放行 —— 不能因为没有数字就把判断全停掉。"""
        plain = {"answers": [{"name": "q", "type": "choice", "choice": "x"}]}
        choices, _ = decisions.usable_choices(plain, SIMPLE_Q, min_margin=0.9)
        self.assertEqual(choices, {"q": "x"})

    def test_zero_floor_restores_the_old_behaviour(self):
        choices, _ = decisions.usable_choices(
            body(answer("q", "x", {"x": 0.34, "y": 0.33, "z": 0.33})),
            SIMPLE_Q, min_margin=0.0)
        self.assertEqual(choices, {"q": "x"})

    def test_scores_are_kept_for_dropped_questions_too(self):
        """被丢掉本身就是要复盘的事，不能连记录一起丢。"""
        _choices, scores = decisions.usable_choices(
            body(answer("q", "x", {"x": 0.35, "y": 0.34, "z": 0.31})),
            SIMPLE_Q, min_margin=0.25)
        self.assertEqual(scores["q"]["choice"], "x")
        self.assertIsNotNone(scores["q"]["margin"])

    def test_default_floor_comes_from_config(self):
        with patch.object(config, "DECISION_MIN_MARGIN", 0.9):
            choices, _ = decisions.usable_choices(
                body(answer("q", "x", {"x": 0.8, "y": 0.2})), SIMPLE_Q)
        self.assertEqual(choices, {})


class _Recorder:
    """duck-typed 的观察日志替身。"""

    def __init__(self, boom=False):
        self.rows = []
        self.boom = boom

    def append(self, kind, group, user=None, **fields):
        if self.boom:
            raise OSError("disk on fire")
        row = {"kind": kind, "group": group, "user": user, **fields}
        self.rows.append(row)
        return row

    def of(self, site):
        return [r for r in self.rows
                if r["kind"] == "decision_scores" and r["site"] == site]


class ConservativeOnDroppedTest(unittest.IsolatedAsyncioTestCase):
    """各个调用点对「这一题不可用」的反应必须一律保守。"""

    HISTORY = [{"sender": A, "text": "在聊天", "time": 1000.0}]

    def setUp(self):
        self._name = naming.platform_name()
        naming.set_platform_name("小王")
        self.addCleanup(lambda: naming.set_platform_name(self._name))
        self.log = _Recorder()
        self.calls = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.tracker = decisions.FeedbackTracker(600.0, 180.0, log=self.log)

    def _feedback_body(self, target_probs, move_probs):
        return body(
            answer("feedback_target", max(target_probs, key=target_probs.get),
                   target_probs),
            answer("next_move", max(move_probs, key=move_probs.get),
                   move_probs))

    async def _assess(self, payload):
        self.calls.return_value = payload
        self.tracker.remember(GID, A, "（插了一句）这你们都不懂？",
                              route="banter", outgoing_id="oid-m",
                              history=self.HISTORY, now=1001.0)
        return await self.tracker.assess(GID, A, "闭嘴", now=1002.0)

    async def test_coin_flip_target_installs_no_quiet_window(self):
        """对象判别近乎掷硬币时，不许据此静默三分钟。"""
        out = await self._assess(self._feedback_body(
            {"bot_response": 0.37, "another_person": 0.35,
             "not_feedback": 0.2, "unclear": 0.08},
            {"back_off": 0.9, "repair_briefly": 0.04, "keep_playful": 0.02,
             "continue_normally": 0.02, "no_adjustment": 0.02}))
        self.assertIsNone(out.get("target"))
        self.assertEqual(out.get("action"), "no_adjustment")
        self.assertFalse(self.tracker.quiet(GID, now=1003.0))

    async def test_coin_flip_move_installs_no_quiet_window(self):
        """对象判对了，但「该怎么调整」是猜的 —— 同样不静默。"""
        out = await self._assess(self._feedback_body(
            {"bot_response": 0.9, "another_person": 0.05,
             "not_feedback": 0.03, "unclear": 0.02},
            {"back_off": 0.3, "repair_briefly": 0.28, "keep_playful": 0.2,
             "continue_normally": 0.12, "no_adjustment": 0.1}))
        self.assertEqual(out.get("target"), "bot_response")
        self.assertIsNone(out.get("action"))
        self.assertFalse(self.tracker.quiet(GID, now=1003.0))

    async def test_confident_back_off_still_works(self):
        out = await self._assess(self._feedback_body(
            {"bot_response": 0.9, "another_person": 0.05,
             "not_feedback": 0.03, "unclear": 0.02},
            {"back_off": 0.85, "repair_briefly": 0.06, "keep_playful": 0.03,
             "continue_normally": 0.03, "no_adjustment": 0.03}))
        self.assertEqual(out.get("action"), "back_off")
        self.assertTrue(self.tracker.quiet(GID, now=1003.0))

    async def test_coin_flip_callback_brings_nothing_up(self):
        self.calls.return_value = body(
            answer("callback", "bring_it_up",
                   {"bring_it_up": 0.55, "hold_it": 0.45}))
        self.assertIsNone(await self.tracker.consider_callback(
            GID, A, quote="我只喝手冲", quote_ts=0.0,
            history=self.HISTORY, now=1002.0))

    async def test_confident_callback_still_works(self):
        self.calls.return_value = body(
            answer("callback", "bring_it_up",
                   {"bring_it_up": 0.9, "hold_it": 0.1}))
        self.assertIs(await self.tracker.consider_callback(
            GID, A, quote="我只喝手冲", quote_ts=0.0,
            history=self.HISTORY, now=1002.0), True)


class MemoryCurationIsConservativeTest(unittest.IsolatedAsyncioTestCase):
    """原话裁决：这一题不可用 → 不撤旧的、不丢新的、照旧短时效。"""

    NOW = 200_000_000.0

    def _row(self, mid, quote, ts):
        return {"who": "阿强", "note": quote, "quote": quote,
                "keywords": ["咖啡"], "ts": ts,
                "evidence": {"group": GID, "sender": A, "message_id": mid,
                             "ts": ts, "quote": quote}}

    def setUp(self):
        self.log = _Recorder()
        self.calls = AsyncMock()
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)

    async def test_coin_flip_kind_keeps_the_default_short_shelf_life(self):
        fresh = self._row("mid-new", "我今天不喝咖啡了", self.NOW - 60)
        entries = [{"sender": A, "message_id": "mid-new", "group": GID,
                    "text": "我今天不喝咖啡了", "time": self.NOW - 60}]

        def reply(_input, questions):
            answers = []
            for q in questions:
                vals = [c["value"] for c in q["choices"]]
                even = {v: round(1.0 / len(vals), 4) for v in vals}
                answers.append(answer(q["name"], vals[0], even,
                                      confidence=0.0))
            return body(*answers)

        async def fake(i, q):
            return reply(i, q)

        self.calls.side_effect = fake
        merged = await decisions.curate_people(
            GID, [fresh], [], entries=entries, now=self.NOW, log=self.log)
        self.assertEqual(len(merged), 1)
        self.assertIsNone(digest.people_decision(merged[0]))
        self.assertTrue(self.log.of("memory"))

    async def test_coin_flip_supersede_keeps_the_old_entry(self):
        old = self._row("mid-old", "我只喝手冲咖啡", self.NOW - 86400)
        old["decision"] = {"kind": "preference", "action": "retain_quote",
                           "message_id": "mid-old"}
        fresh = self._row("mid-new", "咖啡我现在改喝速溶了", self.NOW - 60)
        entries = [{"sender": A, "message_id": "mid-new", "group": GID,
                    "text": "咖啡我现在改喝速溶了", "time": self.NOW - 60}]

        def reply(_input, questions):
            answers = []
            for q in questions:
                vals = [c["value"] for c in q["choices"]]
                if q["name"].endswith("_memory_kind"):
                    answers.append(answer(q["name"], "correction",
                                          {"correction": 0.9,
                                           **{v: 0.02 for v in vals
                                              if v != "correction"}}))
                elif q["name"].endswith("_memory_action"):
                    answers.append(answer(q["name"], "replace_old",
                                          {"replace_old": 0.9,
                                           **{v: 0.03 for v in vals
                                              if v != "replace_old"}}))
                else:   # supersede：掷硬币
                    answers.append(answer(q["name"], "supersede",
                                          {"supersede": 0.51, "keep": 0.49}))
            return body(*answers)

        async def fake(i, q):
            return reply(i, q)

        self.calls.side_effect = fake
        merged = await decisions.curate_people(
            GID, [fresh], [old], entries=entries, now=self.NOW, log=self.log)
        quotes = {row["quote"] for row in merged}
        self.assertIn("我只喝手冲咖啡", quotes, "掷硬币的 supersede 不许撤旧条目")


class ScoreLoggingTest(unittest.IsolatedAsyncioTestCase):
    HISTORY = [{"sender": A, "text": "在聊天", "time": 1000.0}]

    def setUp(self):
        self._name = naming.platform_name()
        naming.set_platform_name("小王")
        self.addCleanup(lambda: naming.set_platform_name(self._name))
        self.calls = AsyncMock()
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)

    async def _callback(self, tracker, probs):
        self.calls.return_value = body(
            answer("callback", max(probs, key=probs.get), probs))
        return await tracker.consider_callback(
            GID, A, quote="我只喝手冲", quote_ts=0.0,
            history=self.HISTORY, now=1002.0)

    async def test_numbers_reach_the_local_log(self):
        log = _Recorder()
        tracker = decisions.FeedbackTracker(600.0, 180.0, log=log)
        await self._callback(tracker, {"bring_it_up": 0.82, "hold_it": 0.18})
        row = log.of("callback")[0]
        self.assertEqual(row["questions"], 1)
        self.assertAlmostEqual(row["scores"]["callback"]["margin"], 0.64)
        self.assertAlmostEqual(row["scores"]["callback"]["confidence"], 0.64)
        self.assertEqual(row["scores"]["callback"]["choice"], "bring_it_up")
        self.assertEqual(row["min_margin"], config.DECISION_MIN_MARGIN)
        self.assertEqual(row["dropped"], [])
        self.assertEqual(row["actor"], "system")

    async def test_dropped_questions_are_named_in_the_log(self):
        log = _Recorder()
        tracker = decisions.FeedbackTracker(600.0, 180.0, log=log)
        await self._callback(tracker, {"bring_it_up": 0.51, "hold_it": 0.49})
        self.assertEqual(log.of("callback")[0]["dropped"], ["callback"])

    async def test_nothing_logged_without_a_log(self):
        tracker = decisions.FeedbackTracker(600.0, 180.0)
        self.assertIs(await self._callback(
            tracker, {"bring_it_up": 0.1, "hold_it": 0.9}), False)

    async def test_a_broken_log_never_breaks_the_decision(self):
        """观察日志永远不许拖垮判断路径。"""
        tracker = decisions.FeedbackTracker(600.0, 180.0,
                                            log=_Recorder(boom=True))
        self.assertIs(await self._callback(
            tracker, {"bring_it_up": 0.1, "hold_it": 0.9}), False)

    async def test_many_questions_log_counts_not_details(self):
        """原话裁决一次能问几十题 —— 明细有界，不把日志撑爆。"""
        log = _Recorder()
        names = [f"q{i}" for i in range(decisions._MAX_LOGGED_SCORES + 5)]
        scores = {n: {"choice": "x", "margin": 0.5, "confidence": 0.5}
                  for n in names}
        decisions._log_scores(log, GID, "memory", scores)
        row = log.of("memory")[0]
        self.assertEqual(row["questions"], len(names))
        self.assertNotIn("scores", row)

    async def test_log_rows_carry_no_quotes_or_identifiers(self):
        """数值留档不等于把原话和身份也留一份 —— 那两样归 archive 管。"""
        log = _Recorder()
        tracker = decisions.FeedbackTracker(600.0, 180.0, log=log)
        self.calls.return_value = body(
            answer("callback", "hold_it",
                   {"bring_it_up": 0.1, "hold_it": 0.9}))
        await tracker.consider_callback(
            GID, A, quote="我只喝手冲，速溶那种不算咖啡", quote_ts=0.0,
            history=self.HISTORY, now=1002.0)
        blob = json.dumps(log.of("callback")[0], ensure_ascii=False)
        self.assertNotIn("我只喝手冲", blob)
        self.assertNotIn("在聊天", blob)


if __name__ == "__main__":
    unittest.main()
