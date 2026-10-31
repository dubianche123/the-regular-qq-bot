"""原生裁决助手：严格解析、来源绑定的耐久记忆、一次批量、失败保守。"""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import config
import digest
from regular import decisions, decision_prompts

NOW = 200_000_000.0
G = "GID_POLICY"


def _row(sender, mid, quote, ts, *, kw=("咖啡",), decision=None, who="阿强"):
    row = {"who": who, "note": quote, "quote": quote, "keywords": list(kw),
           "ts": ts,
           "evidence": {"group": G, "sender": sender, "message_id": mid,
                        "ts": ts, "quote": quote}}
    if decision:
        row["decision"] = decision
    return row


def _native(name, choice, choices):
    """官方真实返回形态：type=choice + confidence + probabilities 数组。"""
    others = [v for v in choices if v != choice]
    share = round(0.1 / len(others), 4) if others else 0.0
    probs = [{"value": choice, "probability": 0.9}]
    probs += [{"value": v, "probability": share} for v in others]
    return {"name": name, "type": "choice", "choice": choice,
            "confidence": 0.9, "probabilities": probs}


def native_body(questions, **overrides):
    """按问题词表生成整组原生答案；名字与值都必须真在问题里。"""
    wanted = {q["name"]: [c["value"] for c in q.get("choices", [])]
              for q in questions}
    for name, value in overrides.items():
        assert name in wanted, f"factory: unknown question {name!r}"
        assert value in wanted[name], \
            f"factory: {value!r} not in {name} choices"
    default = {n: vs[0] for n, vs in wanted.items()}
    default.update(overrides)
    return {"answers": [_native(n, v, wanted[n]) for n, v in default.items()]}


SIMPLE_Q = [{"name": "a", "type": "choice",
             "choices": [{"value": "x"}, {"value": "y"}]},
            {"name": "b", "type": "choice",
             "choices": [{"value": "m"}, {"value": "n"}]}]

# 真实冻结响应节选（去掉响应 ID/延迟/用量）：feedback 前两题 + memory 基础两题
FROZEN_FEEDBACK_ANSWERS = [
    {"name": "feedback_target", "type": "choice", "choice": "bot_response",
     "confidence": 0.97,
     "probabilities": [
         {"value": "bot_response", "probability": 0.98},
         {"value": "another_person", "probability": 0.01},
         {"value": "not_feedback", "probability": 0.01},
         {"value": "unclear", "probability": 0.0}]},
    {"name": "next_move", "type": "choice", "choice": "back_off",
     "confidence": 1.0,
     "probabilities": [
         {"value": "back_off", "probability": 1.0},
         {"value": "repair_briefly", "probability": 0.0},
         {"value": "keep_playful", "probability": 0.0},
         {"value": "continue_normally", "probability": 0.0},
         {"value": "no_adjustment", "probability": 0.0}]}]

FROZEN_MEMORY_ANSWERS = [
    {"name": "memory_kind", "type": "choice", "choice": "preference",
     "confidence": 0.99,
     "probabilities": [
         {"value": "preference", "probability": 0.99},
         {"value": "fact", "probability": 0.0},
         {"value": "temporary", "probability": 0.0},
         {"value": "shared_moment", "probability": 0.0},
         {"value": "correction", "probability": 0.01},
         {"value": "discard", "probability": 0.0}]},
    {"name": "memory_action", "type": "choice", "choice": "retain_quote",
     "confidence": 0.6,
     "probabilities": [
         {"value": "retain_quote", "probability": 0.7},
         {"value": "keep_briefly", "probability": 0.27},
         {"value": "replace_old", "probability": 0.02},
         {"value": "discard", "probability": 0.01}]}]


