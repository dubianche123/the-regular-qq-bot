"""引用小王的话、@ 小王：认得出是在叫它，也看得见回的是哪句（2026-11-03，见 docs/quotes.md）。

11-03 09:00 线上：群友引用小王前一晚给他调的那杯酒，@ 小王问「为什么没有橘光反转爱尔兰」。
@ 用的是小王在群里的成员 openid（不是 robot.id），mentions 又不含它自己，于是记成
「群消息 | @群友 为什么没有……」、小王选了不接；连着两次。第三次喊了名字才接，
可它看不见引用的是哪句，答「不知道，不敢瞎编」。

走真实的 handle_group_msg，只把回话模型和分诊换成假的。
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import archive as archive_mod
import bot
import config
import delivery
import interactions
import naming
import prompts
import quotes
import runtime
import storage
from regular import decisions

G = "GID_QUOTES"
A = "USER_QUOTES_REN"
B = "USER_QUOTES_WANG"
SELF = "SELF_MEMBER_OPENID_D3D7"
DRINK = "老任这杯走沉稳路线：黑朗姆加浓缩咖啡、少许枫糖浆，再挤点橙皮香"


class QuoteHelpersTest(unittest.TestCase):
    def test_scene_reads_indexes_bot_flags_and_quoted_elements(self):
        scene = quotes.scene_of({
            "message_scene": {"source": "default", "ext": [
                "msg_idx=REFIDX_abc==", "auth_token=secret", "ref_msg_idx=REFIDX_def=="]},
            "mentions": [{"member_openid": B, "bot": False}, {"member_openid": SELF, "bot": True}],
            "msg_elements": [{"content": "=== 消息 1 ===\n[消息内容] 今天的学习计划已完成"}]})
        self.assertEqual(scene["msg_idx"], "REFIDX_abc==")
        self.assertEqual(scene["ref_msg_idx"], "REFIDX_def==")
        self.assertEqual(scene["bots"], [SELF])
        self.assertIn("今天的学习计划已完成", scene["quoted"])
        self.assertNotIn("auth_token", scene)
        self.assertEqual(quotes.scene_of({}), {})

    def test_self_is_the_at_that_mentions_leaves_out(self):
        self.assertEqual(quotes.self_in(f"<@{B}> <@{SELF}> 看", [B]), SELF)
        self.assertEqual(quotes.self_in(f"<@{SELF}> 为什么", []), SELF)
        # mentions 也带上了它：认标了 bot 的那个
        self.assertEqual(quotes.self_in(f"<@{B}> <@{SELF}>", [B, SELF], [SELF]), SELF)
        # 认不准就不认：自动带的 @ 被删了、换成 @ 别人；或者两个都不在 mentions 里
        self.assertIsNone(quotes.self_in(f"<@{B}> 你看", [B]))
        self.assertIsNone(quotes.self_in(f"<@{B}> <@{SELF}>", []))

    def test_receipt_ref_and_render(self):
        self.assertEqual(quotes.receipt_ref({"id": "x", "ext_info": {"ref_idx": "REFIDX_1=="}}),
                         "REFIDX_1==")
        self.assertIsNone(quotes.receipt_ref({"id": "x"}))
        self.assertIsNone(quotes.receipt_ref(None))
        self.assertEqual(quotes.render("小王", "a\nb"), "[回复小王：「a b」] ")
        long = quotes.render("小王", "字" * 200)
        self.assertLess(len(long), 100)
        self.assertIn("…」]", long)
        self.assertEqual(quotes.render("小王", ""), "")

    def test_book_keeps_the_latest_rows(self):
        state = SimpleNamespace(data={}, mark_dirty=lambda: None)
        book = quotes.QuoteBook(state)
        for i in range(quotes.KEEP + 5):
            book.remember(G, f"R{i}", A, f"第{i}句")
        self.assertIsNone(book.find(G, "R0"))
        self.assertEqual(book.find(G, f"R{quotes.KEEP + 4}")["text"], f"第{quotes.KEEP + 4}句")
        self.assertIsNone(book.find(G, None))
        book.remember(G, None, A, "没有索引的不记")
        self.assertEqual(len(state.data["quotes"][G]), quotes.KEEP)


class QuoteFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = archive_mod.MessageArchive(self._tmp.name, keep_days=0)
        self.addCleanup(self.archive.close)
        self.log = interactions.InteractionLog(
            os.path.join(self._tmp.name, "ix"), enabled=True, keep_days=0)
        self.addCleanup(self.log.close)
        self.state = storage.StateStore(os.path.join(self._tmp.name, "state.json"))
        self.prompts, self.unprompted = [], []

        async def fake_model(messages, max_tokens, **kw):
            if prompts.BANTER_MARK in messages[-1]["content"]:
                self.unprompted.append(messages[-1]["content"])
                return "〔不接〕"
            self.prompts.append(messages[-1]["content"])
            return "改名叫橘光反转爱尔兰也行，橙皮多挤两下"

        for target, name, value in (
                (runtime, "STATE", self.state),
                (runtime, "QUOTES", quotes.QuoteBook(self.state)),
                (runtime, "INTERACTIONS", self.log),
                (runtime, "ARCHIVE", self.archive),
                (runtime, "FEEDBACK", decisions.FeedbackTracker(120.0, 180.0)),
                (decisions.providers, "call_decision", AsyncMock(return_value=None)),
                (bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)),
                (bot, "_sent_ref_missing", []),
                (runtime, "GROUP_BUCKET",
                 storage.TokenBucket(config.GROUP_RATE_CAPACITY,
                                     config.GROUP_RATE_REFILL_SECONDS)),
                (runtime.KNOWLEDGE, "render", lambda *a, **k: ""),
                (bot, "call_model", fake_model)):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.addCleanup(lambda: bot.group_buffers.pop(G, None))
        self.addCleanup(lambda: bot.group_last_random_reply.pop(G, None))
        self.addCleanup(bot.processed_msg_ids.clear)
        self.addCleanup(bot._recent_at_ids.clear)
        for who in (A, B):
            self.addCleanup(lambda who=who: runtime.RELATIONS.records.pop(f"{G}|{who}", None))

    async def _drive(self, text, *, sender=A, scene=None, mentions=(), is_at=False,
                     msg_id=None, ref="REFIDX_reply=="):
        fake_self = SimpleNamespace(robot=SimpleNamespace(id="BOT_APP_ID", name="regular"))
        msg = SimpleNamespace(
            id=msg_id or "msg-" + os.urandom(5).hex(), content=text, group_openid=G,
            author=SimpleNamespace(member_openid=sender), timestamp=None, attachments=[],
            mentions=[SimpleNamespace(member_openid=m) for m in mentions],
            _scene=dict(scene or {}),
            reply=AsyncMock(return_value={"id": "rcpt", "ext_info": {"ref_idx": ref}}))
        await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=is_at)
        return msg

    def _self_id(self):
        return (self.state.data.get("groups") or {}).get(G, {}).get("self_openid")

    async def test_quoting_xiaowang_calls_it_and_shows_which_line(self):
        bot._remember_sent(G, {"id": "x", "ext_info": {"ref_idx": "REFIDX_drink=="}}, DRINK)
        await self._drive(f"<@{SELF}> 为什么没有橘光反转爱尔兰",
                          scene={"msg_idx": "REFIDX_q1==", "ref_msg_idx": "REFIDX_drink=="})
        self.assertEqual(self.unprompted, [])
        self.assertEqual(len(self.prompts), 1)
        self.assertIn(f"[回复{naming.bot_name()}：「老任这杯走沉稳路线", self.prompts[0])
        self.assertIn("为什么没有橘光反转爱尔兰", self.prompts[0])
        self.assertNotIn("@群友", self.prompts[0])
        self.assertEqual(self._self_id(), SELF)
        # 它这次回的话也记进了引用索引：再被引用时认得出
        self.assertEqual(runtime.QUOTES.find(G, "REFIDX_reply==")["who"], quotes.SELF)
        # 归档里记着回的是哪句，背景里后来的人也看得懂
        rows = [r for r in self.archive.since(G, 0) if r["sender"] == A]
        self.assertTrue(rows[-1]["text"].startswith(f"[回复{naming.bot_name()}："))
        self.assertTrue(rows[-1]["at"])

    async def test_plain_at_counts_once_the_id_is_known(self):
        await self._drive(f"<@{SELF}> 我要橘光反转爱尔兰")      # 还没认出来：当成 @ 了别人
        self.assertEqual(self.prompts, [])
        self.assertEqual(len(self.unprompted), 1)
        self.assertIn("@群友", self.unprompted[0])

        bot._learn_self(G, SELF, "测试")
        await self._drive(f"<@{SELF}> 我要橘光反转爱尔兰")
        self.assertEqual(len(self.prompts), 1)
        this_turn = self.prompts[0].rsplit("\n\n", 1)[1]
        self.assertIn("我要橘光反转爱尔兰", this_turn)
        self.assertNotIn("@群友", this_turn)

    async def test_quote_without_the_at_still_reaches_triage_as_a_call(self):
        bot._remember_sent(G, {"id": "x", "ext_info": {"ref_idx": "REFIDX_drink=="}}, DRINK)
        with patch.object(runtime.FEEDBACK, "assess",
                          AsyncMock(return_value={"decision_attempted": True})) as assess:
            await self._drive("那我的呢", scene={"ref_msg_idx": "REFIDX_drink=="})
        self.assertTrue(assess.await_args.kwargs["named"])
        self.assertIn("[回复", assess.await_args.args[2])
        self.assertIsNone(self._self_id())      # 没有 @ 可认

    async def test_quoting_a_member_names_who_said_it_and_learns_nothing(self):
        await self._drive("浓缩咖啡配朗姆挺搭的", sender=B, scene={"msg_idx": "REFIDX_b1=="})
        self.prompts.clear()
        self.unprompted.clear()
        await self._drive(f"<@{B}> 你看他说的对吗", mentions=[B], is_at=True,
                          scene={"ref_msg_idx": "REFIDX_b1=="})
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("[回复一位群友：「浓缩咖啡配朗姆挺搭的」]", self.prompts[0])
        self.assertIsNone(self._self_id())

    async def test_unknown_quote_adds_nothing(self):
        with self.assertLogs("qqbot", "INFO") as logs:
            await self._drive("你说呢", is_at=True, scene={"ref_msg_idx": "REFIDX_gone=="})
        self.assertNotIn("[回复", self.prompts[0])
        self.assertTrue(any("没记着的消息" in line for line in logs.output))

    async def test_late_at_event_teaches_the_id(self):
        await self._drive(f"在吗<@{SELF}>", msg_id="msg-late")
        self.assertIsNone(self._self_id())
        await self._drive("在吗", msg_id="msg-late", is_at=True)   # @ 事件晚到，正文去掉了 @机器人
        self.assertEqual(self._self_id(), SELF)

    async def test_receipt_without_ref_warns_once(self):
        with self.assertLogs("qqbot", "WARNING") as logs:
            bot._remember_sent(G, {"id": "x"}, "一句")
            bot._remember_sent(G, {"id": "y"}, "又一句")
        self.assertEqual(sum("ref_idx" in line for line in logs.output), 1)
        self.assertEqual((self.state.data.get("quotes") or {}).get(G), None)


if __name__ == "__main__":
    unittest.main()
