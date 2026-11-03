# -*- coding: utf-8 -*-
"""自我迭代工具链全套离线测试（docs/self-iteration.md ③④⑤⑦）：
用例库与种子用例、确定性检查、规则账本与消融、子进程回放、消息层引用/@回放、盲评与整天指标、窄口子措辞搜索。
"""
import asyncio
import copy
import json
import os
import random
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import archive as archive_mod
import bot
import config
import delivery
import interactions
import prompts
import quotes
import runtime
import storage
import turns as turns_mod
from regular import cases as cases_mod
from regular import cli
from regular import decisions
from regular import eval as eval_mod
from regular import replay_worker
from regular import rules as rules_mod


class CasesAndChecksTest(unittest.TestCase):
    def test_seed_cases_cover_all_documented_batches(self):
        self.assertEqual(len(cases_mod.SEED_CASES), 60)
        for case in cases_mod.SEED_CASES:
            cases_mod.validate_case(case)
        deflect = [c for c in cases_mod.SEED_CASES if "deflect" in c.get("tags", ())]
        remind = [c for c in cases_mod.SEED_CASES if "remind" in c.get("tags", ())]
        image = [c for c in cases_mod.SEED_CASES if "image" in c.get("tags", ())]
        banter = [c for c in cases_mod.SEED_CASES if "banter" in c.get("tags", ())]
        msg_depth = [c for c in cases_mod.SEED_CASES if c.get("depth") == "message"]
        self.assertEqual(len(deflect), 9)
        self.assertEqual(len(remind), 10)
        self.assertEqual(len(image), 3)
        self.assertEqual(len(banter), 20)
        self.assertEqual(len(msg_depth), 1)
        # 账本上每条规则都至少挂上一条种子用例，拿掉它重跑才有东西可比
        for rule in rules_mod.load_rules():
            linked = [c["id"] for c in cases_mod.SEED_CASES
                      if rules_mod.matches_case(rule, c["id"], c.get("tags"))]
            self.assertTrue(linked, rule["id"])

    def test_banter_hints_sit_on_both_sides(self):
        # 「刚开过口 / 不爱被搭话」只放在不该接的那一边，考的就成了「看见提示就〔不接〕」
        banter = [c for c in cases_mod.SEED_CASES if "banter" in c.get("tags", ())]
        hinted = [c["checks"][0]["replied"] for c in banter
                  if c["turn"]["kwargs"].get("unprompted_hint")]
        self.assertIn(True, hinted)
        self.assertIn(False, hinted)

    def test_deterministic_checks_all_kinds(self):
        checks = [
            {"must": "黑朗姆|橙皮"},
            {"must_not": "刚醒|洗衣|车刚"},
            {"tag_absent": "不接"},
            {"replied": True},
            {"max_chars": 60},
            {"judge": "顺着那杯酒接"},
        ]
        good = cases_mod.check_reply("这杯底子是黑朗姆，橙皮只管香", checks)
        self.assertTrue(good["passed"])
        bad = cases_mod.check_reply("我刚醒，先去洗衣服了", checks)
        self.assertFalse(bad["passed"])
        reasons = " ".join(d["reason"] for d in bad["details"] if not d["passed"])
        self.assertIn("刚醒", reasons)

        # 提醒时间精确核对
        r_checks = [{"tag_present": "提醒"}, {"remind_time": "11-03 09:00"}]
        self.assertTrue(
            cases_mod.check_reply("〔提醒 11-03 09:00〕提醒大家小王回来了\n好，明早九点喊大家", r_checks)["passed"]
        )
        self.assertFalse(
            cases_mod.check_reply("〔提醒 11-03 10:00〕提醒大家小王回来了\n好", r_checks)["passed"]
        )
        # 写成「11-3 9:00」线上照样记得上（reminders.resolve 认），检查不能因为字面不同判错
        self.assertTrue(
            cases_mod.check_reply("〔提醒 11-3 9:00〕提醒大家小王回来了\n好", r_checks)["passed"]
        )

        # 〔不接〕检查
        b_checks = [{"replied": False}, {"tag_present": "不接"}]
        self.assertTrue(cases_mod.check_reply("〔不接〕", b_checks)["passed"])
        self.assertFalse(cases_mod.check_reply("我也去吃食堂", b_checks)["passed"])

        # 生成失败返回 None
        self.assertFalse(cases_mod.check_reply(None, checks)["passed"])

    def test_create_case_from_turn_and_selector_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "turns")
            cdir = os.path.join(tmp, "cases")
            tlog = turns_mod.TurnLog(tdir, enabled=True)
            row = tlog.append(
                "reply",
                kwargs={"user_text": "为什么没有橘光反转爱尔兰", "context_hint": "- 你：黑朗姆"},
                session=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
                media={"draw": True, "voice": False, "remind": True},
                raw="老任我刚醒",
            )
            tlog.close()
            case = cases_mod.create_case_from_turn(
                tdir, row["turn_id"], "2026-11-03-orange-irish",
                incident="docs/quotes.md", tags=["quotes", "deflect"],
                checks=[{"must_not": "刚醒"}],
            )
            cases_mod.save_case(cdir, case)
            loaded = cases_mod.load_cases(cdir, selector="quotes")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], "2026-11-03-orange-irish")
            self.assertEqual(loaded[0]["reference"], "老任我刚醒")