class ParseChoicesTest(unittest.TestCase):
    def test_frozen_real_responses_parse(self):
        fb_q = decision_prompts.feedback_questions()[:2]
        self.assertEqual(
            decisions.parse_choices({"answers": FROZEN_FEEDBACK_ANSWERS}, fb_q),
            {"feedback_target": "bot_response", "next_move": "back_off"})
        mem_q = [dict(q) for q in decision_prompts.MEMORY_QUESTIONS]
        self.assertEqual(
            decisions.parse_choices({"answers": FROZEN_MEMORY_ANSWERS}, mem_q),
            {"memory_kind": "preference", "memory_action": "retain_quote"})

    def test_valid_parse(self):
        body = native_body(SIMPLE_Q, a="x", b="n")
        self.assertEqual(decisions.parse_choices(body, SIMPLE_Q),
                         {"a": "x", "b": "n"})

    def test_missing_duplicate_unknown_names_rejected(self):
        bads = [
            {"answers": [_native("a", "x", ["x", "y"])]},   # 缺一题
            {"answers": [_native("a", "x", ["x", "y"]),
                         _native("a", "y", ["x", "y"]),
                         _native("b", "m", ["m", "n"])]},   # 重名
            native_body(SIMPLE_Q, a="x", b="m") | {"extra": 1},
            {"answers": "nope"}, {}, None, "text",
            {"answers": [["not-a-dict"], _native("b", "m", ["m", "n"])]},
            {"answers": [_native("a", "x", ["x", "y"]),
                         {"name": ["x"], "type": "choice", "choice": "m"}]},
        ]
        bads[2]["answers"].append(_native("ghost", "x", ["x"]))
        for body in bads:
            self.assertIsNone(decisions.parse_choices(body, SIMPLE_Q), body)

    def test_refusal_and_off_vocabulary_rejected(self):
        for body in (
                {"answers": [{"name": "a", "type": "refusal",
                              "refusal": "can't"},
                             _native("b", "m", ["m", "n"])]},
                {"answers": [_native("a", "maybe", ["x", "y"]),
                             _native("b", "m", ["m", "n"])]},
                {"answers": [_native("a", "x", ["x", "y"]),
                             {"name": "b", "type": "choice",
                              "choice": ["m"]}]}):
            self.assertIsNone(decisions.parse_choices(body, SIMPLE_Q), body)

    def test_confidence_and_probability_list_bounds(self):
        for conf in (-0.1, 1.5, float("nan"), "high", True, 10**400):
            body = {"answers": [
                dict(_native("a", "x", ["x", "y"]), confidence=conf),
                _native("b", "m", ["m", "n"])]}
            self.assertIsNone(decisions.parse_choices(body, SIMPLE_Q), conf)
        for probs in (["not-a-dict"], [{"value": "x"}, ],  # 缺 probability
                      [{"value": "x", "probability": 0.5},
                       {"value": "x", "probability": 0.5}],   # 重复 value
                      [{"value": "ghost", "probability": 0.5}],
                      [{"value": "x", "probability": 1.4}],
                      [{"value": "x", "probability": True}],
                      {"value": "x", "probability": 0.5}):   # 不是数组
            a = dict(_native("a", "x", ["x", "y"]), probabilities=probs)
            body = {"answers": [a, _native("b", "m", ["m", "n"])]}
            self.assertIsNone(decisions.parse_choices(body, SIMPLE_Q), probs)
        # 缺 probabilities / 词表内合法数组都放行
        body = {"answers": [
            {k: v for k, v in _native("a", "x", ["x", "y"]).items()
             if k != "probabilities"},
            _native("b", "m", ["m", "n"])]}
        self.assertEqual(decisions.parse_choices(body, SIMPLE_Q),
                         {"a": "x", "b": "m"})


class PeopleDecisionTagTest(unittest.TestCase):
    def test_source_bound_tag_and_ages(self):
        row = _row("U1", "m1", "q", NOW - 100,
                   decision={"kind": "preference", "action": "retain_quote",
                             "message_id": "m1"})
        self.assertEqual(digest.people_decision(row)["kind"], "preference")
        durable = config.PEOPLE_DURABLE_MAX_AGE_DAYS * 86400
        self.assertEqual(digest.people_max_age_seconds(row), durable)
        row2 = _row("U1", "m1", "q", NOW - 100)      # 未裁决 → 短时效
        self.assertEqual(digest.people_max_age_seconds(row2),
                         config.PEOPLE_NOTE_MAX_AGE_DAYS * 86400)

    def test_malformed_metadata_is_unclassified_not_a_crash(self):
        for bad in ({"kind": ["preference"], "action": "retain_quote",
                     "message_id": "m1"},
                    {"kind": "preference", "action": {"a": 1},
                     "message_id": "m1"},
                    {"kind": "preference", "action": "retain_quote",
                     "message_id": ""},
                    {"kind": "ghost", "action": "retain_quote",
                     "message_id": "m1"},
                    {"kind": "preference", "action": "nuke",
                     "message_id": "m1"},
                    {"kind": "preference", "action": "retain_quote",
                     "message_id": "other"}):
            bad_row = _row("U1", "m1", "q", NOW - 100, decision=bad)
            self.assertIsNone(digest.people_decision(bad_row), bad)
        no_ev = {"decision": {"kind": "preference", "action": "retain_quote",
                              "message_id": "m1"},
                 "evidence": "nope"}
        self.assertIsNone(digest.people_decision(no_ev))


class CuratePeopleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.call = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.call)
        ctx.start(); self.addCleanup(ctx.stop)
        self.entries = [
            {"sender": "U1", "text": "我喜欢咖啡，每天都要来一杯",
             "message_id": "n1", "ts": NOW - 50, "text_complete": True,
             "group": G},
            {"sender": "U2", "text": "我讨厌咖啡，从来不喝",
             "message_id": "n2", "ts": NOW - 40, "text_complete": True,
             "group": G},
        ]

    def _questions(self, payload_json):
        cands = json.loads(payload_json)["candidates"]
        return decision_prompts.memory_questions(cands)

    async def test_one_batch_two_people_single_call_stable_aliases(self):
        new = [_row("U1", "n1", "我喜欢咖啡，每天都要来一杯", NOW - 50),
               _row("U2", "n2", "我讨厌咖啡，从来不喝", NOW - 40)]
        task = decisions.curate_people(G, new, [], entries=self.entries,
                                       now=NOW)
        # 先让它发一次，拿到真实问题再喂同形答案
        payload_holder = {}
        async def capture(text, questions):
            payload_holder["questions"] = questions
            return native_body(questions)
        self.call.side_effect = capture
        out = await task
        self.assertEqual(self.call.await_count, 1)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(digest.people_decision(r) for r in out))
        payload = self.call.await_args.args[0]
        for sentinel in (G, "U1", "U2", "n1", "n2"):
            self.assertNotIn(sentinel, payload)
        cands = json.loads(payload)["candidates"]
        # 同一个人多条候选 → 同一代号；两个人 → 不同代号
        speakers = {c["speaker"] for c in cands.values()}
        self.assertEqual(len(speakers), 2)
        self.assertTrue(all(s.startswith("member_") for s in speakers))

    async def test_same_person_different_kinds_keep_separate_tags(self):
        entries = [
            {"sender": "U1", "text": "我喜欢咖啡", "message_id": "n1",
             "ts": NOW - 50, "text_complete": True, "group": G},
            {"sender": "U1", "text": "今晚要加班到十点", "message_id": "n2",
             "ts": NOW - 40, "text_complete": True, "group": G},
        ]
        new = [_row("U1", "n1", "我喜欢咖啡", NOW - 50),
               _row("U1", "n2", "今晚要加班到十点", NOW - 40, kw=("加班",))]

        async def capture(text, questions):
            return native_body(questions,
                               c0_memory_kind="preference",
                               c0_memory_action="retain_quote",
                               c1_memory_kind="temporary",
                               c1_memory_action="keep_briefly")
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, [], entries=entries,
                                            now=NOW)
        tags = {r["evidence"]["message_id"]:
                digest.people_decision(r)["kind"] for r in out}
        self.assertEqual(tags, {"n1": "preference", "n2": "temporary"})
        # 各自的代号没被串：n2 是 temporary 不会被错标 preference
        payload = json.loads(self.call.await_args.args[0])
        self.assertEqual({c["speaker"] for c in payload["candidates"].values()},
                         {"member_a"})

    async def test_same_batch_correction_supersedes_earlier_candidate(self):
        entries = [
            {"sender": "U1", "text": "我喜欢咖啡", "message_id": "n1",
             "ts": NOW - 50, "text_complete": True, "group": G},
            {"sender": "U1", "text": "其实刚才说错了，我不喝咖啡",
             "message_id": "n2", "ts": NOW - 40, "text_complete": True,
             "group": G},
        ]
        new = [_row("U1", "n1", "我喜欢咖啡", NOW - 50),
               _row("U1", "n2", "其实刚才说错了，我不喝咖啡", NOW - 40)]

        async def capture(text, questions):
            names = {q["name"] for q in questions}
            return native_body(
                questions,
                c0_memory_kind="preference",
                c0_memory_action="retain_quote",
                c1_memory_kind="correction",
                c1_memory_action="replace_old",
                **{n: "supersede" for n in names
                   if n.startswith("c1_supersede_")})
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, [], entries=entries,
                                            now=NOW)
        self.assertEqual([r["evidence"]["message_id"] for r in out], ["n2"])
        self.assertEqual(digest.people_decision(out[0])["kind"], "correction")

    async def test_overlapping_priors_ranked_per_candidate(self):
        """两个修正候选共享旧记忆池：各自的 mN 只影响自己名下那题。"""
        old = [_row("U1", "o1", "我喝咖啡要加冰", NOW - 200,
                    decision={"kind": "preference", "action": "retain_quote",
                              "message_id": "o1"}),
               _row("U1", "o2", "我早上离不开咖啡", NOW - 180,
                    decision={"kind": "fact", "action": "retain_quote",
                              "message_id": "o2"})]
        entries = [
            {"sender": "U1", "text": "其实我最近戒咖啡了",
             "message_id": "n1", "ts": NOW - 50, "text_complete": True,
             "group": G},
            {"sender": "U1", "text": "我现在只喝白水，咖啡彻底不碰",
             "message_id": "n2", "ts": NOW - 40, "text_complete": True,
             "group": G},
        ]
        new = [_row("U1", "n1", "其实我最近戒咖啡了", NOW - 50, kw=("咖啡",)),
               _row("U1", "n2", "我现在只喝白水，咖啡彻底不碰", NOW - 40,
                    kw=("咖啡",))]

        async def capture(text, questions):
            cands = json.loads(text)["candidates"]
            # c0 只看到 o1/o2 排前面；c1 可能把 n1 也当旧记忆 ——
            # 这里验证：各自 mN 映射互不串台。
            sup = {}
            for cid, cand in cands.items():
                for mem in cand["existing_memories"]:
                    q = f"{cid}_supersede_{mem['id']}"
                    # c0 撤 o1；c1 撤它名下的 o2 —— 各自只认自己题面的 mN
                    if cid == "c0" and "加冰" in mem["quote"]:
                        sup[q] = "supersede"
                    elif cid == "c1" and "离不开" in mem["quote"]:
                        sup[q] = "supersede"
                    else:
                        sup[q] = "keep"
            return native_body(questions,
                               c0_memory_kind="correction",
                               c0_memory_action="replace_old",
                               c1_memory_kind="correction",
                               c1_memory_action="replace_old", **sup)
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, old, entries=entries,
                                            now=NOW)
        ids = {r["evidence"]["message_id"] for r in out}
        # o1 被 c0 撤、o2 被 c1 撤；两个新行都留
        self.assertEqual(ids, {"n1", "n2"})

    async def test_no_cross_person_or_newer_rollback(self):
        old = [_row("U2", "o1", "我讨厌咖啡", NOW - 80,
                    decision={"kind": "preference", "action": "retain_quote",
                              "message_id": "o1"}),
               _row("U1", "o2", "未来才说的话", NOW - 10, kw=("未来",),
                    decision={"kind": "fact", "action": "retain_quote",
                              "message_id": "o2"})]
        new = [_row("U1", "n1", "其实我不喝咖啡了", NOW - 40)]
        entries = [{"sender": "U1", "text": "其实我不喝咖啡了",
                    "message_id": "n1", "ts": NOW - 40,
                    "text_complete": True, "group": G}]

        async def capture(text, questions):
            cands = json.loads(text)["candidates"]
            # 合规答案：o2 比候选新 → 根本不进对比；o1 是别人的也不进
            names = {q["name"] for q in questions}
            sup = {n: "supersede" for n in names
                   if n.startswith("c0_supersede_")}
            return native_body(questions, c0_memory_kind="correction",
                               c0_memory_action="replace_old", **sup)
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, old, entries=entries,
                                            now=NOW)
        ids = {r["evidence"]["message_id"] for r in out}
        self.assertIn("o1", ids)   # 别人的旧记忆不许被撤
        self.assertIn("o2", ids)   # 比候选还新的来源不许被回滚
        self.assertIn("n1", ids)
        n1 = next(r for r in out if r["evidence"]["message_id"] == "n1")
        self.assertEqual(digest.people_decision(n1)["kind"], "correction")

    async def test_discard_temp_and_carry_durable(self):
        durable_old = [_row("U1", "o1", "我喝咖啡要加冰", NOW - 10 * 86400,
                            decision={"kind": "preference",
                                      "action": "retain_quote",
                                      "message_id": "o1"})]
        new = [_row("U1", "n1", "我喜欢咖啡", NOW - 50)]
        entries = [{"sender": "U1", "text": "我喜欢咖啡",
                    "message_id": "n1", "ts": NOW - 50,
                    "text_complete": True, "group": G}]

        async def keep_temp(text, questions):
            return native_body(questions, c0_memory_kind="temporary",
                               c0_memory_action="keep_briefly")
        self.call.side_effect = keep_temp
        out = await decisions.curate_people(G, new, durable_old,
                                            entries=entries, now=NOW)
        ids = {r["evidence"]["message_id"] for r in out}
        self.assertEqual(ids, {"o1", "n1"})

        async def discard(text, questions):
            return native_body(questions, c0_memory_kind="discard",
                               c0_memory_action="discard")
        self.call.side_effect = discard
        out = await decisions.curate_people(G, new, durable_old,
                                            entries=entries, now=NOW)
        self.assertEqual([r["evidence"]["message_id"] for r in out], ["o1"])

    async def test_failure_and_disabled_keep_plain_new_rows(self):
        new = [_row("U1", "n1", "我喜欢咖啡", NOW - 50)]
        self.call.return_value = None
        out = await decisions.curate_people(G, new, [], now=NOW)
        self.assertEqual(len(out), 1)
        self.assertIsNone(digest.people_decision(out[0]))
        self.call.return_value = {"answers": [{"name": "bogus"}]}
        out = await decisions.curate_people(G, new, [], now=NOW)
        self.assertIsNone(digest.people_decision(out[0]))
        with patch.object(config, "DECISION_ENABLED", False):
            out = await decisions.curate_people(G, new, [], now=NOW)
            self.assertIsNone(digest.people_decision(out[0]))
            self.assertEqual(self.call.await_count, 2)  # 关着就不再调

    async def test_invalid_sources_get_zero_native_calls(self):
        bad_rows = [
            _row("U1", "bad1", "我喜欢咖啡", NOW - 50,
                 kw=("咖啡",)),       # 关键词其实不在 quote 里也行 —— 这条合规
            {"who": "x", "note": "y", "quote": "短",   # 证据缺字段
             "keywords": ["x"], "ts": NOW - 10,
             "evidence": {"group": G, "sender": "U1"}},
            _row("U2", "bad2", "我讨厌咖啡", NOW - 30 * 86400,
                 kw=("讨厌",)),        # 临时条目早过 2 天 → 不合格
        ]
        bad_rows[0]["evidence"]["group"] = "OTHER_GROUP"   # 跨群
        out = await decisions.curate_people(G, bad_rows, [], now=NOW)
        self.assertEqual(self.call.await_count, 0)
        self.assertEqual(out, [])

    async def test_forged_fresh_decision_is_stripped(self):
        new = [_row("U1", "n1", "我喜欢咖啡", NOW - 50,
                    decision={"kind": "fact", "action": "retain_quote",
                              "message_id": "n1"})]   # 自贴标签不算
        out = await decisions.curate_people(G, new, [], now=NOW)
        self.assertIsNone(digest.people_decision(out[0]))
        self.assertNotIn("decision", out[0])

    async def test_expired_temp_old_row_is_not_carried(self):
        temp_old = [_row("U1", "o1", "我喝咖啡要加冰", NOW - 10 * 86400,
                         decision={"kind": "temporary",
                                   "action": "keep_briefly",
                                   "message_id": "o1"})]
        durable_old = [_row("U2", "o2", "我讨厌咖啡", NOW - 10 * 86400,
                            decision={"kind": "preference",
                                      "action": "retain_quote",
                                      "message_id": "o2"})]
        out = await decisions.curate_people(G, [], temp_old + durable_old,
                                            now=NOW)
        self.assertEqual([r["evidence"]["message_id"] for r in out], ["o2"])

    async def test_cap_and_dedup_exact_24_newest_first(self):
        rows = [_row("U1", f"m{i}", f"我喜欢咖啡口味{i}号", NOW - i,
                     kw=(f"口味{i}",),
                     decision={"kind": "fact", "action": "retain_quote",
                               "message_id": f"m{i}"}) for i in range(30)]
        out = await decisions.curate_people(G, [], rows, now=NOW)
        self.assertEqual(len(out), 24)
        ts = [r["evidence"]["ts"] for r in out]
        self.assertEqual(ts, sorted(ts, reverse=True))
        dup = dict(rows[0]) | {"who": "复制"}
        out = await decisions.curate_people(G, [], [rows[0], dict(dup)],
                                            now=NOW)
        self.assertEqual(len(out), 1)

    async def test_nonhex_private_ids_scrubbed_from_request(self):
        weird = "OPENID-not-hex-12345"
        entries = [
            {"sender": "U2", "text": f"刚才{weird}也在说咖啡",
             "message_id": "ctx-1", "ts": NOW - 60,
             "text_complete": True, "group": G},
            {"sender": weird, "text": "我喜欢咖啡",
             "message_id": "n9", "ts": NOW - 50,
             "text_complete": True, "group": G},
        ]
        new = [_row(weird, "n9", "我喜欢咖啡", NOW - 50)]

        async def capture(text, questions):
            return native_body(questions)
        self.call.side_effect = capture
        await decisions.curate_people(G, new, [], entries=entries, now=NOW)
        payload = self.call.await_args.args[0]
        self.assertNotIn(weird, payload)     # 发言人代号 member_a 顶替
        self.assertNotIn("n9", payload)
        self.assertNotIn("ctx-1", payload)
        self.assertNotIn(G, payload)
        ctx_text = json.dumps(json.loads(payload)["candidates"],
                              ensure_ascii=False)
        self.assertIn("member_", ctx_text)


