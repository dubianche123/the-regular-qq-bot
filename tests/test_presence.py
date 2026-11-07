"""桌宠读到的 agent 状态：只读、粗粒度，不带任何聊天内容或群/人标识。"""
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import activation
from archive import day_key
from regular import presence

SECRET = "群里原话-SECRET-不该出现"


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "interactions").mkdir()
        self.cfg = SimpleNamespace(STATUS_FILE=str(root / "status.json"), STATE_FILE=str(root / "state.json"),
                                   INTERACTION_LOG_DIR=str(root / "interactions"))
        self.now = time.time()
        self.status(self.now - 5)

    def status(self, updated, lifecycle="ready", **changes):
        status = {"schema_version": 1, "updated_at": updated, "pid": os.getpid(), "lifecycle": lifecycle,
                  "gateway_connected": True, "paths": {"state": "/private/" + SECRET}}
        status.update(changes)
        Path(self.cfg.STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")

    def rows(self, *rows, day=None):
        path = Path(self.cfg.INTERACTION_LOG_DIR) / f"{day or day_key(self.now)}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                base = {"schema": "regular.interaction.v1", "event_id": "e" + SECRET, "group": "G-" + SECRET,
                        "user": "U-" + SECRET, "text": SECRET, "message_id": SECRET}
                handle.write(json.dumps({**base, **row}, ensure_ascii=False) + "\n")

    def test_events_are_coarse_kinds_without_any_content_or_identifiers(self):
        n = self.now
        self.rows({"kind": "incoming", "ts": n - 50, "at": True, "cmd": False},
                  {"kind": "incoming", "ts": n - 49, "at": False},
                  {"kind": "incoming", "ts": n - 48, "at": True, "cmd": True},
                  {"kind": "decision", "ts": n - 47, "selected": False, "route": "banter"},
                  {"kind": "decision", "ts": n - 46, "selected": True, "route": "direct"},
                  {"kind": "delivery", "ts": n - 40, "status": "delivered", "route": "reply"},
                  {"kind": "delivery", "ts": n - 30, "status": "failed", "route": "reply"},
                  {"kind": "delivery", "ts": n - 20, "status": "delivered", "route": "image"},
                  {"kind": "delivery", "ts": n - 10, "status": "delivered", "route": "proactive"},
                  {"kind": "natural_feedback", "ts": n - 5})
        snap = presence.snapshot(self.cfg, since=0, now=n)
        self.assertEqual([e["kind"] for e in snap["events"]], ["called", "replying", "said", "drew", "proactive"])
        self.assertEqual(snap["chatter"], 3)
        self.assertEqual(snap["bot"], "ready")
        self.assertTrue(snap["gateway_connected"])
        dumped = json.dumps(snap, ensure_ascii=False)
        self.assertNotIn("SECRET", dumped)
        for event in snap["events"]:
            self.assertEqual(set(event), {"kind", "ts"})

    def test_no_local_records_does_not_claim_the_group_has_no_conversation(self):
        summary = presence.day_summary(self.cfg, now=self.now)
        text = presence.render_day_summary(summary)
        self.assertIn('本机还没有收到群消息记录', text)
        self.assertNotIn('群里还没有消息', text)

    def test_drawing_and_recording_show_while_they_happen_and_a_voice_is_not_plain_text(self):
        n = self.now
        self.rows({"kind": "media", "ts": n - 60, "medium": "image", "status": "started"},
                  {"kind": "delivery", "ts": n - 59, "status": "delivered", "route": "reply"},
                  {"kind": "delivery", "ts": n - 30, "status": "delivered", "route": "image"},
                  {"kind": "media", "ts": n - 25, "medium": "animation", "status": "started"},
                  {"kind": "media", "ts": n - 24, "medium": "animation", "status": "failed"},
                  {"kind": "media", "ts": n - 20, "medium": "voice", "status": "started"},
                  {"kind": "delivery", "ts": n - 12, "status": "delivered", "route": "reply", "medium": "voice"},
                  {"kind": "media", "ts": n - 8, "medium": "voice", "status": "started"},
                  {"kind": "media", "ts": n - 6, "medium": "voice", "status": "unconfirmed"},
                  {"kind": "media", "ts": n - 4, "medium": "hologram", "status": "started"})
        kinds = [e["kind"] for e in presence.snapshot(self.cfg, since=0, now=n)["events"]]
        self.assertEqual(kinds, ["drawing", "said", "drew", "drawing", "media_end",
                                 "singing", "voice", "singing", "media_end"])

    def test_an_at_a_name_and_a_quote_are_told_apart(self):
        n = self.now
        self.rows({"kind": "incoming", "ts": n - 40, "at": True, "call": "at"},
                  {"kind": "incoming", "ts": n - 30, "at": True, "call": "name"},
                  {"kind": "incoming", "ts": n - 20, "at": True, "call": "quote"},
                  {"kind": "incoming", "ts": n - 10, "at": True})              # an older bot: just "called"
        kinds = [e["kind"] for e in presence.snapshot(self.cfg, since=0, now=n)["events"]]
        self.assertEqual(kinds, ["mention", "named", "quoted", "called"])

    def test_searching_and_chiming_in_are_told_apart_from_a_plain_reply(self):
        n = self.now
        self.rows({"kind": "delivery", "ts": n - 30, "status": "delivered", "route": "reply",
                   "generation": "local_fallback"},
                  {"kind": "lookup", "ts": n - 29, "job": "search", "status": "background"},
                  {"kind": "delivery", "ts": n - 20, "status": "delivered", "route": "reply"},
                  {"kind": "delivery", "ts": n - 10, "status": "delivered", "route": "banter"})
        kinds = [e["kind"] for e in presence.snapshot(self.cfg, since=0, now=n)["events"]]
        self.assertEqual(kinds, ["said", "searching", "said", "chimed"])

    def test_since_and_window_limit_what_is_returned(self):
        n = self.now
        self.rows({"kind": "delivery", "ts": n - 900, "status": "delivered", "route": "reply"},
                  {"kind": "delivery", "ts": n - 100, "status": "delivered", "route": "reply"},
                  {"kind": "delivery", "ts": n - 10, "status": "delivered", "route": "image"})
        self.assertEqual(len(presence.snapshot(self.cfg, since=0, now=n)["events"]), 2)
        latest = presence.snapshot(self.cfg, since=n - 50, now=n)["events"]
        self.assertEqual([e["kind"] for e in latest], ["drew"])
        default = presence.snapshot(self.cfg, now=n)["events"]
        self.assertEqual([e["kind"] for e in default], ["drew"])

    def test_stale_or_stopped_bot_reports_offline_and_no_events(self):
        self.rows({"kind": "incoming", "ts": self.now - 5, "at": True})
        for updated, lifecycle in ((self.now - 600, "ready"), (self.now - 5, "stopped"), (float("nan"), "ready")):
            with self.subTest(updated=updated, lifecycle=lifecycle):
                self.status(updated, lifecycle)
                snap = presence.snapshot(self.cfg, since=0, now=self.now)
                self.assertEqual(snap["bot"], "offline")
                self.assertEqual(snap["events"], [])
        Path(self.cfg.STATUS_FILE).unlink()
        self.assertEqual(presence.snapshot(self.cfg, now=self.now)["bot"], "offline")

    def test_a_fresh_status_requires_a_live_process(self):
        for pid in (None, 0, -1, True, "4242"):
            with self.subTest(pid=pid):
                self.status(self.now - 1, pid=pid)
                snap = presence.snapshot(self.cfg, now=self.now)
                self.assertEqual(snap["bot"], "offline")
                self.assertFalse(snap["gateway_connected"])
        self.status(self.now - 1)
        snap = presence.snapshot(self.cfg, now=self.now)
        self.assertEqual(snap["bot"], "ready")
        self.assertTrue(snap["gateway_connected"])

    def test_fresh_snapshot_of_an_exited_process_does_not_replay_activity(self):
        self.status(self.now - 1, pid=4242)
        self.rows({"kind": "incoming", "ts": self.now - 3, "at": True})
        with patch.object(presence, "_pid_alive", return_value=False) as alive:
            snap = presence.snapshot(self.cfg, since=0, now=self.now)
        alive.assert_called_once_with(4242)
        self.assertEqual(snap["bot"], "offline")
        self.assertFalse(snap["gateway_connected"])
        self.assertEqual(snap["events"], [])
        self.assertEqual(snap["chatter"], 0)

    def test_unsupported_status_schema_is_not_reported_as_online(self):
        for version in (None, 0, 2, "1"):
            with self.subTest(version=version):
                self.status(self.now - 1, schema_version=version)
                with patch.object(presence, "_pid_alive") as alive:
                    snap = presence.snapshot(self.cfg, now=self.now)
                alive.assert_not_called()
                self.assertEqual(snap["bot"], "offline")
                self.assertFalse(snap["gateway_connected"])

    def test_starting_or_unknown_lifecycle_does_not_claim_readiness(self):
        for lifecycle in ("starting", "unknown", None):
            with self.subTest(lifecycle=lifecycle):
                self.status(self.now - 1, lifecycle)
                snap = presence.snapshot(self.cfg, now=self.now)
                self.assertEqual(snap["bot"], "offline")
                self.assertFalse(snap["gateway_connected"])

    def test_large_log_reads_only_the_tail_and_tolerates_a_cut_line(self):
        n = self.now
        filler = [{"kind": "incoming", "ts": n - 3000, "at": False, "pad": "x" * 400}] * 600
        self.rows(*filler)
        self.rows({"kind": "delivery", "ts": n - 3, "status": "delivered", "route": "reply"})
        path = Path(self.cfg.INTERACTION_LOG_DIR) / f"{day_key(n)}.jsonl"
        self.assertGreater(path.stat().st_size, presence.TAIL_BYTES)
        with path.open("a") as handle:
            handle.write('{"kind": "delivery", "ts": ')       # a write in progress
        self.assertEqual([e["kind"] for e in presence.snapshot(self.cfg, since=0, now=n)["events"]], ["said"])

    def test_mood_uses_the_bots_persisted_value_with_the_same_half_life(self):
        Path(self.cfg.STATE_FILE).write_text(json.dumps({"mood": {"v": 0.8, "ts": self.now - 6 * 3600}}))
        snap = presence.snapshot(self.cfg, now=self.now)
        self.assertAlmostEqual(snap["mood"], 0.4, places=2)
        self.assertEqual(activation.mood_from_state({"v": "bad"}), 0.0)
        self.assertEqual(activation.mood_from_state(None), 0.0)
        Path(self.cfg.STATE_FILE).write_text("not json")
        self.assertEqual(presence.snapshot(self.cfg, now=self.now)["mood"], 0.0)

    def test_cues_are_merged_after_since(self):
        cues = [{"kind": "cue", "id": "a", "ts": self.now - 30, "action": "cheer", "text": "跑完了"},
                {"kind": "cue", "id": "b", "ts": self.now - 1, "action": "", "text": "再来一次"}]
        snap = presence.snapshot(self.cfg, since=self.now - 10, now=self.now, cues=cues)
        self.assertEqual([e["id"] for e in snap["events"]], ["b"])

    def test_perception_is_content_free_and_older_completion_keeps_other_work_active(self):
        n = self.now
        self.rows({"kind": "perception", "event_id": "image-a", "ts": n - 80,
                   "medium": "image", "status": "started"},
                  {"kind": "perception", "event_id": "image-b", "ts": n - 60,
                   "medium": "image", "status": "started"},
                  {"kind": "perception", "event_id": "voice-a", "ts": n - 40,
                   "medium": "voice", "status": "started"},
                  {"kind": "perception", "activity": "image-a", "ts": n - 35,
                   "medium": "image", "status": "finished"})
        snap = presence.snapshot(self.cfg, since=0, now=n)
        self.assertEqual(snap["state"], "listen")
        self.assertEqual([event["kind"] for event in snap["events"]],
                         ["inspecting", "inspecting", "listening"])
        self.assertNotIn(SECRET, json.dumps(snap))
        for event in snap["events"]:
            self.assertEqual(set(event), {"kind", "ts"})
        self.rows({"kind": "perception", "activity": "voice-a", "ts": n - 20,
                   "medium": "voice", "status": "finished"},
                  {"kind": "delivery", "ts": n - 10, "status": "delivered", "route": "reply"})
        self.assertEqual(presence.snapshot(self.cfg, since=n - 15, now=n)["state"], "inspect")
        self.rows({"kind": "perception", "activity": "image-b", "ts": n - 1,
                   "medium": "image", "status": "finished"})
        self.assertIsNone(presence.snapshot(self.cfg, now=n)["state"])

    def test_unfinished_perception_expires_and_unknown_media_do_not_create_a_state(self):
        n = self.now
        self.rows({"kind": "perception", "event_id": "old", "ts": n - presence.PERCEPTION_WINDOW - 1,
                   "medium": "image", "status": "started"},
                  {"kind": "perception", "event_id": "unknown", "ts": n - 1,
                   "medium": "unknown", "status": "started"})
        self.assertIsNone(presence.snapshot(self.cfg, now=n)["state"])


if __name__ == "__main__":
    unittest.main()
