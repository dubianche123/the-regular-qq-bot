# -*- coding: utf-8 -*-
"""好感度怎么加减分：模型判方向、代码定幅度、每天封顶（2026-10-06 群主定的口径）。

要解决的是「被 @ 且没扣分就 +1」这条 —— 它让好感度退化成**被搭理次数**：
实测一天 100 多次记账里 108 次 +1，连一直骂人的也在涨分。

新口径三句话：
  1. **被 @ 本身不给分**。被搭理不等于被喜欢。
  2. **只判方向**（-1/0/1），绝对幅度交给代码 + 每日封顶。2026-11-16 起方向由分诊那一题判
     （decision_prompts.TOWARD_BOT_QUESTIONS），不再是回复模型自己写 <CMD> 记账、也没有词表兜底。
  3. 封顶**不对称**：加分放宽（关系本来就该越聊越近），扣分收紧（误判更伤人）。
"""
import sys
import time
import unittest

sys.path.insert(0, "..")
import runtime

import bot
import affinity
import config
import prompts
import relations
from types import SimpleNamespace
from regular import decision_prompts

GROUP = "TEST-AFF-GROUP"
OID = "OID-AFF-0001"


class DirectionQuestionTest(unittest.TestCase):
    """分诊那一题：只判方向，且把两条易错标准写死。"""

    def _q(self):
        (q,) = decision_prompts.toward_bot_questions()
        return q

    def test_only_three_values_allowed(self):
        self.assertEqual([c["value"] for c in self._q()["choices"]], ["warm", "hostile", "neutral"])
        self.assertEqual(bot._TOWARD_BOT, {"warm": 1, "neutral": 0, "hostile": -1})

    def test_mentioning_is_not_credit(self):
        self.assertIn("not warmth by itself", self._q()["instructions"])

    def test_discipline_is_not_hostility(self):
        """09-24 老王喊「小王闭嘴」被判 -1 —— 那是管教，不是讨厌。"""
        self.assertIn("is correction, not hostility", self._q()["instructions"])

    def test_the_reply_model_no_longer_grades_its_own_turn(self):
        self.assertFalse(hasattr(prompts, "CMD_PROTOCOL"))
        self.assertFalse(hasattr(config, "CMD_PROTOCOL_ENABLED"))
        self.assertFalse(hasattr(relations, "local_sentiment"), "不再有正负词表兜底")


class DailyCapTest(unittest.TestCase):
    def setUp(self):
        runtime.STATE.data.pop("affinity_daily", None)
        for k in [k for k in runtime.RELATIONS.records if k.startswith(GROUP)]:
            runtime.RELATIONS.records.pop(k, None)
        # 新建档案的 last_seen=0，会被「久未往来」的衰减当成陈年旧账扣分 ——
        # 那是另一条机制，测封顶时先把它排除掉。
        runtime.RELATIONS.get(GROUP, OID)["last_seen"] = time.time()

    def test_gains_stop_at_the_cap(self):
        for _ in range(config.AFFINITY_DAILY_GAIN_CAP + 5):
            bot.apply_affinity_delta_capped(GROUP, OID, 1, "test")
        rec = runtime.RELATIONS.get(GROUP, OID, create=False)
        self.assertLessEqual(rec["score"], config.AFFINITY_DAILY_GAIN_CAP)

    def test_losses_stop_at_the_cap(self):
        for _ in range(config.AFFINITY_DAILY_LOSS_CAP + 5):
            bot.apply_affinity_delta_capped(GROUP, OID, -1, "test")
        rec = runtime.RELATIONS.get(GROUP, OID, create=False)
        self.assertGreaterEqual(rec["score"], -config.AFFINITY_DAILY_LOSS_CAP)

    def test_caps_are_asymmetric_on_purpose(self):
        """涨得快、跌得慢：一次误判的伤害要能靠几天聊回来。"""
        self.assertGreater(config.AFFINITY_DAILY_GAIN_CAP, config.AFFINITY_DAILY_LOSS_CAP)

    def test_zero_costs_nothing(self):
        self.assertIsNone(bot.apply_affinity_delta_capped(GROUP, OID, 0, "test"))
        b = bot.affinity_budget(GROUP, OID)
        self.assertEqual((b["gain"], b["loss"]), (0, 0))

    def test_budget_resets_next_day(self):
        bot.apply_affinity_delta_capped(GROUP, OID, 1, "test")
        tomorrow = time.time() + 86400
        b = bot.affinity_budget(GROUP, OID, now=tomorrow)
        self.assertEqual(b["gain"], 0)
        # 且新的一天真的能再加
        bot.apply_affinity_delta_capped(GROUP, OID, 1, "test", now=tomorrow)
        self.assertEqual(bot.affinity_budget(GROUP, OID, now=tomorrow)["gain"], 1)

    def _settle(self, toward, target=None):
        turn = SimpleNamespace(group_id=GROUP, sender_openid=OID, user_text="……",
                               feedback={"toward_bot": toward, "target": target})
        bot._settle(turn, ("正文", None, object()))
        return runtime.RELATIONS.get(GROUP, OID, create=False)["score"]

    def test_neutral_or_unasked_gives_nothing(self):
        """中性、或者分诊没问这题（没叫它也不在回它的话），都不记这一票。"""
        self.assertEqual(self._settle("neutral"), 0)
        self.assertEqual(self._settle(None), 0)

    def test_insult_still_costs_affinity(self):
        self.assertEqual(self._settle("hostile"), -1)

    def test_hostility_inside_feedback_on_its_reply_costs_nothing(self):
        """被指出问题、嫌插话时机不对：那是反馈，不扣交情。"""
        self.assertEqual(self._settle("hostile", target="bot_response"), 0)

    def test_digest_settlement_respects_the_cap_too(self):
        """压缩结算一次能到 ±6，它要是绕过封顶，封顶就形同虚设。"""
        for _ in range(3):
            bot.apply_affinity_delta_capped(
                GROUP, OID, -2, "压缩结算", span=config.AFFINITY_DIGEST_SPAN, count=False)
        rec = runtime.RELATIONS.get(GROUP, OID, create=False)
        self.assertGreaterEqual(rec["score"], -config.AFFINITY_DAILY_LOSS_CAP)
        self.assertEqual(rec["interactions"], 0, "压缩结算不该虚增搭话次数")


