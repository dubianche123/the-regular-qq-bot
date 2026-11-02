"""群主指代识别回归测试。

这些词用来认人名（压缩记忆、改写旧称呼时当作已知名字）。关键约束是
**不许写死具体人名** —— 机器人要开源，换个群、群主改个名都得开箱能用。
所以指代只能来自两个来源：通用词、群主自己认领的称呼。
（2026-11-02 以前还拿它抬高「聊到群主」时的插嘴概率；骰子拆了，那一层也没了。）
"""
import sys
import unittest

sys.path.insert(0, "..")
import runtime

import bot

GROUP = "TESTGROUP_OWNERMENTION"
OWNER = "OWNER_OPENID_FOR_TEST"
OTHER = "OTHER_OPENID_FOR_TEST"


class OwnerReferenceTest(unittest.TestCase):

    def setUp(self):
        self._saved_owner = runtime.OWNER_OPENID
        runtime.OWNER_OPENID = OWNER
        # 只动内存里的档案，不调 mark_dirty，免得把测试数据刷进 state.json
        runtime.RELATIONS.get(GROUP, OWNER)["nick"] = None
        runtime.RELATIONS.get(GROUP, OTHER)["nick"] = "阿强"

    def tearDown(self):
        runtime.OWNER_OPENID = self._saved_owner
        for k in [k for k in runtime.RELATIONS.records if k.startswith(GROUP)]:
            runtime.RELATIONS.records.pop(k, None)

    def test_generic_terms_always_present(self):
        """群主还没认领称呼时，靠通用词仍然认得出来。"""
        terms = bot.owner_reference_terms(GROUP)
        self.assertIn("群主", terms)
        self.assertIn("群管", terms)

    def test_claimed_nick_joins_terms(self):
        """群主说一句「我是老张」，之后「老张」就自动算指代他。"""
        self.assertNotIn("老张", bot.owner_reference_terms(GROUP))
        runtime.RELATIONS.get(GROUP, OWNER)["nick"] = "老张"
        self.assertIn("老张", bot.owner_reference_terms(GROUP))

    def test_no_owner_openid_does_not_crash(self):
        """owner 还没识别出来（启动早期）也不能炸。"""
        runtime.OWNER_OPENID = None
        self.assertEqual(bot.owner_reference_terms(GROUP), {"群主", "群管"})
