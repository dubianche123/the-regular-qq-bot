"""每日体检（regular/checkup.py）：每种信号拿合成的一天各踩一次，正常的不报。"""
import datetime as dt
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import turns as turns_mod
from interactions import SCHEMA
from regular import checkup
from regular import cli

DAY = "2026-11-03"
G = "GROUP_CHK"
T0 = time.mktime(dt.datetime(2026, 11, 3, 9, 0, 0).timetuple())


def _row(kind, ts, **fields):
    return {"schema": SCHEMA, "event_id": f"e{ts}", "ts": T0 + ts, "kind": kind,
            "group": G, **fields}


def _incoming(ts, user, text, mid, at=False):
    return _row("incoming", ts, user=user, actor="user", message_id=mid, text=text, at=at)


def _delivery(ts, text, *, source=None, user=None, out=None, turn=None,
              generation="model", route="reply", **extra):
    return _row("delivery", ts, user=user, actor="assistant", status="delivered",
                text=text, source_message_id=source, outgoing_id=out or f"o{ts}",
                turn_id=turn, generation=generation, route=route, **extra)


class CheckupSignalsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.idir = os.path.join(base, "interactions")
        self.tdir = os.path.join(base, "turns")
        self.cdir = os.path.join(base, "checkups")
        os.makedirs(self.idir)
        os.makedirs(self.tdir)

    def write(self, rows, turns=(), day=DAY):
        with open(os.path.join(self.idir, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(os.path.join(self.tdir, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps({"schema": turns_mod.SCHEMA, "ts": T0, **t},
                                   ensure_ascii=False) + "\n")

    def run_report(self):
        with patch("wordfilter.has_hit", lambda text: "违禁词" in (text or "")):
            return checkup.report(DAY, interaction_dir=self.idir, turn_dir=self.tdir,
                                  checkup_dir=self.cdir)

    def signals(self, out):
        return [f["signal"] for f in out["findings"]]

    def test_unanswered_and_repeated(self):
        self.write([
            _incoming(0, "LAOREN", "小王 推荐个电影看看呗", "m1", at=True),
            _incoming(60, "LAOREN", "小王 推荐个电影看看", "m2", at=True),
            _incoming(120, "LAOREN", "小王推荐个电影看看呗？", "m3", at=True),
            _delivery(130, "看《教父》吧。", source="m3", user="LAOREN", turn="rep_m3"),
        ], turns=[
            {"turn_id": "tri_m1", "kind": "triage", "message_id": "m1",
             "payload": {}, "asked": ["addressed"], "choices": {"addressed": "let_it_pass"}},
            {"turn_id": "rep_m3", "kind": "reply", "message_id": "m3", "raw": "看《教父》吧。"},
        ])
        out = self.run_report()
        unanswered = [f for f in out["findings"] if f["signal"] == "unanswered"]
        self.assertEqual([f["message_id"] for f in unanswered], ["m1", "m2"])
        repeated = [f for f in out["findings"] if f["signal"] == "repeated"]
        self.assertEqual(len(repeated), 1)
        self.assertIn("3 条", repeated[0]["detail"])
        # 重复提问候选必须挂上关联的 turn_ids（含分诊与生成轮），并生成独立 --id 的存例命令
        self.assertEqual(repeated[0]["turn_ids"], ["tri_m1", "rep_m3"])
        cmds = checkup.case_add_commands(DAY, repeated[0])
        self.assertEqual(len(cmds), 2)
        self.assertNotEqual(cmds[0].split("--id ")[1], cmds[1].split("--id ")[1])
        # 上下文里标出了那一条，前后都有
        self.assertTrue(any(line.startswith("▶") for line in unanswered[0]["context"]))

    def test_short_repeats_and_answered_calls_are_quiet(self):
        self.write([
            _incoming(0, "A", "哈哈哈", "m1"), _incoming(5, "A", "哈哈哈", "m2"),
            _incoming(10, "A", "小王 在吗", "m3", at=True),
            _delivery(15, "在。", source="m3", user="A"),
        ])
        self.assertEqual(self.signals(self.run_report()), [])

    def test_corrected_links_back_to_turn(self):
        self.write([
            _incoming(0, "A", "小王 今天喝啥", "m1", at=True),
            _delivery(5, "车刚打火，回头说。", source="m1", user="A", out="o1", turn="t1"),
            _incoming(20, "A", "我问你喝啥你扯车干嘛", "m2"),
            _row("natural_feedback", 21, user="A", actor="system", source_message_id="m2",
                 target="bot_response", action="repair_briefly", source_reply_id="o1"),
            _row("natural_feedback", 22, user="A", actor="system", source_message_id="m9",
                 target="another_person", action="no_adjustment", source_reply_id="o1"),
        ])
        out = self.run_report()
        (f,) = [f for f in out["findings"] if f["signal"] == "corrected"]
        self.assertEqual(f["turn_ids"], ["t1"])
        self.assertIn("车刚打火", f["detail"])

    def test_bad_reaction_only_right_after_bot(self):
        self.write([
            _delivery(0, "にまますら", turn="t1"),
            _incoming(10, "B", "？？", "m1"),
            _incoming(300, "B", "？？", "m2"),          # 太久以后：不算
            _incoming(301, "C", "你们在说什么意思啊", "m3"),   # 前面没有它的话
        ])
        out = self.run_report()
        self.assertEqual([f["message_id"] for f in out["findings"]
                          if f["signal"] == "bad_reaction"], ["m1"])

    def test_promise_without_reminder_or_lookup(self):
        self.write([
            # t1: 嘴上说去搜，既无 search_supplied 也无 lookup 决策
            _delivery(0, "我去搜搜看，找到再告诉你", source="m1", out="o1", turn="t1"),
            # t2: 真记上了提醒（含（记下了：…）执行回执）→ 不报
            _delivery(10, "好，到点叫你\n（记下了：11-03 21:00）", source="m2", out="o2",
                      turn="t2", remind_applied=True),
            # t3: 真起了查询（search_supplied=True）→ 不报
            _delivery(20, "我去查一下", source="m3", out="o3", turn="t3", search_supplied=True),
            # t4: 本地转后台话术 → 不报
            _delivery(30, "我去查查，等我一会儿。", source="m4", out="o4",
                      generation="local_fallback"),
            # t5: 模型写了〔提醒〕但实际被拒/未生效（无记下回执）→ 必须报只说不做
            _delivery(40, "好，到点叫你", source="m5", out="o5", turn="t5", remind_applied=False),
            # t6: 分诊选了 lookup=search 但被开关/配额挡住，实际没查成 → 必须报只说不做
            _delivery(50, "我去查一下", source="m6", out="o6", turn="t6", search_supplied=False),
            # t7: 门控提前写了 decision(route=search, selected=True)，且同群另一人 U_OTHER 有 followup，但本人 U_A 没查成 → 必须报只说不做
            _row("decision", 59, user="U_A", actor="system", source_message_id="m7",
                 route="search", selected=True),
            _delivery(60, "我去搜一下", user="U_A", source="m7", out="o7", turn="t7",
                      search_supplied=False),
            # t8: 本人 U_C 后台查询完成后产生了本人的 followup 轮次 → 不报
            _delivery(70, "我去搜一下", user="U_C", source="m8", out="o8", turn="t8",
                      search_supplied=False),
        ], turns=[
            {"turn_id": "t1", "kind": "reply", "raw": "我去搜搜看，找到再告诉你"},
            {"turn_id": "t2", "kind": "reply", "raw": "〔提醒 11-03 21:00〕喝水\n好，到点叫你"},
            {"turn_id": "t3", "kind": "reply", "raw": "我去查一下"},
            {"turn_id": "t5", "kind": "reply", "raw": "〔提醒 11-03 21:00〕喝水\n好，到点叫你"},
            {"turn_id": "t6", "kind": "reply", "raw": "我去查一下"},
            {"turn_id": "x6", "kind": "triage", "message_id": "m6",
             "choices": {"lookup": "search"}},
            {"turn_id": "t7", "kind": "reply", "user": "U_A", "message_id": "m7",
             "raw": "我去搜一下"},
            {"turn_id": "t_other_followup", "kind": "reply", "group": G, "user": "U_OTHER",
             "message_id": "m_other", "session_id": f"{G}_U_OTHER", "route": "followup",
             "ts": T0 + 65, "kwargs": {"lookup_note": "done"}, "raw": "别人的查询结果"},
            {"turn_id": "t8", "kind": "reply", "user": "U_C", "message_id": "m8",
             "raw": "我去搜一下"},
            {"turn_id": "t8_followup", "kind": "reply", "group": G, "user": "U_C",
             "message_id": "m8", "session_id": f"{G}_U_C", "route": "followup",
             "ts": T0 + 75, "kwargs": {"lookup_note": "done"}, "raw": "查到了"},
        ])
        out = self.run_report()
        self.assertEqual([f["turn_ids"] for f in out["findings"] if f["signal"] == "promised"],
                         [["t1"], ["t5"], ["t6"], ["t7"]])

    def test_unknown_at_wait_window_and_cross_day_delivery(self):
        adir = os.path.join(self._tmp.name, "archive")
        os.makedirs(adir, exist_ok=True)
        # 1) m_d3d7：at=False，但归档里有 at_ids=["SELF_D3D7"]（D3D7 从未发言）→ 必须报 unanswered 并挂上分诊 turn_id
        # 2) m_fresh：刚发进来 10 秒（尚未满两分钟）→ 不抢报
        # 3) m_late_night：23:59:30 发进来，次日 00:00:10 送达 → 跨日送达窗口内，不报
        end_of_day = time.mktime(dt.datetime(2026, 11, 3, 23, 59, 30).timetuple())
        self.write([
            _incoming(0, "LAOREN", "@群友 为什么没有橘光反转爱尔兰", "m_d3d7", at=False),
            _incoming(10, "LAOREN", "@群友 这一句连分诊都没进", "m_no_turn", at=False),
            _incoming(500, "LAOREN", "小王 刚问你一句", "m_fresh", at=True),
            {"schema": SCHEMA, "event_id": "e_night", "ts": end_of_day, "kind": "incoming",
             "group": G, "user": "LAOREN", "actor": "user", "message_id": "m_night",
             "text": "小王 还在吗", "at": True},
        ], turns=[
            {"turn_id": "tri_d3d7", "kind": "triage", "message_id": "m_d3d7",
             "payload": {}, "asked": ["addressed"], "choices": {"addressed": "let_it_pass"}},
        ])
        # 次日 00:00:10 的送达记录
        next_day = "2026-11-04"
        self.write([
            {"schema": SCHEMA, "event_id": "e_next_deliv", "ts": end_of_day + 40,
             "kind": "delivery", "group": G, "user": "LAOREN", "actor": "assistant",
             "status": "delivered", "text": "在呢", "source_message_id": "m_night",
             "outgoing_id": "o_night", "generation": "model", "route": "reply"},
        ], day=next_day)
        with open(os.path.join(adir, f"{DAY}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": T0, "group": G, "sender": "LAOREN",
                "text": "@群友 为什么没有橘光反转爱尔兰",
                "at": False, "cmd": False, "message_id": "m_d3d7",
                "at_ids": ["SELF_D3D7"],
            }, ensure_ascii=False) + "\n")
            f.write(json.dumps({
                "ts": T0 + 10, "group": G, "sender": "LAOREN",
                "text": "@群友 这一句连分诊都没进",
                "at": False, "cmd": False, "message_id": "m_no_turn",
                "at_ids": ["SELF_D3D7"],
            }, ensure_ascii=False) + "\n")
        # 模拟当前时间在 m_fresh 之后仅 10 秒（但距 m_d3d7 / m_no_turn 已过 500 秒以上）
        out = checkup.report(DAY, interaction_dir=self.idir, turn_dir=self.tdir,
                             archive_dir=adir, checkup_dir=self.cdir, now=T0 + 510)
        unanswered = [f for f in out["findings"] if f["signal"] == "unanswered"]
        self.assertEqual([f["message_id"] for f in unanswered], ["m_d3d7", "m_no_turn"])
        self.assertIn("从没说过话的对象（D3D7）", unanswered[0]["detail"])
        self.assertEqual(unanswered[0]["turn_ids"], ["tri_d3d7"])
        # 无生成、无分诊的未知 @ 候选（turn_ids=[]）也必须给出 --message 存例命令
        self.assertEqual(unanswered[1]["turn_ids"], [])
        no_turn_cmds = checkup.case_add_commands(DAY, unanswered[1])
        self.assertEqual(len(no_turn_cmds), 1)
        self.assertIn("regular case add --message m_no_turn --id", no_turn_cmds[0])

    def test_catchphrase_against_baseline_and_distinct_ids_across_days(self):
        rows = [_delivery(i * 60, f"我刚醒没多久，第{i}件事", user=f"U{i}", turn=f"t{i}")
                for i in range(4)]
        rows.append(_delivery(500, "这锅我背这锅我背", user="U9"))
        self.write(rows)
        out = self.run_report()
        (f,) = [f for f in out["findings"] if f["signal"] == "catchphrase"]
        self.assertIn("刚醒没多久", f["detail"])
        self.assertEqual(len(f["turn_ids"]), 4)
        # 同一候选的多条 turn 生成的 regular case add 命令必须有各自独立的 --id
        md = checkup.render(out)
        import re
        case_ids = re.findall(r"--id\s+(\S+?)`", md)
        self.assertEqual(len(case_ids), 3)
        self.assertEqual(len(set(case_ids)), 3)

        # 另一天再犯同一个口头禅，finding ID 必须包含日期、不继承旧标注
        next_day = "2026-11-12"
        self.write(rows, day=next_day)
        out_next = checkup.report(next_day, interaction_dir=self.idir, turn_dir=self.tdir,
                                  checkup_dir=self.cdir)
        (f_next,) = [x for x in out_next["findings"] if x["signal"] == "catchphrase"]
        self.assertNotEqual(f["id"], f_next["id"])

    def test_catchphrase_quiet_when_baseline_was_just_as_high(self):
        rows = [_delivery(i * 60, f"我刚醒没多久，第{i}件事", user=f"U{i}") for i in range(4)]
        self.write(rows)
        for back in range(1, 4):
            day = (dt.date.fromisoformat(DAY) - dt.timedelta(days=back)).isoformat()
            self.write(rows, day=day)
        self.assertNotIn("catchphrase", self.signals(self.run_report()))

    def test_blind_only_for_its_own_words(self):
        """11-04 11:39 / 11:43：它两次说「只看到图片标记」，第二次对着的是它自己画的蛙。"""
        self.write([
            _delivery(0, "我这边只看到图片标记，图本体没显示出来", turn="t1", user="U1"),
            _delivery(60, "我这边看不到图，等会儿再试试", generation=None),
        ])
        out = self.run_report()
        (f,) = [f for f in out["findings"] if f["signal"] == "blind"]
        self.assertEqual(f["turn_ids"], ["t1"])
        self.assertEqual(f["marker"], "图片标记")

    def test_context_shows_its_own_drawings(self):
        """11-04 11:43：它说看不见的那张蛙是它自己画的，体检前后文里那张图却只剩一行空白。"""
        self.write([
            _incoming(0, "U1", "小王 画只戴眼镜的蛙", "m1", at=True),
            _delivery(20, "", source="m1", user="U1", route="image"),
            _delivery(30, "", source="m9", user="U1", route="image"),
            _delivery(60, "我这边只看到图片标记，图本体没显示出来", turn="t2", user="U1"),
        ], turns=[
            {"turn_id": "t0", "kind": "reply", "message_id": "m1", "ts": T0 + 5,
             "raw": "〔画〕一只青蛙\n安排"},
            {"turn_id": "t1", "kind": "reply", "message_id": "m1", "ts": T0 + 10,
             "raw": "〔画〕一只戴黑框眼镜的青蛙\n安排"},
            {"turn_id": "t3", "kind": "reply", "message_id": "m1", "ts": T0 + 40,
             "raw": "〔画〕一只戴墨镜的青蛙"},
        ])
        (f,) = [f for f in self.run_report()["findings"] if f["signal"] == "blind"]
        self.assertTrue(any(line.endswith("：[图：一只戴黑框眼镜的青蛙]") for line in f["context"]))
        self.assertTrue(any(line.endswith("：[图]") for line in f["context"]))

    def test_not_addressed_is_a_bad_reaction_not_an_unanswered_call(self):
        """11-04 11:49「没问你小王，问的罗老板」：带着名字，却是叫它别插嘴。"""
        self.write([
            _delivery(0, "刚发的就是那只戴黑框眼镜的青蛙图", user="U1"),
            _incoming(11, "U2", "没问你小王，问的罗老板", "m1", at=True),
        ])
        sig = self.signals(self.run_report())
        self.assertIn("bad_reaction", sig)
        self.assertNotIn("unanswered", sig)

    def test_catchphrase_skips_local_lines_and_reports_one_sentence_once(self):
        """11-04「这张被图片审核拦下来了，换个画法吧」是本地固定话术，被切成七段 6 字片段各报一条。"""
        line = "这张被图片审核拦下来了，换个画法吧"
        self.write([_delivery(i * 60, line, user=f"U{i}", generation=None) for i in range(4)])
        self.assertNotIn("catchphrase", self.signals(self.run_report()))
        self.write([_delivery(1000 + i * 60, line, user=f"U{i}") for i in range(4)])
        (f,) = [f for f in self.run_report()["findings"] if f["signal"] == "catchphrase"]
        self.assertIn("这张被图片审核拦下来了", f["detail"])

    def test_repeated_ignores_the_quote_prefix(self):
        """两次引用同一张图、提不同要求：引用前缀把相似度撑过线，被当成同一句问了两遍。"""
        quote = "[回复小王：「[图：一只明黄色小熊站在虚构的中式宫殿屋顶上，朱红屋檐、金色琉璃瓦、飞檐翘角]」] "
        self.write([
            _incoming(0, "U1", quote + "小王，再让它穿上红短袖", "m1", at=True),
            _incoming(60, "U1", quote + "小王，再让他手里拿着包子", "m2", at=True),
            _delivery(5, "穿上了", source="m1"), _delivery(65, "拿上了", source="m2"),
        ])
        self.assertNotIn("repeated", self.signals(self.run_report()))

    def test_sensitive_and_undelivered_raw(self):
        self.write([
            _delivery(0, "这句有违禁词", turn="t1"),
            _delivery(10, "（刚才的话没能发出来）", turn="t2", fallback=True),
        ], turns=[{"turn_id": "t1", "kind": "reply", "raw": "这句有违禁词"},
                  {"turn_id": "t2", "kind": "reply", "raw": "正常"},
                  {"turn_id": "t3", "kind": "reply", "raw": "被拦下的违禁词"}])
        out = self.run_report()
        self.assertEqual(sorted(t for f in out["findings"] if f["signal"] == "sensitive"
                                for t in f["turn_ids"]), ["t1", "t2", "t3"])

    def test_labels_drive_precision_and_ids_are_stable(self):
        self.write([_incoming(0, "A", "小王 在吗", "m1", at=True)])
        first = self.run_report()
        checkup.write(first, self.cdir)
        fid = first["findings"][0]["id"]
        self.assertEqual(self.run_report()["findings"][0]["id"], fid)
        found = checkup.find_finding(self.cdir, fid)
        checkup.add_label(self.cdir, fid, "no", signal=found["signal"])
        checkup.add_label(self.cdir, fid, "yes", signal=found["signal"])   # 改口以最新为准
        out = self.run_report()
        self.assertEqual(out["findings"][0]["verdict"], "yes")
        self.assertEqual(out["precision"]["unanswered"]["precision"], 1.0)
        md = checkup.render(out)
        self.assertIn("100%", md)
        self.assertIn(fid, md)

    def test_empty_day_is_fine(self):
        out = self.run_report()
        self.assertEqual(out["findings"], [])
        self.assertIn("没有可疑", checkup.render(out))

    def test_recall_empty_signal_and_policy_validation(self):
        self.write([
            _incoming(0, "LAOREN", "小王 阿强之前说的奶茶是什么来着", "m_empty", at=True),
            _incoming(20, "LAOREN", "小王 之前说的显卡怎么了", "m_hit", at=True),
            _delivery(3, "我真记不清了", source="m_empty", user="LAOREN", turn="rep1"),
            _delivery(23, "显卡驱动崩了", source="m_hit", user="LAOREN", turn="rep2"),
        ], turns=[
            {"turn_id": "rec_empty", "kind": "recall", "group": G, "user": "LAOREN",
             "message_id": "m_empty", "query": "阿强之前说的奶茶是什么来着",
             "picked": [], "promise": None, "skipped": ""},
            {"turn_id": "rec_hit", "kind": "recall", "group": G, "user": "LAOREN",
             "message_id": "m_hit", "query": "之前说的显卡怎么了",
             "picked": [{"message_id": "arch1", "score": 10}], "promise": None, "skipped": ""},
        ])
        out = self.run_report()
        rec_findings = [f for f in out["findings"] if f["signal"] == "recall_empty"]
        self.assertEqual(len(rec_findings), 1)
        self.assertEqual(rec_findings[0]["turn_ids"], ["rec_empty"])
        self.assertIn("一条都没翻到", rec_findings[0]["detail"])
        # 策略边界校验：不许越界数值，不许往词表里加新词
        with self.assertRaises(ValueError):
            checkup.validate_policy({"reaction_seconds": 9999})
        with self.assertRaises(ValueError):
            checkup.validate_policy({"confused_markers": [*checkup.CONFUSED_MARKERS, "新加的词"]})
        with self.assertRaises(ValueError):
            checkup.validate_policy({"unknown_key": 1})


class CheckupCliTest(unittest.TestCase):
    def test_cli_writes_report_and_labels(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        idir = os.path.join(tmp.name, "i")
        os.makedirs(idir)
        with open(os.path.join(idir, f"{DAY}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(_incoming(0, "A", "小王 在吗", "m1", at=True)) + "\n")
        import config
        with patch.object(config, "INTERACTION_LOG_DIR", idir), \
                patch.object(config, "TURN_LOG_DIR", os.path.join(tmp.name, "t")), \
                patch.object(config, "ARCHIVE_DIR", os.path.join(tmp.name, "a")), \
                patch.object(config, "STATE_FILE", os.path.join(tmp.name, "state.json")), \
                patch.object(config, "CHECKUP_DIR", os.path.join(tmp.name, "c")), \
                patch.object(cli, "load_config", lambda directory: config), \
                patch("builtins.print"):
            self.assertEqual(cli.main(["checkup", "--day", DAY]), 0)
            out = json.loads(open(os.path.join(tmp.name, "c", f"{DAY}.json"),
                                  encoding="utf-8").read())
            fid = out["findings"][0]["id"]
            self.assertEqual(cli.main(["checkup", "--label", fid, "yes"]), 0)
            self.assertEqual(cli.main(["checkup", "--label", "nope", "yes"]), 1)
        self.assertTrue(os.path.exists(os.path.join(tmp.name, "c", f"{DAY}.md")))

    def test_panel_checkup_page_and_label(self):
        from types import SimpleNamespace
        from aiohttp.web_exceptions import HTTPFound
        import panel
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        idir = os.path.join(tmp.name, "interactions")
        os.makedirs(idir)
        with open(os.path.join(idir, f"{DAY}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(_incoming(0, "A", "小王 推荐个电影看看呗", "m1", at=True)) + "\n")
            f.write(json.dumps(_incoming(30, "A", "小王 推荐个电影看看", "m2", at=True)) + "\n")
        tdir = os.path.join(tmp.name, "turns")
        os.makedirs(tdir)
        with open(os.path.join(tdir, f"{DAY}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"schema": turns_mod.SCHEMA, "turn_id": "turn_alpha", "kind": "triage", "message_id": "m1"}) + "\n")
            f.write(json.dumps({"schema": turns_mod.SCHEMA, "turn_id": "turn_beta", "kind": "triage", "message_id": "m2"}) + "\n")
        cfg = SimpleNamespace(DATA_DIR=tmp.name)
        req = SimpleNamespace(query={"day": DAY})
        html = panel._checkup(cfg, req, "csrf-tok")
        self.assertIn("每日体检", html)
        self.assertIn("叫了没回", html)
        # 面板多 turn 存例命令（例如 repeated 候选含 2 条 turn）必须生成各不相同的 --id，绝不重名覆盖
        import re
        repeated_out = [x for x in json.loads(open(os.path.join(tmp.name, "checkups", f"{DAY}.json"),
                                                   encoding="utf-8").read())["findings"]
                        if x["signal"] == "repeated"][0]
        expected_cmds = checkup.case_add_commands(DAY, repeated_out)
        self.assertEqual(len(expected_cmds), 2)
        for cmd in expected_cmds:
            self.assertIn(f"<code>{cmd}</code>", html)
        out = json.loads(open(os.path.join(tmp.name, "checkups", f"{DAY}.json"),
                              encoding="utf-8").read())
        fid = out["findings"][0]["id"]

        async def post():
            return {"csrf": "csrf-tok", "id": fid, "verdict": "yes", "day": DAY}

        with self.assertRaises(HTTPFound):
            import asyncio
            asyncio.run(panel._checkup_label(cfg, SimpleNamespace(post=post), "csrf-tok"))
        html_after = panel._checkup(cfg, req, "csrf-tok")
        self.assertIn("已标 yes", html_after)
        self.assertIn("100%", html_after)


if __name__ == "__main__":
    unittest.main()
