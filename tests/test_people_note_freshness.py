# -*- coding: utf-8 -*-
"""个人原话的**来源核对**：模型提案、代码裁决（2026-10 重构）。

旧名册收的是模型写的「这个人平时什么样」—— 推测、换个说法就续杯、全过期还兜底喂。
新口径把它换成**可核对的原话**：
  · 提案必须给出能分清的 who + 完整 quote（4~240 字，只许合并空白）；
  · 代码把 quote 逐字绑回**本批已处理**的原始发言（发言人 + 消息标识 + 时间）；
  · 时效看原始发言时间：复述不续杯，同一 message_id 重读不续杯，
    只有一条真的新消息（新 ID）才带新时间；
  · 性格推测、伪造的 evidence/ts、别人的话、指令、图片描述、对不上的条目 —— 全丢。

这些测试只证明「入库的是核实过的原话」，不证明真人聊天因此更好。
"""
import sys
import unittest

sys.path.insert(0, "..")

import config
import digest
import prompts

GROUP = "test-group"
ALICE = "user-alice"
BOB = "user-bob"
MSG_ONE = "message-one"
QUOTE = "喝咖啡时我不要糖，喜欢黑咖啡"
KEYWORDS = ["咖啡"]
GUESSED_NOTE = "推测性格不可入库"

resolver = lambda uid: {ALICE: "阿青", BOB: "小白"}.get(uid)


def _entry(text=QUOTE, sender=ALICE, message_id=MSG_ONE, ts=1000.0, **extra):
    row = {"group": GROUP, "sender": sender, "text": text,
           "text_complete": True, "message_id": message_id, "ts": ts}
    row.update(extra)
    return row


def _proposal(who="阿青", quote=QUOTE, note=GUESSED_NOTE, keywords=None, **extra):
    row = {"who": who, "note": note, "quote": quote,
           "keywords": list(KEYWORDS if keywords is None else keywords)}
    row.update(extra)
    return row


def _stamp(new_people, entries, now=1100.0, old=None, group_id=GROUP, res=resolver):
    new_data = {"people": new_people}
    digest.stamp_people(old, new_data, now=now, entries=entries,
                        resolver=res, group_id=group_id)
    return new_data["people"]


