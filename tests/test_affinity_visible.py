# -*- coding: utf-8 -*-
"""好感度要「看得见」：档位决定语气分寸，体现在怎么说上，不体现在嘴上说。

2026-09-21 群主反馈：好感度系统没任何体现。当时两处太软：LEVEL_HINTS 每档只有一句抽象描述，
模型基本不看，八档一个味儿；里程碑那段「自然地流露一下」也常常一句不提，于是改成每档写具体做法、
跨台阶时要求当场说出来。
2026-11-16 起后一条撤了：档位名和「要当场说出来」都不再给，前面又跟着一句「不许复述关系状态」，
是先摆出来再禁止。现在模型只看得到「跟 TA 说话的分寸」，交情只体现在语气上。
"""
import sys
import unittest

sys.path.insert(0, "..")

import relations


class LevelHintsAreActionableTest(unittest.TestCase):
    def test_every_level_has_a_concrete_instruction(self):
        """每档至少带一条具体行为（能做什么 / 不许做什么），不能只是抽象形容词。"""
        for key, hint in relations.LEVEL_HINTS.items():
            self.assertTrue(len(hint) >= 12, f"{key} 档的提示太短，模型不会当回事：{hint}")

    def test_close_levels_may_tease_openly(self):
        """熟到一定程度就该敢损敢护 —— 这是「熟」最直观的体感。"""
        for key in ("friend", "buddy"):
            self.assertIn("损", relations.LEVEL_HINTS[key])
        self.assertIn("偏心护短", relations.LEVEL_HINTS["soulmate"])

    def test_distant_levels_keep_distance(self):
        self.assertIn("别套近乎", relations.LEVEL_HINTS["distant"])
        self.assertIn("老相识", relations.LEVEL_HINTS["familiar"])

    def test_hints_are_not_all_the_same(self):
        """八档要是写出八句差不多的话，等于没分档。"""
        self.assertEqual(len(set(relations.LEVEL_HINTS.values())), len(relations.LEVEL_HINTS))


class InsultIsJudgedAsHostileTest(unittest.TestCase):
    """「小王我操你妈」连着说，好感度一分没掉（09-21）。现在由分诊那一题判，不靠词表。"""

    def test_attacks_count_jests_do_not(self):
        from regular import decision_prompts
        (q,) = decision_prompts.toward_bot_questions()
        self.assertIn("personal insults meant to hurt, harassment, crude sexual pestering", q["instructions"])
        self.assertIn("swearing in jest", q["instructions"])


class TierShowsUpAsToneTest(unittest.TestCase):
    REC = {"score": 12, "nick": "阿强", "interactions": 20, "last_seen": 0, "first_seen": 0}

    def test_note_gives_the_tone_not_the_tier(self):
        note = relations.build_relation_note(self.REC, "some-openid")
        self.assertIn("跟 TA 说话的分寸：" + relations.LEVEL_HINTS["acquaintance"], note)
        for _, _, label in relations.LEVELS:
            self.assertNotIn(label, note)
        self.assertFalse(any(c.isdigit() for c in note), note)

    def test_hints_carry_no_relationship_words_to_repeat(self):
        for key, hint in relations.LEVEL_HINTS.items():
            for word in ("不太熟", "有印象而已", "经常见到", "关系不错", "铁得很", "最亲近的人"):
                self.assertNotIn(word, hint, key)

    def test_old_pending_milestones_are_not_announced(self):
        """老档里可能还留着一条待播报的里程碑：照常读，不再要它说出来。"""
        rec = dict(self.REC, pending_milestone={"from": "familiar", "to": "acquaintance", "ts": 0})
        note = relations.build_relation_note(rec, "some-openid")
        self.assertNotIn("刚刚的变化", note)
        self.assertNotIn("当场说出来", note)
        self.assertNotIn("不许向 TA 复述", note, "没摆出关系状态，也就不用再禁止它复述")


class ToneTierOverridesPersonaTest(unittest.TestCase):
    def test_the_head_tells_model_the_tone_wins(self):
        import prompts
        self.assertIn("「称呼与分寸」是你跟 TA 说话的分寸，优先级高于人设里的默认语气", prompts.PROMPT_SHARED_RULES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
