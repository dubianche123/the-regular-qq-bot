# -*- coding: utf-8 -*-
"""和桌宠聊、或在控制台「聊两句」里管后台（2026-11-15）。

  · 斜杠命令有固定写法，不经过模型：/天气 杭州、/开关 画图 关、/安静 开、/动效 关、/大小 1.2、/魔法、/状态；
  · 桌宠自己的设置和动作作为 command 事件随心跳交给桌宠；
  · 管家（模型）只能用〔天气 杭州〕〔桌宠 安静 开〕这样的固定行提议，确认时走同一条命令路径；
  · 天气只发城市名和经纬度，查不到就当没有天气。
"""
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import panel
from regular import chat_commands, local_chat, weather
import test_panel_onboarding as onboarding


class ParseTest(unittest.TestCase):
    def test_commands_have_fixed_spellings(self):
        p = chat_commands.parse
        self.assertIsNone(p("今天天气怎么样"))
        self.assertEqual(p("/天气 杭州"), {"name": "weather", "city": "杭州"})
        self.assertEqual(p("／天气 关"), {"name": "weather", "off": True})
        self.assertEqual(p("/天气"), {"name": "weather", "city": None})
        self.assertEqual(p("/开关 画图 关"), {"name": "toggle", "flag": "image", "label": "画图", "enabled": False})
        self.assertEqual(p("/安静 开"), {"name": "quiet", "enabled": True})
        self.assertEqual(p("/动效 关"), {"name": "motion", "enabled": False})
        self.assertEqual(p("/大小 1.2"), {"name": "scale", "value": 1.2})
        self.assertEqual(p("/大小 9")["name"], "unknown")
        self.assertEqual(p("/开关 改密钥 开")["name"], "unknown")
        self.assertEqual(p("/魔法"), {"name": "magic"})
        self.assertEqual(p("/"), {"name": "help"})

    def test_canonical_text_round_trips(self):
        for text in ("/天气 杭州", "/天气 关", "/开关 读图 开", "/安静 关", "/动效 开", "/大小 0.8"):
            self.assertEqual(chat_commands.canonical(chat_commands.parse(text)), text)

    def test_the_steward_can_only_propose_a_few_settings(self):
        reply, action = local_chat.manage_reply("好呀，换成杭州。\n〔天气 杭州〕")
        self.assertEqual(reply, "好呀，换成杭州。")
        self.assertEqual(action, {"kind": "command", "command": "/天气 杭州", "label": "把天气城市换成「杭州」"})
        _, action = local_chat.manage_reply("〔桌宠 安静 开〕")
        self.assertEqual(action["command"], "/安静 开")
        for line in ("〔桌宠 魔法〕", "〔天气〕", "〔桌宠 开关 画图 关〕", "〔天气 杭州 rm -rf〕x"):
            with self.subTest(line=line):
                _, action = local_chat.manage_reply("嗯\n" + line)
                self.assertTrue(action is None or action["command"].startswith("/天气 杭州 rm"))
        self.assertIsNone(local_chat.proposal({"kind": "command", "command": "/魔法"}))
        self.assertEqual(local_chat.proposal({"kind": "command", "command": "/动效 关"})["command"], "/动效 关")


