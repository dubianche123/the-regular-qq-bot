# -*- coding: utf-8 -*-
"""对话侧的硬边界：@真人、丢角色、主动碰政治名字（2026-10-06 日志复盘）。

这三条不是审美问题，都是日志里**真实发生过**的：
  · 09-23 它在回复正文里内嵌 @罗老板 / @老王 —— 在 QQ 里那不是排版，是**真的推了提醒**，
    等于替群友决定「现在该被打扰」。
  · 09-23 被人问「你是谁创造的」，回「我是基于开源模型架构和相关训练数据打造的AI助手」
    —— 角色当场没了，下一句又换回另一种人格。
  · 09-21 被人架火上时，它主动把几个政治人物的名字接过来玩梗，还把群主一起拖了进去。
"""
import sys
import time
import unittest

sys.path.insert(0, "..")

import config
import digest
import prompts

RULES = prompts.PROMPT_OUTPUT_RULES + prompts.PROMPT_SHARED_RULES


class NoRealAtMentionTest(unittest.TestCase):
    def test_at_mention_is_forbidden(self):
        self.assertIn("不要在回复里 @ 任何人", RULES)

    def test_it_explains_at_is_not_decoration(self):
        """只写「不许 @」模型会当成排版规则；必须说明它会真的推提醒。"""
        self.assertIn("推送提醒", RULES)


class IdentityHoldsTest(unittest.TestCase):
    def test_it_may_not_switch_to_a_generic_ai(self):
        self.assertIn("你永远就是你", RULES)

    def test_the_forbidden_wording_is_named(self):
        """要具体点破那几句它真说过的，否则「保持人设」等于没说。"""
        self.assertIn("AI 助手", RULES)
        self.assertIn("开源模型", RULES)


class PoliticalNamesTest(unittest.TestCase):
    def test_no_political_names_even_in_a_joke(self):
        self.assertIn("政治相关的人名、机构名", RULES)

    def test_declining_is_the_default_move(self):
        self.assertIn("接不住的时候最省事的是不接", RULES)


class MemeFreshnessTest(unittest.TestCase):
    """「名场面」这块原先无时限无配额、每轮固定喂第一条。"""

    def _state(self, memes):
        return {"brief": "", "data": {"memes": memes}}

    def test_meme_without_timestamp_is_not_served(self):
        """没时间戳的（老 state）宁可不喂：无法证明它新鲜。"""
        out = digest.render_summary(self._state([{"quote": "老梗", "why": "x"}]))
        self.assertNotIn("名场面", out)

    def test_fresh_meme_is_served(self):
        out = digest.render_summary(self._state(
            [{"quote": "新梗", "why": "x", "ts": time.time()}]))
        self.assertIn("名场面", out)

    def test_stale_meme_is_not_served(self):
        old = time.time() - (config.MEME_NOTE_MAX_AGE_DAYS + 1) * 86400
        out = digest.render_summary(self._state([{"quote": "陈梗", "why": "x", "ts": old}]))
        self.assertNotIn("名场面", out)

    def test_copied_meme_keeps_aging_instead_of_getting_a_fresh_stamp(self):
        """模型原样抄回来的梗不许白拿新时间戳 —— 那是续杯。"""
        old = {"memes": [{"quote": "老梗", "why": "x", "ts": 500.0}]}
        new = {"memes": [{"quote": "老梗", "why": "x"}]}
        digest.stamp_memes(old, new, now=1000.0)
        self.assertEqual(new["memes"][0]["ts"], 500.0)

    def test_genuinely_new_meme_gets_a_stamp(self):
        old = {"memes": [{"quote": "老梗", "why": "x", "ts": 500.0}]}
        new = {"memes": [{"quote": "新梗", "why": "x"}]}
        digest.stamp_memes(old, new, now=1000.0)
        self.assertEqual(new["memes"][0]["ts"], 1000.0)

    def test_meme_copied_from_a_timestamp_less_archive_keeps_no_stamp(self):
        """老 state 里的梗（没 ts）被抄回来时，仍然没有 ts ⇒ 不会被当成新鲜货。"""
        old = {"memes": [{"quote": "老梗", "why": "x"}]}
        new = {"memes": [{"quote": "老梗", "why": "x"}]}
        digest.stamp_memes(old, new, now=1000.0)
        self.assertFalse(new["memes"][0].get("ts"))

    def test_reduce_forbids_memes_about_the_bot_itself(self):
        """「你死了十二天」就是被当成好素材收进去的。"""
        self.assertIn("针对", digest.SYSTEM_REDUCE)


class PeopleQuoteFallbackTest(unittest.TestCase):
    """称呼由独立的本地记录提供，个人材料不足时正常接话；不靠过期档案填空。"""

    RESOLVER = staticmethod(
        lambda uid: {"user-alice": "阿青"}.get(uid))

    def _row(self, ts=1000.0):
        quote = "喝咖啡时我不要糖，喜欢黑咖啡"
        return {"who": "阿青", "note": quote, "quote": quote,
                "keywords": ["咖啡"], "ts": ts,
                "evidence": {"group": "test-group", "sender": "user-alice",
                             "message_id": "message-one", "ts": ts, "quote": quote}}

    def test_all_stale_renders_nothing(self):
        now = 10 ** 9
        stale = now - (config.PEOPLE_NOTE_MAX_AGE_DAYS + 5) * 86400
        state = {"data": {"people": [self._row(ts=stale)]}}
        out = digest.render_people(state, group_id="test-group",
                                   sender="user-alice", text="咖啡",
                                   resolver=self.RESOLVER, now=now)
        self.assertEqual(out, "")

    def test_source_timestamp_beats_compression_time(self):
        """时效看原始发言时间：复述/重压都不续杯。"""
        entry = {"group": "test-group", "sender": "user-alice",
                 "message_id": "message-one", "text": "喝咖啡时我不要糖，喜欢黑咖啡",
                 "text_complete": True, "ts": 1000.0}
        new = {"people": [{"who": "阿青", "note": "x", "quote": entry["text"],
                           "keywords": ["咖啡"]}]}
        digest.stamp_people(None, new, now=99999.0, entries=[entry],
                            resolver=self.RESOLVER, group_id="test-group")
        self.assertEqual(new["people"][0]["ts"], 1000.0)

    def test_rephrasing_an_old_quote_never_mints_a_new_source(self):
        """旧行没有来源就永远不会有来源 —— 换个措辞也不能造出证据。"""
        old = {"people": [{"who": "阿青", "note": "喝咖啡时我不要糖，喜欢黑咖啡",
                           "ts": 500.0}]}
        new = {"people": [{"who": "阿青", "note": "x",
                           "quote": "喝咖啡时我不要糖，喜欢黑咖啡",
                           "keywords": ["咖啡"]}]}
        digest.stamp_people(old, new, now=1000.0, entries=[],
                            resolver=self.RESOLVER, group_id="test-group")
        self.assertEqual(new["people"], [])


class GroupBufferTtlTest(unittest.TestCase):
    def test_buffer_window_is_bounded(self):
        """buffer 会落盘跨重启，安静半天后的第一条消息不该配上半天前的话。"""
        self.assertLessEqual(config.GROUP_BUFFER_TTL_SECONDS, 3600.0)
        self.assertGreaterEqual(config.GROUP_BUFFER_TTL_SECONDS, 600.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
