"""2026-11-06 设计落地回归：状态动作层、控制台信息架构、桌宠入口收敛。

这里只做纯计算和只读断言，不需要临时目录，也不碰运行中的机器人。
"""
from importlib.resources import files
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch

import panel
from regular import presence
from regular.pet_brain import CLIPS, CUE_ACTIONS, STATE_CLIPS, STATE_HEARTBEAT, PetBrain


ROOT = Path(__file__).resolve().parents[1]


def brain(seed=7):
    b = PetBrain(rng=random.Random(seed), now=0.0)
    # Stable fallback poses isolate heartbeat and posture-bridge contracts.
    # Adopted-art variation is exercised by MotionRepertoireTest in test_pet_brain.
    b.repertoire.actions = {}
    b.set_ground(0, 1440, 860)
    b.place(700)
    return b


def run(b, start, seconds, fps=30):
    t = start
    clips = []
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        b.tick(t)
        if not clips or clips[-1] != b.clip:
            clips.append(b.clip)
    return t, clips


def ready(state=None, events=()):
    payload = {"bot": "ready", "available": True, "qq_connected": True, "mood": 0.0,
               "circadian": "", "chatter": 0, "events": list(events)}
    if state is not None:
        payload["state"] = state
    return payload


class ShippedStateSpritesTest(unittest.TestCase):
    def test_six_state_strips_ship_and_match_the_clip_table(self):
        root = files("regular").joinpath("assets", "pet")
        spec = json.loads(root.joinpath("sprites.json").read_text(encoding="utf-8"))
        self.assertEqual(set(STATE_CLIPS), {"reply", "listen", "inspect", "checkup", "handoff", "stuck"})
        for name, clip in STATE_CLIPS.items():
            with self.subTest(state=name):
                self.assertIn(clip, CLIPS)
                strip = CLIPS[clip][0]
                self.assertIn(strip, spec["actions"])
                self.assertEqual(spec["actions"][strip]["frames"], 4)
                self.assertLessEqual(max(CLIPS[clip][1]), 3)
                self.assertTrue(root.joinpath(spec["actions"][strip]["file"]).is_file())
                self.assertIn(clip, CUE_ACTIONS)
        total = sum(len(root.joinpath(a["file"]).read_bytes()) for a in spec["actions"].values())
        # Keep a bounded compressed library after the 232 new action strips.
        self.assertLess(total, 120_000 * len(spec["actions"]))


class StateLayerTest(unittest.TestCase):
    def test_heartbeat_holds_a_state_and_stopping_it_returns_to_daily_life(self):
        b = brain()
        b.observe(1.0, ready(state="reply"))
        t, clips = run(b, 1.0, 2)
        self.assertIn("reply", clips)
        self.assertEqual(b.clip, "reply")
        b.observe(t, ready(state="reply"))       # a heartbeat renews, it must not restart the clip
        self.assertEqual(b.clip, "reply")
        _, later = run(b, t, STATE_HEARTBEAT + 5)
        self.assertNotEqual(b.clip, "reply")
        self.assertIn("stand_up", later)         # leaving a seated state still uses a bridge
        self.assertNotEqual(later[-1], "reply")

    def test_a_seated_state_inserts_a_bridge_instead_of_jumping(self):
        b = brain()
        b.observe(1.0, ready(state="reply"))
        _, clips = run(b, 1.0, 3)
        self.assertEqual(clips[0], "sit_down")
        self.assertIn("reply", clips)

    def test_state_layer_beats_log_events_and_random_ambient(self):
        b = brain()
        b.observe(1.0, ready(state="reply", events=[{"kind": "replying", "ts": 1.0}]))
        _, clips = run(b, 1.0, 3)
        self.assertNotIn("phone", clips)        # no more "randomly looking at her phone"
        self.assertEqual(b.clip, "reply")

    def test_one_shot_state_events_expire_on_their_own(self):
        b = brain()
        b.observe(1.0, ready(events=[{"kind": "state", "id": "a", "ts": 1.0, "state": "handoff"}]))
        t, clips = run(b, 1.0, 2)
        self.assertIn("handoff", clips)
        _, later = run(b, t, 12)
        self.assertNotEqual(b.clip, "handoff")

    def test_clearing_a_real_long_state_ends_it_immediately(self):
        b = brain()
        b.set_state("listen", 1.0, seconds=3600.0)   # a live continuous voice session
        t, clips = run(b, 1.0, 2)
        self.assertEqual(b.clip, "listen")
        b.clear_state(t)
        _, later = run(b, t, 2)
        self.assertNotIn("listen", later)


