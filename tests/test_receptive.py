"""插嘴接纳度：证据来源、声明门槛、衰减、回忆额度、脏档案。

守的核心是**克制**：证据不足时乘数必须恰为 1.0（照群体口径），
不是「保守一点」、更不是「先当他讨厌机器人」。声明门槛是跨成员置换检验，
所以「与众不同」永远是相对群里其他人说的 —— 没有可比的人就没有声明。
"""
import random
import unittest
from unittest.mock import patch

import config
import receptive

DAY = 86400.0
NOW = 1_800_000_000.0


def _store(**kw):
    kw.setdefault("min_evidence", 4.0)
    return receptive.Receptiveness(**kw)


def _say(store, member, verdict, times, group="G", now=NOW):
    for _ in range(times):
        store.record(group, member, verdict, route="banter", now=now)


def _welcoming_peers(store, group="G", now=NOW, names=("B", "C")):
    """一群爱接梗的人，给要检验的那个人一个可比的参照。"""
    for who in names:
        _say(store, who, "keep_playful", 8, group=group, now=now)


class EvidenceSourceTest(unittest.TestCase):
    """只有「没人请的插嘴」之后的嫌烦 / 接着逗才算打扰的证据。"""

    def setUp(self):
        self.store = _store()

    def test_only_unsolicited_route_counts(self):
        self.assertFalse(self.store.record("G", "A", "back_off", route="reply"))
        self.assertFalse(self.store.record("G", "A", "back_off", route=None))
        self.assertTrue(self.store.record("G", "A", "back_off", route="banter"))

    def test_only_two_verdicts_count(self):
        for verdict in ("repair_briefly", "continue_normally",
                        "no_adjustment", "", None, "unclear"):
            self.assertFalse(
                self.store.record("G", "A", verdict, route="banter"),
                f"{verdict!r} 不该被当成打扰证据")
        self.assertTrue(self.store.record("G", "A", "keep_playful",
                                          route="banter"))

    def test_no_evidence_is_not_neutral_evidence(self):
        """没有证据就是没有证据 —— 不许顺手记成 0.5 把分母撑起来。"""
        self.store.record("G", "A", "repair_briefly", route="banter")
        self.assertEqual(self.store.assess("G", "A")["receptive_n"], 0.0)

    def test_blank_identities_are_ignored(self):
        self.assertFalse(self.store.record("", "A", "back_off", route="banter"))
        self.assertFalse(self.store.record("G", "", "back_off", route="banter"))


class RestraintTest(unittest.TestCase):
    """声明门槛：只有跨成员置换检验说「这个人确实不一样」，才允许改变行为。"""

    def test_cold_start_is_exactly_the_population(self):
        store = _store()
        out = store.assess("G", "NEWCOMER")
        self.assertEqual(out["receptive_multiplier"], 1.0)
        self.assertFalse(out["receptive_claimed"])
        self.assertIsNone(out["receptive_p"])
        self.assertAlmostEqual(out["receptive_personal"],
                               out["receptive_population"])

    def test_below_the_evidence_floor_changes_nothing(self):
        store = _store(min_evidence=4.0)
        _welcoming_peers(store)
        _say(store, "A", "back_off", 3)
        out = store.assess("G", "A", now=NOW)
        self.assertLess(out["receptive_n"], 4.0)
        self.assertIsNone(out["receptive_p"])
        self.assertFalse(out["receptive_claimed"])
        self.assertEqual(out["receptive_multiplier"], 1.0)

    def test_a_lone_member_is_never_singled_out(self):
        """群里只有一个人有证据时，他只能和一个模糊的自己比 —— 第一版正是在这里误报。"""
        store = _store()
        _say(store, "A", "back_off", 20)
        out = store.assess("G", "A", now=NOW)
        self.assertFalse(out["receptive_claimed"])
        self.assertEqual(out["receptive_multiplier"], 1.0)

    def test_repeated_annoyance_eventually_backs_off(self):
        store = _store()
        _welcoming_peers(store)
        _say(store, "A", "back_off", 8)
        out = store.assess("G", "A", now=NOW)
        self.assertTrue(out["receptive_claimed"])
        self.assertLessEqual(out["receptive_p"], store.alpha)
        self.assertLess(out["receptive_multiplier"], 1.0)
        self.assertGreaterEqual(out["receptive_multiplier"],
                                store.multiplier_min)

    def test_repeated_welcome_leans_in(self):
        store = _store()
        for who in ("B", "C"):
            _say(store, who, "back_off", 8)
        _say(store, "A", "keep_playful", 8)
        out = store.assess("G", "A", now=NOW)
        self.assertTrue(out["receptive_claimed"])
        self.assertGreater(out["receptive_multiplier"], 1.0)
        self.assertLessEqual(out["receptive_multiplier"], store.multiplier_max)

    def test_agreeing_with_the_group_makes_no_claim(self):
        """全群都一样的时候，怎么打乱都一样 —— p 恰为 1，没有可声明的差异。"""
        store = _store()
        for who in ("A", "B", "C"):
            _say(store, who, "back_off", 8)
        out = store.assess("G", "A", now=NOW)
        self.assertEqual(out["receptive_p"], 1.0)
        self.assertFalse(out["receptive_claimed"])
        self.assertEqual(out["receptive_multiplier"], 1.0)

    def test_groups_do_not_bleed_into_each_other(self):
        store = _store()
        _welcoming_peers(store, group="G1")
        _say(store, "A", "back_off", 8, group="G1")
        self.assertTrue(store.assess("G1", "A", now=NOW)["receptive_claimed"])
        self.assertEqual(store.assess("G2", "A", now=NOW)
                         ["receptive_multiplier"], 1.0)


