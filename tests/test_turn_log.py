"""每轮模型输入记录（turns.py，docs/self-iteration.md ①）：记得全、能关联到送达、私聊不记。"""
import gzip
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
import config
import runtime
import turns as turns_mod
from regular import decisions as decisions_mod
from test_interactions import _HandlerBase, _rows


class TurnLogFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.log = turns_mod.TurnLog(self.dir, enabled=True, keep_days=30)
        self.addCleanup(self.log.close)

    def test_append_find_and_reserved_keys(self):
        row = self.log.append("reply", turn_id="spoofed", route="reply", raw="嗯")
        self.assertNotEqual(row["turn_id"], "spoofed")
        self.assertEqual(row["schema"], turns_mod.SCHEMA)
        found = turns_mod.find(self.dir, row["turn_id"])
        self.assertEqual(found["raw"], "嗯")

    def test_disabled_writes_nothing(self):
        log = turns_mod.TurnLog(os.path.join(self.dir, "off"), enabled=False)
        self.assertIsNone(log.append("reply", raw="x"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "off")))

    def test_old_days_are_compressed_and_still_readable(self):
        old = {"schema": turns_mod.SCHEMA, "turn_id": "t-old", "ts": 1.0, "kind": "reply"}
        with open(os.path.join(self.dir, "2026-10-01.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(old) + "\n")
        self.log._compress_before("2026-11-03")
        names = os.listdir(self.dir)
        self.assertIn("2026-10-01.jsonl.gz", names)
        self.assertNotIn("2026-10-01.jsonl", names)
        with gzip.open(os.path.join(self.dir, "2026-10-01.jsonl.gz"), "rt") as f:
            self.assertIn("t-old", f.read())
        self.assertEqual(turns_mod.find(self.dir, "t-old")["turn_id"], "t-old")
        self.assertEqual(turns_mod.days(self.dir), ["2026-10-01"])

    def test_prune_removes_compressed_files_past_retention(self):
        for name in ("2020-01-01.jsonl.gz", "2020-01-02.jsonl"):
            with open(os.path.join(self.dir, name), "wb") as f:
                f.write(b"")
        self.log._last_prune = 0.0
        self.log._maybe_prune(time.time())
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith("2020")], [])


class GetAiReplyRecordsTurnTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = turns_mod.TurnLog(self._tmp.name, enabled=True)
        self.addCleanup(self.log.close)
        self.calls = []

        async def fake_model(messages, max_tokens, **kw):
            self.calls.append(messages)
            return "行，记下了。"

        for target, name, value in ((runtime, "TURNS", self.log),
                                    (bot, "call_model", fake_model)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    def rows(self):
        return turns_mod.read_day(self._tmp.name, turns_mod.day_key())

    async def test_records_params_history_and_head_hash(self):
        sid = "turn_rec_1"
        runtime.SESSIONS.record(sid, "上一句", "上一答")
        self.addCleanup(lambda: runtime.SESSIONS._sessions.pop(sid, None))
        flags = {"draw": True, "animate": True, "voice": False, "remind": True}
        with patch.object(bot, "media_flags", return_value=flags):
            await bot.get_ai_reply(sid, "在吗", context_hint="- 阿强：今天天气不错",
                                   group_memory="【群记忆】黑猴", media_note="画不了")
        (row,) = self.rows()
        self.assertEqual(row["kind"], "reply")
        self.assertEqual(row["route"], "reply")
        self.assertEqual(row["session_id"], sid)
        self.assertEqual(row["kwargs"]["user_text"], "在吗")
        self.assertEqual(row["kwargs"]["media_note"], "画不了")
        self.assertEqual(row["kwargs"]["group_memory"], "【群记忆】黑猴")
        self.assertNotIn("allow_fallback", row["kwargs"])
        self.assertNotIn("session_id", row["kwargs"])
        # 会话历史 = 这一轮真送出去的那段，不含稳定头、记忆和本轮那条
        self.assertEqual([m["content"] for m in row["session"]], ["上一句", "上一答"])
        head = self.calls[0][0]["content"]
        self.assertEqual(row["head_chars"], len(head))
        self.assertEqual(len(row["head_sha"]), 12)
        self.assertEqual(row["media"], flags)
        self.assertIn(bot.prompts.PROMPT_ANIMATION_RULES, head)
        self.assertEqual(row["raw"], "行，记下了。")
        self.assertTrue(row["release"])

    async def test_route_follows_kwargs_and_trace(self):
        await bot.get_ai_reply("turn_rec_2", "哈哈", unprompted=True)
        await bot.get_ai_reply("turn_rec_2", "查好了吗", lookup_note="查完了")
        token = bot._reply_trace.set({"route": "proactive", "group": "G"})
        try:
            await bot.get_ai_reply("G_proactive", "随便说点")
            self.assertTrue(bot._reply_trace.get()["turn_id"])
        finally:
            bot._reply_trace.reset(token)
        self.assertEqual([r["route"] for r in self.rows()], ["banter", "followup", "proactive"])
        self.assertEqual(self.rows()[-1]["group"], "G")

    async def test_private_is_never_recorded(self):
        await bot.get_ai_reply("private_x", "悄悄话", private=True)
        self.assertEqual(self.rows(), [])

    async def test_failed_generation_still_recorded(self):
        async def dead(messages, max_tokens, **kw):
            return None
        with patch.object(bot, "call_model", dead):
            await bot.get_ai_reply("turn_rec_3", "在吗", allow_fallback=False)
        (row,) = self.rows()
        self.assertIsNone(row["raw"])

    async def test_write_failure_never_breaks_reply(self):
        def boom(*a, **k):
            raise RuntimeError("disk gone")
        with patch.object(self.log, "append", boom):
            body, _ = await bot.get_ai_reply("turn_rec_4", "在吗")
        self.assertEqual(body, "行，记下了。")


class DeliveryCarriesTurnIdTest(_HandlerBase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.turns = turns_mod.TurnLog(os.path.join(self._tmp.name, "turns"), enabled=True)
        self.addCleanup(self.turns.close)
        ctx = patch.object(runtime, "TURNS", self.turns)
        ctx.start()
        self.addCleanup(ctx.stop)

    async def test_delivery_row_links_back_to_turn(self):
        msg = self._msg("hi", msg_id="M-turn-1")
        await bot.reply_in_session(msg, f"{self.G}_{self.A}", "hi")
        (turn,) = turns_mod.read_day(self.turns.log_dir, turns_mod.day_key())
        self.assertEqual((turn["group"], turn["user"], turn["message_id"]),
                         (self.G, self.A, "M-turn-1"))
        delivered = [r for r in _rows(self.log.log_dir)
                     if r["kind"] == "delivery" and r["status"] == "delivered"]
        self.assertEqual(delivered[-1]["turn_id"], turn["turn_id"])
        # turn_id 只是本地关联键，不进提示词
        self.assertNotIn(turn["turn_id"], json.dumps(self.model_calls, ensure_ascii=False))


class TriageInputRecordedTest(unittest.IsolatedAsyncioTestCase):
    async def test_assess_records_payload_asked_and_choices(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = turns_mod.TurnLog(tmp.name, enabled=True)
        self.addCleanup(log.close)
        tracker = decisions_mod.FeedbackTracker(600, 300, turns=lambda: log)

        async def fake_decision(input, questions):
            return None

        with patch.object(config, "DECISION_ENABLED", True), \
                patch.object(decisions_mod.providers, "call_decision", fake_decision):
            await tracker.assess("G", "U", "小王你在吗", message_id="M1", is_direct=True)
        (row,) = turns_mod.read_day(tmp.name, turns_mod.day_key())
        self.assertEqual(row["kind"], "triage")
        self.assertEqual(row["message_id"], "M1")
        self.assertEqual(row["payload"]["latest_message"]["text"], "小王你在吗")
        self.assertIn("next_move", row["asked"])
        # 送出去的本来就是脱敏代号：真实 ID 不在 payload 里
        self.assertNotIn("\"U\"", json.dumps(row["payload"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