class PresenceStateTest(unittest.TestCase):
    def test_recent_state_follows_the_last_decisive_event(self):
        rows = [{"kind": "incoming", "ts": 991.0, "at": True, "call": "at"},
                {"kind": "decision", "ts": 995.0, "selected": True}]
        with patch.object(presence, "_recent_rows", return_value=rows):
            self.assertEqual(presence._recent_state(None, 1000.0), "reply")
            rows.append({"kind": "delivery", "ts": 998.0, "status": "delivered", "route": "reply"})
            self.assertIsNone(presence._recent_state(None, 1000.0))
            rows[:] = [{"kind": "media", "ts": 997.0, "medium": "voice", "status": "started"}]
            self.assertEqual(presence._recent_state(None, 1000.0), "listen")


class ConsoleNavigationTest(unittest.TestCase):
    def test_the_sidebar_has_scenario_groups_and_no_catch_all_drawer(self):
        html = panel._page("测试", "<p>内容</p>", "home")
        self.assertNotIn("nav-more", html)
        self.assertNotIn("其他页面", html)
        self.assertEqual(html.count('class="nav-item'), 6)
        for href in ("/", "/companion", "/checkup", "/knowledge", "/capabilities", "/setup"):
            self.assertIn(f'href="{href}"', html)
        self.assertLess(html.index('href="/checkup"'), html.index('<span class="nav-label">设置</span>'))
        self.assertLess(html.index('<span class="nav-label">设置</span>'), html.index('href="/setup"'))

    def test_folded_pages_light_up_the_entry_they_live_under(self):
        for active, home in (("iterate", "/checkup"), ("memory", "/knowledge"),
                             ("decision", "/capabilities"), ("search", "/capabilities")):
            with self.subTest(active=active):
                html = panel._page("测试", "<p>内容</p>", active)
                self.assertIn(f'class="nav-item active" href="{home}"', html)

    def test_capabilities_voice_note_matches_the_continuous_conversation(self):
        source = ROOT.joinpath("panel.py").read_text(encoding="utf-8")
        self.assertIn("点一下开始连续对话，再点结束", source)
        self.assertNotIn("只在你按住说话时收音", source)
        self.assertIn("ms-advanced", source)


class PetMenuTest(unittest.TestCase):
    def test_native_context_menu_and_its_five_panel_shortcuts_are_gone(self):
        source = ROOT.joinpath("regular", "desktop_pet.py").read_text(encoding="utf-8")
        self.assertNotIn("QMenu(", source)
        self.assertNotIn("QActionGroup", source)
        self.assertNotIn('menu.addMenu("打开工作台")', source)
        self.assertIn("class PetMoreMenu", source)
        self.assertIn("class PetToolbar", source)

    def test_voice_session_drives_the_listen_state(self):
        b = brain()
        b.set_state("listen", 1.0, source="local")
        b.observe(2.0, ready(state="inspect", events=[
            {"kind": "state", "id": "a", "ts": 2.0, "state": "checkup"}]))
        self.assertEqual(b.state_name, "listen")
        _, clips = run(b, 2.0, STATE_HEARTBEAT + 10)
        self.assertEqual(b.clip, "listen")
        b.clear_state(30.0, source="local")
        b.observe(31.0, ready(state="inspect"))
        self.assertEqual(b.state_name, "inspect")


if __name__ == "__main__":
    unittest.main()


class ConsolePolishTest(unittest.TestCase):
    def test_pending_checkup_findings_show_as_a_badge_until_labelled(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            base = _Path(tmp) / "checkups"
            base.mkdir()
            cfg = SimpleNamespace(DATA_DIR=tmp, CHECKUP_DIR=str(base))
            (base / "2026-11-06.json").write_text(_json.dumps({"findings": [
                {"id": "a"}, {"id": "b"}, {"id": "c", "verdict": "no"}]}))
            self.assertEqual(panel._pending_findings(cfg), 2)
            self.assertIn('class="nav-badge" aria-label="2 条待看">2<', panel._page("t", "", "home", cfg=cfg))
            (base / "labels.jsonl").write_text(_json.dumps({"id": "a", "verdict": "yes"}) + "\n")
            self.assertEqual(panel._pending_findings(cfg), 1)
            (base / "labels.jsonl").write_text("\n".join(_json.dumps({"id": i, "verdict": "no"}) for i in "ab"))
            self.assertNotIn('class="nav-badge"', panel._page("t", "", "home", cfg=cfg))
        self.assertEqual(panel._pending_findings(None), 0)

    def test_knowledge_and_memory_are_two_tabs_of_one_entry(self):
        for active in ("knowledge", "memory"):
            tabs = panel._knowledge_tabs(active)
            self.assertIn('href="/knowledge"', tabs)
            self.assertIn('href="/memory"', tabs)
            self.assertEqual(tabs.count("aria-current=page"), 1)
