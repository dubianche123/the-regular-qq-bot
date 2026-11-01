# -*- coding: utf-8 -*-
"""个人原话渲染（render_people）+ 单会话清除的回归测试。

渲染口径（全部满足才输出，否则空串）：
  · 当前群、当前说话人本人（来源里的 sender，不是显示名）；
  · 来源时间仍新鲜（PEOPLE_NOTE_MAX_AGE_DAYS 以内）；
  · 至少一个话题词命中本轮输入；
  · 最多两条、新的在前；
  · 敏感词、机器编号、来源标识（群号/openid/消息 ID）一律不出现。

没有全群名册兜底、没有过期兜底、没有按名字猜人 —— 这是「准入与相关性」闸，
不证明真人聊天因此更好。
"""
import sys
import time
import unittest

sys.path.insert(0, "..")

import config
import digest as digest_mod
import storage

GROUP = "test-group"
ALICE = "user-alice"
BOB = "user-bob"
MSG_ONE = "message-one"
QUOTE = "喝咖啡时我不要糖，喜欢黑咖啡"
KEYWORDS = ["咖啡"]
GUESSED_NOTE = "推测性格不可入库"

resolver = lambda uid: {ALICE: "阿青", BOB: "小白"}.get(uid)


def _row(who="阿青", quote=QUOTE, keywords=None, ts=1000.0,
         group=GROUP, sender=ALICE, message_id=MSG_ONE, note=None):
    return {"who": who, "note": QUOTE if note is None else note,
            "quote": quote, "keywords": list(KEYWORDS if keywords is None else keywords),
            "evidence": {"group": group, "sender": sender,
                         "message_id": message_id, "ts": ts, "quote": quote},
            "ts": ts}


def _state(people):
    return {"data": {"people": people}}


def _render(state, **kw):
    args = dict(group_id=GROUP, sender=ALICE, text="咖啡喝什么",
                resolver=resolver, now=1100.0)
    args.update(kw)
    return digest_mod.render_people(state, **args)


