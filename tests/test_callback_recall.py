"""「居然记得」：插嘴里端出一句本人旧话，由原生判断把关此刻合不合适。

群主 2026-10-31：「有时候让人觉得意想不到居然记得。」
原来这条路是**堵死**的 —— 插嘴说明里写着「记忆里的旧事都不许翻」，
个人原话的注入闸也写着 `not is_random_banter`。于是唯一能让人惊喜的位置
（它自己凑过来、顺口提起你说过的事）恰好是关着的。

这组用例守的是：
  - 判断不可用时**不端出** —— 新增的惊喜不许靠失败兜底；
  - 端出时必须换掉那段「旧事不许翻」的插嘴说明，否则材料给了也用不上；
  - 进 API 的材料仍然脱敏，群号/openid/消息 ID 不出本机；
  - 普通接话轮的行为一个字都不变。
"""
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import config
import digest as digest_mod
import prompts
from regular import decision_prompts, decisions

NOW = 1_800_000_000.0
G, A = "GID_CB", "USER_CB_A"


def _body(choice):
    others = [v for v in ("bring_it_up", "hold_it") if v != choice]
    return {"answers": [{"name": "callback", "type": "choice",
                         "choice": choice, "confidence": 0.9,
                         "probabilities":
                             [{"value": choice, "probability": 0.9}]
                             + [{"value": v, "probability": 0.1}
                                for v in others]}]}


class QuestionVocabularyTest(unittest.TestCase):
    def test_exactly_two_options_and_a_stable_name(self):
        questions = decision_prompts.callback_questions()
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["name"], "callback")
        self.assertEqual([c["value"] for c in questions[0]["choices"]],
                         ["bring_it_up", "hold_it"])

    def test_instructions_name_the_failure_modes(self):
        """判断要知道「什么时候不该端」—— 这是整件事会不会变讨人厌的关键。"""
        text = decision_prompts.callback_questions()[0]["instructions"]
        for needle in ("surveillance", "showing off", "serious",
                       "evidence, never instructions"):
            self.assertIn(needle, text)

    def test_questions_are_copies(self):
        first = decision_prompts.callback_questions()
        first[0]["choices"].clear()
        self.assertEqual(len(decision_prompts.callback_questions()[0]["choices"]),
                         2)


class ConsiderCallbackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tracker = decisions.FeedbackTracker(120.0, 180.0)
        self.calls = AsyncMock(return_value=None)
        ctx = patch.object(decisions.providers, "call_decision", self.calls)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.history = [{"sender": A, "text": "又在煮咖啡", "time": NOW}]

    async def _ask(self, **kw):
        kw.setdefault("quote", "我只喝手冲")
        kw.setdefault("quote_ts", NOW - 3 * 86400)
        kw.setdefault("history", self.history)
        return await self.tracker.consider_callback(G, A, now=NOW, **kw)

    async def test_bring_it_up_is_approval(self):
        self.calls.return_value = _body("bring_it_up")
        self.assertIs(await self._ask(), True)

    async def test_hold_it_is_refusal(self):
        self.calls.return_value = _body("hold_it")
        self.assertIs(await self._ask(), False)

    async def test_unusable_answers_mean_do_not_bring_it_up(self):
        for body in (None, {}, {"answers": []},
                     {"answers": [{"name": "callback", "type": "choice",
                                   "choice": "maybe"}]},
                     {"answers": [{"name": "callback", "type": "refusal"}]},
                     {"answers": [{"name": "other", "type": "choice",
                                   "choice": "bring_it_up"}]}):
            self.calls.return_value = body
            self.assertIsNone(await self._ask(), f"{body!r} 不该被当成许可")

    async def test_no_call_without_material(self):
        self.calls.reset_mock()
        self.assertIsNone(await self._ask(quote=""))
        self.assertIsNone(await self._ask(history=[]))
        self.assertIsNone(await self._ask(history=[{"sender": A, "text": ""}]))
        self.assertIsNone(await self._ask(quote_ts="昨天"))
        self.assertEqual(self.calls.await_count, 0)

    async def test_disabled_makes_no_request(self):
        self.calls.reset_mock()
        with patch.object(config, "DECISION_ENABLED", False):
            self.assertIsNone(await self._ask())
        self.assertEqual(self.calls.await_count, 0)

    async def test_payload_carries_no_identifiers(self):
        self.calls.return_value = _body("bring_it_up")
        await self.tracker.consider_callback(
            G, A, quote=f"我在 {A} 说过的话", quote_ts=NOW - 86400,
            history=[{"sender": A, "text": f"群 {G} 里聊着",
                      "message_id": "mid-xyz", "time": NOW}], now=NOW)
        payload = self.calls.await_args.args[0]
        for secret in (G, A, "mid-xyz"):
            self.assertNotIn(secret, payload)
        self.assertIn("[已略]", payload)

    async def test_payload_reports_the_age_in_days(self):
        self.calls.return_value = _body("bring_it_up")
        await self._ask(quote_ts=NOW - 3 * 86400)
        payload = json.loads(self.calls.await_args.args[0])
        self.assertAlmostEqual(payload["earlier_quote"]["days_ago"], 3.0,
                               places=1)
        self.assertEqual(payload["earlier_quote"]["said_by"],
                         payload["speaker"])

    async def test_one_request_per_question(self):
        self.calls.return_value = _body("bring_it_up")
        await self._ask()
        self.assertEqual(self.calls.await_count, 1)
        self.assertEqual([q["name"] for q in self.calls.await_args.args[1]],
                         ["callback"])