class SourceBindingTest(unittest.TestCase):
    """提案 quote 必须逐字等于本批已处理的原文；来源字段全部来自代码侧核对。"""

    def test_full_quote_binds_the_real_source(self):
        """伪造的 evidence/ts/note 一律不作数；note 被换成原话本身。"""
        proposal = _proposal()
        proposal["evidence"] = {"group": "forged", "sender": "forged",
                                "message_id": "forged", "ts": 1.0, "quote": "伪造"}
        proposal["ts"] = 99999.0
        people = _stamp([proposal], [_entry()], now=9999.0)
        self.assertEqual(len(people), 1)
        bound = people[0]
        self.assertEqual(bound["ts"], 1000.0)           # 原始发言时间，不是压缩时间
        self.assertEqual(bound["note"], QUOTE)          # 推测措辞不进库
        self.assertEqual(bound["quote"], QUOTE)
        self.assertEqual(bound["keywords"], KEYWORDS)
        self.assertEqual(bound["evidence"], {"group": GROUP, "sender": ALICE,
                                             "message_id": MSG_ONE, "ts": 1000.0,
                                             "quote": QUOTE})

    def test_substring_that_drops_the_negation_is_rejected(self):
        """截掉否定/条件来凑证据不许过 —— quote 必须是完整一条。"""
        for frag in ("不要糖", "喜欢黑咖啡", "喝咖啡时我不要糖"):
            people = _stamp([_proposal(quote=frag)], [_entry()])
            self.assertEqual(people, [], frag)

    def test_someone_elses_quote_is_not_attached_to_the_claimed_speaker(self):
        """提案说这话是小白说的，但原文是 user-alice 发的 —— 对不上就丢。"""
        people = _stamp([_proposal(who="小白")], [_entry(sender=ALICE)])
        self.assertEqual(people, [])

    def test_same_display_name_and_quote_across_two_senders_is_ambiguous(self):
        """两个人显示名相同、原话也相同：来源身份模糊，宁可丢也不猜。"""
        both_named = lambda uid: "阿青"
        entries = [_entry(sender=ALICE), _entry(sender=BOB, message_id="message-two")]
        people = _stamp([_proposal()], entries, res=both_named)
        self.assertEqual(people, [])

    def test_unnamed_speaker_gets_no_evidence(self):
        people = _stamp([_proposal()], [_entry()], res=lambda uid: None)
        self.assertEqual(people, [])
        people = _stamp([_proposal()], [_entry()],
                        res=lambda uid: digest.UNAMED_WHO)
        self.assertEqual(people, [])

    def test_commands_and_derived_kinds_get_no_evidence(self):
        people = _stamp([_proposal()], [_entry(cmd=1)])
        self.assertEqual(people, [])
        people = _stamp([_proposal()], [_entry(kind="caption")])
        self.assertEqual(people, [])

    def test_sensitive_quote_is_dropped(self):
        people = _stamp([_proposal(quote="主席这个称呼我喜欢")],
                        [_entry(text="主席这个称呼我喜欢")])
        self.assertEqual(people, [])

    def test_quote_without_a_matching_source_is_dropped(self):
        people = _stamp([_proposal()], [])                      # 根本没这条原文
        self.assertEqual(people, [])
        people = _stamp([_proposal()], [_entry(text="别的话别的内容")])
        self.assertEqual(people, [])
        people = _stamp([_proposal()], [_entry(group="other-group")],
                        group_id=GROUP)
        self.assertEqual(people, [])

    def test_missing_ids_and_bad_timestamps_get_no_evidence(self):
        for bad in ({"message_id": ""}, {"message_id": None}, {"ts": 0},
                    {"ts": -5.0}, {"ts": float("nan")}, {"ts": float("inf")},
                    {"ts": 1100.0 + 1},                     # 比压缩时刻还晚：伪时间
                    {"ts": "1000.0"},                       # 类型不对
                    {"sender": ""}):
            entry = _entry(**{k: v for k, v in bad.items()})
            people = _stamp([_proposal()], [entry], now=1100.0)
            self.assertEqual(people, [], bad)

    def test_proposal_who_must_be_clean_and_present(self):
        for bad_who in ("", None, "一位群友-ish 主席"):
            people = _stamp([_proposal(who=bad_who)], [_entry()])
            self.assertEqual(people, [], bad_who)
        # who 里混进机器编号（16+ 位十六进制）也拒
        people = _stamp([_proposal(who="ABCDEF0123456789")], [_entry()])
        self.assertEqual(people, [])

    def test_incomplete_or_unknown_text_is_ineligible(self):
        """归档被截断或无法确认完整性的原始记录，没有资格当个人原话来源。"""
        row = _entry()
        row.pop("text_complete")
        self.assertEqual(_stamp([_proposal()], [row]), [])
        self.assertEqual(_stamp([_proposal()], [_entry(text_complete=False)]), [])

    def test_quote_containing_source_or_machine_ids_is_rejected(self):
        """quote 字面里嵌着群号/发言人/消息标识或机器编号：证据闸拒收。"""
        for bad_quote, key in (
            (f"group {GROUP} stuff", "group"),
            (f"来自 {ALICE} 的话", "来自"),
            (f"消息 {MSG_ONE} 说过", "消息"),
            ("看到 ABCDEF0123456789 了", "看到"),
        ):
            people = _stamp([_proposal(quote=bad_quote, keywords=[key])],
                            [_entry(text=bad_quote)])
            self.assertEqual(people, [], bad_quote)

    def test_quote_length_bounds(self):
        short = "太短了"                                         # 3 字 < 4
        people = _stamp([_proposal(quote=short, keywords=["太短"])],
                        [_entry(text=short)])
        self.assertEqual(people, [])
        long = "长" * 241
        people = _stamp([_proposal(quote=long, keywords=["长长"])],
                        [_entry(text=long)])
        self.assertEqual(people, [])

    def test_keywords_must_live_in_the_quote(self):
        people = _stamp([_proposal(keywords=["奶茶"])], [_entry()])
        self.assertEqual(people, [])                            # 词不在原话里
        people = _stamp([_proposal(keywords=[])], [_entry()])
        self.assertEqual(people, [])                            # 一个词都没有
        people = _stamp([_proposal(keywords=["咖啡", "阿青"])], [_entry()])
        self.assertEqual(people[0]["keywords"], ["咖啡"])        # 人名不算话题词

    def test_mode_proposal_rides_along_on_admitted_rows(self):
        people = _stamp([_proposal(mode="损友")], [_entry()])
        self.assertEqual(people[0]["mode"], "损友")

    def test_six_row_cap(self):
        entries = [_entry(text=f"原话内容第{i}条", message_id=f"m{i}")
                   for i in range(9)]
        proposals = [_proposal(quote=f"原话内容第{i}条", keywords=["原话内容"])
                     for i in range(9)]
        people = _stamp(proposals, entries)
        self.assertEqual(len(people), 6)


