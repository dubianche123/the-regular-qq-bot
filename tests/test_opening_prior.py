"""开口时机先验：特征语义、保量、边界、脏档案。

这组用例守的是三件事：
  1. 特征算法必须和离线拟合逐字一致 —— 系数只对那一套归一化有意义。
  2. **保量**：乘数的长期均值≈1，所以 BANTER_PROBABILITY 仍然决定整体话量。
  3. 坏档案、空缓冲、关掉开关，都退回原来的行为，不许悄悄改门控。
"""
import unittest

import opening


def _rows(spec, start=1_000_000.0):
    """spec: [(相对秒, 发言人, 文本), ...] —— 最后一条就是「当前这条」。"""
    return [{"sender": who, "text": text, "time": start + offset,
             "message_id": f"m{i}"}
            for i, (offset, who, text) in enumerate(spec)]


class FeatureSemanticsTest(unittest.TestCase):
    """burst60 **不含**当前这条，speakers5m **含**当前这条 —— 与拟合一致。"""

    def test_burst_excludes_the_current_message(self):
        rows = _rows([(0, "A", "一"), (10, "B", "二"), (20, "A", "三")])
        feats = opening.features(rows)
        self.assertAlmostEqual(feats["burst60"], 2 / 8)

    def test_speakers_include_the_current_speaker(self):
        rows = _rows([(0, "A", "一"), (10, "B", "二"), (20, "C", "三")])
        feats = opening.features(rows)
        self.assertAlmostEqual(feats["speakers5m"], 3 / 4)

    def test_windows_are_respected(self):
        """61 秒前的不算 burst；6 分钟前的发言人不算在场。"""
        rows = _rows([(0, "A", "老话"), (410, "B", "旧话"), (461, "C", "现在")])
        feats = opening.features(rows)
        self.assertAlmostEqual(feats["burst60"], 1 / 8)   # 只有 410s 那条在 60s 内
        self.assertAlmostEqual(feats["speakers5m"], 2 / 4)  # A 已出 5 分钟窗
        # 刚好越过 60 秒的那条就不算热度了
        edge = _rows([(0, "A", "老话"), (400, "B", "旧话"), (461, "C", "现在")])
        self.assertAlmostEqual(opening.features(edge)["burst60"], 0.0)

    def test_monologue_and_bracket_and_length(self):
        rows = _rows([(0, "A", "自言"), (5, "A", "[图片]")])
        feats = opening.features(rows)
        self.assertEqual(feats["monologue"], 1.0)
        self.assertEqual(feats["is_bracket"], 1.0)
        rows = _rows([(0, "A", "自言"), (5, "B", "x" * 200)])
        self.assertAlmostEqual(opening.features(rows)["length"], 1.0)

    def test_empty_buffer_is_not_an_estimate(self):
        self.assertIsNone(opening.features([]))
        self.assertIsNone(opening.estimate([]))
        self.assertIsNone(opening.estimate([object()]))


class RankingTest(unittest.TestCase):
    """热场必须排在死群前面 —— 这是整件事的全部意义。"""

    LIVELY = _rows([(0, "A", "哈哈"), (8, "B", "真的吗"), (15, "C", "我也看了"),
                    (22, "A", "笑死"), (28, "B", "再来一个")])
    DEAD = _rows([(0, "A", "有人吗"), (3000, "A", "？")])

    def test_lively_beats_dead_air(self):
        lively = opening.estimate(self.LIVELY)
        dead = opening.estimate(self.DEAD)
        self.assertGreater(lively, dead)
        # 留出集上五分位实际命中率 0.28 → 0.65，先验至少要拉开两倍以上
        self.assertGreater(lively / dead, 2.0)

    def test_long_message_and_image_lower_the_score(self):
        base = _rows([(0, "A", "哈哈"), (10, "B", "对"), (20, "C", "短")])
        wall = _rows([(0, "A", "哈哈"), (10, "B", "对"), (20, "C", "字" * 120)])
        image = _rows([(0, "A", "哈哈"), (10, "B", "对"), (20, "C", "[图片]")])
        self.assertGreater(opening.estimate(base), opening.estimate(wall))
        self.assertGreater(opening.estimate(base), opening.estimate(image))


