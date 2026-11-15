"""一轮群消息的各段可以单独驱动：收件 → 分诊 → 计划 → 生成与投递 → 落账。

整条路的行为由别的测试钉着（都走真实的 handle_group_msg）；这里只钉段与段之间的约定 ——
收件时冻结的现场之后不变、每段把结论写回 GroupTurn、哪一段说「到此为止」后面就不再跑。
"""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import archive as archive_mod
import bot
import config
import delivery
import interactions
import naming
import runtime
import storage
from regular import decisions

G = "GID_TURN_STAGES"
A = "USER_TURN_A"
B = "USER_TURN_B"
ROBOT = SimpleNamespace(id="BOT_TURN_STAGES", name="regular")


def _message(text, *, sender=A):
    return SimpleNamespace(
        id="msg-" + os.urandom(5).hex(), content=text, group_openid=G,
        author=SimpleNamespace(member_openid=sender), mentions=[], timestamp=None, attachments=[],
        reply=AsyncMock(return_value={"id": "rcpt-" + os.urandom(3).hex()}))


class BackgroundSelectionTest(unittest.TestCase):
    """挑背景行、渲染成 context_hint：两个不依赖闭包的函数。"""

    def setUp(self):
        self.now = time.time()

    def _row(self, sender, text, ago):
        return {"sender": sender, "text": text, "time": self.now - ago, "message_id": text}

    def test_keeps_the_latest_rows_and_drops_stale_and_current(self):
        history = [self._row(B, "十二天前", config.GROUP_BUFFER_TTL_SECONDS + 60),
                   self._row(B, "一", 40), self._row(B, "二", 30),
                   self._row(A, "眼前这句", 20), self._row(B, "三", 10)]
        picked = bot._pick_background(history, A, "眼前这句", 2, self.now)
        self.assertEqual([row["text"] for row in picked], ["二", "三"])

    def test_own_recent_image_survives_the_row_limit(self):
        history = [self._row(A, "[图片：一只橘猫]", 50), self._row(B, "[图片：别人的图]", 45),
                   self._row(B, "一", 40), self._row(B, "二", 30)]
        picked = bot._pick_background(history, A, "这张怎么样", 2, self.now)
        self.assertEqual([row["text"] for row in picked], ["[图片：一只橘猫]", "一", "二"])

    def test_render_labels_speakers_and_interleaves_its_own_lines(self):
        rows = [self._row(A, "我先说", 30), self._row(B, "我接一句", 10)]
        own = [{"time": self.now - 20, "text": "它夹在中间说的"},
               {"time": self.now + 5, "text": "收件以后才说的"}]
        hint = bot._render_background(G, A, rows, own, until=self.now, now=self.now)
        self.assertEqual(hint.splitlines(), [
            bot._BG_PREAMBLE, "- 当前说话人：我先说", "- 你：它夹在中间说的",
            "- 其他群友（一位群友）：我接一句"])

    def test_render_without_rows_is_empty(self):
        self.assertEqual(bot._render_background(G, A, [], [{"time": self.now, "text": "它说过话"}],
                                                until=self.now, now=self.now), "")


class GroupTurnStagesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = interactions.InteractionLog(
            os.path.join(self._tmp.name, "ix"), enabled=True, keep_days=0)
        self.addCleanup(self.log.close)
        self.model = AsyncMock(return_value="（模型应答）")
        for target, name, value in (
                (runtime, "STATE", storage.StateStore(os.path.join(self._tmp.name, "state.json"))),
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", decisions.FeedbackTracker(120.0, 180.0)),
                (runtime, "SESSIONS", storage.SessionStore(
                    max_turns=config.CONTEXT_MAX_TURNS, max_chars=config.CONTEXT_MAX_CHARS,
                    ttl=config.CONTEXT_TTL_SECONDS)),
                (decisions.providers, "call_decision", AsyncMock(return_value=None)),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (runtime, "GROUP_BUCKET", storage.TokenBucket(
                    config.GROUP_RATE_CAPACITY, config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", self.model),
                (config, "SEARCH_ENABLED", False),
                (config, "DECISION_ENABLED", True),
                (config, "BANTER_BURST_SECONDS", 0.0)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.addCleanup(lambda: bot.group_buffers.pop(G, None))
        self.addCleanup(lambda: bot.own_lines.pop(G, None))
        self.addCleanup(lambda: bot.group_last_random_reply.pop(G, None))
        self.addCleanup(bot.processed_msg_ids.clear)
        for member in (A, B):
            self.addCleanup(lambda m=member: runtime.RELATIONS.records.pop(f"{G}|{m}", None))

    async def _through_plan(self, message, *, is_at=False):
        turn = bot._receive(message, is_at, ROBOT)
        self.assertTrue(await bot._admit(turn))
        self.assertFalse(await bot._local_command(turn))
        await bot._triage(turn)
        return turn, await bot._plan(turn)

    def test_stage_table_lists_the_stages_in_order(self):
        self.assertEqual([stage.__name__ for stage in bot.GROUP_TURN_STAGES], [
            "_receive", "_admit", "_assess", "_local_command", "_triage",
            "_plan_lookup", "_plan_scene", "_plan_notes", "_plan", "_respond", "_settle"])

    def test_receive_drops_duplicates_and_unreadable_messages(self):
        message = _message("在吗")
        self.assertIsNotNone(bot._receive(message, False, ROBOT))
        self.assertIsNone(bot._receive(message, False, ROBOT), "同一条消息投递两次只算一轮")
        self.assertIsNone(bot._receive(_message(""), False, ROBOT), "读不出人话又没叫它：这一轮不存在")

    def test_receive_reads_how_it_was_called(self):
        name = naming.bot_name()
        named = bot._receive(_message(f"{name} 今天吃什么"), False, ROBOT)
        self.assertEqual((named.at_bot, named.named, named.is_at), (False, True, True))
        at = bot._receive(_message("今天吃什么"), True, ROBOT)
        self.assertEqual((at.at_bot, at.named, at.is_at), (True, False, True))
        plain = bot._receive(_message("今天吃什么"), False, ROBOT)
        self.assertEqual((plain.at_bot, plain.named, plain.is_at), (False, False, False))
        self.assertEqual(plain.session_id, f"{G}_{A}")

    async def test_scene_frozen_at_receipt_ignores_what_arrives_later(self):
        first = bot._receive(_message("早上好", sender=B), False, ROBOT)
        await bot._admit(first)
        turn = bot._receive(_message("今天吃什么"), True, ROBOT)
        self.assertEqual([row["text"] for row in turn.history_at_receipt], ["早上好"])
        await bot._admit(turn)
        await bot._admit(bot._receive(_message("我先插一句", sender=B), False, ROBOT))
        self.assertEqual([row["text"] for row in bot.group_buffers[G]],
                         ["早上好", "今天吃什么", "我先插一句"])
        self.assertEqual([row["text"] for row in turn.history_at_receipt], ["早上好"],
                         "收件以后进群的话不改写它当时面对的现场")

    async def test_cancel_all_reminders_ends_the_turn_before_it_reaches_the_scene(self):
        turn = bot._receive(_message("停止所有定时任务"), True, ROBOT)
        self.assertTrue(bot.is_cancel_all_reminders(turn.user_input))
        self.assertFalse(await bot._admit(turn))
        self.assertNotIn(G, bot.group_buffers, "命令留痕在归档里，不进群聊背景")

    async def test_direct_address_plans_a_reply_from_the_frozen_scene(self):
        await bot._admit(bot._receive(_message("早上好", sender=B), False, ROBOT))
        turn, planned = await self._through_plan(_message("今天吃什么"), is_at=True)
        self.assertTrue(planned)
        self.assertEqual((turn.should_reply, turn.unprompted), (True, False))
        self.assertEqual(turn.user_text, "今天吃什么")
        self.assertIn("- 其他群友（一位群友）：早上好", turn.context_hint)
        kwargs = turn.reply_kwargs
        self.assertEqual(kwargs["context_hint"], turn.context_hint)
        self.assertEqual(kwargs["received_at"], turn.receipt_time)
        self.assertIs(kwargs["history_snapshot"], turn.session_at_receipt)
        self.assertEqual([row["text"] for row in kwargs["history"]], ["早上好", "今天吃什么"])
        self.assertFalse(kwargs["unprompted"])
        self.model.assert_not_awaited()   # 到计划为止一次模型都没调

    async def test_unaddressed_line_is_left_to_the_model_and_only_delivery_is_settled(self):
        turn, planned = await self._through_plan(_message("今天天气不错"))
        self.assertTrue(planned)
        self.assertEqual((turn.should_reply, turn.unprompted), (True, True))
        self.assertTrue(turn.reply_kwargs["unprompted"])
        self.model.return_value = "〔不接〕"
        outcome = await bot._respond(turn)
        self.assertIsNone(outcome[2], "它写了〔不接〕：没有投递结果")
        with patch.object(bot, "apply_affinity_delta_capped") as settle:
            bot._settle(turn, outcome)
            settle.assert_not_called()
            bot._settle(turn, None)
            settle.assert_not_called()

    async def test_quiet_window_stops_the_turn_at_plan(self):
        runtime.FEEDBACK._groups.setdefault(G, {})["quiet_until"] = time.time() + 60
        turn, planned = await self._through_plan(_message("今天天气不错"))
        self.assertFalse(turn.should_reply)
        self.assertFalse(planned)
        self.assertEqual(turn.reply_kwargs, {})


class ReplyTurnStagesTest(unittest.IsolatedAsyncioTestCase):
    """reply_in_session 的各段：开场写下本轮能做什么，草稿读出它想做什么。"""

    def _turn(self, **fields):
        rt = bot.ReplyTurn(message=_message("今天吃什么"), session_id="SESSION_TURN_STAGES",
                           user_text="今天吃什么", context={}, **fields)
        self.addCleanup(lambda: runtime.SESSIONS._sessions.pop(rt.session_id, None))
        bot._reply_begin(rt)
        return rt

    def test_begin_states_what_this_turn_cannot_do(self):
        rt = self._turn()
        self.assertIn("这轮不执行画图〔画〕、动图〔动图〕、发语音〔语音〕", rt.context["media_note"])
        self.assertIn("message_id", rt.trace)
        self.assertFalse(rt.lookup_used)

    async def test_prepared_line_is_not_generated(self):
        rt = self._turn(prepared=("我去查查，等我一会儿。", None), prepared_local=True)
        with patch.object(bot, "get_ai_reply", AsyncMock()) as model:
            self.assertIsNone(await bot._reply_draft(rt))
        model.assert_not_awaited()
        self.assertEqual((rt.body, rt.generated, rt.said.kind), ("我去查查，等我一会儿。", False, None))

    async def test_draft_that_declines_ends_the_turn(self):
        rt = self._turn()
        with patch.object(bot, "get_ai_reply", AsyncMock(return_value=("〔不接〕", 1))):
            self.assertEqual(await bot._reply_draft(rt), ("", 1, None))

    async def test_stale_draft_is_not_sent(self):
        rt = self._turn(is_stale=lambda: True)
        with patch.object(bot, "get_ai_reply", AsyncMock(return_value=("接一句", 0))):
            self.assertEqual(await bot._reply_draft(rt), ("", 0, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