class FreshnessSemanticsTest(unittest.TestCase):
    """时效看来源，不看压缩动作本身。"""

    def test_source_ts_wins_over_compression_time(self):
        people = _stamp([_proposal()], [_entry(ts=1000.0)], now=99999.0)
        self.assertEqual(people[0]["ts"], 1000.0)

    def _first_row(self):
        return _stamp([_proposal()], [_entry()], now=1100.0)[0]

    def test_rephrased_proposal_keeps_source_keywords_and_old_time(self):
        """换个说法复述同一条已核实原话：来源、话题词、时间全部沿用旧的。"""
        old_row = dict(self._first_row())
        old = {"people": [old_row]}
        people = _stamp(
            [_proposal(note="他好像挺讲究咖啡的", keywords=["压根不在原话里的词"])],
            [], now=1200.0, old=old)
        self.assertEqual(len(people), 1)
        kept = people[0]
        self.assertEqual(kept["ts"], 1000.0)
        self.assertEqual(kept["keywords"], KEYWORDS)
        self.assertEqual(kept["evidence"]["message_id"], MSG_ONE)
        self.assertEqual(kept["note"], QUOTE)

    def test_replaying_the_same_message_id_never_renews(self):
        """同一来源消息（同 message_id）带着更晚的时间被再读一遍：仍沿用旧来源时间。"""
        old = {"people": [dict(self._first_row())]}
        people = _stamp([_proposal()], [_entry(ts=2000.0)], now=5000.0, old=old)
        self.assertEqual(people[0]["ts"], 1000.0)
        self.assertEqual(people[0]["evidence"]["ts"], 1000.0)

    def test_a_genuinely_new_message_with_a_new_id_advances_the_time(self):
        """同一句话真的又出现了一次（新 message_id）：取它的来源时间。"""
        old = {"people": [dict(self._first_row())]}
        people = _stamp([_proposal()],
                        [_entry(message_id="message-two", ts=2000.0)],
                        now=2100.0, old=old)
        self.assertEqual(people[0]["ts"], 2000.0)
        self.assertEqual(people[0]["evidence"]["message_id"], "message-two")

    def test_legacy_rows_never_acquire_provenance_by_being_paraphrased(self):
        """旧档里没有来源的条目：就算模型拿它措辞提案，也不会凭空得到证据。"""
        old = {"people": [{"who": "阿青", "note": QUOTE, "ts": 99999.0}]}
        people = _stamp([_proposal()], [], now=1100.0, old=old)
        self.assertEqual(people, [])

    def test_a_different_note_is_never_evidence(self):
        """旧行 note 是性格推测：推测本身绑不回任何原文，复述推测不给来源。"""
        old = {"people": [{"who": "阿青", "note": GUESSED_NOTE, "ts": 500.0}]}
        # 提案拿推测当 quote，但原文里没人说过这句推测 —— 丢
        people = _stamp([_proposal(quote=GUESSED_NOTE)],
                        [_entry()], now=1100.0, old=old)
        self.assertEqual(people, [])
        # 提案给真实原话 + 推测措辞：收的是原话与其来源，推测不进库
        people = _stamp([_proposal(quote=QUOTE, note=GUESSED_NOTE)],
                        [_entry()], now=1100.0, old=old)
        self.assertEqual(people[0]["ts"], 1000.0)
        self.assertEqual(people[0]["note"], QUOTE)


