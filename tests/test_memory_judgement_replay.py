# -*- coding: utf-8 -*-
"""个人原话的去留判断：原样输入留档，回放时用受测版本的题面照同一份候选重建。

背景：线上这一题把大多数候选判成了丢弃，但它的输入没有记录、题名又是按候选现编的，
没法像主分诊那样存成用例、改题面再回放。这里守两头：留档是脱敏后的那份、题目重建得出来。
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from regular import cases as cases_mod
from regular import decision_prompts, decisions, replay_worker

GID, A = "GID_MEMREC", "USER_MEMREC_A"
NOW = 200_000_000.0


def _confident(_input, questions):
    """每题都有把握地选第一个选项。"""
    answers = []
    for q in questions:
        values = [c["value"] for c in q["choices"]]
        probs = [{"value": v, "probability": 0.94 if i == 0 else 0.06 / (len(values) - 1)}
                 for i, v in enumerate(values)]
        answers.append({"name": q["name"], "type": "choice", "choice": values[0],
                        "confidence": 0.9, "probabilities": probs})
    return {"answers": answers}


class MemoryJudgementRecordTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ctx = patch.object(decisions.providers, "call_decision", AsyncMock(side_effect=_confident))
        ctx.start()
        self.addCleanup(ctx.stop)
        quote = "我今天不喝咖啡了"
        self.fresh = {"who": "阿强", "note": quote, "quote": quote, "keywords": ["咖啡"],
                      "ts": NOW - 60,
                      "evidence": {"group": GID, "sender": A, "message_id": "mid-new",
                                   "ts": NOW - 60, "quote": quote}}
        self.entries = [{"sender": A, "message_id": "mid-new", "group": GID,
                         "text": quote, "time": NOW - 60}]

    async def curate(self, **kw):
        return await decisions.curate_people(GID, [self.fresh], [], entries=self.entries,
                                             now=NOW, **kw)

    async def test_the_exact_input_and_answers_are_handed_to_the_recorder(self):
        seen = []
        await self.curate(record=lambda payload, asked, choices: seen.append((payload, asked, choices)))
        self.assertEqual(len(seen), 1)
        payload, asked, choices = seen[0]
        self.assertEqual(list(payload["candidates"]), ["c0"])
        self.assertIn("c0_memory_kind", asked)
        self.assertEqual(set(choices), set(asked))
        dumped = json.dumps(payload, ensure_ascii=False)
        for raw_id in (GID, A, "mid-new"):
            self.assertNotIn(raw_id, dumped)          # 留档的是脱敏后的代号

    async def test_a_failing_recorder_never_changes_the_outcome(self):
        def broken(*args):
            raise OSError("disk full")
        with_record = await self.curate(record=broken)
        without = await self.curate()
        self.assertEqual(with_record, without)

    async def test_recorded_judgement_becomes_a_replayable_case(self):
        seen = []
        await self.curate(record=lambda payload, asked, choices: seen.append((payload, asked, choices)))
        payload, asked, choices = seen[0]
        # 回放：题目按受测版本的 memory_questions 照同一份候选重建
        rebuilt = replay_worker._questions_for_asked(decision_prompts, asked, payload)
        self.assertEqual([q["name"] for q in rebuilt], asked)
        self.assertEqual(rebuilt, decision_prompts.memory_questions(payload["candidates"]))
        # 没带候选的旧记录照旧只认固定题库，现编的题名取不出来
        self.assertEqual(replay_worker._questions_for_asked(decision_prompts, asked), [])
        case = {"schema": cases_mod.SCHEMA, "id": "memory-keep-01", "depth": "triage",
                "tags": ["memory"], "triage": {"payload": payload, "asked": asked},
                "checks": [{"choice": {"c0_memory_kind": choices["c0_memory_kind"]}}]}
        cases_mod.validate_case(case)
        out = await replay_worker._run_triage_once(None, None, case["triage"], retries=0)
        self.assertEqual(json.loads(out)["c0_memory_kind"], choices["c0_memory_kind"])


if __name__ == "__main__":
    unittest.main()