class ChronoOrderTest(unittest.IsolatedAsyncioTestCase):
    """对比池按原始发言时间序排，不靠模型提案顺序；每条来源每候选只出现一次。"""

    def setUp(self):
        self.call = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.call)
        ctx.start(); self.addCleanup(ctx.stop)

    async def test_durable_first_then_new_then_correction_chain(self):
        """旧耐久 + 新偏好 + 其更正：更正撤的是「本批更早的新行」，不是旧行。"""
        old = [_row("U1", "o1", "我喝咖啡要加冰", NOW - 300, kw=("咖啡",),
                    decision={"kind": "preference", "action": "retain_quote",
                              "message_id": "o1"})]
        # 提案顺序故意与时间相反：更正在前，原偏好在后
        new = [_row("U1", "n2", "其实我不喝咖啡了改喝白水", NOW - 40,
                    kw=("咖啡",)),
               _row("U1", "n1", "我喜欢咖啡", NOW - 50)]
        entries = [
            {"sender": "U1", "text": "我喜欢咖啡", "message_id": "n1",
             "ts": NOW - 50, "text_complete": True, "group": G},
            {"sender": "U1", "text": "其实我不喝咖啡了改喝白水",
             "message_id": "n2", "ts": NOW - 40, "text_complete": True,
             "group": G},
        ]
        seen = {}

        async def capture(text, questions):
            cands = json.loads(text)["candidates"]
            for cid, cand in cands.items():
                seen[cid] = [m["quote"] for m in cand["existing_memories"]]
            names = {q["name"] for q in questions}
            sup = {}
            for cid, cand in cands.items():
                # 更正候选撤掉「我喜欢咖啡」那条（它是更早的新行，也可能映射成 mN）
                if "改喝白水" in cand["candidate_quote"]:
                    for mem in cand["existing_memories"]:
                        sup[f"{cid}_supersede_{mem['id']}"] = (
                            "supersede" if "我喜欢咖啡" in mem["quote"]
                            else "keep")
                else:
                    for mem in cand["existing_memories"]:
                        sup[f"{cid}_supersede_{mem['id']}"] = "keep"
            return native_body(
                questions,
                **{f"{cid}_memory_kind": (
                    "correction" if "改喝白水" in cand["candidate_quote"]
                    else "preference") for cid, cand in cands.items()},
                **{f"{cid}_memory_action": (
                    "replace_old" if "改喝白水" in cand["candidate_quote"]
                    else "retain_quote") for cid, cand in cands.items()},
                **sup)
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, old, entries=entries,
                                            now=NOW)
        ids = {r["evidence"]["message_id"] for r in out}
        # n1 被 n2 撤掉；o1 留下来 —— 每条来源在每个候选的对比里只出现一次
        self.assertEqual(ids, {"o1", "n2"})
        for cid, mems in seen.items():
            self.assertEqual(len(mems), len(set(mems)),
                             f"{cid} 的对比池有重复来源")

    async def test_forged_durable_tag_cannot_resurrect_expired_row(self):
        # 45 天前的未裁决发言贴了伪造的 90 天耐久标签 —— 过期就是过期
        forged = [_row("U1", "n1", "我喜欢咖啡", NOW - 45 * 86400,
                       decision={"kind": "preference",
                                 "action": "retain_quote",
                                 "message_id": "n1"})]
        out = await decisions.curate_people(G, forged, [], now=NOW)
        self.assertEqual(self.call.await_count, 0)
        self.assertEqual(out, [])

    async def test_fresh_temp_old_row_rebinds_without_native(self):
        """还在短时效内的临时裁决条目被原样重提：继承旧标签，不调原生。"""
        temp_old = [_row("U1", "o1", "今晚要加班", NOW - 3600, kw=("加班",),
                         decision={"kind": "temporary",
                                   "action": "keep_briefly",
                                   "message_id": "o1"})]
        new = [_row("U1", "o1", "今晚要加班", NOW - 3600, kw=("加班",))]
        out = await decisions.curate_people(G, new, temp_old, now=NOW)
        self.assertEqual(self.call.await_count, 0)
        self.assertEqual(len(out), 1)
        self.assertEqual(digest.people_decision(out[0])["kind"], "temporary")


