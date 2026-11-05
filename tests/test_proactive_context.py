"""主动现场复用实际结果；成功、未确认、失败和重启后的记录不混为一谈。"""
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import expression
import proactive
import storage
from test_media_replies import _Reply
from test_proactive_quotes import _rows

_REAL_OBSERVER = bot._delivery_observer


class ContextRenderingTest(unittest.TestCase):
    def test_chronological_scene_keeps_completed_picture_before_human_correction(self):
        rows = _rows(2)
        rows[0].update(time=100, text="画一只花园里的猫")
        rows[1].update(time=130, text="我说的是黑猫，刚才那只是白的")
        own = [{"time": 120, "text": "[图：一只白猫在花园里]", "medium": "image", "status": "delivered"}]
        context = proactive.render_context(rows, own, now=190, label=lambda sender: "同学甲")
        self.assertLess(context.index("画一只花园里的猫"), context.index("[图：一只白猫"))
        self.assertLess(context.index("[图：一只白猫"), context.index("我说的是黑猫"))
        self.assertIn("[1] 同学甲", context)
        self.assertIn("[2] 同学甲", context)
        self.assertIn("图片已送达", context)
        self.assertIn("1分钟前", context)
        for row in rows:
            for key in ("sender", "message_id", "msg_idx"):
                self.assertNotIn(row[key], context)
        self.assertIs(proactive.parse("〔引用 2〕\n接住这次更正", rows)[1], rows[1])

    def test_long_message_preserves_the_final_correction_with_visible_omission(self):
        rows = [{"time": 10, "sender": "private", "text": "先聊猫：" + "很长的背景" * 100 + "最后更正：别再画白猫了"}]
        context = proactive.render_context(rows, [], now=20, label=lambda sender: "一位群友")
        self.assertIn("先聊猫", context)
        self.assertIn("最后更正：别再画白猫了", context)
        self.assertIn("…", context)

    def test_image_evidence_and_delivery_uncertainty_are_explicit(self):
        rows = [{"time": 10, "text": "[图片]", "sender": "private"},
                {"time": 20, "text": "[图片：一只戴帽子的猫]", "sender": "private"}]
        own = [{"time": 30, "text": "一句语音", "medium": "voice", "status": "unconfirmed"},
               {"time": 40, "text": "画图服务出错", "medium": "image", "status": "failed"}]
        context = proactive.render_context(rows, own, now=50, label=lambda sender: "一位群友")
        self.assertIn("暂不可读，尚无描述", context)
        self.assertIn("图片：一只戴帽子的猫", context)
        self.assertIn("语音送达未确认，勿重复发送", context)
        self.assertIn("图片未完成", context)
        self.assertNotIn("语音已送达", context)
        self.assertNotIn("图片已送达", context)

    def test_total_budget_keeps_recent_lines_and_original_number_mapping(self):
        rows = [{"time": i, "sender": "private", "text": "很长的原话" * 1000}
                for i in range(40)]
        rows[-1]["text"] = "最近的关键纠正"
        own = [{"time": i + 0.5, "text": "已经说过的话" * 1000} for i in range(50)]
        context = proactive.render_context(rows, own, now=100, label=lambda sender: "一位群友")
        self.assertLessEqual(len(context), proactive.CONTEXT_CHARS)
        self.assertIn("现场已略去", context)
        self.assertIn("[40] 一位群友：最近的关键纠正", context)
        self.assertIs(proactive.parse("〔引用 40〕\n接住更正", rows)[1], rows[-1])