class CalibrationTest(unittest.TestCase):
    """门槛是校准出来的，不是手定的：零假设下全群错认任何一人的概率 ≤ α。"""

    def test_null_false_claims_are_controlled(self):
        """六个人真实接纳度完全相同，每人 8 条。同一批数据上第一版的手定门槛
        （≥ 4 条且差 ≥ 0.15）会在一大半的群里错认至少一人；置换门槛不会。"""
        rng = random.Random(20261031)
        groups, permuted, hand_set = 150, 0, 0
        for g in range(groups):
            store = _store(permutations=99)
            members = [f"M{m}" for m in range(6)]
            for who in members:
                for _ in range(8):
                    store.record(f"G{g}", who,
                                 "keep_playful" if rng.random() < 0.5
                                 else "back_off", route="banter", now=NOW)
            outs = [store.assess(f"G{g}", who, now=NOW) for who in members]
            permuted += any(o["receptive_claimed"] for o in outs)
            hand_set += any(o["receptive_n"] >= 4.0
                            and abs(o["receptive_personal"]
                                    - o["receptive_population"]) >= 0.15
                            for o in outs)
        self.assertLessEqual(permuted / groups, 0.12)   # 名义 5%，留出模拟噪声
        self.assertGreater(hand_set / groups, 0.30)     # 实测约 60%

    def test_a_real_outlier_is_found_and_direction_is_never_wrong(self):
        store = _store()
        _say(store, "A", "back_off", 12)
        for who in ("B", "C", "D", "E", "F"):
            _say(store, who, "keep_playful", 10)
            _say(store, who, "back_off", 2)
        out = store.assess("G", "A", now=NOW)
        self.assertTrue(out["receptive_claimed"])
        self.assertLess(out["receptive_multiplier"], 1.0)
        for who in ("B", "C", "D", "E", "F"):
            # 其余人确实高于群体均值；就算被声明，也只能往「多搭话」那边
            self.assertGreaterEqual(
                store.assess("G", who, now=NOW)["receptive_multiplier"], 1.0)

    def test_same_evidence_same_verdict(self):
        """种子由证据内容决定：两个实例、或重启前后，同样的证据给同样的 p。"""
        outs = []
        for _ in range(2):
            store = _store()
            _welcoming_peers(store)
            _say(store, "A", "back_off", 5)
            _say(store, "A", "keep_playful", 1)
            outs.append(store.assess("G", "A", now=NOW))
        self.assertEqual(outs[0], outs[1])

    def test_new_evidence_invalidates_the_cached_test(self):
        store = _store()
        _welcoming_peers(store)
        _say(store, "A", "back_off", 3)
        self.assertFalse(store.assess("G", "A", now=NOW)["receptive_claimed"])
        _say(store, "A", "back_off", 5)             # 同一时刻：只能靠版本号失效
        self.assertTrue(store.assess("G", "A", now=NOW)["receptive_claimed"])

    def test_record_order_does_not_matter(self):
        in_order, shuffled = _store(), _store()
        stamps = [NOW - k * 3600.0 for k in range(8)]
        for store, order in ((in_order, sorted(stamps)),
                             (shuffled, [stamps[i] for i in (3, 0, 7, 1, 5, 2, 6, 4)])):
            _welcoming_peers(store)
            for ts in order:
                store.record("G", "A", "back_off", route="banter", now=ts)
        self.assertEqual(in_order.records, shuffled.records)
        self.assertEqual(in_order.assess("G", "A", now=NOW),
                         shuffled.assess("G", "A", now=NOW))

    def test_items_per_member_are_bounded(self):
        store = _store()
        for k in range(100):
            store.record("G", "A", "back_off", route="banter", now=NOW + k)
        items = store.records["G|A"]["items"]
        self.assertEqual(len(items), receptive._MAX_ITEMS)
        self.assertEqual(items[-1][0], NOW + 99)    # 留下的是最近的

    def test_too_few_permutations_cannot_silently_disable_the_gate(self):
        """B 次置换的 p 最小是 1/(B+1)；B 太小门槛永远过不去，必须启动时就报。"""
        def receptive_errors():
            return [e for e in config.validate() if "RECEPTIVE_PERMUTATIONS" in e]
        with patch.object(config, "RECEPTIVE_ALPHA", 0.05), \
                patch.object(config, "RECEPTIVE_PERMUTATIONS", 10):
            self.assertTrue(receptive_errors())
        with patch.object(config, "RECEPTIVE_ALPHA", 0.05), \
                patch.object(config, "RECEPTIVE_PERMUTATIONS", 19):
            self.assertFalse(receptive_errors())


