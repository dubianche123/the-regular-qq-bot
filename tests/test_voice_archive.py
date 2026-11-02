"""Synthetic voice evidence and buffer regressions; no audio/network/QQ."""
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import digest
import recall
from archive import MessageArchive, day_key
from storage import StateStore


class FakeState:
    def __init__(self):
        self.data = {}
        self.dirty = 0

    def mark_dirty(self):
        self.dirty += 1


class VoiceArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = MessageArchive(self.tmp.name, keep_days=0)
        self.addCleanup(self.a.close)
        self.now = time.time()

    def add_voice(self, group="g", sender="speaker", mid="v1", text="[语音]", **kwargs):
        return self.a.append(group, sender, text, message_id=mid,
                             ts=kwargs.pop("ts", self.now - 5), **kwargs)

    def test_source_must_be_matching_voice_message(self):
        self.add_voice()
        self.a.append("g", "speaker", "ordinary text", message_id="t1")
        for group, mid, sender in (("g", "missing", "speaker"),
                                   ("other", "v1", "speaker"),
                                   ("g", "v1", "wrong"),
                                   ("g", "v1", None),
                                   ("g", "t1", "speaker")):
            with self.subTest(group=group, mid=mid, sender=sender):
                self.assertIsNone(self.a.append_voice_transcript(group, mid, "hello", sender=sender))
        self.assertEqual(self.a.stats()["lines"], 2)

    def test_derived_row_cannot_become_an_original_source(self):
        self.a._write_row({"ts": self.now, "day": day_key(self.now), "group": "g",
                           "sender": "speaker", "kind": "voice_transcript", "message_id": "v1",
                           "text": "[语音]"})
        self.assertIsNone(self.a.append_voice_transcript("g", "v1", "hello", sender="speaker"))

    def test_late_pending_and_search_survive_restart_and_deduplicate(self):
        source = self.add_voice()
        _, cursor = self.a.pending("g")
        with open(self.a._file_for(source["day"]), encoding="utf-8") as f:
            original = f.readline()
        row = self.a.append_voice_transcript("g", "v1", "下次记得买冰淇淋", sender="speaker", ts=self.now)
        self.assertEqual(row["kind"], "voice_transcript")
        self.assertEqual(row["message_id"], "v1")
        self.assertEqual(row["source_ts"], source["ts"])
        self.assertEqual(row["text"], "[语音转写：下次记得买冰淇淋]")
        self.assertEqual(self.a.append_voice_transcript("g", "v1", "下次记得买冰淇淋", sender="speaker"), row)
        self.assertEqual(self.a.append_voice_transcript("g", "v1", "另一份识别结果", sender="speaker"), row)
        self.assertEqual(self.a.stats()["lines"], 2)
        late, next_cursor = self.a.pending("g", cursor=cursor)
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["kind"], "voice_transcript")
        self.assertIn("冰淇淋", digest.render_transcript(late))
        self.assertEqual(self.a.search("g", keyword="冰淇淋")[0]["message_id"], "v1")
        self.assertIn("冰淇淋", recall.build_note("g", "之前说的冰淇淋", archive=self.a,
                                                name_of=lambda _: "群友", now=self.now + 1))
        with open(self.a._file_for(source["day"]), encoding="utf-8") as f:
            self.assertEqual(f.readline(), original)
        self.a.close()
        restarted = MessageArchive(self.tmp.name, keep_days=0)
        try:
            self.assertEqual(restarted.append_voice_transcript("g", "v1", "不同结果", sender="speaker"), row)
            self.assertEqual(len(restarted.pending("g", cursor=cursor)[0]), 1)
            self.assertEqual(restarted.pending("g", cursor=next_cursor)[0], [])
            self.assertEqual(restarted.stats()["lines"], 2)
        finally:
            restarted.close()

    def test_sensitive_urls_and_binary_rejected_before_clip(self):
        self.a.max_text = 4
        self.add_voice()
        sensitive = "正常内容很长" + "BADWORD"
        cases = (sensitive, "看https://example.invalid/a", "看 file://example.invalid/a", "看 //example.invalid/a",
                 "看www.example.invalid", "data:audio/wav;base64,abc", b"RIFF\\x00", "", "a\x00b")
        with patch("wordfilter.has_hit", side_effect=lambda text: "BADWORD" in text):
            for value in cases:
                with self.subTest(value_type=type(value).__name__):
                    self.assertIsNone(self.a.append_voice_transcript("g", "v1", value, sender="speaker"))
        self.assertEqual(self.a.stats()["lines"], 1)

    def test_command_flag_preserved(self):
        self.add_voice(cmd=True)
        _, cursor = self.a.pending("g")
        row = self.a.append_voice_transcript("g", "v1", "普通的假命令文字", sender="speaker", ts=self.now)
        self.assertEqual(row["cmd"], 1)
        self.assertEqual(self.a.pending("g", cursor=cursor)[0], [])
        self.assertEqual(len(self.a.pending("g", cursor=cursor, include_cmd=True)[0]), 1)

    def test_voice_command_marked_before_clip_and_excluded_from_digest_and_recall(self):
        source = self.add_voice()
        with open(self.a._file_for(source["day"]), encoding="utf-8") as f:
            original = f.readline()
        # The command is beyond the persisted text limit: classification must
        # use the full recognized utterance, not just its archived prefix.
        transcript = "普通语音" * 150 + "锁起名"
        row = self.a.append_voice_transcript("g", "v1", transcript, sender="speaker", at=True)
        self.assertEqual(row["cmd"], 1)
        self.assertEqual(row["at"], 1)
        self.assertNotIn("锁起名", row["text"])
        default, _ = self.a.pending("g")
        self.assertEqual(len(default), 1)
        explicit, _ = self.a.pending("g", include_cmd=True)
        self.assertEqual(len(explicit), 2)
        self.assertNotIn("普通语音", digest.render_transcript(explicit))
        self.assertEqual(self.a.search("g", keyword="普通语音"), [])
        self.assertEqual(recall._score(row, {"普通语音"}, (), self.now), 0)
        with open(self.a._file_for(source["day"]), encoding="utf-8") as f:
            self.assertEqual(f.readline(), original)

    def test_derived_at_preserves_event_address_and_explicit_voice_address(self):
        self.add_voice(mid="direct", at=True)
        self.add_voice(mid="spoken")
        self.add_voice(mid="ordinary")
        self.assertEqual(self.a.append_voice_transcript("g", "direct", "今天天气", sender="speaker")["at"], 1)
        self.assertEqual(self.a.append_voice_transcript("g", "spoken", "小王今天天气", sender="speaker", at=True)["at"], 1)
        self.assertNotIn("at", self.a.append_voice_transcript("g", "ordinary", "今天天气", sender="speaker"))

    def test_backfill_only_same_group_sender_source_and_persist_state(self):
        self.add_voice(text="请听 [语音]")
        self.add_voice(sender="other", mid="v2")
        entries = [{"sender": "speaker", "message_id": "v1", "text": "请听 [语音]"},
                   {"sender": "other", "message_id": "v1", "text": "[语音]"},
                   {"sender": "other", "message_id": "v2", "text": "[语音]"}]
        buffers = {"g": entries, "other": [{"sender": "speaker", "message_id": "v1", "text": "[语音]"}]}
        state = FakeState()
        row = self.a.attach_voice_transcript("g", "v1", "一起去公园", sender="speaker",
                                             buffers=buffers, state=state, ts=self.now)
        self.assertEqual(entries[0]["text"], "请听 [语音转写：一起去公园]")
        self.assertEqual(entries[1]["text"], "[语音]")
        self.assertEqual(entries[2]["text"], "[语音]")
        self.assertEqual(buffers["other"][0]["text"], "[语音]")
        self.assertEqual(state.data["buffers"]["g"][0]["text"], entries[0]["text"])
        self.assertEqual(state.dirty, 1)
        self.assertEqual(self.a.attach_voice_transcript("g", "v1", "不同文本", sender="speaker",
                                                       buffers=buffers, state=state), row)
        self.assertEqual(state.dirty, 1)
        self.assertEqual(self.a.stats()["lines"], 3)

    def test_failed_filter_never_updates_buffer_or_state(self):
        self.add_voice()
        buffers = {"g": [{"sender": "speaker", "message_id": "v1", "text": "[语音]"}]}
        state = FakeState()
        with patch("wordfilter.has_hit", return_value=True):
            result = self.a.attach_voice_transcript("g", "v1", "sensitive", sender="speaker",
                                                    buffers=buffers, state=state)
        self.assertIsNone(result)
        self.assertEqual(buffers["g"][0]["text"], "[语音]")
        self.assertEqual(state.dirty, 0)
        self.assertEqual(self.a.stats()["lines"], 1)

    def test_buffer_round_trips_through_real_state_after_restart(self):
        self.add_voice()
        buffers = {"g": [{"sender": "speaker", "message_id": "v1", "text": "[语音]"}]}
        state_file = self.tmp.name + "/state.json"
        state = StateStore(state_file)
        self.a.attach_voice_transcript("g", "v1", "我们明天去公园", sender="speaker",
                                       buffers=buffers, state=state, ts=self.now)
        self.assertTrue(state.save_now())
        restored = StateStore(state_file)
        self.assertEqual(restored.data["buffers"]["g"][0]["text"], "[语音转写：我们明天去公园]")

    def test_voice_arriving_on_later_day_is_new_pending_evidence(self):
        self.add_voice(ts=self.now - 2 * 86400)
        _, cursor = self.a.pending("g")
        row = self.a.append_voice_transcript("g", "v1", "新的语音转写", sender="speaker", ts=self.now)
        self.assertNotEqual(row["day"], day_key(self.now - 2 * 86400))
        pending, _ = self.a.pending("g", cursor=cursor)
        self.assertEqual([e["text"] for e in pending], ["[语音转写：新的语音转写]"])


if __name__ == "__main__":
    unittest.main()