class NoMilestoneAnnouncementTest(unittest.TestCase):
    """2026-11-16：跨台阶不再排一句「咱俩更熟了」让它当场说出来 —— 交情是内心戏不是台词。"""

    def test_crossing_a_step_moves_the_score_without_queueing_a_line(self):
        for k in [k for k in runtime.RELATIONS.records if k.startswith("TEST-MS")]:
            runtime.RELATIONS.records.pop(k, None)
        rec = runtime.RELATIONS.get("TEST-MS", OID)
        rec["last_seen"] = time.time()
        runtime.RELATIONS.apply("TEST-MS", OID, 6, span=config.AFFINITY_DIGEST_SPAN)
        score = runtime.RELATIONS.apply("TEST-MS", OID, 6, span=config.AFFINITY_DIGEST_SPAN)[0]
        self.assertGreaterEqual(score, 10, "跨过了一个 10 分台阶")
        self.assertIsNone(rec.get("pending_milestone"))


class TwoTierStandardsTest(unittest.TestCase):
    """逐轮和压缩判的必须是两件事：逐轮判单句（宁可漏记不许错记），
    压缩判关系走势（负责对冲逐轮的误判）。两层用同一把尺子的话，
    「压缩能纠偏」就只是巧合，不是设计 —— 2026-10-06 群主指出后补的分工。"""

    GID = "TEST-LEDGER"

    def test_per_turn_judges_the_sentence_only(self):
        (q,) = decision_prompts.toward_bot_questions()
        self.assertIn("teasing, roasting or swearing in jest", q["instructions"])
        self.assertIn("When unsure, choose neutral", q["instructions"])
        self.assertIn("not the speaker's whole day", q["instructions"])

    def test_reduce_judges_the_trajectory(self):
        import digest
        self.assertIn("关系走势，不是逐句流水账", digest.SYSTEM_REDUCE)
        self.assertIn("该对冲就对冲", digest.SYSTEM_REDUCE)
        # 原来的「分界跟着交情走」已改写成更紧的口径：交情只帮助理解笑骂，
        # 不构成开玩笑的许可，也不许因为熟就忽略喊停。
        self.assertIn("交情可以帮助理解笑骂，但不是开玩笑许可", digest.SYSTEM_REDUCE)
        self.assertIn("不能因为交情深就忽略喊停", digest.SYSTEM_REDUCE)
        self.assertIn("【当前关系底账】", digest.SYSTEM_REDUCE)

    def test_ledger_lists_tiers_by_nick(self):
        runtime.RELATIONS.set_nick(self.GID, "OID-LD-1", "阿强")
        runtime.RELATIONS.get(self.GID, "OID-LD-1")["score"] = 40
        text = affinity.affinity_ledger(self.GID)
        self.assertIn("阿强=", text)
        self.assertIn(relations.level_label(relations.level_key(40)), text)

    def test_ledger_none_when_nobody_named(self):
        for k in [k for k in list(runtime.RELATIONS.records) if k.startswith(self.GID)]:
            runtime.RELATIONS.records.pop(k, None)
        self.assertIsNone(affinity.affinity_ledger(self.GID))

    def test_ledger_reaches_the_reduce_payload(self):
        """底账不接线等于没做 —— 它必须真的进 reduce 的 user payload。"""
        import asyncio

        import digest

        runtime.RELATIONS.set_nick(self.GID, "OID-LD-2", "铁蛋")
        runtime.RELATIONS.get(self.GID, "OID-LD-2")["score"] = -6

        seen = {}

        async def ask(system, user):
            seen["user"] = user
            return '简报\n<DIGEST>{"topics":[],"affinity":[]}</DIGEST>'

        store = digest.GroupDigest()
        entries = [{"sender": "OID-LD-2", "text": "在吗", "ts": time.time()}]
        out = asyncio.run(digest.compress_group(
            store, self.GID, entries, ask,
            ledger=affinity.affinity_ledger(self.GID)))
        self.assertIsNotNone(out)
        self.assertIn("【当前关系底账】", seen["user"])
        self.assertIn("铁蛋=", seen["user"])
        for k in [k for k in list(runtime.RELATIONS.records) if k.startswith(self.GID)]:
            runtime.RELATIONS.records.pop(k, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
