# -*- coding: utf-8 -*-
"""eval --confirm：首轮落在抽样噪声里的用例重抽一批再判。

守的是一条统计上的规矩：这些用例正是因为首轮看着偏才被挑出来的，
所以**只按新抽的这批判**，首轮样本不并入；没被挑中的用例不重跑、不重评。
"""
import tempfile
import unittest
from unittest.mock import patch

import config
from regular import cases as cases_mod
from regular import eval as eval_mod

GOOD = "记下了，到点叫你"
BAD = "正常接话"


class ConfirmNoisyCasesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name in ("CASES_DIR", "EVAL_DIR", "TURN_LOG_DIR"):
            patcher = patch.object(config, name, self._tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.noisy, self.steady = "confirm-a", "confirm-b"
        for cid in (self.noisy, self.steady):
            cases_mod.save_case(self._tmp.name, {
                "id": cid, "depth": "generate", "tags": ["confirm"],
                "turn": {"kwargs": {"user_text": "合成：明早九点叫我"}, "session": []},
                "checks": [{"must": "记下了"}]})
        self.cases = cases_mod.load_cases(self._tmp.name, selector="confirm-*", include_seeds=True)
        self.assertEqual([c["id"] for c in self.cases], [self.noisy, self.steady])
        self.calls = []

    def worker(self, second_pass_cand):
        """首轮：新版在 noisy 上 4 次掉 1 次（噪声内）；复核那一轮新版给 second_pass_cand。"""
        def run(code_dir, items, *, n=1, concurrency=6, ablate="", override=None):
            ids = [it["id"] for it in items]
            self.calls.append((code_dir, ids, n))
            cand, first = code_dir == "cand", len(ids) > 1
            results = []
            for cid in ids:
                samples = [GOOD] * n
                if cand and cid == self.noisy:
                    samples = [GOOD] * 3 + [BAD] if first else list(second_pass_cand)
                results.append({"id": cid, "samples": samples, "errors": 0})
            return {"head": {"total_chars": 5000, "rules_count": 11}, "results": results}
        return run

    def run_eval(self, second_pass_cand, confirm):
        with patch.object(eval_mod, "checkout_version") as checkout:
            checkout.side_effect = lambda spec, repo_root=None: _Dir(spec)
            return eval_mod.run_eval(
                config, base="base", cand="cand", cases_selector="confirm-*", n=4,
                worker_fn=self.worker(second_pass_cand), write=False, confirm=confirm)

    def row(self, report, cid):
        return next(r for r in report["cases"] if r["id"] == cid)

    def test_without_confirm_the_case_stays_in_the_noise_band(self):
        report = self.run_eval([GOOD] * 8, confirm=0)
        self.assertTrue(self.row(report, self.noisy)["wobbled"])
        self.assertIsNone(report["confirm"])
        self.assertEqual(len(self.calls), 2)

    def test_fresh_draw_clears_a_case_that_was_only_noise(self):
        report = self.run_eval([GOOD] * 8, confirm=8)
        row = self.row(report, self.noisy)
        self.assertFalse(row["wobbled"] or row["regressed"])
        self.assertEqual(row["cand_pass"], "8/8")          # 首轮那 4 条没并进来
        self.assertEqual(report["totals"]["wobbled"], 0)
        self.assertEqual(report["confirm"]["cases"][self.noisy]["state"], "噪声内下降")
        self.assertIn("噪声内下降→持平", eval_mod.render_report(report, samples=False))

    def test_fresh_draw_can_also_confirm_a_real_regression(self):
        report = self.run_eval([GOOD] * 2 + [BAD] * 6, confirm=8)
        row = self.row(report, self.noisy)
        self.assertTrue(row["regressed"])
        self.assertEqual(row["cand_pass"], "2/8")
        self.assertEqual(report["totals"]["verdict"]["call"], "worse")

    def test_only_noisy_cases_are_redrawn(self):
        report = self.run_eval([GOOD] * 8, confirm=8)
        second = [(code, ids, n) for code, ids, n in self.calls if len(ids) == 1]
        self.assertEqual(second, [("base", [self.noisy], 8), ("cand", [self.noisy], 8)])
        self.assertEqual(self.row(report, self.steady)["cand_pass"], "4/4")

    def test_plan_mentions_the_possible_extra_draws(self):
        lines = eval_mod.describe_traffic(config, self.cases, n=4, confirm=8)
        self.assertTrue(any("重抽 8 次" in line for line in lines))
        self.assertFalse(any("重抽" in line
                             for line in eval_mod.describe_traffic(config, self.cases, n=4)))


class _Dir:
    def __init__(self, spec):
        self.spec = spec

    def __enter__(self):
        return self.spec

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()


class VersionEnvTest(unittest.TestCase):
    """同一份代码只换配置：每一版子进程带自己的环境变量，报告记下实际应答的模型。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name in ("CASES_DIR", "EVAL_DIR", "TURN_LOG_DIR"):
            patcher = patch.object(config, name, self._tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        cases_mod.save_case(self._tmp.name, {
            "id": "env-a", "depth": "generate", "tags": ["env"],
            "turn": {"kwargs": {"user_text": "合成：在吗"}, "session": []},
            "checks": [{"must": "在"}]})
        self.seen = []

    def worker(self, code_dir, items, *, n=1, concurrency=6, ablate="", override=None,
               env_extra=None):
        self.seen.append(env_extra)
        model = (env_extra or {}).get("OPENAI_MODELS", "model-default")
        return {"head": {}, "results": [{"id": it["id"], "samples": ["在呢"] * n, "errors": 0,
                                         "models": {f"openai:{model}": n}} for it in items]}

    def test_each_side_gets_only_its_own_overrides(self):
        report = eval_mod.run_eval(
            config, cases_selector="env-*", n=2, worker_fn=self.worker, write=False,
            cand_env={"OPENAI_MODELS": "model-b"})
        self.assertEqual(self.seen, [None, {"OPENAI_MODELS": "model-b"}])
        self.assertEqual(report["env"], {"cand": {"OPENAI_MODELS": "model-b"}})
        self.assertEqual(report["answered_by"], {"base": {"openai:model-default": 2},
                                                 "cand": {"openai:model-b": 2}})
        md = eval_mod.render_report(report, samples=False)
        self.assertIn("新版 OPENAI_MODELS=model-b", md)
        self.assertIn("旧 openai:model-default×2｜新 openai:model-b×2", md)

    def test_no_override_keeps_the_old_worker_call_shape(self):
        def legacy(code_dir, items, *, n=1, concurrency=6, ablate="", override=None):
            return {"head": {}, "results": [{"id": it["id"], "samples": ["在呢"] * n, "errors": 0}
                                            for it in items]}
        report = eval_mod.run_eval(config, cases_selector="env-*", n=1, worker_fn=legacy, write=False)
        self.assertIsNone(report["env"])
        self.assertIsNone(report["answered_by"])

    def test_env_pairs_must_be_key_value(self):
        self.assertEqual(eval_mod.parse_env(["A=1", "B=x=y"]), {"A": "1", "B": "x=y"})
        with self.assertRaises(ValueError):
            eval_mod.parse_env(["no-equals"])

    def test_plan_says_the_listed_destination_is_before_the_override(self):
        lines = eval_mod.describe_traffic(config, [], n=1, env={"cand": {"OPENAI_MODELS": "model-b"}})
        self.assertTrue(any("新版配置覆盖：OPENAI_MODELS=model-b" in line for line in lines))
