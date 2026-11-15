# -*- coding: utf-8 -*-
"""激活度（作息/心情/关注度）的回归测试（2026-10-19 群主定调的新系统）。

三个信号融合成一个 profile，不各自成系统：
- 作息：看消息几点发的——凌晨是「被吵醒」，插嘴意愿打折、真回了也带起床气
- 心情：跟随每轮好感度结算漂移、按半衰期回中的全局情绪值
- 关注度：亲密度决定能看到的背景条数——越熟记得越多
"""
import sys
import time
import unittest

sys.path.insert(0, "..")

import activation  # noqa: E402


class CircadianTest(unittest.TestCase):

    def test_deep_night_is_low_with_wakeup_flavor(self):
        factor, label = activation.circadian(3)
        self.assertLess(factor, 0.6)
        self.assertEqual(label, "凌晨被吵醒")

    def test_daytime_is_full_and_silent(self):
        factor, label = activation.circadian(15)
        self.assertEqual(factor, 1.0)
        self.assertEqual(label, "")

    def test_morning_is_waking_up(self):
        factor, label = activation.circadian(8)
        self.assertEqual(label, "刚睡醒")


class MoodTest(unittest.TestCase):

    def setUp(self):
        activation._mood_state.update({"v": 0.0, "ts": 0.0})

    def tearDown(self):
        activation._mood_state.update({"v": 0.0, "ts": 0.0})

    def test_events_move_mood_and_it_clamps(self):
        for _ in range(10):
            activation.note_mood(+2, now=1000.0)
        self.assertLessEqual(activation.current_mood(1000.0), 1.0)

    def test_mood_decays_toward_zero(self):
        activation.note_mood(+2, now=1000.0)
        self.assertGreater(activation.current_mood(1000.0), 0.3)
        # 两个半衰期之后基本回中
        self.assertLess(abs(activation.current_mood(1000.0 + 2 * activation._MOOD_HALFLIFE)), 0.6)

    def test_reading_does_not_reset_the_decay_clock(self):
        """读操作不许刷新衰减时钟——否则心情永远不衰减（写的时候才动 ts）。"""
        activation.note_mood(+2, now=1000.0)
        for _ in range(50):
            activation.current_mood(1000.0)
        self.assertLess(activation.current_mood(1000.0 + activation._MOOD_HALFLIFE), 1.0)

    def test_dump_and_hydrate_roundtrip(self):
        activation.note_mood(+2, now=1000.0)
        data = {}
        activation.dump_mood_into(data)
        self.assertIn("mood", data)
        activation._mood_state.update({"v": 0.0, "ts": 0.0})
        activation.hydrate_mood(data["mood"])
        self.assertAlmostEqual(activation._mood_state["v"], data["mood"]["v"])


class StateLineTest(unittest.TestCase):

    def test_deep_night_shapes_length_not_a_story(self):
        line = activation.state_line("凌晨被吵醒", 0.0)
        self.assertIn("短", line)
        # 2026-11-03：写「你刚被吵醒」，它就开口「我这刚醒」，还拿它躲问题
        self.assertNotIn("醒", line)
        self.assertIn("别拿它当不回答的理由", line)

    def test_morning_writes_no_state(self):
        """早上只留插嘴意愿打折；「你刚醒没多久」被它念成了躲问题的借口。"""
        self.assertEqual(activation.state_line("刚睡醒", 0.0), "")

    def test_bad_mood_is_visible(self):
        line = activation.state_line("", -0.5)
        self.assertIn("烦", line)

    def test_nothing_to_say_is_empty(self):
        self.assertEqual(activation.state_line("", 0.0), "")


class StateNowTest(unittest.TestCase):
    """2026-11-16 起只剩状态自述：亲密度不再决定背景条数，插嘴意愿乘数没人用，一并撤了。"""

    def test_state_now_is_the_line_for_this_hour_and_mood(self):
        activation._mood_state.update({"v": 0.0, "ts": time.time()})
        self.assertEqual(activation.state_now(hour=3), activation.state_line("凌晨被吵醒", 0.0))
        self.assertEqual(activation.state_now(hour=15), "")

    def test_affinity_no_longer_lives_here(self):
        self.assertFalse(hasattr(activation, "context_turns"))
        self.assertFalse(hasattr(activation, "profile"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