class SameTimestampOrderTest(unittest.IsolatedAsyncioTestCase):
    """同 sender 同 ts：顺序以已处理前缀位置为准，提案反序不许倒因为果。"""

    def setUp(self):
        self.call = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.call)
        ctx.start(); self.addCleanup(ctx.stop)

    async def test_reversed_proposals_respect_entry_position(self):
        entries = [
            {"sender": "U1", "text": "我喜欢咖啡", "message_id": "n1",
             "ts": NOW - 50, "text_complete": True, "group": G},
            {"sender": "U1", "text": "其实我不喝了", "message_id": "n2",
             "ts": NOW - 50, "text_complete": True, "group": G},
        ]
        # 提案顺序反了：更正在前。n1 不许看见 n2，n2 可以看见 n1。
        new = [_row("U1", "n2", "其实我不喝了", NOW - 50, kw=("不喝",)),
               _row("U1", "n1", "我喜欢咖啡", NOW - 50)]
        priors = {}

        async def capture(text, questions):
            cands = json.loads(text)["candidates"]
            for cid, cand in cands.items():
                priors[cid] = [m["quote"] for m in cand["existing_memories"]]
            names = {q["name"] for q in questions}
            sup = {n: "supersede" for n in names if "_supersede_" in n}
            return native_body(
                questions,
                **{f"{cid}_memory_kind": "correction" for cid in cands},
                **{f"{cid}_memory_action": "replace_old" for cid in cands},
                **sup)
        self.call.side_effect = capture
        out = await decisions.curate_people(G, new, [], entries=entries,
                                            now=NOW)
        # n1 的对比池不许出现「其实我不喝了」（同 ts 但排它后面）；
        # n2 可以看见「我喜欢咖啡」。
        for cid, mems in priors.items():
            for mem_quote in mems:
                if "不喝了" in (json.loads(self.call.await_args.args[0])
                               ["candidates"][cid]["candidate_quote"]):
                    pass
        n2_cid = next(cid for cid, c in
                      json.loads(self.call.await_args.args[0])
                      ["candidates"].items() if "不喝了" in c["candidate_quote"])
        n1_cid = next(cid for cid, c in
                      json.loads(self.call.await_args.args[0])
                      ["candidates"].items() if "我喜欢咖啡" in c["candidate_quote"])
        self.assertNotIn("其实我不喝了", priors.get(n1_cid, []))
        self.assertEqual([r["evidence"]["message_id"] for r in out], ["n2"])


