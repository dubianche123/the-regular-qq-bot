"""会话按原格式落账，给模型的历史按真实收件/送达时间展开。"""
import json
import unittest
from unittest.mock import patch

from storage import SessionStore


def contents(messages):
    return [message["content"] for message in messages]


class SessionTimelineTest(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore(max_turns=8, max_chars=3000, ttl=100)

    def record_overlap(self):
        self.store.record("g_u", "A问", "A答", received_at=1, now=3)
        self.store.record("g_u", "B问", "B答", received_at=2, now=4)

    def test_future_turn_sees_arrival_order_instead_of_commit_pairs(self):
        self.record_overlap()
        self.assertEqual(contents(self.store._sessions["g_u"]["messages"]),
                         ["A问", "A答", "B问", "B答"])
        snapshot = self.store.snapshot("g_u", now=5)
        self.assertEqual(contents(snapshot), ["A问", "B问", "A答", "B答"])
        self.assertEqual([message["role"] for message in snapshot],
                         ["user", "user", "assistant", "assistant"])
        messages = self.store.build_messages("g_u", "HEAD", "C问",
                                             memory_block="MEM", turn_context="NOW", now=5)
        self.assertEqual(contents(messages),
                         ["HEAD", "MEM", "A问", "B问", "A答", "B答", "NOW\n\nC问"])

    def test_receipt_snapshot_is_independent_of_later_delivery_and_edits(self):
        self.store.record("g_u", "旧问", "旧答", received_at=1, now=2)
        snapshot = self.store.snapshot("g_u", now=3)
        self.store.record("g_u", "A问", "A答", received_at=3, now=4)
        self.store._sessions["g_u"]["messages"][0]["content"] = "已修改"
        self.assertEqual(contents(snapshot), ["旧问", "旧答"])
        messages = self.store.build_messages("g_u", "HEAD", "B问", now=5,
                                             history_snapshot=snapshot)
        self.assertEqual(contents(messages), ["HEAD", "旧问", "旧答", "B问"])

    def test_empty_receipt_snapshot_does_not_fall_back_to_current_history(self):
        snapshot = self.store.snapshot("g_u", now=1)
        self.store.record("g_u", "A问", "A答", received_at=1, now=3)
        self.assertEqual(contents(self.store.build_messages(
            "g_u", "HEAD", "B问", now=4, history_snapshot=snapshot)), ["HEAD", "B问"])
        self.assertEqual(contents(self.store.build_messages(
            "g_u", "HEAD", "C问", now=4)), ["HEAD", "A问", "A答", "C问"])

    def test_equal_times_keep_original_order_and_positional_now_still_works(self):
        self.store.record("g_u", "A问", "A答", 3)
        self.store.record("g_u", "B问", "B答", now=3, received_at=3)
        self.assertEqual(contents(self.store.snapshot("g_u", now=4)),
                         ["A问", "A答", "B问", "B答"])
        self.assertEqual(self.store._sessions["g_u"]["message_times"], [3, 3, 3, 3])

    def test_local_metadata_never_reaches_model_messages(self):
        self.record_overlap()
        self.store._sessions["g_u"]["messages"][0]["source_message_id"] = "local-only"
        snapshot = self.store.snapshot("g_u", now=5)
        self.assertTrue(all(set(message) == {"role", "content"} for message in snapshot))
        snapshot[0]["received_at"] = 1
        messages = self.store.build_messages("g_u", "HEAD", "C问", now=5,
                                             history_snapshot=snapshot)
        self.assertTrue(all(set(message) == {"role", "content"} for message in messages))

    def test_message_count_trim_keeps_times_aligned(self):
        store = SessionStore(max_turns=2, max_chars=3000, ttl=100)
        store.record("g_u", "A问", "A答", received_at=1, now=2)
        store.record("g_u", "B问", "B答", received_at=3, now=5)
        store.record("g_u", "C问", "C答", received_at=4, now=6)
        self.assertEqual(contents(store._sessions["g_u"]["messages"]), ["C问", "C答"])
        self.assertEqual(store._sessions["g_u"]["message_times"], [4, 6])
        store.record("g_u", "D问", "D答", received_at=5, now=7)
        self.assertEqual(contents(store.snapshot("g_u", now=8)),
                         ["C问", "D问", "C答", "D答"])

    def test_character_trim_keeps_times_aligned(self):
        store = SessionStore(max_turns=8, max_chars=10, ttl=100)
        store.record("g_u", "AAAA", "aaaa", received_at=1, now=3)
        store.record("g_u", "BBBB", "bbbb", received_at=2, now=4)
        self.assertEqual(contents(store.snapshot("g_u", now=5)), ["BBBB", "bbbb"])
        self.assertEqual(store._sessions["g_u"]["message_times"], [2, 4])

    def test_dump_hydrate_preserves_timeline_and_trims_both_lists(self):
        self.record_overlap()
        self.store.record("g_u", "C问", "C答", received_at=5, now=7)
        dumped = {}
        self.store.dump_into(dumped)
        restored = SessionStore(max_turns=2, max_chars=3000, ttl=100)
        with patch("storage.time.time", return_value=8):
            self.assertEqual(restored.hydrate(json.loads(json.dumps(dumped["sessions"]))), 1)
        self.assertEqual(restored._sessions["g_u"]["message_times"], [2, 4, 5, 7])
        self.assertEqual(contents(restored.snapshot("g_u", now=8)),
                         ["B问", "B答", "C问", "C答"])

    def test_legacy_history_without_times_survives_and_new_events_are_sorted(self):
        old = [{"role": "user", "content": "旧问"},
               {"role": "assistant", "content": "旧答"}]
        with patch("storage.time.time", return_value=1):
            self.store.hydrate({"g_u": {"messages": old, "last_time": 1}})
        self.assertEqual(contents(self.store.snapshot("g_u", now=1)), ["旧问", "旧答"])
        self.record_overlap()
        self.assertEqual(self.store._sessions["g_u"]["message_times"],
                         [None, None, 1, 3, 2, 4])
        self.assertEqual(contents(self.store.snapshot("g_u", now=5)),
                         ["旧问", "旧答", "A问", "B问", "A答", "B答"])

    def test_ttl_clears_timestamps_with_messages_and_record_starts_fresh(self):
        self.record_overlap()
        self.assertEqual(self.store.snapshot("g_u", now=105), [])
        self.assertEqual(self.store._sessions["g_u"]["message_times"], [])
        self.store.record("g_u", "新问", "新答", received_at=105, now=106)
        self.assertEqual(contents(self.store.snapshot("g_u", now=107)), ["新问", "新答"])
        self.assertEqual(self.store._sessions["g_u"]["message_times"], [105, 106])

    def test_rename_and_clear_keep_existing_scope(self):
        self.store.record("g_u", "阿龙来了", "阿龙好", received_at=1, now=3)
        self.store.record("other_u", "阿龙来了", "阿龙好", received_at=1, now=3)
        self.assertEqual(self.store.rename_user("g", "阿龙", "阿远"), 2)
        self.assertEqual(contents(self.store.snapshot("g_u", now=4)), ["阿远来了", "阿远好"])
        self.assertEqual(self.store._sessions["g_u"]["message_times"], [1, 3])
        self.assertEqual(self.store.clear_group("g"), 1)
        self.assertEqual(self.store.snapshot("g_u", now=4), [])
        self.assertEqual(contents(self.store.snapshot("other_u", now=4)),
                         ["阿龙来了", "阿龙好"])
        self.assertTrue(self.store.drop_session("other_u"))
        self.assertEqual(self.store.snapshot("other_u", now=4), [])
