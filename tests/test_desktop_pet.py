"""Optional Qt pet: transparent animated character, proximity palette, bubbles and async requests."""
import importlib.util
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
    from PySide6.QtGui import QImage, QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWidgets import QPushButton
    from regular import desktop_pet
    from regular.desktop_pet import PetWidget, SpriteBank
    from regular.pet_brain import Step, TAP_POOL

CSRF = "synthetic-csrf-0123456789"


@unittest.skipUnless(HAS_QT, "optional desktop extra is not installed")
class DesktopPetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen", "REGULAR_REDUCE_MOTION": ""})
        cls.env.start()
        cls.app = QApplication.instance() or QApplication(["regular-pet-tests"])
        cls.sprites = SpriteBank()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def setUp(self):
        self.client = Mock()
        self.t = [100.0]
        self.pet = self.make()

    def make(self, path=None):
        pet = PetWidget(self.client, quit_on_close=False, preferences_path=path, rng=random.Random(4),
                        clock=lambda: self.t[0], sprites=self.sprites)
        for timer in (pet.animation, pet.poll, pet.presence_poll):
            timer.stop()
        return pet

    def tearDown(self):
        self.pet.close()
        self.pet.pool.waitForDone(1000)
        self.app.processEvents()

    def advance(self, seconds, fps=30, pet=None):
        pet = pet or self.pet
        for _ in range(int(seconds * fps)):
            self.t[0] += 1 / fps
            pet.step()

    def settle(self):
        """Finish the drop-in entrance and stand idle."""
        self.advance(1.5)
        from regular.pet_brain import Step
        self.pet.brain._queue(Step("idle", 60), now=self.t[0], interrupt=True)
        self.pet.step()

    def ready(self):
        self.pet._finished("session", {"ok": True, "value": {
            "csrf": CSRF, "available": True, "reason": "", "qq_connected": True}})

    def mouse(self, kind, where, *, button=None, buttons=None):
        if button is None:
            button = Qt.MouseButton.LeftButton
        return QMouseEvent(kind, QPointF(10, 10), QPointF(*where), button,
                           button if buttons is None else buttons, Qt.KeyboardModifier.NoModifier)

    def test_character_is_a_transparent_floating_window_with_a_click_through_effect_layer(self):
        pet = self.pet
        for window in (pet, pet.toolbar, pet.bubble, pet.fx):
            self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        self.assertTrue(pet.fx.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertFalse(pet.toolbar.isVisible())
        self.assertIsNone(pet.chat_bar)
        self.assertIsNone(pet.activity_card)
        self.client.send.assert_not_called()

    def test_she_drops_in_lands_and_keeps_animating(self):
        self.pet.show()
        self.assertEqual(self.pet.brain.mode, "air")
        self.advance(1.5)
        self.assertEqual(self.pet.brain.mode, "ground")
        floor = self.pet.brain.floor
        self.assertEqual(self.pet.brain.y, floor)
        frames, positions = set(), set()
        for _ in range(60 * 3):
            self.advance(1)
            strip, frame, *_ = self.pet.brain.pose()
            frames.add((strip, frame))
            positions.add(self.pet.pos().x())
        self.assertGreater(len(frames), 8)
        self.assertGreater(len(positions), 3)          # she walks around on her own
        anchor = self.pet.window_anchor()
        self.assertAlmostEqual(self.pet.pos().y() + anchor.y(), floor, delta=1)

    def test_dragging_moves_the_window_and_release_falls_back_to_the_floor(self):
        self.settle()
        pet = self.pet
        start = pet.pos()
        pet.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, (pet.brain.x, pet.brain.y - 80)))
        for i in range(1, 12):
            self.t[0] += 1 / 60
            pet.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove, (pet.brain.x + 15, 400 - i * 10),
                                          button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton))
            pet.step()
        self.assertEqual(pet.brain.clip, "dangle")
        self.assertNotEqual(pet.pos(), start)
        self.assertTrue(pet.mask().isEmpty())           # rotated frames are not clipped while held
        pet.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, (pet.brain.x, 290),
                                         buttons=Qt.MouseButton.NoButton))
        self.assertEqual(pet.brain.mode, "air")
        self.advance(2, fps=60)
        self.assertEqual(pet.brain.mode, "ground")
        self.assertFalse(pet.mask().isEmpty())
        self.assertFalse(pet.mask().contains(QPoint(0, 0)))

    def test_press_squishes_at_once_click_reacts_and_double_click_stays_with_the_pet(self):
        self.settle()
        pet = self.pet
        where = (pet.brain.x, pet.brain.y - 80)
        pet.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, where))
        self.advance(0.1)
        self.assertLess(pet.brain.transform(self.t[0])["sy"], 0.97)      # immediate feedback
        pet.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, where, buttons=Qt.MouseButton.NoButton))
        self.assertIn(pet.brain.clip, TAP_POOL)                           # 点击立刻有小幅反应
        self.assertNotIn(pet.brain.clip, ("pout", "point"))               # 不再跺脚 / 指人
        self.assertLess(abs(pet.brain.transform(self.t[0] + 0.1)["dy"]), 1.0)  # 点击不弹跳
        self.advance(0.5)
        # Qt replaces the second press with MouseButtonDblClick.
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonDblClick, where))
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonRelease, where, buttons=Qt.MouseButton.NoButton))
        self.assertEqual(len(pet.brain.pokes), 2)
        self.assertIsNone(pet.chat_bar)                                    # 双击不再抢点击弹对话框
        pet.toolbar.chat.click()                                           # 聊天只走圆盘的「聊两句」
        self.assertTrue(pet.chat_bar.isVisible())

    def test_double_click_during_a_rally_never_opens_chat(self):
        self.settle()
        pet = self.pet
        pet.play()
        where = (pet.brain.x, pet.brain.y - 80)
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonPress, where))
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonRelease, where, buttons=Qt.MouseButton.NoButton))
        self.advance(.6)       # she raises her hand for the next round
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonDblClick, where))
        self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonRelease, where, buttons=Qt.MouseButton.NoButton))
        self.assertIsNone(pet.chat_bar)
        self.assertEqual(pet.brain.game_streak, 2)                       # 点击全归游戏
        self.assertEqual(len(pet.brain.pokes), 2)
        pet.toolbar.chat.click()
        self.assertTrue(pet.chat_bar.isVisible())
        self.assertFalse(pet.brain.game_active)                           # 聊天打开，本局取消但不算输
        self.assertEqual(pet.brain.game_best, 2)

    def test_four_fast_clicks_are_four_interactions_with_qt_double_click_events(self):
        self.settle()
        pet = self.pet
        where = (pet.brain.x, pet.brain.y - 80)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                     QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            self.app.sendEvent(pet, self.mouse(kind, where))
            self.t[0] += .05
            self.app.sendEvent(pet, self.mouse(QEvent.Type.MouseButtonRelease, where,
                                              buttons=Qt.MouseButton.NoButton))
        self.assertEqual(len(pet.brain.pokes), 4)
        self.assertIsNone(pet.chat_bar)

    def test_nearby_cursor_reveals_the_side_palette_and_leaving_hides_it_later(self):
        self.settle()
        pet = self.pet
        pet.show()
        near = QPoint(round(pet.brain.x), round(pet.brain.y - 60))
        with patch.object(desktop_pet.QCursor, "pos", return_value=near):
            self.advance(0.2)
        self.assertTrue(pet.toolbar.isVisible())
        self.assertTrue(pet.brain.attention)
        left, top, right, bottom = pet.brain.body_rect()
        self.assertFalse(QRect(round(left), round(top), round(right - left), round(bottom - top))
                         .intersects(pet.toolbar.geometry().adjusted(4, 4, -4, -4)))
        with patch.object(desktop_pet.QCursor, "pos", return_value=QPoint(-5000, -5000)):
            self.advance(0.3)
            self.assertTrue(pet.toolbar.isVisible())
            self.advance(0.8)
        self.assertFalse(pet.toolbar.isVisible())

    def test_chat_pill_never_covers_the_palette_and_activity_card_fits_its_entries(self):
        self.settle()
        pet = self.pet
        pet.show()
        near = QPoint(round(pet.brain.x), round(pet.brain.y - 60))
        with patch.object(desktop_pet.QCursor, "pos", return_value=near):
            self.advance(0.2)
            pet.show_chat()
            self.advance(0.2)
        self.assertTrue(pet.toolbar.isVisible())
        self.assertFalse(pet.chat_bar.geometry().intersects(pet.toolbar.geometry()))
        self.assertEqual(pet.toolTip(), "")          # status lives in the activity card, not over the palette
        pet.show_activity()
        empty = pet.activity_card.height()
        for i in range(8):
            pet._record(f"合成动态 {i}")
        self.assertGreater(pet.activity_card.height(), empty)
        self.assertLessEqual(pet.activity_card.height(), 420)

    def test_notifications_and_settings_dismiss_on_an_outside_click(self):
        self.settle()
        pet = self.pet
        pet.show()
        far = QPoint(4000, 3000)
        pet.show_activity()
        self.assertTrue(pet.activity_card.isVisible())
        pet.toolbar.hide()
        pet.dismiss_on_outside_click(far)
        self.assertFalse(pet.activity_card.isVisible())
        pet.show_menu()
        self.assertTrue(pet._menu.isVisible())
        pet.dismiss_on_outside_click(far)
        self.assertFalse(pet._menu.isVisible())

    def test_notifications_and_settings_are_mutually_exclusive_and_toggle(self):
        self.settle()
        pet = self.pet
        pet.show()
        pet.toolbar.show()
        pet._place_toolbar()
        pet.show_menu()
        self.assertTrue(pet._menu.isVisible())
        pet.show_activity()
        self.assertFalse(pet._menu.isVisible())
        self.assertTrue(pet.activity_card.isVisible())
        pet.show_menu()
        self.assertFalse(pet.activity_card.isVisible())
        self.assertTrue(pet._menu.isVisible())
        pet.show_menu()                                  # 再点一次「更多」就收起
        self.assertFalse(pet._menu.isVisible())
        pet.show_activity()
        self.assertTrue(pet.activity_card.isVisible())
        pet.show_activity()                              # 再点一次通知就收起
        self.assertFalse(pet.activity_card.isVisible())

    def test_ring_has_five_separate_buttons_and_play_reacts(self):
        self.settle()
        pet = self.pet
        buttons = (pet.toolbar.play, pet.toolbar.chat, pet.toolbar.voice,
                   pet.toolbar.activity, pet.toolbar.more)
        self.assertEqual(len({(b.x(), b.y()) for b in buttons}), 5)
        for i, first in enumerate(buttons):
            for second in buttons[i + 1:]:
                self.assertFalse(first.geometry().intersects(second.geometry()))
        pet.play()
        self.assertEqual(pet.brain.clip, "highfive")       # 击掌接龙，不再是随机小动作
        self.assertTrue(pet.brain.game_active)
        self.assertGreater(pet.brain.game_until, self.t[0])
        self.assertNotIn(pet.brain.clip, ("pout", "point"))

    def test_a_gesture_never_makes_the_whole_figure_translucent(self):
        """交叉淡入曾经把整个人画成半透明，每换一帧就闪一下。"""
        self.settle()
        pet = self.pet
        pet.brain._queue(Step("pat"), now=self.t[0], interrupt=True)

        def opaque_ratio():
            img = pet.grab().toImage().convertToFormat(QImage.Format_ARGB32)
            solid = opaque = 0
            for y in range(0, img.height(), 4):
                for x in range(0, img.width(), 4):
                    alpha = (img.pixel(x, y) >> 24) & 255
                    if alpha > 8:
                        solid += 1
                        opaque += alpha > 245
            return opaque / max(1, solid)

        ratios = []
        for _ in range(24):
            self.t[0] += 1 / 30
            pet.step(self.t[0])
            self.app.processEvents()
            ratios.append(opaque_ratio())
        self.assertGreater(min(ratios), 0.6)

    def test_toolbar_actions_are_explicit(self):
        self.settle()
        pet = self.pet
        pet.toolbar.activity.click()
        self.assertTrue(pet.activity_card.isVisible())
        pet.toolbar.show()
        pet._place_toolbar()
        pet.toolbar.more.click()                                     # 更多: beside the ring, not at the pointer
        menu = pet._menu
        self.assertTrue(menu.isVisible())
        ring, panel = pet.toolbar.geometry(), menu.geometry()
        self.assertFalse(ring.intersects(panel))
        self.assertLess(abs(panel.center().y() - ring.center().y()), panel.height() // 2 + 1)
        feed = next(b for b in menu.findChildren(QPushButton) if b.text() == "投喂一块小饼干")
        feed.click()
        self.assertEqual(pet.brain.clip, "eat")
        with patch.object(desktop_pet.webbrowser, "open") as browser:
            self.client.management_url.return_value = "http://127.0.0.1:8765/?token=x"
            next(b for b in menu.findChildren(QPushButton) if b.text() == "打开工作台").click()
        browser.assert_called_once_with("http://127.0.0.1:8765/?token=x")
        self.client.management_url.assert_called_once_with("/")   # the overview leads to setup while it is unfinished
        pet.toolbar.chat.click()
        self.assertTrue(pet.chat_bar.isVisible())
        self.client.send.assert_not_called()

    def test_a_manager_proposal_waits_for_a_click_before_any_switch_moves(self):
        from regular.desktop import Reply
        self.settle()
        self.ready()
        pet = self.pet
        reply = Reply("画图先关一下？你点个确认。")
        reply.action = {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        pet._finished("chat", {"ok": True, "value": reply})
        card = pet.proposal_card
        self.assertTrue(card.isVisible())
        self.assertIn("关掉「画图」", card.label.text())
        self.client.toggle.assert_not_called()
        card.no.click()                                   # 算了: nothing is sent
        self.app.processEvents()
        self.client.toggle.assert_not_called()
        pet._finished("chat", {"ok": True, "value": reply})
        self.client.toggle.return_value = "机器人在运行"
        pet.proposal_card.yes.click()
        for _ in range(100):
            self.app.processEvents()
            if "toggle" not in pet._jobs:
                break
            QTest.qWait(1)
        self.client.toggle.assert_called_once_with("image", False, CSRF)
        self.assertIn("已保存", pet.brain.bubble.text)
        self.assertIn("画图", pet.brain.bubble.text)
        self.assertIn("机器人在运行", pet.brain.bubble.text)
        handoff = Reply("这得改代码。")
        handoff.action = {"kind": "handoff"}
        pet._finished("chat", {"ok": True, "value": handoff})
        with patch.object(desktop_pet.webbrowser, "open"):
            pet.proposal_card.yes.click()
        self.client.management_url.assert_called_with("/iterate")

    def test_confirmation_keeps_her_in_place_and_has_real_transparent_corners(self):
        self.settle()
        from regular.pet_brain import Step
        self.pet.brain._queue(Step("walk", target_x=self.pet.brain.x + 150), now=self.t[0], interrupt=True)
        self.pet.propose({"kind": "toggle", "flag": "image", "label": "画图", "enabled": False})
        x = self.pet.brain.x
        self.advance(.5)
        self.assertEqual(self.pet.brain.x, x)
        self.assertTrue(self.pet._attention())
        shot = self.pet.proposal_card.grab().toImage()
        self.assertEqual(shot.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(shot.pixelColor(shot.width() // 2, shot.height() // 2).alpha(), 0)
        self.pet.proposal_card.no.click()
        self.assertFalse(self.pet._attention())

    def test_a_pending_toggle_keeps_its_own_identity_and_next_confirmation_waits(self):
        self.settle()
        self.ready()
        first = {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        second = {"kind": "toggle", "flag": "voice_output", "label": "发语音", "enabled": True}
        with patch.object(self.pet, "_submit") as submit:
            self.pet.propose(first)
            self.pet.confirm_proposal(first)
            self.pet._jobs["toggle"] = object()  # the first authenticated request is still in flight
            self.pet.propose(second)
            self.pet.confirm_proposal(second)
            self.assertEqual(submit.call_count, 1)
            self.assertIs(self.pet._pending_toggle, first)
            self.assertTrue(self.pet.proposal_card.isVisible())
            self.assertFalse(self.pet.proposal_card.yes.isEnabled())
            self.pet._finished("toggle", {"ok": True, "value": "正在运行的机器人还不认这里的开关：更新并重启后才生效。"})
            self.assertIn("画图", self.pet.brain.bubble.text)
            self.assertIn("更新并重启后才生效", self.pet.brain.bubble.text)
            self.assertNotIn("画图关掉了", self.pet.brain.bubble.text)
            self.assertIsNone(self.pet._pending_toggle)
            self.assertTrue(self.pet.proposal_card.yes.isEnabled())
            self.pet.confirm_proposal(second)
            self.assertEqual(submit.call_count, 2)

    def test_dismissed_or_superseded_confirmation_cannot_execute_an_old_action(self):
        self.settle()
        self.ready()
        old = {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        new = {"kind": "handoff"}
        with patch.object(self.pet, "_submit") as submit:
            self.pet.propose(old)
            self.pet.proposal_card.no.click()
            self.pet.confirm_proposal(old)
            self.pet.propose(new)
            self.pet.confirm_proposal(old)
        submit.assert_not_called()
        self.client.toggle.assert_not_called()

    def test_pending_toggle_does_not_keep_the_qt_pool_alive_on_exit(self):
        self.settle()
        self.ready()
        entered, release = threading.Event(), threading.Event()
        def save(flag, enabled, csrf):
            entered.set()
            release.wait(2)
            return "已保存"
        self.client.toggle.side_effect = save
        action = {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        self.pet.propose(action)
        self.pet.proposal_card.yes.click()
        self.assertTrue(entered.wait(1))
        self.pet.close()
        self.assertTrue(self.pet.pool.waitForDone(10))
        release.set()
        for _ in range(100):
            self.app.processEvents()
            if "toggle" not in self.pet._jobs:
                break
            QTest.qWait(1)
        self.assertNotIn("toggle", self.pet._jobs)

    def test_failed_toggle_does_not_mark_a_separate_inflight_chat_as_finished(self):
        self.settle()
        self.ready()
        self.pet.chat_busy = True
        self.pet.brain.chat_sent(self.t[0])
        self.pet._pending_toggle = {"kind": "toggle", "flag": "image", "label": "画图", "enabled": False}
        self.pet._finished("toggle", {"ok": False, "message": "开关尚未保存，请稍后重试。"})
        self.assertTrue(self.pet.chat_busy)
        self.assertTrue(self.pet.brain.chat_pending)

    def test_replaced_confirmation_cards_and_exit_do_not_crash_qt_destruction(self):
        script = r'''
import gc
from unittest.mock import Mock
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from regular.desktop_pet import PetWidget
app = QApplication([])
client = Mock()
client.toggle.return_value = "已保存；下次启动使用。"
pet = PetWidget(client, quit_on_close=False)
for timer in (pet.animation, pet.poll, pet.presence_poll): timer.stop()
pet.available, pet.csrf = True, "synthetic-csrf-0123456789"
for n in range(18):
    pet.propose({"kind":"toggle", "flag":"image", "label":"画图", "enabled":False})
    if n % 3 == 0:
        pet.proposal_card.no.click()
    elif n % 3 == 1:
        pet.proposal_card.yes.click()
        for _ in range(100):
            app.processEvents()
            if "toggle" not in pet._jobs: break
            QTest.qWait(1)
        assert "toggle" not in pet._jobs
    else:
        pet.propose({"kind":"handoff"})
        pet.proposal_card.close()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
pet.close()
pet.deleteLater()
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
del pet
gc.collect()
print("confirmation ownership and exit passed")
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("confirmation ownership and exit passed", result.stdout)

    def test_the_last_typed_characters_are_painted_too(self):
        """2026-11-06：投喂后的气泡停在「谢谢投」—— 补齐最后几个字的那一帧没有重绘。"""
        self.settle()
        pet = self.pet
        pet.brain.say("谢谢投喂～", now=self.t[0])
        with patch.object(pet.bubble, "update", side_effect=lambda: pet.bubble.grab()):   # paint only when asked
            for _ in range(12):
                self.t[0] += 1 / 30
                pet.step()
        self.assertEqual(pet.bubble.painted, "谢谢投喂～")
        self.assertFalse(pet.bubble.needs_paint(self.t[0]))

    def test_chat_is_single_flight_and_the_reply_comes_back_as_her_speech_bubble(self):
        self.settle()
        self.ready()
        pet = self.pet
        pet.show_chat()
        bar = pet.chat_bar
        bar.entry.setText("<img src='never-fetch-this'>合成文字")
        with patch.object(pet, "_submit") as submit:
            bar.send()
            bar.send()
        submit.assert_called_once()
        self.assertEqual(pet.brain.clip, "think")
        submit.call_args.args[1]()
        self.client.send.assert_called_once_with("<img src='never-fetch-this'>合成文字", CSRF)
        pet._finished("chat", {"ok": True, "value": "<script>这也是纯文本</script>"})
        pet.step()
        self.assertEqual(pet.brain.clip, "talk")
        self.assertTrue(pet.bubble.isVisible())
        self.assertEqual(pet.bubble.text, "<script>这也是纯文本</script>")
        self.assertLessEqual(pet.bubble.geometry().bottom(), bar.geometry().bottom())
        bar.close()
        self.app.processEvents()
        self.assertFalse(pet._closing)
        self.assertFalse(self.app.quitOnLastWindowClosed())

    def test_presence_drives_her_and_is_logged_in_activity_once(self):
        self.settle()
        pet = self.pet
        payload = {"ok": True, "value": {"bot": "ready", "mood": 0.2, "events": [
            {"kind": "mention", "ts": 50.0}, {"kind": "said", "ts": 51.0},
            {"kind": "cue", "id": "c1", "ts": 52.0, "action": "cheer", "text": "测试全过了"}]}}
        pet._finished("presence", payload)
        pet._finished("presence", payload)
        texts = [text for _, text in pet.activity]
        self.assertEqual(texts, ["群里有人@了她", "她在群里回了一句", "Agent：测试全过了"])
        self.assertEqual(pet._presence_since, 51.0)
        self.assertEqual(pet.brain.clip, "cheer")
        self.assertEqual(pet._unread, 3)
        pet.show_activity()
        self.assertEqual(pet._unread, 0)
        pet._finished("presence", {"ok": False, "message": "old panel"})

    def test_preferences_restore_scale_position_and_modes_and_migrate_the_old_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "desktop-pet.json"
            first = self.make(path)
            self.assertFalse(path.exists())
            first.brain.place(600)
            first.set_scale(1.2)
            first.set_quiet(True)
            first.set_motion(False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved, {"scale": 1.2, "x": 600.0, "quiet": True, "motion": False})
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            size = first.size()
            first.close()
            with patch("regular.desktop_pet.atomic_write_json") as write:
                second = self.make(path)
                write.assert_not_called()       # restoring is read-only
            self.assertEqual(second.brain.scale, 1.2)
            self.assertTrue(second.brain.quiet)
            self.assertFalse(second.brain.motion)
            self.assertEqual(second.brain.x, 600.0)
            self.assertEqual(second.size(), size)
            second.close()
            path.write_text(json.dumps({"scale": 0.85, "motion": True, "position": [640, 120]}))
            old = self.make(path)
            self.assertEqual((old.brain.scale, old.brain.x, old.brain.quiet), (0.85, 640.0, False))
            old.close()
            for text in ("not json", json.dumps({"scale": True, "x": 1, "quiet": False, "motion": True}),
                         json.dumps({"scale": 9, "x": 1, "quiet": False, "motion": True}),
                         json.dumps({"scale": 1, "x": float("nan"), "quiet": False, "motion": True})):
                path.write_text(text)
                pet = self.make(path)
                self.assertEqual(pet.brain.scale, 1.0)
                pet.close()

    def test_disabling_motion_stops_an_in_progress_walk_immediately(self):
        self.settle()
        from regular.pet_brain import Step
        self.pet.brain._queue(Step("walk", target_x=self.pet.brain.x + 200), now=self.t[0], interrupt=True)
        self.advance(.3)
        self.assertEqual(self.pet.brain.clip, "walk")
        self.pet.set_motion(False)
        stopped = self.pet.brain.x
        self.advance(1)
        self.assertEqual(self.pet.brain.x, stopped)
        self.assertNotEqual(self.pet.brain.clip, "walk")

    def test_bigger_size_scales_window_and_keeps_her_feet_on_the_floor(self):
        self.settle()
        pet = self.pet
        small = pet.size()
        pet.set_scale(1.5)
        self.assertGreater(pet.width(), small.width() * 1.4)
        self.assertAlmostEqual(pet.pos().y() + pet.window_anchor().y(), pet.brain.floor, delta=1)

    def test_she_stays_on_a_remaining_screen_when_a_monitor_is_removed(self):
        primary = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 800, 800))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop-pet.json"
            path.write_text(json.dumps({"scale": 1.0, "x": -1000, "quiet": False, "motion": False}))
            with patch.object(QApplication, "screens", return_value=[primary]), \
                 patch.object(QApplication, "primaryScreen", return_value=primary):
                pet = self.make(path)
                self.advance(0.5, pet=pet)
                self.assertTrue(0 < pet.brain.x < 800)
                self.assertEqual(pet.brain.floor, 800)
                pet.close()

    def test_status_worker_runs_off_gui_thread_without_duplicate_inflight_read(self):
        seen = []

        def session():
            seen.append(threading.get_ident())
            return {"csrf": CSRF, "available": True, "reason": "", "qq_connected": False}

        self.client.session.side_effect = session
        self.pet.refresh_connection()
        self.pet.refresh_connection()
        for _ in range(100):
            QTest.qWait(10)
            if not self.pet._jobs:
                break
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], threading.get_ident())
        self.assertTrue(self.pet.available)
        self.client.send.assert_not_called()

    def test_pending_chat_does_not_keep_a_qt_pool_task_alive_during_exit(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def send(*args):
            entered.set()
            release.wait(3)
            return "后台仅完成这一次请求"

        self.ready()
        self.client.send.side_effect = send
        self.pet.send_chat("合成请求")
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.pet.pool.activeThreadCount(), 0)
        self.pet.close()
        self.assertTrue(self.pet._closing)
        release.set()
        for _ in range(100):
            QTest.qWait(10)
            if "chat" not in self.pet._jobs:
                break
        self.client.send.assert_called_once()

    def test_native_signals_leave_qt_before_propagating_and_restore_handlers(self):
        originals = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        for signum in originals:
            with self.subTest(signum=signum):
                app, pet = Mock(), Mock(_closing=False)

                def event_loop():
                    handler = signal.getsignal(signum)
                    handler(signum, None)  # simulate delivery, never signal a real process
                    app.exit.assert_called_once_with(0)
                    app.quit.assert_not_called()   # macOS quit() terminates before cleanup
                    return 0

                app.exec.side_effect = event_loop
                with patch.object(desktop_pet.QApplication, "instance", return_value=app), \
                     patch.object(desktop_pet, "PetWidget", return_value=pet), \
                     patch.object(desktop_pet.QTimer, "singleShot"):
                    with self.assertRaises(KeyboardInterrupt):
                        desktop_pet.run(port=8765, token="synthetic-no-network-token")
                pet.close.assert_called_once_with()
                for saved, handler in originals.items():
                    self.assertIs(signal.getsignal(saved), handler)


if __name__ == "__main__":
    unittest.main()