class ActualResultHistoryTest(_Reply):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for p in (patch.object(bot, "own_lines", collections.defaultdict(lambda: collections.deque(maxlen=10))),
                  patch.object(bot, "_delivery_observer", _REAL_OBSERVER),
                  patch.object(bot, "_remember_sent")):
            p.start(); self.addCleanup(p.stop)

    async def test_normal_reply_and_correction_are_actual_persisted_words(self):
        with self.said("刚才理解反了，这次按黑猫来"):
            await bot.reply_in_session(self.message, "group_member", "不是白猫", media=self.media())
        row = bot.own_lines["group"][-1]
        self.assertEqual(row["text"], self.sent()[-1])
        self.assertEqual((row["medium"], row["status"]), ("text", "delivered"))
        self.assertEqual(self.state.data["groups"]["group"]["own_lines"][-1], row)

    async def test_confirmed_voice_is_marked_as_voice_in_shared_history(self):
        with self.said("〔语音〕\n让尾巴先当一天书签"), patch.object(bot._VOICE_OUT, "attempt",
                  AsyncMock(return_value=(SimpleNamespace(status="sent"), {"id": "voice-receipt"}))):
            await bot.reply_in_session(self.message, "group_member", "念一句", media=self.media())
        row = bot.own_lines["group"][-1]
        self.assertEqual((row["medium"], row["status"]), ("voice", "delivered"))
        self.assertEqual(row["text"], "让尾巴先当一天书签")

    async def test_unknown_voice_never_claims_success_in_session_or_shared_history(self):
        with self.said("〔语音〕\n让尾巴先当一天书签"), patch.object(bot._VOICE_OUT, "attempt",
                  AsyncMock(return_value=(SimpleNamespace(status="delivery_unknown"), None))):
            await bot.reply_in_session(self.message, "group_member", "念一句", media=self.media())
        self.message.reply.assert_not_awaited()
        row = bot.own_lines["group"][-1]
        self.assertEqual((row["medium"], row["status"]), ("voice", "unconfirmed"))
        self.assertIn("送达未确认", self.last_record())
        self.assertNotIn(expression.VOICE_SENT, self.last_record())
        self.assertIn("不要重复发送", self.last_record())

    async def test_picture_success_failure_and_unknown_remain_distinct(self):
        for outcome, status in ((True, "delivered"), (False, "failed"), (None, "unconfirmed")):
            with self.subTest(outcome=outcome), self.said("〔画〕一只花园里的猫\n给猫安排个位子"), \
                 patch.object(bot.image_generation, "generate_and_send", AsyncMock(return_value=outcome)):
                await bot.reply_in_session(self.message, "group_member", "画一只猫", media=self.media())
                image = [r for r in bot.own_lines["group"] if r["medium"] == "image"][-1]
                self.assertEqual(image["status"], status)
                self.assertIn("猫", image["text"])
                if outcome is None:
                    self.assertIn("回执不明", image["text"])

    async def test_unconfirmed_text_is_recorded_once_only_after_later_receipt(self):
        self.message.reply.side_effect = [None, None, {"id": "late-confirmation"}]
        with self.said("等确认后才算我真的说过"):
            await bot.reply_in_session(self.message, "group_member", "说一句", media=self.media())
        self.assertEqual(list(bot.own_lines.get("group", ())), [])
        await bot.SENDER.drain()
        await bot.SENDER.drain()
        self.assertEqual([r["text"] for r in bot.own_lines["group"]], ["等确认后才算我真的说过"])
        self.assertEqual(bot.own_lines["group"][-1]["status"], "delivered")

    async def test_rejected_reply_never_becomes_something_the_bot_said(self):
        self.message.reply.side_effect = RuntimeError("explicitly refused")
        with self.said("这一句没有发出去"):
            await bot.reply_in_session(self.message, "group_member", "随便说一句", media=self.media())
        self.assertEqual(list(bot.own_lines.get("group", ())), [])


class RestartHistoryTest(unittest.TestCase):
    def test_own_results_and_proactive_cooldown_restore_from_existing_group_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state = storage.StateStore(path)
            with patch.object(bot.runtime, "STATE", state), \
                 patch.object(bot, "own_lines", collections.defaultdict(lambda: collections.deque(maxlen=3))):
                for i in range(5):
                    bot._note_own_line("restart-group", f"确认送达第{i}句")
                state.data["groups"]["restart-group"]["last_proactive"] = time.time()
                state.save_now()
            env = dict(os.environ, REGULAR_HOME=tmp, STATE_FILE=path, DATA_DIR=tmp,
                       LOG_FILE=os.path.join(tmp, "bot.log"), QQ_APP_ID="", QQ_APP_SECRET="")
            code = """
import asyncio,json
from unittest.mock import AsyncMock,patch
import bot
seen=[]
def gate(mood,hour,now,last,latest):
    seen.append(last)
    return False,'cold'
async def run():
    with patch.object(bot.STATUS,'sleep',AsyncMock(side_effect=[None,asyncio.CancelledError()])), patch.object(bot.activation,'proactive_ok',gate):
        try: await bot.proactive_loop()
        except asyncio.CancelledError: pass
bot.group_buffers['restart-group'].append({'text':'现场','time':__import__('time').time()})
asyncio.run(run())
print(json.dumps({'own':list(bot.own_lines['restart-group']),'last':seen[0]},ensure_ascii=False))
"""
            result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
            restored = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual([r["text"] for r in restored["own"]], ["确认送达第2句", "确认送达第3句", "确认送达第4句"])
            self.assertTrue(all(r["status"] == "delivered" for r in restored["own"]))
            self.assertEqual(restored["last"], state.data["groups"]["restart-group"]["last_proactive"])