class DecayTest(unittest.TestCase):
    """半衰期 14 天：它是**当下**的估计，不是永久标签。"""

    def test_old_evidence_fades_back_to_the_population(self):
        store = _store(half_life_days=14.0)
        _welcoming_peers(store)
        _say(store, "A", "back_off", 8)
        self.assertTrue(store.assess("G", "A", now=NOW)["receptive_claimed"])
        later = store.assess("G", "A", now=NOW + 120 * DAY)
        self.assertFalse(later["receptive_claimed"])
        self.assertEqual(later["receptive_multiplier"], 1.0)
        self.assertLess(later["receptive_n"], 0.1)

    def test_fresh_evidence_overtakes_stale_evidence(self):
        store = _store(half_life_days=14.0)
        _say(store, "A", "back_off", 8)
        for who in ("B", "C"):
            _say(store, who, "back_off", 8, now=NOW + 60 * DAY)
        _say(store, "A", "keep_playful", 8, now=NOW + 60 * DAY)
        out = store.assess("G", "A", now=NOW + 60 * DAY)
        self.assertGreater(out["receptive_multiplier"], 1.0)

    def test_prune_drops_invisible_evidence(self):
        store = _store()
        store.record("G", "A", "back_off", route="banter", now=NOW)
        store.prune(now=NOW + 400 * DAY)
        self.assertEqual(store.records, {})

    def test_prune_keeps_the_fresh_part_of_a_record(self):
        store = _store()
        store.record("G", "A", "back_off", route="banter", now=NOW)
        store.record("G", "A", "keep_playful", route="banter",
                     now=NOW + 300 * DAY)
        store.prune(now=NOW + 301 * DAY)
        self.assertEqual(store.records["G|A"]["items"],
                         [[NOW + 300 * DAY, 1]])