class OldSummaryTextTest(unittest.TestCase):
    """回喂给 reduce 的上一版材料：只给「仍新鲜、已核实来源」的原话。"""

    def test_only_fresh_verified_quotes_are_fed_back(self):
        from unittest.mock import patch
        now = 1_000_000.0
        fresh_ts, stale_ts = now - 100, now - (config.PEOPLE_NOTE_MAX_AGE_DAYS + 1) * 86400
        summary = {"brief": "简报", "data": {"people": [
            {"who": "阿青", "note": "x", "quote": "新鲜原话加咖啡", "keywords": ["咖啡"],
             "ts": fresh_ts, "evidence": {"group": GROUP, "sender": ALICE,
             "message_id": MSG_ONE, "ts": fresh_ts, "quote": "新鲜原话加咖啡"}},
            {"who": "阿青", "note": "x", "quote": "过期原话谈奶茶", "keywords": ["奶茶"],
             "ts": stale_ts, "evidence": {"group": GROUP, "sender": ALICE,
             "message_id": "m-stale", "ts": stale_ts, "quote": "过期原话谈奶茶"}},
            {"who": "阿青", "note": "推测性格旧摘要", "ts": fresh_ts},
        ]}}
        store = digest.GroupDigest()
        store.groups[GROUP] = {"summary": summary, "last_run": 0.0}
        slot = store._slot(GROUP)
        with patch("digest.time.time", return_value=now):
            text = store.old_summary_text(slot, resolver=resolver)
            anonymous = store.old_summary_text(slot, resolver=lambda uid: uid)
        self.assertIn("上一版个人原话", text)
        self.assertIn("新鲜原话加咖啡", text)
        self.assertIn("阿青", text)
        self.assertNotIn("过期原话谈奶茶", text)      # 过期不回喂
        self.assertNotIn("推测性格旧摘要", text)        # 旧推测不回喂
        for ident in (GROUP, ALICE, MSG_ONE, "m-stale"):
            self.assertNotIn(ident, text)              # 来源标识不进模型
        self.assertNotIn("上一版个人原话", anonymous)   # 叫不出当前名字不给


class ArchiveCompletenessTest(unittest.TestCase):
    """归档按 max_text 截断原文：被截断的前缀不许冒充「完整原话」。"""

    def test_clipped_archive_text_cannot_bind_a_quote(self):
        import tempfile
        from archive import MessageArchive
        clipped = "我喜欢咖啡"
        with tempfile.TemporaryDirectory() as tmp:
            ar = MessageArchive(tmp, max_text=len(clipped))
            try:
                ar.append(GROUP, ALICE, "我喜欢咖啡是反话，其实不喜欢",
                          ts=1000.0, message_id=MSG_ONE)
                ar.append(GROUP, ALICE, clipped, ts=1100.0, message_id="m-whole")
                rows, _ = ar.pending(GROUP)
            finally:
                ar.close()
        # 完整性标记随 JSONL 落盘往返
        self.assertEqual(rows[0]["text"], clipped)
        self.assertIs(rows[0]["text_complete"], False)
        self.assertIs(rows[1]["text_complete"], True)
        # 只有被截断的那条时：字面相同也拒收（它丢掉了「其实不喜欢」）
        self.assertEqual(_stamp([_proposal(quote=clipped)], [rows[0]], now=1200.0), [])
        # 边界上正好 max_text 的完整消息照常入库，且来源是它自己
        people = _stamp([_proposal(quote=clipped)], rows, now=1200.0)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["evidence"]["message_id"], "m-whole")
        self.assertEqual(people[0]["ts"], 1100.0)
        # 压缩稿照旧干净：内部标记与标识一个都不进模型
        rendered = digest.render_transcript(rows, resolver=resolver)
        self.assertNotIn("text_complete", rendered)
        for ident in (MSG_ONE, "m-whole", ALICE):
            self.assertNotIn(ident, rendered)

    def test_markdown_export_shows_quotes_without_source_ids(self):
        """每日 MD 写的是「TA 说过的话」，来源标识与嵌套 evidence 一律不出口。"""
        import tempfile
        from archive import MessageArchive
        with tempfile.TemporaryDirectory() as tmp:
            ar = MessageArchive(tmp)
            try:
                data = {"people": [{
                    "who": "阿青", "note": QUOTE, "quote": QUOTE,
                    "keywords": list(KEYWORDS), "ts": 1000.0,
                    "evidence": {"group": GROUP, "sender": ALICE,
                                 "message_id": MSG_ONE, "ts": 1000.0,
                                 "quote": QUOTE}}]}
                path = ar.write_memory(GROUP, "简报", data)
                with open(path, encoding="utf-8") as fp:
                    body = fp.read()
            finally:
                ar.close()
        self.assertIn("人物发言摘录", body)
        self.assertIn("说过", body)
        self.assertIn(QUOTE, body)
        for ident in (ALICE, MSG_ONE, "message_id", "evidence"):
            self.assertNotIn(ident, body)


class CompressIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """走真实 compress_group：只有已处理前缀里的发言能当来源，模型字段全忽略。"""

    async def _compress(self, store, entries, people_rows, gid=GROUP):
        import json as _json
        payload = _json.dumps({"topics": [], "people": people_rows},
                              ensure_ascii=False)

        async def ask(system, user):
            return f"简报正文\n<DIGEST>{payload}</DIGEST>"

        return await digest.compress_group(
            store, gid, entries, ask, resolver=resolver)

    async def test_processed_source_is_bound_and_forged_fields_ignored(self):
        proposal = _proposal()
        proposal["evidence"] = {"group": "x", "sender": "x", "message_id": "x",
                                "ts": 1.0, "quote": QUOTE}
        proposal["ts"] = 1.0
        store = digest.GroupDigest()
        out = await self._compress(store, [_entry()], [proposal])
        self.assertIsNotNone(out)
        bound = out["data"]["people"][0]
        self.assertEqual(bound["ts"], 1000.0)
        self.assertEqual(bound["evidence"]["message_id"], MSG_ONE)
        self.assertEqual(bound["note"], QUOTE)
        self.assertNotIn("forged", str(bound))

    async def test_unprocessed_suffix_is_never_a_source(self):
        """max_blocks 截断后，后半截消息根本没被处理 —— 引用它的提案要丢。"""
        store = digest.GroupDigest(chunk_chars=30, max_blocks=1)
        entries = [_entry(text="先说一堆积攒长度的话")]
        entries += [_entry(text=QUOTE)]
        proposal = _proposal()
        out = await self._compress(store, entries, [proposal])
        self.assertIsNotNone(out)
        self.assertEqual(out["data"]["people"], [])


class PromptContractTest(unittest.TestCase):
    """提示词与代码同一口径：quote 完整照抄、不写性格、不自动续杯。"""

    def test_reduce_asks_for_full_quotes_not_personality(self):
        self.assertIn("完整照抄", digest.SYSTEM_REDUCE)
        self.assertIn('"quote"', digest.SYSTEM_REDUCE)
        self.assertIn('"keywords"', digest.SYSTEM_REDUCE)
        self.assertIn("不许删掉否定、条件或转述对象", digest.SYSTEM_REDUCE)
        self.assertIn("不写性格速写或控诉书", digest.SYSTEM_REDUCE)

    def test_reduce_says_no_auto_renewal(self):
        self.assertIn("续杯", digest.SYSTEM_REDUCE)
        self.assertIn("过期由代码处理", digest.SYSTEM_REDUCE)

    def test_prompt_text_never_names_a_real_incident(self):
        """提示词常量里不许出现真实事故的原话（例句本身就是复读素材）。"""
        for mod in (prompts, digest):
            body = "\n".join(
                str(v) for k, v in vars(mod).items()
                if isinstance(v, str) and k.isupper() and len(v) > 40)
            for token in ("封号", "解封", "蹄髈", "蹄膀"):
                self.assertNotIn(token, body,
                                 f"{mod.__name__} 的提示词常量里出现了 {token}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