class RuleLedgerTest(unittest.TestCase):
    def test_rules_parsed_and_ablate_reduces_head(self):
        rules = rules_mod.load_rules()
        ids = {r["id"] for r in rules}
        self.assertIn("deflect-in-place", ids)
        self.assertIn("remind-tag-rules", ids)
        self.assertIn("banter-decide-self", ids)

        deflect = rules_mod.get_rule("deflect-in-place")
        self.assertEqual(deflect["date"], "2026-11-03")
        self.assertEqual(deflect["doc"], "docs/deflection.md")
        self.assertTrue(rules_mod.matches_case(deflect, "deflect-01-expose"))
        self.assertFalse(rules_mod.matches_case(deflect, "remind-01-tomorrow-9am"))

        # 在副本上测试 ablate，不改动全局 prompts
        fake_prompts = type("P", (), {k: getattr(prompts, k) for k in dir(prompts) if not k.startswith("__")})()
        before = rules_mod.head_stats(fake_prompts)["total_chars"]
        removed = rules_mod.ablate("deflect-in-place", prompts_mod=fake_prompts)
        after = rules_mod.head_stats(fake_prompts)["total_chars"]
        self.assertLess(after, before)
        self.assertEqual(before - after, len(removed["text"]))
        self.assertNotIn("岔开是就地拆台", fake_prompts.PROMPT_NORMAL)


class ArchiveAndMessageReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_archive_records_raw_at_and_quote_indices_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = archive_mod.MessageArchive(tmp, keep_days=0)
            self.addCleanup(arch.close)
            ix = interactions.InteractionLog(os.path.join(tmp, "ix"), enabled=True)
            self.addCleanup(ix.close)
            state = storage.StateStore(os.path.join(tmp, "state.json"))
            gid = "G_MSG_REPLAY"
            self_oid = "SELF_D3D7"

            async def fake_model(messages, max_tokens, **kw):
                if prompts.BANTER_MARK in messages[-1]["content"]:
                    return "〔不接〕"
                return "因为底子是黑朗姆，橙皮没权限反转国籍"

            with patch.object(runtime, "STATE", state), \
                    patch.object(runtime, "QUOTES", quotes.QuoteBook(state)), \
                    patch.object(runtime, "ARCHIVE", arch), \
                    patch.object(runtime, "INTERACTIONS", ix), \
                    patch.object(runtime, "FEEDBACK", decisions.FeedbackTracker(120.0, 180.0)), \
                    patch.object(decisions.providers, "call_decision", AsyncMock(return_value=None)), \
                    patch.object(bot, "SENDER", delivery.Sender(fallback=bot._blocked_reply)), \
                    patch.object(runtime.KNOWLEDGE, "render", lambda *a, **k: ""), \
                    patch.object(bot, "call_model", fake_model):
                bot._remember_sent(
                    gid, {"id": "b0", "ext_info": {"ref_idx": "REFIDX_drink=="}},
                    "老任这杯走沉稳路线：黑朗姆加浓缩咖啡、少许枫糖浆，再挤点橙皮香",
                )
                msg = replay_worker.archive_row_to_message(
                    {"sender": "LAOREN", "text": f"<@{self_oid}> 为什么没有橘光反转爱尔兰",
                     "at_ids": [self_oid], "msg_idx": "REFIDX_q1==", "ref_msg_idx": "REFIDX_drink=="},
                    gid,
                    AsyncMock(return_value={"id": "r1", "ext_info": {"ref_idx": "REFIDX_r1=="}}),
                )
                fake_self = type("S", (), {"robot": type("R", (), {"id": "BOT_APP", "name": "regular"})()})()
                await bot.GroupBot.handle_group_msg(fake_self, msg, is_at=False)

                rows = arch.since(gid, 0)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["at_ids"], [self_oid])
                self.assertEqual(rows[0]["msg_idx"], "REFIDX_q1==")
                self.assertEqual(rows[0]["ref_msg_idx"], "REFIDX_drink==")

                # 用归档行做消息层回放（depth=message），验证能认出引用与 @ 并正经回复
                case = next(c for c in cases_mod.SEED_CASES if c["id"] == "quote-orange-irish-msg")
                res = await replay_worker.replay_item(
                    case, n=1, bot=bot, runtime=runtime, config=config)
                self.assertEqual(res["errors"], 0)
                self.assertIn("黑朗姆", res["samples"][0])

                # 验证归档行写的是 at=True（无 is_at、无 <@>）且带固定 message_id 时，重复抽样 n=3 不会被 processed_msg_ids 去重吞掉
                at_case = {
                    "id": "archive-at-and-triage-msg",
                    "depth": "message",
                    "messages": [
                        {"sender": "A_USER", "text": "在吗", "at": True,
                         "message_id": "fixed_msg_001",
                         "triage_choices": {"next_move": "continue_normally", "request_kind": "sincere"}},
                    ],
                }
                at_res = await replay_worker.replay_item(
                    at_case, n=3, bot=bot, runtime=runtime, config=config)
                self.assertEqual(at_res["errors"], 0)
                self.assertEqual(
                    at_res["samples"],
                    ["因为底子是黑朗姆，橙皮没权限反转国籍"] * 3,
                )

    async def test_case_add_from_message_without_turn_or_triage(self):
        # 无生成、无分诊的事故可通过 regular case add --message 从归档一键存成消息层用例并重复抽样回放。
        # 现场照线上真实的样子造：被引用的是小王前一晚送达的那句（交互日志 delivery 行的 ref_idx），
        # 归档行只有 at_ids / mentions / msg_idx / ref_msg_idx，不带引用原文；
        # 线上已经认出来的小王的 @ 在 state.json 里（11-03 09:00 那次就是这样）。
        with tempfile.TemporaryDirectory() as tmp:
            adir, idir, tdir, cdir = (os.path.join(tmp, x)
                                      for x in ("archive", "interactions", "turns", "cases"))
            for d in (adir, idir, tdir):
                os.makedirs(d, exist_ok=True)
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump({"groups": {"G_ARC": {"self_openid": "SELF_D3D7"}}}, f)
            with open(os.path.join(idir, "2026-11-02.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": 500.0, "kind": "delivery", "status": "delivered", "actor": "assistant",
                    "group": "G_ARC", "text": "这杯叫橘光爱尔兰", "ref_idx": "REFIDX_bot1==",
                }, ensure_ascii=False) + "\n")
            with open(os.path.join(adir, "2026-11-03.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": 1000.0, "group": "G_ARC", "sender": "U_PREV",
                    "text": "今天调什么酒", "message_id": "m_prev",
                }, ensure_ascii=False) + "\n")
                f.write(json.dumps({
                    "ts": 1010.0, "group": "G_ARC", "sender": "LAOREN",
                    "text": "[回复小王：「这杯叫橘光爱尔兰」] 为什么没有橘光反转爱尔兰",
                    "message_id": "m_no_turn", "at_ids": ["SELF_D3D7"],
                    "msg_idx": "REFIDX_q1==", "ref_msg_idx": "REFIDX_bot1==",
                }, ensure_ascii=False) + "\n")

            with patch.object(config, "ARCHIVE_DIR", adir), \
                    patch.object(config, "INTERACTION_LOG_DIR", idir), \
                    patch.object(config, "TURN_LOG_DIR", tdir), \
                    patch.object(config, "CASES_DIR", cdir), \
                    patch.object(config, "STATE_FILE", state_file), \
                    patch.object(cli, "load_config", lambda directory: config), \
                    patch("builtins.print"):
                rc = cli.main(["case", "add", "--message", "m_no_turn",
                               "--id", "2026-11-03-unanswered-no-turn"])
                self.assertEqual(rc, 0)

            loaded = cases_mod.load_cases(cdir, selector="2026-11-03-unanswered-no-turn")
            self.assertEqual(len(loaded), 1)
            saved_case = loaded[0]
            self.assertEqual(saved_case["depth"], "message")
            self.assertEqual(saved_case["source_message_id"], "m_no_turn")
            self.assertEqual(saved_case["quotes"],
                             [{"ref": "REFIDX_bot1==", "who": "self", "text": "这杯叫橘光爱尔兰"}])
            self.assertEqual(saved_case["self_openid"], "SELF_D3D7")

            fake_call = AsyncMock(return_value="OFFLINE_OK")
            with patch.object(bot, "call_model", fake_call):
                res = await replay_worker.replay_item(
                    saved_case, n=3, bot=bot, runtime=runtime, config=config)
            self.assertEqual(res["errors"], 0)
            self.assertEqual(res["samples"], ["OFFLINE_OK", "OFFLINE_OK", "OFFLINE_OK"])
            # 回放时小王看得见引用的那杯酒，也认得出这是 @ 它
            seen = [c.args[0][-1]["content"] for c in fake_call.call_args_list]
            self.assertTrue(any("[回复" in t and "这杯叫橘光爱尔兰" in t for t in seen), seen)

    async def test_message_replay_never_really_sends_voice_or_draws(self):
        # 消息层回放走真的 reply_in_session：它写了〔语音〕〔画〕，不许真合成、真画图 ——
        # 那会花钱，语音还会记进部署目录里线上那份语音额度
        real_voice = AsyncMock(side_effect=AssertionError("回放里真发了语音"))
        real_image = AsyncMock(side_effect=AssertionError("回放里真画了图"))
        for tag, reply, stub in (("voice", "〔语音〕\n早上好", "_no_voice"),
                                 ("image", "〔画〕一只戴墨镜的橘猫\n来了", "_no_image")):
            spy = AsyncMock(wraps=getattr(replay_worker, stub))
            case = {"id": f"media-{tag}", "depth": "message", "messages": [
                {"sender": "U_MEDIA", "text": "小王来一个", "at": True, "message_id": f"m_{tag}"}]}
            with patch.object(bot._VOICE_OUT, "ready", lambda: True), \
                    patch.object(bot._VOICE_OUT, "attempt", real_voice), \
                    patch.object(bot.image_generation, "available", lambda: True), \
                    patch.object(bot.image_generation, "precheck", lambda *a, **k: "normal"), \
                    patch.object(bot.image_generation, "generate_and_send", real_image), \
                    patch.object(replay_worker, stub, spy), \
                    patch.object(bot, "call_model", AsyncMock(return_value=reply)):
                res = await replay_worker.replay_item(case, n=1, bot=bot, runtime=runtime, config=config)
            self.assertEqual(res["errors"], 0)
            self.assertTrue(spy.await_count, f"{tag}: 回放没走到发{tag}那一步，这条测试没测到东西")
        real_voice.assert_not_called()
        real_image.assert_not_called()

    async def test_concurrent_replay_does_not_leak_media_flags_or_config(self):
        # 复现并验证 P1-1：A 用例（remind=False）首次失败重试时，期间 B 用例（remind=True）并发插入，
        # A 第二次重试仍须看到 remind=False，且结束后 config.REMINDER_ENABLED 必须恢复原值。
        seen_heads = {"A": [], "B": []}
        a_attempts = {"n": 0}
        b_entered = asyncio.Event()

        async def interleaved_model(messages, max_tokens, **kw):
            sys_head = messages[0]["content"]
            user_msg = messages[-1]["content"]
            if "CASE_A" in user_msg:
                a_attempts["n"] += 1
                seen_heads["A"].append("〔提醒" in sys_head)
                if a_attempts["n"] == 1:
                    await b_entered.wait()
                    return None
                return "A_OK"
            if "CASE_B" in user_msg:
                seen_heads["B"].append("〔提醒" in sys_head)
                b_entered.set()
                await asyncio.sleep(0.01)
                return "B_OK"
            return "OK"

        case_a = {
            "id": "conc-a",
            "depth": "generate",
            "turn": {
                "kwargs": {"user_text": "CASE_A"},
                "session": [],
                "media": {"draw": False, "voice": False, "remind": False},
            },
        }
        case_b = {
            "id": "conc-b",
            "depth": "generate",
            "turn": {
                "kwargs": {"user_text": "CASE_B"},
                "session": [],
                "media": {"draw": True, "voice": True, "remind": True},
            },
        }
        orig_remind = config.REMINDER_ENABLED
        with patch.object(bot, "call_model", interleaved_model):
            out = await replay_worker.replay_batch([case_a, case_b], n=1, concurrency=6, retries=1)
        self.assertEqual(config.REMINDER_ENABLED, orig_remind)
        self.assertEqual(out[0]["samples"], ["A_OK"])
        self.assertEqual(out[1]["samples"], ["B_OK"])
        # A 两次尝试都必须不含〔提醒〕规则，B 必须含〔提醒〕规则
        self.assertEqual(seen_heads["A"], [False, False])
        self.assertEqual(seen_heads["B"], [True])

    async def test_triage_case_save_and_replay_chain(self):
        # 验证 P2-4：分诊记录可一键存成 depth=triage 用例，并通过 _run_triage_once + choice 检查执行回放
        with tempfile.TemporaryDirectory() as tmp:
            tlog = turns_mod.TurnLog(tmp, enabled=True)
            row = tlog.append(
                "triage", group="G1", user="U1", message_id="m_tri",
                payload={"latest_message": {"speaker": "member_1", "text": "小王推荐个电影"}},
                asked=["addressed", "lookup"],
                choices={"addressed": "answer", "lookup": "none"},
                stale=False,
            )
            tlog.close()
            # 存用例多半是因为那次判错了：不写该判成什么，不能拿当时线上的答案当标准
            with self.assertRaises(ValueError):
                cases_mod.create_case_from_turn(
                    tmp, row["turn_id"], "2026-11-03-triage-1", incident="docs/when-to-reply.md")
            case = cases_mod.create_case_from_turn(
                tmp, row["turn_id"], "2026-11-03-triage-1", incident="docs/when-to-reply.md",
                expect_recorded=True)
            self.assertEqual(case["depth"], "triage")
            self.assertEqual(case["triage"]["asked"], ["addressed", "lookup"])

            fake_decision = AsyncMock(side_effect=lambda payload_json, qs:
                replay_worker._choices_to_decision_body(
                    {"addressed": "answer", "lookup": "none"}, qs))
            with patch("regular.decisions.providers.call_decision", fake_decision):
                res = await replay_worker.replay_item(case, n=1, bot=bot, runtime=runtime, config=config)
            self.assertEqual(res["errors"], 0)
            chk = cases_mod.check_reply(res["samples"][0], case["checks"])
            self.assertTrue(chk["passed"])
            fail_chk = cases_mod.check_reply(
                res["samples"][0], [{"choice": {"addressed": "let_it_pass"}}])
            self.assertFalse(fail_chk["passed"])

    async def test_mixed_message_and_triage_concurrency_does_not_leak(self):
        # 验证第 2 轮 P1-1：混合批次（message + triage）并发回放时，triage 绝不会读到 message 预置的 triage_choices
        msg_case = {
            "id": "mix-msg",
            "depth": "message",
            "messages": [
                {
                    "sender": "U_MSG",
                    "text": "小王 顺便问一句",
                    "at": False,
                    "message_id": "m_mix_1",
                    "triage_choices": {"addressed": "answer", "lookup": "none"},
                }
            ],
        }
        triage_case = {
            "id": "mix-tri",
            "depth": "triage",
            "triage": {
                "payload": {"latest_message": {"speaker": "member_1", "text": "查一下天气"}},
                "asked": ["addressed", "lookup"],
            },
        }
        triage_calls = []

        async def slow_model(messages, max_tokens, **kw):
            await asyncio.sleep(0.02)
            return "消息层回复"

        async def real_decision(payload_json, qs, **kw):
            await asyncio.sleep(0.01)
            triage_calls.append(payload_json)
            return replay_worker._choices_to_decision_body(
                {"addressed": "let_it_pass", "lookup": "search"}, qs)

        with patch.object(bot, "call_model", slow_model), \
                patch("regular.decisions.providers.call_decision", real_decision):
            out = await replay_worker.replay_batch(
                [msg_case, triage_case], n=1, concurrency=6, retries=0)
        self.assertEqual(out[0]["samples"], ["消息层回复"])
        # triage_case 必须真正调用 real_decision 并拿到 let_it_pass + search，绝不能被 msg_case 的 answer + none 污染
        self.assertEqual(len(triage_calls), 1)
        self.assertEqual(
            json.loads(out[1]["samples"][0]),
            {"addressed": "let_it_pass", "lookup": "search"},
        )