class RenderAgeTest(unittest.TestCase):
    """耐久 90 天 vs 短 2 天，在 render_people 与回喂摘要上同口径。"""

    def _state(self, rows):
        return {"data": {"people": rows}}

    def test_render_people_respects_per_row_age(self):
        durable = _row("U1", "d1", "我喜欢咖啡加冰", NOW - 10 * 86400,
                       kw=("咖啡",),
                       decision={"kind": "preference",
                                 "action": "retain_quote",
                                 "message_id": "d1"})
        temp = _row("U1", "t1", "今晚要加班到十点", NOW - 10 * 86400,
                    kw=("加班",))
        resolver = lambda s: "阿强"
        with patch.object(config, "PERSONAL_MEMORY_ENABLED", True), \
                patch.object(digest.time, "time", return_value=NOW):
            out = digest.render_people(
                self._state([durable, temp]), group_id=G, sender="U1",
                text="今晚咖啡和加班都聊", resolver=resolver, now=NOW)
        self.assertIn("咖啡加冰", out)
        self.assertNotIn("加班", out)

    def test_old_summary_text_respects_per_row_age(self):
        durable = _row("U1", "d1", "我喜欢咖啡加冰", NOW - 10 * 86400,
                       kw=("咖啡",),
                       decision={"kind": "preference",
                                 "action": "retain_quote",
                                 "message_id": "d1"})
        temp = _row("U1", "t1", "今晚要加班到十点", NOW - 10 * 86400,
                    kw=("加班",))
        slot = {"summary": {"brief": "x", "data": {"people": [durable, temp]}}}
        store = digest.GroupDigest()
        with patch.object(digest.time, "time", return_value=NOW):
            text = store.old_summary_text(
                slot, resolver=lambda s: "阿强")
        self.assertIn("咖啡加冰", text)
        self.assertNotIn("加班", text)


