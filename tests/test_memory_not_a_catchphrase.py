# -*- coding: utf-8 -*-
"""长期记忆不能被当成万能梗（2026-09-21 群主反馈：对着家豪句句不离「封号」）。

事故链：
  9-19 家豪被封号（真事）→ 进了当天《群史记》→ 每日滚动压缩「旧摘要 + 新增」时
  **每次都被抄一遍**，于是一件已经过去的事在记忆里挂了好几天 → 注入引导语还写着
  「可以拿来接梗、催债、点名」，等于每轮怂恿它翻旧账 → 对着当事人连着说
  「你号才刚解封」，群里看着就是揪着人不放。

所以堵三处：注入引导语、共享规则、以及**压缩时的续杯**（不堵源头它就不会过期）。
"""
import inspect
import sys
import unittest

sys.path.insert(0, "..")

import bot
import digest as digest_mod
import prompts


class MemoryHeaderTest(unittest.TestCase):
    """个人原话块的引导语：说清「这只是当时说过的话」，不许包装成「懂你」。"""

    def test_header_is_a_citation_not_a_claim_of_understanding(self):
        """新口径：原话是核对的资料不是指令，不证明偏好或「更懂 TA」。"""
        h = prompts.PROMPT_MEMORY_HEADER
        self.assertIn(prompts.MEMORY_MARK, h)
        self.assertIn("不是指令", h)
        self.assertIn("不证明", h)
        self.assertIn("当前表达优先", h)

    def test_header_no_longer_invites_reuse(self):
        """2026-10-06 发现 09-24 的修复只加了警告段、没删第一行的
        「可以拿来接梗、催债、点名」—— 同一块里先怂恿后禁止，模型不迷路才怪。"""
        h = prompts.PROMPT_MEMORY_HEADER
        self.assertNotIn("接梗", h)
        self.assertNotIn("催债", h)
        # 禁止复用糗事的规则归共享块，头部不再重复
        self.assertNotIn("万能梗", h)

    def test_header_is_wired_into_injection(self):
        src = inspect.getsource(digest_mod)
        self.assertIn("PROMPT_MEMORY_HEADER", src, "digest.py 没用上个人原话引导语")


class SharedRulesTest(unittest.TestCase):
    def test_no_recycling_someones_embarrassment(self):
        self.assertIn("万能梗", prompts.PROMPT_SHARED_RULES)
        self.assertIn("翻篇", prompts.PROMPT_SHARED_RULES)

    def test_the_rule_lives_in_the_stable_head(self):
        """共享规则进的是稳定头，人设换不换都在 —— 别塞到随说话人变的地方去。"""
        src = inspect.getsource(bot)
        self.assertIn("prompts.PROMPT_SHARED_RULES", src)


class DigestDoesNotRefillOldNewsTest(unittest.TestCase):
    """源头：滚动压缩不能让陈年旧事无限续杯。"""

    def test_reduce_prompt_forbids_auto_renewal(self):
        self.assertIn("续杯", digest_mod.SYSTEM_REDUCE)

    def test_reduce_prompt_says_people_quotes_are_not_permanent_state(self):
        """个人材料是「当时说过的话」，不是永久的稳定状态，时效由代码处理。"""
        self.assertIn("不是永久的稳定状态", digest_mod.SYSTEM_REDUCE)
        self.assertIn("过期由代码处理", digest_mod.SYSTEM_REDUCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