class EvalAndSubprocessTest(unittest.IsolatedAsyncioTestCase):
    def test_worker_subprocess_runs_and_ablates(self):
        code_dir = str(rules_mod._prompts_path().parent)
        sample_case = [cases_mod.SEED_CASES[0]]
        env = {"REGULAR_FAKE_MODEL_REPLY": "这题就在你这句话里歪着接"}
        base_out = eval_mod.run_worker_subprocess(
            code_dir, sample_case, n=1, env_extra=env)
        cand_out = eval_mod.run_worker_subprocess(
            code_dir, sample_case, n=1, ablate="deflect-in-place", env_extra=env)
        self.assertEqual(base_out["results"][0]["samples"], ["这题就在你这句话里歪着接"])
        self.assertLess(cand_out["head"]["total_chars"], base_out["head"]["total_chars"])
        self.assertEqual(cand_out["ablated"]["id"], "deflect-in-place")

        # 验证 P1-3：在没有 regular/rules.py 和 turns.py 的旧 commit（HEAD）worktree 上回放与消融均不报 ImportError
        with eval_mod.checkout_version("HEAD") as old_wt:
            old_out = eval_mod.run_worker_subprocess(
                old_wt, sample_case, n=1, ablate="deflect-in-place", env_extra=env)
            self.assertEqual(old_out["results"][0]["samples"], ["这题就在你这句话里歪着接"])
            self.assertEqual(old_out["ablated"]["id"], "deflect-in-place")

    async def test_judge_pair_randomizes_and_Evaluates_regressions_first(self):
        case = cases_mod.SEED_CASES[0]

        async def fake_judge(messages):
            user_prompt = messages[-1]["content"]
            # 选包含「顺着接」的那条赢；包含「越线词」的标 crossed
            a_section = user_prompt.split("【回复 A】\n", 1)[1].split("\n\n【回复 B】", 1)[0]
            b_section = user_prompt.split("\n\n【回复 B】\n", 1)[1]
            winner = "A" if "顺着接" in a_section else "B"
            crossed = []
            if "越线词" in a_section:
                crossed.append("A")
            if "越线词" in b_section:
                crossed.append("B")
            return json.dumps({"winner": winner, "crossed": crossed, "reason": "自然贴题"})

        for seed in range(4):
            verdict = await eval_mod.judge_pair(
                case, "这车刚打火去洗衣服了", "顺着接你这句话调侃一下",
                judge_fn=fake_judge, rng=random.Random(seed))
            self.assertEqual(verdict["winner"], "cand")

        cases = [cases_mod.SEED_CASES[0], cases_mod.SEED_CASES[9]]  # deflect-01 + remind-01
        good, bad = "〔提醒 11-03 09:00〕提醒大家小王回来了\n好", "好，九点叫你（忘了写标记）"
        base_res = [
            {"id": cases[0]["id"], "samples": ["顺着接你这句"] * 4},
            {"id": cases[1]["id"], "samples": [good] * 4},
        ]
        cand_res = [
            {"id": cases[0]["id"], "samples": ["顺着接你这句"] * 4},
            {"id": cases[1]["id"], "samples": [bad] * 4},
        ]
        rows, totals = await eval_mod.evaluate_cases(
            cases, base_res, cand_res, judge_fn=fake_judge)
        self.assertEqual(totals["regressed"], 1)
        # 退步的 remind-01 必须排在第一位
        self.assertEqual(rows[0]["id"], cases[1]["id"])
        self.assertTrue(rows[0]["regressed"])

        # 4 次里只掉 1 次：同一版本跑两遍也常见，记成噪声内下降，不算退步，但排在没变化的前面
        wobble_rows, wobble_totals = await eval_mod.evaluate_cases(
            cases, base_res,
            [cand_res[0], {"id": cases[1]["id"], "samples": [good, good, good, bad]}],
            judge_fn=fake_judge)
        self.assertEqual(wobble_totals["regressed"], 0)
        self.assertEqual(wobble_totals["wobbled"], 1)
        self.assertEqual(wobble_rows[0]["id"], cases[1]["id"])
        self.assertTrue(wobble_rows[0]["wobbled"])
        self.assertIn("WOBBLE", eval_mod.render_report(
            {"base": ".", "cand": ".", "created_at": "-", "n": 4, "totals": wobble_totals,
             "cases": wobble_rows}))

        # 验证 P1-2：确定性检查全过、但盲评判输且越线时，必须标为 regressed，不能显示 OK
        judge_loss_rows, judge_loss_totals = await eval_mod.evaluate_cases(
            [cases[0]],
            [{"id": cases[0]["id"], "samples": ["顺着接你这句"]}],
            [{"id": cases[0]["id"], "samples": ["随便敷衍一句带越线词"]}],
            judge_fn=fake_judge,
        )
        self.assertTrue(judge_loss_rows[0]["regressed"])
        self.assertEqual(judge_loss_totals["regressed"], 1)
        self.assertEqual(judge_loss_totals["cand_crossed"], 1)
        self.assertTrue(any("越线" in r for r in judge_loss_rows[0]["failed_reasons"]))

    def test_day_metrics_counts_patterns_and_catchphrases(self):
        samples = [
            "〔不接〕",
            "我这刚醒没多久，先去厨房洗衣服了",
            "我这刚醒没多久，车刚打火去买菜",
            "我这刚醒没多久，脑子还在转圈",
            "正常聊一句天",
        ]
        with patch("wordfilter.has_hit", lambda text: "敏感" in (text or "")):
            m = eval_mod.day_metrics(samples)
        self.assertEqual(m["total"], 5)
        self.assertEqual(m["pass_count"], 1)
        self.assertAlmostEqual(m["pass_rate"], 0.2)
        self.assertEqual(m["patterns"]["housework"], 2)
        self.assertEqual(m["patterns"]["fake_car"], 1)
        self.assertEqual(m["patterns"]["just_woke"], 3)
        self.assertTrue(any("刚醒没多久" in c["gram"] for c in m["catchphrases"]))

    def test_run_eval_and_search_wording_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "turns")
            cdir = os.path.join(tmp, "cases")
            edir = os.path.join(tmp, "evals")
            tlog = turns_mod.TurnLog(tdir, enabled=True)
            tlog.append("reply", kwargs={"user_text": "hi"}, session=[], raw="ok")
            tlog.close()

            def fake_worker(code_dir, items, *, n=1, concurrency=6, ablate="", override=None):
                results = []
                for it in items:
                    iid = it.get("id") or it.get("turn_id")
                    if str(iid).startswith("remind-01"):
                        s = "〔提醒 11-03 09:00〕提醒大家小王回来了\n记下了"
                    else:
                        s = "正常接话"
                    results.append({"id": iid, "samples": [s] * n, "errors": 0})
                return {"head": {"total_chars": 4800 if ablate else 5000, "rules_count": 11},
                        "ablated": {"id": ablate, "chars": 200} if ablate else None,
                        "results": results}

            with patch.object(config, "TURN_LOG_DIR", tdir), \
                    patch.object(config, "CASES_DIR", cdir), \
                    patch.object(config, "EVAL_DIR", edir):
                rep = eval_mod.run_eval(
                    config, base=".", cand=".", cases_selector="remind-01*",
                    day=turns_mod.day_key(), n=1, ablate="remind-tag-rules",
                    worker_fn=fake_worker)
                self.assertTrue(os.path.exists(rep["report_path"]))
                self.assertTrue(rep["ablate"]["deletion_candidate"])
                md = eval_mod.render_report(rep)
                self.assertIn("稳定头长度：`5000` → `4800` 字（-200 字）", md)
                self.assertIn("可删除候选", md)

                # 验证第 2 轮 P1-2(a)：评测集里根本没有该规则的关联用例时，证据不完整，不能推荐删除
                rep_unlinked = eval_mod.run_eval(
                    config, base=".", cand=".", cases_selector="remind-01*",
                    n=1, ablate="deflect-in-place", worker_fn=fake_worker)
                self.assertFalse(rep_unlinked["ablate"]["deletion_candidate"])
                self.assertTrue(rep_unlinked["ablate"]["incomplete_evidence"])
                self.assertEqual(rep_unlinked["ablate"]["verdict"], "no_cases")
                md_unlinked = eval_mod.render_report(rep_unlinked)
                self.assertIn("判断不了", md_unlinked)
                self.assertNotIn("拿掉后有退步", md_unlinked)

                # 验证第 2 轮 P1-2(b)：关联用例两版全部生成失败（samples=[None]）、仅无关用例成功时，绝不能推荐删除
                def partial_fail_worker(code_dir, items, *, n=1, concurrency=6, ablate="", override=None):
                    results = []
                    for it in items:
                        iid = str(it.get("id") or "")
                        if iid.startswith("remind-"):
                            results.append({"id": iid, "samples": [None] * n, "errors": n})
                        else:
                            results.append({"id": iid, "samples": ["就地顺着拆台"] * n, "errors": 0})
                    return {"head": {"total_chars": 4800 if ablate else 5000, "rules_count": 11},
                            "ablated": {"id": ablate, "chars": 200} if ablate else None,
                            "results": results}

                rep_incomplete = eval_mod.run_eval(
                    config, base=".", cand=".", cases_selector="remind",
                    n=1, ablate="remind-tag-rules", worker_fn=partial_fail_worker)
                self.assertFalse(rep_incomplete["ablate"]["deletion_candidate"])
                self.assertTrue(rep_incomplete["ablate"]["incomplete_evidence"])

                # 验证 P1-2：确定性检查全过、但盲评判输且越线时，消融报告绝不能建议删除规则
                def judge_loss_worker(code_dir, items, *, n=1, concurrency=6, ablate="", override=None):
                    return {
                        "head": {"total_chars": 4800 if ablate else 5000, "rules_count": 11},
                        "ablated": {"id": ablate, "chars": 200} if ablate else None,
                        "results": [
                            {"id": it["id"],
                             "samples": ["压线的敷衍回复" if ablate else "就地顺着拆台"] * n,
                             "errors": 0}
                            for it in items
                        ],
                    }

                async def strict_judge(messages):
                    user_prompt = messages[-1]["content"]
                    a_sec = user_prompt.split("【回复 A】\n", 1)[1].split("\n\n【回复 B】", 1)[0]
                    cand_is_a = "压线的敷衍回复" in a_sec
                    return json.dumps({
                        "winner": "B" if cand_is_a else "A",
                        "crossed": ["A"] if cand_is_a else ["B"],
                        "reason": "消融后越线且生硬",
                    })

                rep_bad = eval_mod.run_eval(
                    config, base=".", cand=".", cases_selector="deflect-01*",
                    n=1, ablate="deflect-in-place", judge_fn=strict_judge,
                    worker_fn=judge_loss_worker)
                self.assertFalse(rep_bad["ablate"]["deletion_candidate"])
                self.assertEqual(rep_bad["totals"]["regressed"], 1)
                md_bad = eval_mod.render_report(rep_bad)
                self.assertIn("仍需保留", md_bad)
                self.assertIn("REGRESSED", md_bad)

                # 窄口子措辞搜索：打平挑更短的候选
                search_rep = eval_mod.search_wording(
                    config, target="remind", k=2, n=1,
                    propose_fn=lambda orig, keep, k: [
                        "短版规则：写〔提醒 11-03 09:00〕或〔取消提醒〕，自称{bot}。"
                    ],
                    worker_fn=fake_worker)
                self.assertEqual(search_rep["best"]["name"], "cand-01")
                self.assertTrue(os.path.exists(search_rep["report_path"]))

    def test_cli_rules_case_and_eval_paid_guard(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "CASES_DIR", os.path.join(tmp, "cases")), \
                patch.object(cli, "load_config", lambda directory: config), \
                patch("builtins.print"):
            self.assertEqual(cli.main(["rules", "--json"]), 0)
            self.assertEqual(cli.main(["case", "seed"]), 0)
            self.assertEqual(cli.main(["case", "list", "--cases", "remind-*", "--json"]), 0)
            with self.assertRaises(SystemExit):
                cli.main(["eval", "--cases", "remind-*"])  # 缺少 --allow-paid 必须拦截



