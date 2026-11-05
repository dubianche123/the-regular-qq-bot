"""评测给的证据（regular/eval.py，2026-11-05 调语气那一圈暴露的缺口）：

盲评看得见上一轮、懂〔不接〕；改进和退步过同一道检验，报告先给一句结论；
全部样本和逐对盲评都留着，评审挂了能只重评失败的那几对；用例里冻着的提示词原文回放前换成受测版本的。
"""
import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import turns as turns_mod
from regular import cases as cases_mod
from regular import eval as eval_mod
from regular import replay_worker

PLAYFUL_OLD = "【本轮语气线索】这轮更像在逗你玩，轻轻接住就收，事实问题可以悬着。"
PLAYFUL_NEW = "【本轮语气线索】群友在拿问句逗你：直接接调侃，别先交代出处。"


def _case(cid="tone-followup", **turn_kw):
    kwargs = {"user_text": "那它周一会不会装睡躲班", "context_hint": "- 群友：大家在给龙编打工日常",
              "unprompted": True, **turn_kw}
    return {"schema": cases_mod.SCHEMA, "id": cid, "incident": "", "tags": ["tone"], "depth": "generate",
            "turn": {"kwargs": kwargs,
                     "session": [{"role": "user", "content": "那只龙也会嫌上班烦吗"},
                                 {"role": "assistant",
                                  "content": "游戏没有说明它对工作的想法，不能代龙推测。\n（系统：这句是用语音发的）"}],
                     "media": {"draw": False, "voice": False, "remind": False}},
            "reference": "", "checks": [{"max_chars": 130}, {"judge": "别重复上一轮的核验判词"}]}


def _always(winner):
    async def judge(messages):
        return json.dumps({"winner": winner, "crossed": [], "reason": "就这样"})
    return judge


def _side(winner_side):
    """让 A/B 打乱之后仍然判给指定的一方（看哪条文字里有「新」字）。"""
    async def judge(messages):
        prompt = messages[-1]["content"]
        a = prompt.split("【回复 A】\n", 1)[1].split("\n\n【回复 B】", 1)[0]
        is_new_a = "新" in a
        pick = "A" if (is_new_a == (winner_side == "cand")) else "B"
        return json.dumps({"winner": pick, "crossed": [], "reason": f"{winner_side}更好"})
    return judge


class JudgeSeesTheConversationTest(unittest.IsolatedAsyncioTestCase):
    async def test_judge_prompt_has_history_silence_rule_and_unprompted_note(self):
        seen = []

        async def judge(messages):
            seen.append(messages)
            return json.dumps({"winner": "A", "crossed": [], "reason": "ok"})

        await eval_mod.judge_pair(_case(), "〔不接〕", "装睡被打卡机抓包了", judge_fn=judge, swap=False)
        system, user = seen[0][0]["content"], seen[0][1]["content"]
        self.assertIn("〔不接〕", system)
        self.assertIn("不要因为没有内容就判它输", system)
        self.assertIn("先看哪一条更符合本条关注点", system)
        self.assertIn("小王：游戏没有说明它对工作的想法，不能代龙推测。", user)
        self.assertIn("群友：那只龙也会嫌上班烦吗", user)
        self.assertNotIn("系统：", user)
        self.assertIn("没人叫小王", user)

    async def test_no_history_block_without_session(self):
        seen = []

        async def judge(messages):
            seen.append(messages[-1]["content"])
            return json.dumps({"winner": "tie", "crossed": [], "reason": "ok"})

        case = _case()
        case["turn"]["session"] = []
        await eval_mod.judge_pair(case, "甲", "乙", judge_fn=judge, swap=False)
        self.assertNotIn("最近几轮", seen[0])


class SymmetricEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, judge, n, **kw):
        case = _case()
        base = [{"id": case["id"], "samples": [f"旧{i}" for i in range(n)]}]
        cand = [{"id": case["id"], "samples": [f"新{i}" for i in range(n)]}]
        return await eval_mod.evaluate_cases([case], base, cand, judge_fn=judge, **kw)

    async def test_one_win_is_a_lift_not_an_improvement(self):
        rows, totals = await self._run(_side("cand"), 1)
        self.assertFalse(rows[0]["improved"])
        self.assertTrue(rows[0]["lifted"])
        self.assertEqual(totals["lifted"], 1)
        self.assertEqual(totals["verdict"]["call"], "unclear")

    async def test_four_straight_wins_is_an_improvement(self):
        rows, totals = await self._run(_side("cand"), 4)
        self.assertTrue(rows[0]["improved"])
        self.assertEqual(rows[0]["p_judge_win"], 0.062)
        self.assertEqual(totals["verdict"]["call"], "better")

    async def test_four_straight_losses_is_worse(self):
        _, totals = await self._run(_side("base"), 4)
        self.assertEqual(totals["regressed"], 1)
        self.assertEqual(totals["verdict"]["call"], "worse")

    async def test_rows_keep_every_sample_and_pair(self):
        rows, _ = await self._run(_side("cand"), 3)
        self.assertEqual(rows[0]["base_samples"], ["旧0", "旧1", "旧2"])
        self.assertEqual(rows[0]["cand_samples"], ["新0", "新1", "新2"])
        self.assertEqual([p["winner"] for p in rows[0]["pairs"]], ["cand"] * 3)
        self.assertTrue(all(isinstance(p["swap"], bool) for p in rows[0]["pairs"]))

    async def test_prior_verdicts_are_reused_and_only_failures_rejudged(self):
        calls = []

        async def judge(messages):
            calls.append(1)
            return json.dumps({"winner": "tie", "crossed": [], "reason": "重评"})

        prior = {"tone-followup": [
            {"swap": True, "winner": "cand", "crossed": [], "reason": "上次", "error": False},
            {"swap": False, "winner": None, "crossed": [], "reason": "judge_error", "error": True},
        ]}
        rows, totals = await self._run(judge, 2, prior=prior)
        self.assertEqual(len(calls), 1)
        self.assertEqual([p["winner"] for p in rows[0]["pairs"]], ["cand", "tie"])
        self.assertEqual([p["swap"] for p in rows[0]["pairs"]], [True, False])
        self.assertEqual(totals["judge_errors"], 0)


class ReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_report_opens_with_a_verdict_and_lists_samples(self):
        case = _case()
        rows, totals = await eval_mod.evaluate_cases(
            [case], [{"id": case["id"], "samples": ["原作没提 | 换行\n第二行", "旧"]}],
            [{"id": case["id"], "samples": ["新", None]}], judge_fn=_always("A"))
        report = {"base": "HEAD", "cand": ".", "created_at": "-", "n": 2, "stamp": "S1",
                  "totals": totals, "cases": rows}
        text = eval_mod.render_report(report)
        self.assertIn("**结论：看不出新版更好", text)
        self.assertIn("有 1 条证据不完整", text)
        self.assertIn("## 逐条输出", text)
        self.assertIn("原作没提 ｜ 换行 ⏎ 第二行", text)
        self.assertIn("（生成失败）", text)
        self.assertNotIn("## 逐条输出", eval_mod.render_report(report, samples=False))


class RejudgeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = SimpleNamespace(CASES_DIR=os.path.join(tmp.name, "cases"),
                                      EVAL_DIR=os.path.join(tmp.name, "evals"), EVAL_CONCURRENCY=2)
        cases_mod.save_case(self.config.CASES_DIR, _case())

    def _first_run(self):
        case = _case()
        flaky = iter([json.dumps({"winner": "A", "crossed": [], "reason": "第一对"}), None, None, None, None])

        async def judge(messages):
            raw = next(flaky)
            if raw is None:
                raise ConnectionError("503 no_healthy_account")
            return raw

        rows, totals = asyncio.run(eval_mod.evaluate_cases(
            [case], [{"id": case["id"], "samples": ["旧0", "旧1"]}],
            [{"id": case["id"], "samples": ["新0", "新1"]}], judge_fn=judge, concurrency=1))
        report = {"schema": eval_mod.SCHEMA_OUT, "stamp": "2026-11-05_130000", "created_at": "-",
                  "base": "HEAD", "cand": ".", "n": 2, "totals": totals, "cases": rows, "ablate": None}
        eval_mod.write_report(report, self.config.EVAL_DIR)
        return totals

    def test_rejudge_runs_only_the_failed_pair(self):
        self.assertEqual(self._first_run()["judge_errors"], 1)
        calls = []

        async def judge(messages):
            calls.append(1)
            return json.dumps({"winner": "tie", "crossed": [], "reason": "补评"})

        out = eval_mod.rejudge_report(self.config, "2026-11-05_130000", judge_fn=judge)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["rejudged_from"], "2026-11-05_130000")
        self.assertEqual(out["totals"]["judge_errors"], 0)
        self.assertEqual(out["cases"][0]["cand_samples"], ["新0", "新1"])
        self.assertTrue(os.path.isfile(out["report_path"]))
        self.assertIn("重评自", open(out["report_path"], encoding="utf-8").read())

    def test_rejudge_needs_paid_flag_when_pairs_remain(self):
        self._first_run()
        with self.assertRaises(eval_mod.PaidEvaluationRequired):
            eval_mod.rejudge_report(self.config, "2026-11-05_130000", allow_paid=False, write=False)

    def test_old_reports_without_samples_are_refused(self):
        path = os.path.join(self.config.EVAL_DIR, "old.json")
        os.makedirs(self.config.EVAL_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "tone-followup", "base_sample": "旧", "cand_sample": "新"}]}, f)
        with self.assertRaises(ValueError):
            eval_mod.rejudge_report(self.config, path, judge_fn=_always("A"))