class RenderPeopleTest(unittest.TestCase):
    STATE = _state([_row()])

    def test_current_speaker_and_topic_renders_the_quote(self):
        out = _render(self.STATE)
        self.assertIn("阿青", out)
        self.assertIn("说过", out)
        self.assertIn(f'"{QUOTE}"', out)

    def test_never_renders_the_guessed_note_or_source_ids(self):
        state = _state([_row(note=GUESSED_NOTE)])
        rendered = _render(state)
        self.assertNotIn("推测性格", rendered)
        for identifier in (GROUP, ALICE, MSG_ONE):
            self.assertNotIn(identifier, rendered)

    def test_missing_identity_or_query_returns_empty(self):
        self.assertEqual(_render(self.STATE, sender=""), "")
        self.assertEqual(_render(self.STATE, sender=None), "")
        self.assertEqual(_render(self.STATE, text=""), "")
        self.assertEqual(_render(self.STATE, resolver=None), "")
        self.assertEqual(_render(self.STATE, group_id=""), "")

    def test_only_the_current_speakers_quotes(self):
        """同一句原话在档案里，但说话人不是当前这位 —— 不给。"""
        self.assertEqual(
            digest_mod.render_people(
                self.STATE, group_id=GROUP, sender=BOB,
                text="咖啡喝什么", resolver=resolver, now=1100), "")
        # 别群存的同一条也不串群
        self.assertEqual(_render(self.STATE, group_id="other-group"), "")

    def test_unrelated_input_gets_nothing(self):
        self.assertEqual(
            digest_mod.render_people(
                self.STATE, group_id=GROUP, sender=ALICE,
                text="今天天气怎么样", resolver=resolver, now=1100), "")
        # 只叫名字、没有话题命中，也不算相关
        self.assertEqual(_render(self.STATE, text="阿青你好呀"), "")

    def test_stale_and_timestamp_less_rows_render_nothing(self):
        old_ts = 1100.0 - (config.PEOPLE_NOTE_MAX_AGE_DAYS + 1) * 86400.0
        if old_ts <= 0:
            old_ts = 1.0
            now = 1.0 + (config.PEOPLE_NOTE_MAX_AGE_DAYS + 1) * 86400.0
        else:
            now = 1100.0
        self.assertEqual(_render(_state([_row(ts=old_ts)]), now=now), "")
        # 没时间的旧档：无法证明新鲜 → 不喂
        row = _row()
        row["ts"] = 0
        row["evidence"]["ts"] = 0
        self.assertEqual(_render(_state([row])), "")
        # 老格式的 note 行：没有来源，永远不渲染
        self.assertEqual(_render(_state([{"who": "阿青", "note": QUOTE, "ts": 1000.0}])), "")

    def test_sensitive_query_quote_or_name_is_dropped(self):
        self.assertEqual(_render(self.STATE, text="主席这个怎么说"), "")
        self.assertEqual(_render(_state([_row(quote="主席这个词我喜欢")])), "")
        self.assertEqual(_render(self.STATE,
                                 resolver=lambda uid: "主席" if uid == ALICE else None),
                         "")
        self.assertEqual(_render(self.STATE,
                                 resolver=lambda uid: "ABCDEF0123456789"), "")
        # resolver 退回 openid / 通用词时也不算有名字
        self.assertEqual(_render(self.STATE, resolver=lambda uid: uid), "")
        self.assertEqual(_render(self.STATE,
                                 resolver=lambda uid: digest_mod.UNAMED_WHO), "")

    def test_quote_identical_to_this_turns_text_is_skipped(self):
        self.assertEqual(_render(self.STATE, text=QUOTE), "")

    def test_limit_zero_or_negative_is_empty(self):
        self.assertEqual(_render(self.STATE, limit=0), "")
        self.assertEqual(_render(self.STATE, limit=-1), "")

    def test_disabled_flag_returns_empty(self):
        orig = config.PERSONAL_MEMORY_ENABLED
        config.PERSONAL_MEMORY_ENABLED = False
        try:
            self.assertEqual(_render(self.STATE), "")
        finally:
            config.PERSONAL_MEMORY_ENABLED = orig

    def test_max_two_newest_first_and_deduped_by_message(self):
        rows = [
            _row(message_id="m1", ts=1000.0),
            _row(message_id="m2", ts=900.0),
            _row(message_id="m3", ts=1050.0),
            _row(message_id="m3", ts=1050.0),   # 同一条来源出现两次只算一次
        ]
        out = _render(_state(rows), now=1100.0)
        self.assertEqual(out.count("\n- "), 2, "最多两条")
        # 新的在前（m3 的 1050 先于 m1 的 1000）
        import re as _re
        times = _re.findall(r"（(\d\d-\d\d \d\d:\d\d)）", out)
        self.assertEqual(times[0], time.strftime("%m-%d %H:%M", time.localtime(1050.0)))

    def test_renamed_display_name_resolves_the_same_stable_id(self):
        """本人改了名：来源认的是 sender，渲染时叫当前名字。"""
        renamed = lambda uid: "阿澈" if uid == ALICE else None
        out = _render(self.STATE, resolver=renamed)
        self.assertIn("阿澈", out)
        self.assertIn(f'"{QUOTE}"', out)

    def test_a_recycled_nickname_does_not_transfer_evidence(self):
        """旧名字被另一个人拿去用了：来源还是原来那个 sender，绝不转移。"""
        recycled = lambda uid: {"user-bob": "阿青"}.get(uid)
        self.assertEqual(
            digest_mod.render_people(
                self.STATE, group_id=GROUP, sender=BOB,
                text="咖啡喝什么", resolver=recycled, now=1100), "")


class PeopleBlockReachesTheModelTest(unittest.TestCase):
    """注入接线：render_people 必须被 bot 接到本轮动态层。"""

    def test_injection_wiring_calls_render_people(self):
        import inspect
        import bot
        src = inspect.getsource(bot)
        self.assertIn("render_people", src, "bot.py 没有接个人原话渲染")

    def test_render_needs_a_resolver_and_identity(self):
        """没人能叫出名字时一条都不喂 —— 归属不清的材料不算个人材料。"""
        self.assertEqual(digest_mod.render_people(
            _state([_row()]), group_id=GROUP, sender=ALICE,
            text="咖啡", resolver=None), "")


class DropSessionTest(unittest.TestCase):
    def setUp(self):
        self.store = storage.SessionStore(max_turns=4, max_chars=2000, ttl=3600)

    def test_drops_exactly_one_session(self):
        self.store.record("g1_a", "hi", "ho")
        self.store.record("g1_b", "hi", "ho")
        self.assertTrue(self.store.drop_session("g1_a"))
        self.assertNotIn("g1_a", self.store._sessions)
        self.assertIn("g1_b", self.store._sessions, "别的会话不许被连坐")

    def test_missing_session_returns_false(self):
        self.assertFalse(self.store.drop_session("nope"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
