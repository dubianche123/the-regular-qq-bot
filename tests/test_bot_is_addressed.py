# -*- coding: utf-8 -*-
"""判断必须知道机器人在群里叫什么。

### 这条测试是怎么来的（2026-10-31，真实 /v1/decisions 实测）

群主授权用 U 盘的真实语料打真实请求。整份归档（1,782 条）里最干净的一句喊停是
归档 803–808 那一串：

    家豪：你这什么逼ai
    家豪：小王你疯了？
    家豪：谁问你了
    老王：小王闭嘴          ← 当前这句

机器人在那个群里叫**小王**。原来的 payload 把所有人匿名成 member_a/b/c，
却从不说明机器人的叫法 —— 于是判断把「小王闭嘴」读成**在说第三个人**：

    feedback_target = another_person   (bot_response 0.10–0.24，两次复现都是这个方向)

而代码在 target != bot_response 时会把动作压成 no_adjustment，
**暂停窗根本装不上** —— 被当面叫闭嘴之后，机器人会继续插嘴。

同一句话，只往 payload 里加 `bot_is_addressed_as: ["小王"]`：

    directed=false，不声明 → bot_response 0.10   不装暂停窗
    directed=true， 不声明 → bot_response 0.72   装
    directed=false，声明   → bot_response 0.67   装
    directed=true， 声明   → bot_response 0.96   装

两个信号互补，都要留：`latest_is_directed` 只描述**最后一句**，而且只有配了
触发词才为真（实测部署里 BOT_NAMES 是空的）；声明叫法还能救上下文里的
「小王你疯了？」「谁问你了」，以及根本没有 directed 字段的插话仲裁和回忆判断。

⚠️ 上面每格只打了一两次，判断本身是抽样的 —— 方向稳定，具体概率会抖。
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

import config
import naming
import wordfilter
from regular import decision_prompts, decisions

GID, A = "GID_ADDR", "USER_ADDR_A"

# 真实冻结返回（2026-10-31，gpt-6-luna，归档 803–808 + bot_is_addressed_as=["小王"]）。
# 去掉了响应 ID 与用量；只留现在还问的两题（2026-11-02 起分诊不再问 continuation /
# intervention，严格解析会拒收没问过的题），留下的 answers 逐字保留。
FROZEN_REAL_BODY = {
    "model": "gpt-6-luna",
    "answers": [
        {"type": "choice", "name": "feedback_target", "choice": "bot_response",
         "confidence": 0.56,
         "probabilities": [{"value": "bot_response", "probability": 0.67},
                           {"value": "another_person", "probability": 0.31},
                           {"value": "not_feedback", "probability": 0.01},
                           {"value": "unclear", "probability": 0.01}]},
        {"type": "choice", "name": "next_move", "choice": "back_off",
         "confidence": 0.73,
         "probabilities": [{"value": "back_off", "probability": 0.78},
                           {"value": "repair_briefly", "probability": 0.14},
                           {"value": "keep_playful", "probability": 0.03},
                           {"value": "continue_normally", "probability": 0.01},
                           {"value": "no_adjustment", "probability": 0.04}]},
    ],
}

# 真实语料（归档 803–808）。保留原文：这正是这条测试存在的理由。
REAL_HISTORY = [
    {"sender": "U_LUO", "text": "我看情况", "time": 1000.0},
    {"sender": "U_LUO", "text": "应该可以", "time": 1001.0},
    {"sender": "U_JIA", "text": "小王你疯了？", "time": 1002.0},
    {"sender": "U_JIA", "text": "谁问你了", "time": 1003.0},
]
REAL_LATEST = "小王闭嘴"


class AliasListTest(unittest.TestCase):
    def setUp(self):
        self._saved = naming.platform_name()
        self.addCleanup(lambda: naming.set_platform_name(self._saved))

    def test_takes_configured_names_then_platform_nickname(self):
        naming.set_platform_name("小王")
        with patch.object(config, "BOT_NAMES", ["机器人", "小王"]):
            self.assertEqual(decisions.bot_aliases(), ["机器人", "小王"])
        with patch.object(config, "BOT_NAMES", []):
            self.assertEqual(decisions.bot_aliases(), ["小王"])

    def test_falls_back_to_the_generic_word(self):
        """没配触发词、也拿不到平台昵称时仍然给一个值，不让这个键消失。"""
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES", []):
            self.assertEqual(decisions.bot_aliases(),
                             [naming.DEFAULT_BOT_NAME])

    def test_duplicates_collapse_before_the_cap(self):
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES", ["a"] * 20 + ["b"]):
            self.assertEqual(decisions.bot_aliases(), ["a", "b"])

    def test_the_list_is_capped(self):
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES", [f"n{i}" for i in range(20)]):
            self.assertEqual(len(decisions.bot_aliases()),
                             decisions._MAX_ALIASES)

    def test_drops_machine_shaped_names(self):
        """openid 形状的「名字」不许当叫法送出去。"""
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES",
                          ["0123456789ABCDEF0123456789ABCDEF", "小王"]):
            self.assertEqual(decisions.bot_aliases(), ["小王"])

    def test_drops_filtered_names(self):
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES", ["脏词", "小王"]), \
                patch.object(wordfilter, "has_hit", lambda t: t == "脏词"):
            self.assertEqual(decisions.bot_aliases(), ["小王"])

    def test_long_names_are_truncated(self):
        naming.set_platform_name("")
        with patch.object(config, "BOT_NAMES", ["王" * 200]):
            self.assertTrue(all(len(n) <= 40 for n in decisions.bot_aliases()))


class PayloadCarriesAliasesTest(unittest.IsolatedAsyncioTestCase):
    """三个 payload 都得带上，否则该路径还是瞎的。"""

    def setUp(self):
        self._saved = naming.platform_name()
        naming.set_platform_name("小王")
        self.addCleanup(lambda: naming.set_platform_name(self._saved))
        self.names = patch.object(config, "BOT_NAMES", ["小王"])
        self.names.start()
        self.addCleanup(self.names.stop)
        self.calls = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.tracker = decisions.FeedbackTracker(600.0, 180.0)

    def _sent(self):
        return json.loads(self.calls.await_args.args[0])

    async def test_assess_declares_the_names(self):
        self.tracker.remember(GID, "U_JIA", "（插了一句）这你们都不懂？",
                              route="banter", outgoing_id="oid-1",
                              history=REAL_HISTORY, now=1004.0)
        await self.tracker.assess(GID, "U_WANG", REAL_LATEST, now=1005.0)
        self.assertEqual(self._sent()["bot_is_addressed_as"], ["小王"])

    async def test_consider_callback_declares_the_names(self):
        await self.tracker.consider_callback(
            GID, A, quote="小王，我要玩原神", quote_ts=0.0,
            history=REAL_HISTORY, now=1005.0)
        self.assertEqual(self._sent()["bot_is_addressed_as"], ["小王"])

    async def test_identifiers_are_still_stripped(self):
        """声明叫法**不是**放宽脱敏：群号/openid/消息 ID 照旧抹掉。"""
        await self.tracker.consider_callback(
            GID, A, quote="我只喝手冲", quote_ts=0.0,
            history=[{"sender": A, "text": f"群 {GID} 里 {A} 说话",
                      "message_id": "mid-1", "time": 1000.0}],
            now=1001.0)
        payload = self.calls.await_args.args[0]
        for secret in (GID, A, "mid-1"):
            self.assertNotIn(secret, payload)
        self.assertIn("小王", json.dumps(self._sent()["bot_is_addressed_as"],
                                        ensure_ascii=False))


class QuestionsMentionTheNamesTest(unittest.TestCase):
    """字段给了但问题不提，判断不会去用它。"""

    def test_every_addressing_question_explains_the_field(self):
        # 合并自 CodeX（2026-11-02）：检查关键含义，不硬绑英文措辞
        questions = (decision_prompts.feedback_questions()
                     + decision_prompts.addressed_questions()
                     + decision_prompts.callback_questions())
        for question in questions:
            with self.subTest(question=question["name"]):
                instructions = question["instructions"]
                if question["name"] in {"feedback_target", "addressed"}:
                    # 这两题要结合上下文判断指向，名字出现不等于邀请回复。
                    self.assertIn(decision_prompts.CONTEXTUAL_ADDRESS, instructions)
                else:
                    self.assertIn(decision_prompts.ADDRESSED_AS, instructions)

        direct = decision_prompts.ADDRESSED_AS.lower()
        contextual = decision_prompts.CONTEXTUAL_ADDRESS.lower()
        for meaning in (direct, contextual):
            self.assertIn("bot_is_addressed_as", meaning)
            self.assertIn("name", meaning)
            self.assertIn("bot", meaning)
        self.assertIn("latest_is_directed", contextual)
        self.assertIn("not", contextual)
        self.assertIn("invit", contextual)

    def test_contextual_questions_distinguish_target_from_invitation(self):
        feedback = {q["name"]: q for q in decision_prompts.feedback_questions()}["feedback_target"]
        addressed = decision_prompts.addressed_questions()[0]
        # 点名叫停指向机器人；点了名要不要答，由 addressed 结合上下文判断。
        # （没点名的那种 2026-11-02 起由小王自己定，分诊不再问 continuation。）
        feedback_instructions = feedback["instructions"].lower()
        self.assertIn("stop", feedback_instructions)
        self.assertIn("bot", feedback_instructions)
        self.assertTrue({"bot_response", "another_person"}.issubset(
            {choice["value"] for choice in feedback["choices"]}))
        addressed_instructions = addressed["instructions"].lower()
        self.assertIn("conversation", addressed_instructions)
        self.assertIn("invit", addressed_instructions)
        self.assertEqual({"answer", "quip", "let_it_pass"},
                         {choice["value"] for choice in addressed["choices"]})

    def test_memory_questions_do_not_need_it(self):
        """原话裁决看的是单条引语，不判「在跟谁说话」。"""
        questions = decision_prompts.memory_questions(
            {"c0": {"candidate_quote": "我只喝手冲", "existing_memories": []}})
        self.assertTrue(questions)
        for question in questions:
            self.assertNotIn("bot_is_addressed_as", question["instructions"])


class FrozenRealResponseTest(unittest.IsolatedAsyncioTestCase):
    """用真实返回锁住结论：这句真实的喊停必须真的装上暂停窗。"""

    def setUp(self):
        self._saved = naming.platform_name()
        naming.set_platform_name("小王")
        self.addCleanup(lambda: naming.set_platform_name(self._saved))
        self.calls = AsyncMock(return_value=FROZEN_REAL_BODY)
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)

    async def test_real_stop_installs_the_quiet_window(self):
        tracker = decisions.FeedbackTracker(600.0, 180.0)
        tracker.remember(GID, "U_JIA", "（没人叫它，它自己插了一句）这你们都不懂？",
                         route="banter", outgoing_id="oid-real",
                         history=REAL_HISTORY, now=1004.0)
        out = await tracker.assess(GID, "U_WANG", REAL_LATEST, now=1005.0)
        self.assertEqual(out["target"], "bot_response")
        self.assertEqual(out["action"], "back_off")
        self.assertTrue(tracker.quiet(GID, now=1006.0),
                        "被当面叫闭嘴却没装暂停窗 —— 这正是 2026-10-31 实测到的那个 bug")
        self.assertEqual(out["source_route"], "banter")

    async def test_the_real_body_parses_strictly(self):
        """真实返回带 confidence + probabilities 数组，严格解析必须认它。"""
        questions = decision_prompts.feedback_questions()
        self.assertEqual(
            decisions.parse_choices(FROZEN_REAL_BODY, questions),
            {"feedback_target": "bot_response", "next_move": "back_off"})

    async def test_that_verdict_becomes_intrusion_evidence(self):
        """这一笔必须能喂给跨天的接纳度 —— 不然喊停还是只值三分钟。"""
        import receptive
        store = receptive.Receptiveness()
        tracker = decisions.FeedbackTracker(600.0, 180.0)
        tracker.remember(GID, "U_JIA", "（插了一句）这你们都不懂？",
                         route="banter", outgoing_id="oid-real-2",
                         history=REAL_HISTORY, now=1004.0)
        out = await tracker.assess(GID, "U_WANG", REAL_LATEST, now=1005.0)
        self.assertTrue(store.record(GID, "U_WANG", out["action"],
                                     route=out["source_route"]))
        self.assertEqual([label for _, label in
                          store.records[f"{GID}|U_WANG"]["items"]], [0])


if __name__ == "__main__":
    unittest.main()