class VolumePreservedTest(unittest.TestCase):
    """乘数按本群分数均值归一 —— 整体话量交给 BANTER_PROBABILITY，不由先验加量。"""

    def _stream(self):
        """一段混了热场和冷场的流水，每条都过一次 assess。"""
        prior = opening.OpeningPrior()
        shapes, start = [], 2_000_000.0
        for cycle in range(40):
            # 热场五条
            for i in range(5):
                rows = _rows([(0, "A", "哈"), (8, "B", "嗯"), (16, "C", "噢"),
                              (24, "A", "哈哈"), (30 + i, "B", "再来")],
                             start=start)
                shapes.append(prior.assess("G", rows,
                                           now=start + 30 + i)["opening_shape"])
            start += 600
            # 冷场两条
            for i in range(2):
                rows = _rows([(0, "A", "有人吗"), (2500 + i, "A", "？")],
                             start=start)
                shapes.append(prior.assess("G", rows,
                                           now=start + 2500 + i)["opening_shape"])
            start += 4000
        return shapes

    def test_mean_multiplier_is_about_one(self):
        shapes = self._stream()
        mean = sum(shapes) / len(shapes)
        # 「约等于保量」：夹断与非稳态会让它偏离 1 一点，不是守恒定律
        self.assertGreater(mean, 0.80)
        self.assertLess(mean, 1.25)

    def test_multiplier_stays_inside_the_clamp(self):
        for shape in self._stream():
            self.assertGreaterEqual(shape, opening.SHAPE_MIN)
            self.assertLessEqual(shape, opening.SHAPE_MAX)

    def test_spread_is_real(self):
        """夹断之后仍然要有可用的高低差，否则等于没装。"""
        shapes = self._stream()
        self.assertGreater(max(shapes) / min(shapes), 2.0)


class AssessOrderTest(unittest.TestCase):
    def test_shape_uses_the_mean_before_folding_in_this_score(self):
        """顺序错了每条消息都在跟自己比，乘数会被压平成 1。"""
        prior = opening.OpeningPrior()
        rows = _rows([(0, "A", "哈"), (6, "B", "嗯"), (12, "C", "噢")])
        first = prior.assess("G", rows)
        self.assertAlmostEqual(first["opening_mean"], opening.POPULATION_BASE)
        self.assertAlmostEqual(
            first["opening_shape"],
            min(opening.SHAPE_MAX,
                first["opening_score"] / opening.POPULATION_BASE), places=3)

    def test_empty_buffer_leaves_the_gate_untouched(self):
        prior = opening.OpeningPrior()
        out = prior.assess("G", [])
        self.assertIsNone(out["opening_score"])
        self.assertEqual(out["opening_shape"], 1.0)
        self.assertNotIn("G", prior.records)


class PersistenceTest(unittest.TestCase):
    def test_round_trip(self):
        prior = opening.OpeningPrior()
        prior.observe("G", 0.8, now=5.0)
        dumped = {}
        prior.dump_into(dumped)
        restored = opening.OpeningPrior()
        self.assertEqual(restored.hydrate(dumped["opening"]), 1)
        self.assertAlmostEqual(restored.mean_for("G"), prior.mean_for("G"))

    def test_dirty_records_are_dropped_not_trusted(self):
        restored = opening.OpeningPrior()
        restored.hydrate({
            "A": {"mean": 5.0, "count": 1},        # 越界
            "B": {"mean": "0.7", "count": 1},      # 字符串
            "C": {"mean": 0.7, "count": -3},       # 负计数
            "D": {"mean": float("nan"), "count": 1},
            "E": "not a dict",
            "F": {"mean": 0.7, "count": 9},        # 好的
        })
        self.assertEqual(list(restored.records), ["F"])
        for group in ("A", "B", "C", "D", "E"):
            self.assertEqual(restored.mean_for(group), opening.POPULATION_BASE)

    def test_group_table_is_bounded(self):
        prior = opening.OpeningPrior(max_groups=3)
        for i in range(10):
            prior.observe(f"G{i}", 0.5, now=float(i))
        self.assertLessEqual(len(prior.records), 3)
        self.assertIn("G9", prior.records)


class MeanFollowsTheGroupTest(unittest.TestCase):
    def test_running_mean_then_ema(self):
        """前 32 条算真均值（新群不被头几条带偏），之后按 1/32 跟随变化。"""
        prior = opening.OpeningPrior()
        for _ in range(32):
            prior.observe("G", 0.2)
        self.assertAlmostEqual(prior.mean_for("G"), 0.2, places=3)
        for _ in range(200):
            prior.observe("G", 0.9)
        self.assertGreater(prior.mean_for("G"), 0.85)

    def test_mean_is_clamped_away_from_zero(self):
        prior = opening.OpeningPrior()
        for _ in range(500):
            prior.observe("G", 0.0)
        self.assertGreater(prior.mean_for("G"), 0.0)
        self.assertEqual(prior.shape("G", 0.0), opening.SHAPE_MIN)


if __name__ == "__main__":
    unittest.main()