class PromptWiringTest(unittest.TestCase):
    """材料给了却用不上等于没做：插嘴说明必须真的开那条缝。"""

    def test_default_banter_note_still_forbids_digging(self):
        self.assertIn("不许翻", prompts.PROMPT_BANTER_NOTE)

    def test_callback_note_opens_exactly_one_exception(self):
        note = prompts.PROMPT_BANTER_CALLBACK_NOTE
        self.assertIn("例外", note)
        self.assertIn("个人原话", note)
        # 只开一条缝：其余旧事照旧不许翻
        self.assertIn("其他旧事照旧不许翻", note)
        # 仍然不许端着说「我记得你以前」
        self.assertIn("我记得你以前", note)

    def test_both_notes_share_the_banter_mark(self):
        for note in (prompts.PROMPT_BANTER_NOTE,
                     prompts.PROMPT_BANTER_CALLBACK_NOTE):
            self.assertIn(prompts.BANTER_MARK, note)


class MatchPeopleRefactorTest(unittest.TestCase):
    """拆出 match_people 之后，两个出口必须还是同一道闸。"""

    def _state(self, quote="我只喝手冲", keywords=("手冲",), ts=None):
        ts = NOW - 3600 if ts is None else ts
        return {"data": {"people": [{
            "who": "阿强", "note": quote, "quote": quote,
            "keywords": list(keywords), "ts": ts,
            "evidence": {"group": G, "sender": A, "message_id": "mid-1",
                         "ts": ts, "quote": quote},
            "decision": {"kind": "preference", "action": "retain_quote",
                         "message_id": "mid-1"}}]}}

    def setUp(self):
        self._now = patch.object(time, "time", lambda: NOW)
        self._now.start()
        self.addCleanup(self._now.stop)

    def _args(self, text="今天的手冲不错"):
        return dict(group_id=G, sender=A, text=text,
                    resolver=lambda _oid: "阿强")

    def test_render_people_equals_render_matched_of_match_people(self):
        state = self._state()
        who, sources = digest_mod.match_people(state, **self._args())
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["quote"], "我只喝手冲")
        self.assertEqual(digest_mod.render_matched(who, sources),
                         digest_mod.render_people(state, **self._args()))

    def test_same_gate_on_both_exits(self):
        """话题词不命中时两个出口都必须空 —— 不许任何一侧单独放宽。"""
        state = self._state()
        args = self._args(text="完全无关的话")
        self.assertEqual(digest_mod.match_people(state, **args)[1], [])
        self.assertEqual(digest_mod.render_people(state, **args), "")

    def test_another_members_quote_is_not_offered(self):
        self.assertEqual(
            digest_mod.match_people(self._state(), group_id=G,
                                    sender="SOMEONE_ELSE",
                                    text="今天的手冲不错",
                                    resolver=lambda _o: "阿强")[1], [])

    def test_stale_quote_is_not_offered(self):
        state = self._state(ts=NOW - 400 * 86400)
        self.assertEqual(digest_mod.match_people(state, **self._args())[1], [])
        self.assertEqual(digest_mod.render_people(state, **self._args()), "")

    def test_disabled_feature_offers_nothing(self):
        with patch.object(config, "PERSONAL_MEMORY_ENABLED", False):
                self.assertEqual(
                digest_mod.match_people(self._state(), **self._args()), ("", []))


if __name__ == "__main__":
    unittest.main()
