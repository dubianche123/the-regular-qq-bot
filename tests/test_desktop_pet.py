"""Optional Qt pet: persistent character, proximity palette and asynchronous chat."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit, QTextBrowser
    from regular import desktop_pet
    from regular.desktop_pet import PetWidget


@unittest.skipUnless(HAS_QT, "optional desktop extra is not installed")
class DesktopPetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen", "REGULAR_REDUCE_MOTION": "1"})
        cls.env.start()
        cls.app = QApplication.instance() or QApplication(["regular-pet-tests"])

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def setUp(self):
        self.client = Mock()
        # Interaction tests use a deterministic static fallback. Optional
        # production clips must not silently change the drag/idle fixture.
        with patch.object(PetWidget, "_load_actions"):
            self.pet = PetWidget(self.client, quit_on_close=False)
        self.pet.poll.stop()
        self.pet.animation.stop()

    def tearDown(self):
        self.pet.close()
        self.pet.pool.waitForDone(1000)
        self.app.processEvents()

    def ready(self):
        self.pet._finished("session", {"ok": True, "value": {
            "csrf": "synthetic-csrf-0123456789", "available": True, "reason": "", "qq_connected": True}})

    def preference_pet(self, path):
        with patch.object(PetWidget, "_load_actions"):
            pet = PetWidget(Mock(), quit_on_close=False, preferences_path=path)
        pet.poll.stop()
        pet.animation.stop()
        pet.proximity.stop()
        return pet

    def mouse(self, kind, where, *, buttons=None):
        return QMouseEvent(kind, QPointF(170, 170), QPointF(*where), Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton if buttons is None else buttons,
                           Qt.KeyboardModifier.NoModifier)

    def test_idle_is_only_a_static_transparent_character(self):
        self.assertTrue(self.pet.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(self.pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        self.assertTrue(self.pet.toolbar.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        self.assertFalse(self.pet.toolbar.isVisible())
        self.assertIsNone(self.pet.chat_dialog)
        self.assertIsNone(self.pet.notifications_dialog)
        self.assertEqual(self.pet.findChildren(QLineEdit), [])
        self.assertEqual(self.pet.findChildren(QTextBrowser), [])
        self.assertFalse(self.pet.mask().contains(QPoint(0, 0)))
        self.assertFalse(self.pet.mask().isEmpty())
        frame = self.pet._frame.cacheKey()
        for _ in range(10):
            self.pet._tick()
        self.assertEqual(self.pet._frame.cacheKey(), frame)
        self.client.session.assert_not_called()

    def test_drag_moves_without_transforming_the_sticker_or_opening_a_menu(self):
        self.pet.set_motion(True)
        start = self.pet.pos()
        frame = self.pet._frame.toImage()
        with patch.object(self.pet, "show_menu") as menu:
            self.pet.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, (100, 100)))
            self.pet.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove, (150, 120)))
            self.assertEqual(self.pet.pos(), start + QPoint(50, 20))
            self.assertEqual(self.pet._frame.toImage(), frame)  # no clip installed: static faithful fallback
            self.pet.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, (150, 120),
                                                  buttons=Qt.MouseButton.NoButton))
        menu.assert_not_called()
        self.assertFalse(self.pet._pressed)

    def test_click_does_not_open_menu_and_right_menu_contains_only_maintenance(self):
        with patch.object(self.pet, "show_menu") as menu:
            self.pet.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, (100, 100)))
            self.pet.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, (100, 100),
                                                  buttons=Qt.MouseButton.NoButton))
        menu.assert_not_called()
        self.pet.show_menu(QPoint(10, 10))
        labels = [action.text() for action in self.pet._menu.actions()]
        self.assertNotIn("文字聊聊", labels)
        self.assertFalse(any("语音" in label for label in labels))
        self.assertIn("刷新连接状态", labels)
        self.assertIn("退出桌宠", labels)
        self.pet._menu.close()

    def test_nearby_hover_reveals_toolbar_and_toolbar_crossing_delays_hide(self):
        self.pet.show()
        self.pet.proximity.stop()
        # Near a transparent canvas edge: a native alpha-mask enter event isn't required.
        near = self.pet.mapToGlobal(QPoint(35, 100))
        self.pet._update_toolbar(near, now=10)
        self.assertTrue(self.pet.toolbar.isVisible())
        self.assertIsNone(self.pet.chat_dialog)
        self.pet._update_toolbar(self.pet.toolbar.geometry().center(), now=10.2)
        self.assertTrue(self.pet.toolbar.isVisible())
        far = QPoint(-5000, -5000)
        self.pet._update_toolbar(far, now=11)
        self.pet._update_toolbar(far, now=11.5)
        self.assertTrue(self.pet.toolbar.isVisible())
        self.pet._update_toolbar(far, now=11.7)
        self.assertFalse(self.pet.toolbar.isVisible())
        self.assertTrue(self.pet.isVisible())
        self.client.send.assert_not_called()

    def test_drag_pins_toolbar_and_settings_button_is_explicit(self):
        self.pet.show()
        self.pet.proximity.stop()
        far = QPoint(-5000, -5000)
        self.pet._pressed = True
        self.pet._update_toolbar(far, now=1)
        self.pet._update_toolbar(far, now=5)
        self.assertTrue(self.pet.toolbar.isVisible())
        self.pet._pressed = False
        self.pet._update_toolbar(far, now=6)
        self.pet._update_toolbar(far, now=7)
        self.assertFalse(self.pet.toolbar.isVisible())
        with patch.object(self.pet, "open_panel") as panel:
            self.pet.toolbar.settings.click()
        panel.assert_called_once_with("/setup")

    def test_frame_sequence_uses_same_anchor_and_reduced_motion_holds_original(self):
        self.ready()
        original = self.pet._frame.toImage()
        second = self.pet._image.copy()
        second.fill(Qt.GlobalColor.green)
        self.pet._actions["wave"] = ([self.pet._image, second, self.pet._image], 6, False)
        self.pet.set_motion(True)
        self.pet.set_pose("wave")
        anchor = self.pet.pos()
        started = self.pet._action_started
        with patch("regular.desktop_pet.time.monotonic", return_value=started + 0.2):
            self.pet._tick()
        self.assertNotEqual(self.pet._frame.toImage(), original)
        self.assertEqual(self.pet.pos(), anchor)
        self.pet.set_motion(False)
        self.assertEqual(self.pet._frame.toImage(), original)
        self.pet.set_motion(True)
        with patch("regular.desktop_pet.time.monotonic", return_value=started + 1):
            self.pet._tick()
        self.assertEqual(self.pet.pose, "idle")
        self.assertEqual(self.pet._frame.toImage(), original)

    def test_resize_preserves_feet_anchor_with_limits_and_repositions_toolbar(self):
        self.pet.show()
        self.pet.move(200, 150)
        self.pet.proximity.stop()
        self.pet.show_menu(QPoint(10, 10))
        sizes = self.pet._menu.actions()[0].menu()
        next(action for action in sizes.actions() if "120%" in action.text()).trigger()
        self.assertEqual(self.pet.scale_factor, 1.2)
        self.pet._menu.close()
        self.pet.set_scale(1.0)

        def feet():
            return self.pet.mapToGlobal(QPoint(50 + self.pet._image.width() // 2,
                                               24 + self.pet._image.height()))

        anchor = feet()
        self.pet.set_scale(1.4)
        self.assertEqual(self.pet._image.size().toTuple(), (336, 504))
        self.assertEqual(feet(), anchor)
        self.assertEqual(self.pet._frame.size(), self.pet.size())
        self.assertEqual(self.pet.toolbar.geometry().center().x(), feet().x() - 1)
        self.pet.set_scale(0.1)
        self.assertEqual(self.pet.scale_factor, .7)
        self.assertEqual(self.pet._image.size().toTuple(), (168, 252))
        self.assertEqual(feet(), anchor)
        self.pet.set_scale(10)
        self.assertEqual(self.pet.scale_factor, 1.4)
        self.assertEqual(feet(), anchor)

    def test_floating_surfaces_have_transparent_corners_and_close_without_stopping_pet(self):
        self.ready()
        self.pet.show()
        self.pet.show_chat()
        self.pet.show_notifications()
        self.pet.toolbar.show()
        self.app.processEvents()
        for window in (self.pet.toolbar, self.pet.chat_dialog, self.pet.notifications_dialog):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
                self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
                image = window.grab().toImage()
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2).alpha(), 255)
                self.assertFalse(window.mask().contains(QPoint(0, 0)))
        dialog = self.pet.chat_dialog
        start = dialog.pos()
        dialog.header.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, (100, 100)))
        dialog.header.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove, (140, 125)))
        dialog.header.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, (140, 125),
                                                 buttons=Qt.MouseButton.NoButton))
        self.assertEqual(dialog.pos(), start + QPoint(40, 25))
        QTest.keyClick(dialog.entry, Qt.Key.Key_Escape)
        self.assertFalse(dialog.isVisible())
        self.pet.notifications_dialog.header.close_button.click()
        self.assertFalse(self.pet.notifications_dialog.isVisible())
        self.assertTrue(self.pet.isVisible())
        self.assertFalse(self.pet._closing)

    def test_installed_body_clips_resize_and_hold_drag_terminal_frame(self):
        self.pet._load_actions(self.pet._raw_images["idle"].size())
        self.assertTrue({"wave", "drag", "land"}.issubset(self.pet._actions))
        self.pet.set_scale(.85)
        self.pet.set_motion(True)
        self.pet._pressed = True
        self.pet.set_pose("drag")
        anchor = self.pet.pos()
        started = self.pet._action_started
        # A busy GUI may skip the middle frame: still hold the terminal drag pose.
        with patch("regular.desktop_pet.time.monotonic", return_value=started + 1):
            self.pet._tick()
        last = self.pet._frame.toImage()
        self.assertEqual(self.pet._action_index, len(self.pet._action_frames) - 1)
        with patch("regular.desktop_pet.time.monotonic", return_value=started + 2):
            self.pet._tick()
        self.assertEqual(self.pet._frame.toImage(), last)
        self.assertEqual(self.pet.pose, "drag")
        self.assertEqual(self.pet.pos(), anchor)
        self.assertEqual(self.pet._action_frames[0].size(), self.pet._image.size())

    def test_local_preferences_restore_size_motion_and_drag_position(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"REGULAR_REDUCE_MOTION": ""}):
            path = Path(directory) / "data" / "desktop-pet.json"
            first = self.preference_pet(path)
            self.assertFalse(path.exists())
            first.move(200, 150)
            first.set_scale(.85)
            first.set_motion(False)
            first.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, (100, 100)))
            first.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove, (120, 120)))
            first.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, (120, 120),
                                               buttons=Qt.MouseButton.NoButton))
            expected_position = first.pos()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved, {"scale": .85, "motion": False,
                                     "position": [expected_position.x(), expected_position.y()]})
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            first.close()
            with patch("regular.desktop_pet.atomic_write_json") as write:
                second = self.preference_pet(path)
                write.assert_not_called()  # restoring is read-only
            self.assertEqual(second.scale_factor, .85)
            self.assertFalse(second.motion)
            self.assertEqual(second.pos(), expected_position)
            second.close()

    def test_corrupt_preferences_are_ignored_and_timers_never_write(self):
        valid = {"scale": 1.0, "motion": True, "position": [200, 150]}
        bad = ["not json", json.dumps({**valid, "scale": True}), json.dumps({**valid, "scale": 8}),
               json.dumps({**valid, "scale": float("nan")}), json.dumps({**valid, "motion": "true"}),
               json.dumps({**valid, "position": [False, 2]}), json.dumps({**valid, "extra": "ignored"})]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"REGULAR_REDUCE_MOTION": ""}):
            path = Path(directory) / "desktop-pet.json"
            for text in bad:
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    pet = self.preference_pet(path)
                    self.assertEqual(pet.scale_factor, 1.0)
                    self.assertTrue(pet.motion)
                    with patch("regular.desktop_pet.atomic_write_json") as write:
                        for _ in range(10):
                            pet._tick()
                            pet._update_toolbar()
                        write.assert_not_called()
                    pet.close()

    def test_saved_external_display_position_recovers_when_monitor_is_removed(self):
        primary = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 800, 800))
        external = SimpleNamespace(availableGeometry=lambda: QRect(-1200, 0, 1200, 900))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop-pet.json"
            path.write_text(json.dumps({"scale": 1.0, "motion": False, "position": [-1000, 200]}))
            with patch.object(QApplication, "screens", return_value=[primary, external]):
                pet = self.preference_pet(path)
                self.assertEqual(pet.pos(), QPoint(-1000, 200))
                pet.close()
            with patch.object(QApplication, "screens", return_value=[primary]):
                recovered = self.preference_pet(path)
                area = primary.availableGeometry()
                body = QRect(recovered.mapToGlobal(QPoint(50, 24)), recovered._image.size())
                self.assertTrue(area.contains(body))
                self.assertTrue(area.contains(recovered.toolbar.geometry()))
                self.assertGreater(recovered.x(), -1000)
                recovered.close()

    def test_chat_is_explicit_plain_text_single_flight_and_close_keeps_pet(self):
        self.ready()
        self.pet.show()
        self.pet.show_chat()
        dialog = self.pet.chat_dialog
        dialog.entry.setText("<img src='never-fetch-this'>合成文字")
        with patch.object(self.pet, "_submit") as submit:
            dialog.send()
            dialog.send()
        submit.assert_called_once()
        self.client.send.assert_not_called()
        submit.call_args.args[1]()
        self.client.send.assert_called_once()
        self.assertIn("<img src='never-fetch-this'>", dialog.history.toPlainText())
        self.pet._finished("chat", {"ok": True, "value": "<script>这也是纯文本</script>"})
        self.assertIn("<script>这也是纯文本</script>", dialog.history.toPlainText())
        self.assertEqual(len(self.pet.notifications), 1)
        dialog.close()
        self.app.processEvents()
        self.assertTrue(self.pet.isVisible())
        self.assertFalse(self.pet._closing)
        self.assertFalse(self.app.quitOnLastWindowClosed())

    def test_notifications_record_actual_local_connection_changes_only_once(self):
        self.ready()
        self.assertEqual(len(self.pet.notifications), 0)
        state = {"ok": True, "value": {"csrf": "synthetic-csrf-0123456789",
                 "available": False, "reason": "本机连接已断开", "qq_connected": False}}
        self.pet._finished("session", state)
        self.pet._finished("session", state)
        self.assertEqual(len(self.pet.notifications), 1)
        self.pet.show_notifications()
        self.assertIn("本机连接已断开", self.pet.notifications_dialog.history.toPlainText())
        self.client.send.assert_not_called()

    def test_status_worker_runs_off_gui_thread_without_duplicate_inflight_read(self):
        seen = []

        def session():
            seen.append(threading.get_ident())
            return {"csrf": "synthetic-csrf-0123456789", "available": True,
                    "reason": "", "qq_connected": False}

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
        self.pet.show_chat()
        self.pet.chat_dialog.entry.setText("合成请求")
        self.pet.chat_dialog.send()
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
                    app.quit.assert_called_once_with()
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