class JudgeAndSignificanceTest(unittest.TestCase):
    def test_judge_uses_only_its_own_provider(self):
        # 盲评那一家没凭据时不借别家的：借了就是把一家的密钥发到另一家的地址上
        with patch.object(config, "EVAL_JUDGE_PROVIDER", "zhipu"), \
                patch.object(config, "load_api_keys",
                             lambda provider: ["k-openai"] if provider == "openai" else []):
            with self.assertRaises(ValueError):
                eval_mod._judge_endpoint(config)

    def test_preflight_reports_misconfigured_judge(self):
        # 2026-11-04：默认把 kimi-k3.1-pro 发到 api.openai.com，一律超时，盲评整批被剔掉也没人知道
        async def timed_out(messages):
            raise TimeoutError("Request timed out.")

        async def chatty(messages):
            return "我觉得 A 更好"

        async def fine(messages):
            return '{"winner": "A", "crossed": [], "reason": "自然"}'

        with self.assertRaises(RuntimeError) as ctx:
            eval_mod.preflight_judge(config, judge_fn=timed_out)
        self.assertIn(config.EVAL_JUDGE_MODEL, str(ctx.exception))
        with self.assertRaises(RuntimeError):
            eval_mod.preflight_judge(config, judge_fn=chatty)
        eval_mod.preflight_judge(config, judge_fn=fine)

    def test_run_eval_preflights_before_generating(self):
        generated = []

        def worker(*args, **kwargs):
            generated.append(1)
            return {"head": {}, "results": []}

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "CASES_DIR", tmp), \
                patch.object(eval_mod, "_default_judge_call",
                             AsyncMock(side_effect=TimeoutError("Request timed out."))):
            with self.assertRaises(RuntimeError):
                eval_mod.run_eval(config, cases_selector="deflect-01*", n=1,
                                  worker_fn=worker, write=False)
        self.assertEqual(generated, [])

    def test_judging_stays_within_gateway_concurrency(self):
        # 网关同时最多扛两个请求：盲评用自己的 EVAL_JUDGE_CONCURRENCY，不跟生成共用 EVAL_CONCURRENCY
        self.assertLessEqual(config.EVAL_JUDGE_CONCURRENCY, 2)
        live = {"now": 0, "peak": 0}

        async def slow_judge(messages):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.01)
            live["now"] -= 1
            return '{"winner": "tie", "crossed": [], "reason": "一样"}'

        case = cases_mod.SEED_CASES[0]
        rows, _ = asyncio.run(eval_mod.evaluate_cases(
            [case], [{"id": case["id"], "samples": ["旧%d" % i for i in range(8)]}],
            [{"id": case["id"], "samples": ["新%d" % i for i in range(8)]}],
            judge_fn=slow_judge, concurrency=config.EVAL_JUDGE_CONCURRENCY))
        self.assertEqual(rows[0]["judge"]["tie"], 8)
        self.assertLessEqual(live["peak"], config.EVAL_JUDGE_CONCURRENCY)

    def test_regression_needs_more_than_sampling_noise(self):
        drop, loss, alpha = eval_mod.drop_p_value, eval_mod.judge_loss_p_value, eval_mod.REGRESSION_P
        self.assertGreater(drop(4, 4, 3, 4), alpha)        # 4 次掉 1 次：噪声
        self.assertGreater(drop(4, 4, 2, 4), alpha)        # 掉 2 次：还分不清
        self.assertLessEqual(drop(4, 4, 1, 4), alpha)      # 掉 3 次：退步
        self.assertLessEqual(drop(3, 4, 0, 4), alpha)
        self.assertAlmostEqual(drop(4, 4, 1, 4), 5 / 70)
        self.assertGreater(loss(2, 0), alpha)              # 同一版本对同一版本实测出现过 2:0
        self.assertLessEqual(loss(4, 0), alpha)            # 盲评旧版 4:0


if __name__ == "__main__":
    unittest.main()