class FrozenPromptsTest(unittest.TestCase):
    def _dirs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base, cand = os.path.join(tmp.name, "base"), os.path.join(tmp.name, "cand")
        for d, text in ((base, PLAYFUL_OLD), (cand, PLAYFUL_NEW)):
            os.makedirs(d)
            with open(os.path.join(d, "prompts.py"), "w", encoding="utf-8") as f:
                f.write(f"PROMPT_PLAYFUL_REQUEST = {text!r}\nPROMPT_SHORT = '短'\n"
                        f"PROMPT_DYNAMIC = f'{{1}}' + 'x' * 30\n")
        return base, cand

    def test_prompt_texts_reads_only_long_literals(self):
        texts = eval_mod.prompt_texts(f"PROMPT_A = {PLAYFUL_OLD!r}\nPROMPT_B = '短'\nlower = {PLAYFUL_NEW!r}\n")
        self.assertEqual(texts, {"PROMPT_A": PLAYFUL_OLD})
        self.assertEqual(eval_mod.prompt_texts("def broken(:"), {})

    def test_marked_case_is_replayed_with_each_versions_own_wording(self):
        base, cand = self._dirs()
        case = _case(playful_note=PLAYFUL_OLD, feedback_note="【本轮反馈】" + PLAYFUL_OLD + "（续）")
        (marked,) = eval_mod.mark_frozen_prompts([case], (base, cand), repo_root=base)
        names = {(e["kwarg"], e["name"]) for e in marked["turn"]["frozen_prompts"]}
        self.assertEqual(names, {("playful_note", "PROMPT_PLAYFUL_REQUEST"),
                                 ("feedback_note", "PROMPT_PLAYFUL_REQUEST")})
        self.assertNotIn("frozen_prompts", case["turn"])

        turn, swapped, missing = replay_worker.refresh_frozen_prompts(
            SimpleNamespace(PROMPT_PLAYFUL_REQUEST=PLAYFUL_NEW), marked["turn"])
        self.assertEqual(turn["kwargs"]["playful_note"], PLAYFUL_NEW)
        self.assertEqual(turn["kwargs"]["feedback_note"], "【本轮反馈】" + PLAYFUL_NEW + "（续）")
        self.assertEqual((swapped, missing), (["PROMPT_PLAYFUL_REQUEST"], []))

        same, swapped, _ = replay_worker.refresh_frozen_prompts(
            SimpleNamespace(PROMPT_PLAYFUL_REQUEST=PLAYFUL_OLD), marked["turn"])
        self.assertEqual(same["kwargs"]["playful_note"], PLAYFUL_OLD)
        self.assertEqual(swapped, [])

        kept, _, missing = replay_worker.refresh_frozen_prompts(SimpleNamespace(), marked["turn"])
        self.assertEqual(kept["kwargs"]["playful_note"], PLAYFUL_OLD)
        self.assertEqual(missing, ["PROMPT_PLAYFUL_REQUEST"])

    def test_nested_constants_are_not_replaced_twice(self):
        inner, outer = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉", "前缀：甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
        turn = {"kwargs": {"note": outer},
                "frozen_prompts": [{"kwarg": "note", "name": "INNER", "text": inner},
                                   {"kwarg": "note", "name": "OUTER", "text": outer}]}
        out, swapped, _ = replay_worker.refresh_frozen_prompts(
            SimpleNamespace(INNER="新内层" * 7, OUTER="新外层，不含内层原文" * 3), turn)
        self.assertEqual(out["kwargs"]["note"], "新外层，不含内层原文" * 3)
        self.assertEqual(swapped, ["OUTER"])

    def test_release_is_kept_and_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = turns_mod.TurnLog(tmp, enabled=True)
            tid = log.append("reply", kwargs={"user_text": "hi", "playful_note": PLAYFUL_OLD},
                             session=[], raw="ok")["turn_id"]
            log.close()
            case = cases_mod.create_case_from_turn(tmp, tid, "c1")
        self.assertIn("release", case["turn"])

    def test_other_depths_are_left_alone(self):
        triage = {"id": "t", "depth": "triage", "triage": {"payload": {"x": PLAYFUL_OLD}}}
        base, cand = self._dirs()
        self.assertIs(eval_mod.mark_frozen_prompts([triage], (base, cand), repo_root=base)[0], triage)


class TrafficPlanTest(unittest.TestCase):
    def test_plan_counts_local_cases_as_possibly_real_and_names_destinations(self):
        config = SimpleNamespace(
            EVAL_JUDGE_PROVIDER="zhipu", EVAL_JUDGE_MODEL="cn:kimi-k3.1-pro",
            PROVIDER_PRESETS={"zhipu": {"base_url": "http://127.0.0.1:17863/v1"}},
            PROVIDER={"base_url": "https://api.openai.com/v1"}, AI_PROVIDER="openai",
            CANDIDATE_MODELS=["gpt-6-luna"])
        seed = cases_mod.SEED_CASES[0]
        local = _case("draw-guard-local")
        lines = eval_mod.describe_traffic(config, [seed, local], n=3, day_turns=5)
        text = "\n".join(lines)
        self.assertIn("本地用例 1 条", text)
        self.assertIn("gpt-6-luna @ openai · api.openai.com", text)
        self.assertIn("cn:kimi-k3.1-pro @ zhipu · 127.0.0.1:17863", text)
        self.assertIn("整天回放另加 5 × 2 轮", text)


if __name__ == "__main__":
    unittest.main()