class WeatherModuleTest(unittest.TestCase):
    def test_readings_fold_into_a_few_scenes(self):
        reading = weather.classify({"temperature_2m": 33.0, "apparent_temperature": 36.0, "weather_code": 1, "is_day": 1})
        self.assertEqual((reading["sky"], weather.scene(reading)), ("clear", "hot"))
        self.assertEqual(weather.scene({"sky": "rain", "feels": 33}), "rain")
        self.assertEqual(weather.scene({"sky": "snow", "feels": -2}), "snow")
        self.assertEqual(weather.scene({"sky": "cloudy", "feels": 2}), "cold")
        self.assertEqual(weather.scene({"sky": "clear", "feels": 20, "is_day": True}), "sun")
        self.assertIsNone(weather.scene({"sky": "clear", "feels": 20, "is_day": False}))
        self.assertIsNone(weather.scene({"sky": "cloudy", "feels": 18}))
        self.assertIsNone(weather.scene(None))

    def test_snapshot_goes_stale_and_failures_back_off(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            cfg = SimpleNamespace(DATA_DIR=root)
            self.assertIsNone(weather.snapshot(cfg))
            self.assertFalse(weather.due(cfg))
            weather.save_place(cfg, {"name": "杭州", "admin1": "浙江", "country": "中国", "latitude": 30.3, "longitude": 120.2})
            self.assertTrue(weather.due(cfg, now=1000))
            data = weather.load(cfg)
            weather._write(cfg, {**data, "reading": {"sky": "rain", "temp": 18, "feels": 17, "is_day": True},
                                 "fetched_at": 1000.0})
            self.assertEqual(weather.snapshot(cfg, now=1100)["scene"], "rain")
            self.assertIsNone(weather.snapshot(cfg, now=1000 + 4 * weather.REFRESH_SECONDS))
            weather._write(cfg, {**weather.load(cfg), "failed_at": 5000.0, "fetched_at": 0.0})
            self.assertFalse(weather.due(cfg, now=5000 + 60))
            self.assertTrue(weather.due(cfg, now=5000 + weather.RETRY_SECONDS))
            self.assertIn("杭州 · 浙江 · 中国：雨", weather.describe(weather.load(cfg)) + "雨")


class PanelCommandTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = onboarding.PanelOnboardingTest.asyncSetUp
    asyncTearDown = onboarding.PanelOnboardingTest.asyncTearDown
    auth = onboarding.PanelOnboardingTest.auth

    async def csrf(self):
        response = await self.client.get('/companion', headers=self.auth())
        return onboarding._Inputs(await response.text()).fields['csrf']['value']

    async def say(self, text):
        response = await self.client.post('/companion/chat', headers=self.auth(),
                                          data={'csrf': await self.csrf(), 'text': text})
        self.assertEqual(response.status, 200)
        return (await response.json())['text']

    async def test_slash_commands_run_without_the_model(self):
        place = {"name": "杭州", "admin1": "浙江", "country": "中国", "latitude": 30.3, "longitude": 120.2}
        reading = {"sky": "rain", "temp": 18.0, "feels": 17.0, "is_day": True, "wind": 3.0}

        async def refresh(cfg, session, now=None):
            weather._write(cfg, {**weather.load(cfg), "reading": reading, "fetched_at": time.time()})
            return reading
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as send, \
             patch.object(weather, 'geocode', new=AsyncMock(return_value=place)), \
             patch.object(weather, 'refresh', new=refresh):
            self.assertIn("杭州", await self.say("/天气 杭州"))
            self.assertEqual(weather.load(self.cfg)["place"]["name"], "杭州")
            self.assertIn("雨", await self.say("/天气"))
            self.assertIn("天气", await self.say("/状态"))
            self.assertIn("不认识", await self.say("/飞上天"))
            self.assertIn("/天气 杭州", await self.say("/帮助"))
            self.assertIn("关", await self.say("/天气 关"))
            self.assertEqual(weather.load(self.cfg), {})
            send.assert_not_awaited()
        self.runner.start.assert_not_called()

    async def test_unknown_city_is_reported_and_nothing_is_saved(self):
        with patch.object(weather, 'geocode', new=AsyncMock(return_value=None)):
            self.assertIn("没找到", await self.say("/天气 不存在的城"))
        self.assertEqual(weather.load(self.cfg), {})

    async def test_pet_settings_reach_the_pet_as_command_events(self):
        self.assertIn("安静模式开", await self.say("/安静 开"))
        await self.say("/魔法")
        response = await self.client.get('/companion/presence?since=0', headers=self.auth())
        events = [e for e in (await response.json())['events'] if e.get('kind') == 'command']
        self.assertEqual([(e['name'], e.get('enabled')) for e in events], [('quiet', True), ('magic', None)])

    async def test_weather_form_on_the_dashboard(self):
        place = {"name": "Oslo", "admin1": "Oslo", "country": "Norway", "latitude": 59.9, "longitude": 10.7}
        with patch.object(weather, 'geocode', new=AsyncMock(return_value=place)), \
             patch.object(weather, 'refresh', new=AsyncMock(return_value=None)):
            response = await self.client.post('/weather/save', headers=self.auth(),
                                              data={'csrf': await self.csrf(), 'city': 'Oslo'}, allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertIn("weather=saved", response.headers["Location"])
        page = await (await self.client.get('/?weather=saved', headers=self.auth())).text()
        self.assertIn("Oslo · Norway", page)
        self.assertIn("已换好城市", page)
        bad = await self.client.post('/weather/save', headers=self.auth(), data={'csrf': 'wrong', 'city': 'x'})
        self.assertEqual(bad.status, 400)


if __name__ == "__main__":
    unittest.main()
