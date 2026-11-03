# -*- coding: utf-8 -*-
"""split_cmd 对打坏的协议标签的容错（2026-10-15 线上实测）。

模型偶尔把记账标签打坏成 `<CMD!` 这类残片——正规正则不匹配，整段连着
`&*%"#$#` 一起漏进群里，群友看到的就是乱码（当天老王回「小王坏掉了」）。
协议规定标签永远在最末尾，所以正文里残留任何 <CMD 片段，从那里砍到结尾，
原文进日志备查。
"""
import sys
import unittest

sys.path.insert(0, "..")

import bot  # noqa: E402


class SplitCmdGarbageTest(unittest.TestCase):

    def test_malformed_tag_remnant_is_cut(self):
        """`<CMD!` 不匹配正规标签——必须从 <CMD 起截断，不许漏进群里。"""
        body, payload = bot.split_cmd("想卖&*%#想<CMD!卖了换排骨汤，你还没那2斤肉沉")
        self.assertNotIn("<CMD", body)
        self.assertNotIn("卖了换排骨汤", body, "残片之后的内容一并砍掉")
        self.assertIsNone(payload)

    def test_wellformed_tag_still_parsed(self):
        """正规路径不许被新截断逻辑误伤。"""
        body, payload = bot.split_cmd("行吧<CMD>{\"aff\":-1}</CMD>")
        self.assertEqual(body, "行吧")
        self.assertEqual(payload, {"aff": -1})

    def test_clean_text_untouched(self):
        body, payload = bot.split_cmd("正常说话，没有标签")
        self.assertEqual(body, "正常说话，没有标签")
        self.assertIsNone(payload)

    def test_html_wrapper_is_stripped(self):
        """2026-11-01 网关 glm 把整段回复包成 <p>…</p>，原样进了群、又被下一条学去。"""
        body, payload = bot.split_cmd("<p>能看个趋势<br/>别急着下结论</p><CMD>{\"aff\":1}</CMD>")
        self.assertEqual(body, "能看个趋势别急着下结论")
        self.assertEqual(payload, {"aff": 1})
        self.assertEqual(bot.split_cmd("<p>行吧</p>")[0], "行吧")
        self.assertEqual(bot.split_cmd("我<3你，1<2 也成立")[0], "我<3你，1<2 也成立")


class SkewedCmdTagTest(unittest.TestCase):
    """2026-11-03 下午 gpt-6-luna 把记账标签写歪、原样发进群的四条（interactions 原文）。

    括号换成了〔〈，或者 < 后面多个空格，收尾的 </CMD> 全丢了——旧正则只认 <CMD>…</CMD>
    和 <CMD 开头，四条一条都没拦住。
    """

    LEAKED = (
        ('这位女仆看着像刚发现今天的菜单里没有休息日〔CMD>{"aff":0}',
         "这位女仆看着像刚发现今天的菜单里没有休息日", 0),
        ('老王这句我记下了，终于有合法摸鱼许可证了，龙少那边我先假装没看见😌\n< CMD>{"aff":1}',
         "老王这句我记下了，终于有合法摸鱼许可证了，龙少那边我先假装没看见😌", 1),
        ('好，这版只留四张侧脸，脖子往下直接裁掉\n< CMD>{"aff":0}',
         "好，这版只留四张侧脸，脖子往下直接裁掉", 0),
        ('哦，我刚才认错图了，这边没看到你说的那张具体画面，刚才那段解读作废了〈CMD>{"aff":0}',
         "哦，我刚才认错图了，这边没看到你说的那张具体画面，刚才那段解读作废了", 0),
    )

    def test_leaked_tags_are_split_off_and_still_scored(self):
        for raw, body, aff in self.LEAKED:
            with self.subTest(raw=raw):
                self.assertEqual(bot.split_cmd(raw), (body, {"aff": aff}))

    def test_other_brackets_and_closers(self):
        for raw in ('行〔CMD〕{"aff":-1}〔/CMD〕', '行【CMD】{"aff":-1}', '行＜CMD＞{"aff": -1}＜/CMD＞',
                    '行CMD>{"aff":-1}'):
            with self.subTest(raw=raw):
                self.assertEqual(bot.split_cmd(raw), ("行", {"aff": -1}))

    def test_tag_written_first_keeps_the_words_after_it(self):
        self.assertEqual(bot.split_cmd('〔CMD>{"aff":0}\n正文在后面'), ("正文在后面", {"aff": 0}))

    def test_unparseable_payload_is_still_cut(self):
        self.assertEqual(bot.split_cmd('行吧〔CMD>{“aff”:0}'), ("行吧", None))

    def test_ordinary_brackets_untouched(self):
        for text in ("〈红楼梦〉里那段", "【公告】今晚八点", "打开CMD窗口输 ipconfig",
                     '{"aff":0} 这是一段 JSON', "[图片] 这张〔不是标记〕"):
            with self.subTest(text=text):
                self.assertEqual(bot.split_cmd(text), (text, None))


class GarbageRunDetectorTest(unittest.TestCase):
    """采样乱码检测器：两个历史形态都命中，正常缩写不误伤。"""

    def test_hex_percent_shape_is_caught(self):
        run = bot._garbage_run("哥哥0A9F%F5块钱的那个瀑布过滤算不算")
        self.assertIsNotNone(run)
        self.assertIn("%", run)

    def test_symbol_run_shape_is_caught(self):
        self.assertIsNotNone(bot._garbage_run("想卖&*%#想说话"))

    def test_normal_abbreviations_pass(self):
        for text in ("你这话把我 CPU 干烧了", "我加92的", "这开关带1080P吗",
                     "boss真难打", "GG了兄弟们", "问个正经问题"):
            self.assertIsNone(bot._garbage_run(text), text)

    def test_long_model_numbers_are_accepted_false_positive(self):
        """RTX4090 这类长型号会误报——设计上只重试一次不吞回复，如实钉住。"""
        self.assertIsNotNone(bot._garbage_run("你这卡是RTX4090吧"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
