"""按需出现的材料（2026-11-16）：没提到就看不见，而不是摆在眼前再叫它别用。"""
import unittest
from unittest.mock import patch

import bot
import prompts


class DisclosureTest(unittest.IsolatedAsyncioTestCase):
    async def _prompt(self, user_text, **kw):
        seen = []

        async def fake_model(messages, max_tokens, **_kw):
            seen.append(messages)
            return "（应答）"

        with patch.object(bot, "call_model", fake_model):
            await bot.get_ai_reply("sid-disclosure", user_text, **kw)
        return seen[0]

    async def test_game_lines_only_when_a_game_comes_up(self):
        quiet = await self._prompt("今天降温了好冷", context_hint="- 其他群友（阿强）：下班挺早")
        self.assertNotIn("杀戮尖塔", "\n".join(m["content"] for m in quiet))
        self.assertNotIn("梗小鬼", "\n".join(m["content"] for m in quiet))
        talk = await self._prompt("晚上来两把", context_hint="- 其他群友（阿强）：炉石又更新了")
        dynamic = talk[-1]["content"]
        self.assertIn(prompts.GAME_HEADER, dynamic)
        self.assertIn("《炉石传说》", dynamic)
        self.assertNotIn("《杀戮尖塔》", dynamic)
        self.assertNotIn("《炉石传说》", talk[0]["content"], "游戏梗不再常驻稳定头")

    def test_game_note_reads_common_short_names_once(self):
        note = prompts.game_note("LOL 和 英雄联盟 是一个东西，尖塔也好玩")
        self.assertEqual(note.count("《英雄联盟》"), 1)
        self.assertIn("《杀戮尖塔》", note)
        self.assertEqual(prompts.game_note("今天吃什么"), "")

    async def test_owner_named_only_when_the_owner_comes_up(self):
        plain = await self._prompt("小王晚饭吃啥", owner_label="老王")
        self.assertNotIn("老王", "\n".join(m["content"] for m in plain))
        asked = await self._prompt("群主今天怎么不说话", owner_label="老王")
        self.assertIn(prompts.PROMPT_OWNER_FACT.format(owner="老王"), asked[-1]["content"])
        self.assertNotIn("老王", asked[0]["content"], "群主的名字不进稳定头")
        own = await self._prompt("我今天加班", owner_label="老王", is_owner=True)
        self.assertNotIn("【本群群主】", own[-1]["content"])
        unclaimed = await self._prompt("群主呢", owner_label="群主")
        self.assertNotIn("【本群群主】", unclaimed[-1]["content"])


if __name__ == "__main__":
    unittest.main()