class StampInheritanceTest(unittest.TestCase):
    def test_same_message_inherits_prior_decision_and_ts(self):
        old_row = _row("U1", "m1", "我喜欢咖啡", NOW - 500,
                       decision={"kind": "preference",
                                 "action": "retain_quote",
                                 "message_id": "m1"})
        old_data = {"people": [old_row]}
        new_data = {"people": [{"who": "阿强", "quote": "我喜欢咖啡",
                                "note": "我喜欢咖啡",
                                "keywords": ["咖啡"]}]}
        entries = []
        with patch.object(digest.time, "time", return_value=NOW):
            n = digest.stamp_people(old_data, new_data, now=NOW,
                                    entries=entries,
                                    resolver=lambda s: "阿强", group_id=G)
        self.assertEqual(n, 1)
        row = new_data["people"][0]
        self.assertEqual(row["ts"], NOW - 500)
        self.assertEqual(digest.people_decision(row)["kind"], "preference")


class FeedbackTrackerEdgeTest(unittest.IsolatedAsyncioTestCase):
    def test_lru_evicts_oldest_group(self):
        tracker = decisions.FeedbackTracker(120, 180, max_groups=2)
        tracker.remember("g1", "u", "t", route="reply", outgoing_id="o1")
        tracker.remember("g2", "u", "t", route="reply", outgoing_id="o2")
        tracker._slot("g1")                      # 摸一下 g1 → 最新
        tracker.remember("g3", "u", "t", route="reply", outgoing_id="o3")
        self.assertNotIn("g2", tracker._groups)
        self.assertIn("g1", tracker._groups)
        self.assertIn("g3", tracker._groups)