class CallbackQuotaTest(unittest.TestCase):
    """「居然记得」要稀才稀奇：每人冷却 + 每群每天上限。"""

    def setUp(self):
        self.store = _store(callback_cooldown=6 * 3600.0,
                            callback_max_per_day=2)

    def test_first_time_is_allowed(self):
        self.assertTrue(self.store.callback_allowed("G", "A", now=NOW))

    def test_cooldown_blocks_the_same_person(self):
        self.store.mark_callback("G", "A", now=NOW)
        self.assertFalse(self.store.callback_allowed("G", "A",
                                                     now=NOW + 3600.0))
        self.assertTrue(self.store.callback_allowed("G", "A",
                                                    now=NOW + 7 * 3600.0))

    def test_daily_cap_is_per_group(self):
        self.store.mark_callback("G", "A", now=NOW)
        self.store.mark_callback("G", "B", now=NOW + 60.0)
        self.assertFalse(self.store.callback_allowed("G", "C", now=NOW + 120.0))
        self.assertTrue(self.store.callback_allowed("G2", "C", now=NOW + 120.0))

    def test_cap_resets_with_the_local_day(self):
        self.store.mark_callback("G", "A", now=NOW)
        self.store.mark_callback("G", "B", now=NOW + 60.0)
        self.assertTrue(self.store.callback_allowed("G", "C", now=NOW + DAY))

    def test_zero_cap_disables_it(self):
        store = _store(callback_max_per_day=0)
        self.assertFalse(store.callback_allowed("G", "A", now=NOW))

    def test_judging_against_it_costs_no_quota(self):
        """判断说「先别」时调用方不该登记 —— 额度只被真的端出去消耗。"""
        self.assertTrue(self.store.callback_allowed("G", "A", now=NOW))
        self.assertTrue(self.store.callback_allowed("G", "A", now=NOW))


class PersistenceTest(unittest.TestCase):
    def test_round_trip(self):
        store = _store()
        _welcoming_peers(store)
        _say(store, "A", "back_off", 8)
        store.mark_callback("G", "A", now=NOW)
        dumped = {}
        store.dump_into(dumped)
        restored = _store()
        restored.hydrate(dumped["receptive"])
        out = restored.assess("G", "A", now=NOW)
        self.assertTrue(out["receptive_claimed"])
        self.assertEqual(out, store.assess("G", "A", now=NOW))
        self.assertFalse(restored.callback_allowed("G", "A", now=NOW + 60.0))

    def test_forged_records_are_dropped(self):
        """标签只能是 0/1；一条脏了整份不收。字符串、布尔、负时间同样不收。"""
        restored = _store()
        restored.hydrate({"records": {
            "G|TWO": {"items": [[NOW, 1], [NOW, 2]]},
            "G|STRING": {"items": [[NOW, "1"]]},
            "G|BOOL": {"items": [[NOW, True]]},
            "G|TS_STRING": {"items": [["1800000000", 1]]},
            "G|NEGATIVE_TS": {"items": [[-1.0, 1]]},
            "G|TRIPLE": {"items": [[NOW, 1, 1]]},
            "G|EMPTY": {"items": []},
            "NOPIPE": {"items": [[NOW, 1]]},
            "G|GOOD": {"items": [[NOW, 0], [NOW - 60.0, 1]]},
        }})
        self.assertEqual(list(restored.records), ["G|GOOD"])
        self.assertEqual(restored.records["G|GOOD"]["items"],
                         [[NOW - 60.0, 1], [NOW, 0]])
        self.assertEqual(
            restored.assess("G", "TWO", now=NOW)["receptive_multiplier"], 1.0)

    def test_aggregate_format_is_not_accepted(self):
        """第一版只存累计数，没有逐条标签，没法参与置换 —— 不收，也不猜。"""
        restored = _store()
        restored.hydrate({"records": {"G|A": {"n": 8.0, "s": 0.0, "ts": NOW}}})
        self.assertEqual(restored.records, {})

    def test_garbage_shapes_do_not_raise(self):
        for junk in (None, [], "x", {"records": "x"}, {"records": {"G|A": 1}},
                     {"records": {"G|A": {"items": "x"}}},
                     {"records": {"G|A": {"items": [[1]]}}},
                     {"records": {"G|A": {"items": [None]}}},
                     {"callbacks": {"G|A": "soon"}}, {"quota": {"G": 1}}):
            _store().hydrate(junk)

    def test_record_table_is_bounded(self):
        store = _store(max_records=5)
        for i in range(20):
            store.record("G", f"M{i}", "back_off", route="banter",
                         now=NOW + i)
        self.assertLessEqual(len(store.records), 5)


if __name__ == "__main__":
    unittest.main()