class CuratorSeamTest(unittest.IsolatedAsyncioTestCase):
    """people_curator 在摘要落槽【之前】被调用：等待期间槽里仍是旧摘要。"""

    async def test_slot_holds_previous_summary_until_curator_resolves(self):
        store = digest.GroupDigest()
        old_row = {"who": "阿强", "note": "旧原话", "quote": "旧原话",
                   "keywords": ["咖啡"], "ts": NOW - 100,
                   "evidence": {"group": G, "sender": "U1",
                                "message_id": "o-old", "ts": NOW - 100,
                                "quote": "旧原话"},
                   "decision": {"kind": "fact", "action": "retain_quote",
                                "message_id": "o-old"}}
        store.groups[G] = {"summary": {"brief": "旧简报",
                                     "data": {"people": [old_row]},
                                     "updated": NOW - 200},
                           "last_run": 0.0}
        entry = {"sender": "U1", "text": "我喜欢咖啡",
                 "message_id": "n-new", "ts": NOW - 50,
                 "text_complete": True, "group": G}
        payload = json.dumps({"topics": [],
                              "people": [{"who": "阿强",
                                          "quote": "我喜欢咖啡",
                                          "note": "我喜欢咖啡",
                                          "keywords": ["咖啡"]}]},
                             ensure_ascii=False)

        async def ask(system, user):
            return f"新简报\n<DIGEST>{payload}</DIGEST>"

        gate = asyncio.Event()
        seen = {}

        async def curator(new_rows, old_rows, *, entries, now):
            seen["new"] = list(new_rows)
            seen["old"] = list(old_rows)
            seen["entries"] = list(entries)
            await gate.wait()
            return list(new_rows) + list(old_rows)

        task = asyncio.create_task(digest.compress_group(
            store, G, [entry], ask,
            resolver=lambda sender: {"U1": "阿强"}.get(sender, ""),
            people_curator=curator))
        for _ in range(10):
            await asyncio.sleep(0)
        current = (store.groups[G]["summary"] or {}).get("data") or {}
        self.assertEqual([r["evidence"]["message_id"]
                          for r in current.get("people", [])], ["o-old"])
        gate.set()
        out = await task
        ids = [r["evidence"]["message_id"] for r in out["data"]["people"]]
        self.assertEqual(set(ids), {"n-new", "o-old"})
        self.assertEqual(seen["entries"], [entry])
        self.assertEqual([r["evidence"]["message_id"]
                          for r in seen["old"]], ["o-old"])

    async def test_curator_exception_keeps_previous_summary(self):
        store = digest.GroupDigest()
        store.groups[G] = {"summary": {"brief": "旧简报",
                                     "data": {"people": []},
                                     "updated": NOW - 200},
                           "last_run": 0.0}
        entry = {"sender": "U1", "text": "我喜欢咖啡",
                 "message_id": "n-new", "ts": NOW - 50,
                 "text_complete": True, "group": G}
        payload = json.dumps({"topics": [], "people": []},
                             ensure_ascii=False)

        async def ask(system, user):
            return f"新简报\n<DIGEST>{payload}</DIGEST>"

        async def boom(new_rows, old_rows, *, entries, now):
            raise RuntimeError("native down")

        with self.assertRaises(RuntimeError):
            await digest.compress_group(
                store, G, [entry], ask,
                resolver=lambda s: "阿强", people_curator=boom)
        # 中途抛错：旧摘要原样还在，新名单一次都没露过脸
        self.assertEqual(store.groups[G]["summary"]["brief"], "旧简报")
        self.assertEqual(store.groups[G]["summary"]["data"]["people"], [])


if __name__ == "__main__":
    unittest.main()
