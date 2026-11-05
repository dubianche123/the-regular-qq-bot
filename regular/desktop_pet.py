"""透明人物桌宠，只连接已有面板；不加载机器人，不管理其进程。"""
from __future__ import annotations

from collections import deque
from importlib.resources import files
import json
import os
from pathlib import Path
import signal
import threading
import time
import webbrowser

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QActionGroup, QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from regular.desktop import CompanionError, PanelClient
from storage import atomic_write_json


def _read_preferences(path):
    if path is None:
        return {}
    try:
        if path.stat().st_size > 4096:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"scale", "position", "motion"}:
            return {}
        scale, position = data["scale"], data["position"]
        if (type(scale) not in (int, float) or not .7 <= scale <= 1.4
                or type(data["motion"]) is not bool or not isinstance(position, list) or len(position) != 2
                or any(type(value) is not int or abs(value) > 100000 for value in position)):
            return {}
        return data
    except (OSError, UnicodeError, ValueError):
        return {}


class _Signals(QObject):
    finished = Signal(str, object)


class _Request(QRunnable):
    def __init__(self, key, operation):
        super().__init__()
        self.key, self.operation = key, operation
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            result = {"ok": True, "value": self.operation()}
        except CompanionError as error:
            result = {"ok": False, "message": str(error)}
        except Exception:
            result = {"ok": False, "message": "本机请求未完成；没有自动重试。"}
        try:
            self.signals.finished.emit(self.key, result)
        except RuntimeError:
            pass  # The user may already have exited the native GUI.


def _append_text(view, speaker, text):
    cursor = view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(f"{speaker}：{text}\n\n")
    view.setTextCursor(cursor)
    view.ensureCursorVisible()


def _style_dialog(dialog):
    dialog.setStyleSheet("""
        QLabel { color:#36564a; background:transparent; font-size:13px; padding:4px 0; }
        QLabel#pet-card-title { color:#173b32; font-size:17px; font-weight:600; }
        QTextBrowser { background:#fffdf7; color:#173b32; border:1px solid #dce1d7;
                       border-radius:14px; padding:12px; font-size:14px; }
        QLineEdit { background:#fffdf7; color:#173b32; border:1px solid #a4b7a8;
                    border-radius:12px; padding:11px; font-size:14px; }
        QLineEdit:focus { border:1px solid #426c56; }
        QPushButton { color:#fffdf7; background:#173b32; border:0; border-radius:12px;
                      padding:10px 16px; font-size:14px; }
        QPushButton:hover { background:#315d49; }
        QPushButton:disabled { background:#dce1d7; color:#6a7d70; }
        QPushButton#pet-card-close { background:transparent; color:#526b5d; border-radius:15px;
                                    font-size:18px; padding:0; }
        QPushButton#pet-card-close:hover, QPushButton#pet-card-close:focus { background:#e8e7ef; }
    """)


def _rounded_region(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    return QRegion(path.toFillPolygon().toPolygon())


class _CardHeader(QWidget):
    """The title/blank area moves the card; close remains a real accessible button."""
    def __init__(self, dialog, title):
        super().__init__(dialog)
        self._drag_start = self._window_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(title)
        self.title.setObjectName("pet-card-title")
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("pet-card-close")
        self.close_button.setAccessibleName("关闭" + title)
        self.close_button.setToolTip("关闭 · Esc")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(dialog.close)
        layout.addWidget(self.title)
        layout.addStretch()
        layout.addWidget(self.close_button)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._window_start = self.window().pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self.window().move(self._window_start + event.globalPosition().toPoint() - self._drag_start)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = self._window_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class _CardDialog(QDialog):
    """Transparent native shell and rounded paper, without a square dialog fill."""
    def __init__(self, pet, title, size):
        super().__init__(pet, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.pet = pet
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.resize(*size)
        _style_dialog(self)
        self.layout_body = QVBoxLayout(self)
        self.layout_body.setContentsMargins(28, 22, 28, 28)
        self.layout_body.setSpacing(12)
        self.header = _CardHeader(self, title)
        self.layout_body.addWidget(self.header)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMask(_rounded_region(self.rect(), 30))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        # Also provide a subtle shadow on compositors that omit native shadows
        # for translucent frameless windows.
        for spread in range(7, 0, -1):
            painter.setBrush(QColor(20, 47, 40, 3 + (7 - spread)))
            painter.drawRoundedRect(card.adjusted(-spread, -spread + 2, spread, spread + 2),
                                    22 + spread, 22 + spread)
        painter.setBrush(QColor("#f5f5ef"))
        painter.setPen(QPen(QColor("#d9dfd3"), 1))
        painter.drawRoundedRect(card, 22, 22)
        painter.end()


class ChatDialog(_CardDialog):
    def __init__(self, pet):
        super().__init__(pet, "和老群友聊聊", (446, 476))
        layout = self.layout_body
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.history = QTextBrowser()
        self.history.setAccessibleName("本机文字对话")
        self.history.setOpenLinks(False)
        self.history.setOpenExternalLinks(False)
        self.entry = QLineEdit()
        self.entry.setMaxLength(2000)
        self.entry.setPlaceholderText("只发送本机文字，最多 2000 字")
        self.entry.setAccessibleName("想和老群友说的话")
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send)
        self.entry.returnPressed.connect(self.send)
        for widget in (self.state, self.history, self.entry, self.send_button):
            layout.addWidget(widget)
        self.update_status()

    def update_status(self):
        self.state.setText(self.pet.connection_text())
        self.send_button.setEnabled(self.pet.available and bool(self.pet.csrf) and not self.pet.chat_busy)

    def send(self):
        text = self.entry.text().strip()
        if self.pet.chat_busy or not self.pet.available or not self.pet.csrf or not text:
            return
        self.pet.chat_busy = True
        self.pet.set_pose("thinking")
        self.entry.clear()
        _append_text(self.history, "我", text)
        self.update_status()
        csrf = self.pet.csrf
        self.pet._submit("chat", lambda: self.pet.client.send(text, csrf))


class NotificationsDialog(_CardDialog):
    def __init__(self, pet):
        super().__init__(pet, "老群友 · 通知", (406, 376))
        layout = self.layout_body
        label = QLabel("本机聊天的新回复、连接动态，都会收在这里。")
        label.setWordWrap(True)
        self.history = QTextBrowser()
        self.history.setAccessibleName("本机通知记录")
        self.history.setOpenLinks(False)
        layout.addWidget(label)
        layout.addWidget(self.history)
        self.refresh()

    def refresh(self):
        self.history.setPlainText("\n\n".join(self.pet.notifications) or "还没有本机通知。")


def _floating_character(window):
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # Qt::Tool otherwise hides on macOS when another application becomes active.
    window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


class _ToolbarButton(QPushButton):
    """Draw our own rounded hover/focus surface instead of a native square bezel."""
    def __init__(self, text, icon):
        super().__init__(text)
        self.icon_kind, self.badge = icon, 0
        self.setFlat(True)
        self.setAutoFillBackground(False)
        self.setFixedHeight(40)
        font = self.font()
        font.setPointSize(12)
        self.setFont(font)

    def set_badge(self, count):
        self.badge = count
        self.setAccessibleName("查看本机通知" + (f"，{count} 条未读" if count else ""))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.underMouse() or self.hasFocus() or self.isDown():
            painter.setBrush(QColor("#35574b"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 18, 18)
        painter.setPen(QPen(QColor("#fffdf7"), 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.save()
        painter.translate(12, (self.height() - 16) / 2)
        if self.icon_kind == "chat":
            painter.drawRoundedRect(QRectF(0, 0, 15, 11), 3, 3)
            painter.drawLine(4, 11, 2, 15)
            painter.drawLine(2, 15, 8, 11)
        elif self.icon_kind == "notifications":
            path = QPainterPath()
            path.moveTo(1, 12); path.lineTo(3, 9); path.lineTo(3, 5)
            path.cubicTo(3, -1, 12, -1, 12, 5)
            path.lineTo(12, 9); path.lineTo(14, 12); path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(6, 15, 9, 15)
        else:
            for x, y in ((2, 4), (8, 10), (14, 5)):
                painter.drawLine(x, 0, x, 16)
                painter.setBrush(QColor("#173b32"))
                painter.drawEllipse(QPoint(x, y), 2, 2)
        painter.restore()
        painter.setPen(QColor("#fffdf7"))
        painter.drawText(QRect(34, 0, self.width() - 39, self.height()), Qt.AlignmentFlag.AlignCenter, self.text())
        if self.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d8ed9a"))
            painter.drawEllipse(QPoint(self.width() - 9, 8), 4, 4)
        painter.end()


class PetToolbar(QWidget):
    """Independent palette; moving from the character onto it preserves hover."""
    def __init__(self, pet):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        _floating_character(self)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self.setWindowTitle("老群友 · 快捷操作")
        self.setAccessibleName("老群友快捷操作：聊天、通知、设置")
        self.setObjectName("regular-pet-toolbar")
        self.setFixedSize(272, 56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        self.chat = _ToolbarButton("聊聊", "chat")
        self.notifications = _ToolbarButton("通知", "notifications")
        self.settings = _ToolbarButton("设置", "settings")
        for button, label, callback in ((self.chat, "和老群友文字聊天", pet.show_chat),
                                        (self.notifications, "查看本机通知", pet.show_notifications),
                                        (self.settings, "打开老群友设置", lambda: pet.open_panel("/setup"))):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMask(_rounded_region(self.rect(), self.height() / 2))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(QColor("#173b32"))
        painter.setPen(QPen(QColor("#436356"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 28, 28)
        painter.end()


class PetWidget(QWidget):
    def __init__(self, client, *, quit_on_close=True, preferences_path=None):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.client, self.quit_on_close = client, quit_on_close
        self.preferences_path = Path(preferences_path) if preferences_path is not None else None
        self._preferences_ready = self._preferences_warning = False
        preferences = _read_preferences(self.preferences_path)
        self.setObjectName("regular-desktop-pet")
        self.setWindowTitle("The Regular · 老群友")
        self.setAccessibleName("老群友，靠近显示聊天和设置，拖动移动")
        self.setToolTip("靠近显示聊天和设置，拖动移动，右键查看维护选项")
        _floating_character(self)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # Tool windows do not always count as primary windows. Closing a chat
        # dialog must not terminate the still-visible character.
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.motion = (preferences.get("motion", True)
                       and os.environ.get("REGULAR_REDUCE_MOTION", "").lower() not in ("1", "true"))
        self._closing = self._pressed = self._dragged = False
        self._press_global = self._window_start = self._last_delta = QPoint()
        self.csrf, self.available, self.qq_connected = "", False, False
        self.reason = "正在检查本机连接…"
        self.chat_busy = False
        self.notifications = deque(maxlen=30)
        self._unread = 0
        self._last_connection = None
        self.chat_dialog = self.notifications_dialog = None
        self._menu = None
        self._jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._images, self._actions = {}, {}
        self._raw_images, self._raw_actions, self._raw_frames = {}, {}, {}
        self.scale_factor = 1.0
        # Use the clip canvas for idle too: a second downsampling path from the
        # archival master can otherwise produce a small flash at clip edges.
        original = self._load_image("mascot-idle.png")
        if original is None:
            original = self._load_image("mascot.png")
        if original is None:
            raise ValueError("The Regular mascot asset is unavailable")
        self._image = original.scaled(QSize(240, 360), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
        self._raw_images["idle"] = original
        self._images["idle"] = self._image
        for pose in ("blink", "thinking", "celebrate", "sleeping"):
            image = self._load_image(f"mascot-{pose}.png")
            if image is not None and image.width() * original.height() == image.height() * original.width():
                self._raw_images[pose] = image
                self._images[pose] = image.scaled(self._image.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
        self._load_actions(original.size())
        self._action_frames, self._action_index = [], 0
        self._action_started, self._action_fps, self._action_loop = 0.0, 6, False
        self.pose = "idle"
        self.setFixedSize(self._image.width() + 100, self._image.height() + 48)
        self._frame = QPixmap(self.size())
        self._render_frame()
        self.animation = QTimer(self)
        self.animation.setInterval(33)
        self.animation.timeout.connect(self._tick)
        self.animation.start()
        self.toolbar = PetToolbar(self)
        self._toolbar_leave_at = None
        self.proximity = QTimer(self)
        self.proximity.setInterval(80)
        self.proximity.timeout.connect(self._update_toolbar)
        self.proximity.start()
        self.poll = QTimer(self)
        self.poll.setInterval(15000)
        self.poll.timeout.connect(self.refresh_connection)
        self.poll.start()
        if preferences:
            self.set_scale(preferences["scale"])
            self.move(*preferences["position"])
        else:
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 72)
        self._keep_on_screen()
        self._position_toolbar()
        self._preferences_ready = True

    @staticmethod
    def _load_image(name):
        asset = files("regular").joinpath("assets", name)
        if not asset.is_file():
            return None
        image = QPixmap()
        return image if image.loadFromData(asset.read_bytes()) else None

    def _load_actions(self, original):
        """PNG clips use one canvas and anchor; optional missing art keeps the original."""
        manifest = files("regular").joinpath("assets", "pet-actions.json")
        if not manifest.is_file():
            return
        try:
            spec = json.loads(manifest.read_text(encoding="utf-8"))
            canvas = spec.get("canvas", [])
            if (spec.get("schema") != 1 or not isinstance(canvas, list) or len(canvas) != 2
                    or any(type(n) is not int or not 1 <= n <= 4096 for n in canvas)
                    or canvas[0] * original.height() != canvas[1] * original.width()):
                return
            for name in ("blink", "wave", "drag", "land", "thinking", "celebrate", "sleeping"):
                action = spec.get("actions", {}).get(name, {})
                filenames, fps = action.get("frames", []), action.get("fps", 6)
                if (not isinstance(filenames, list) or not 1 <= len(filenames) <= 24
                        or type(fps) not in (int, float) or not 1 <= fps <= 24):
                    continue
                frames, originals = [], []
                for filename in filenames:
                    if not isinstance(filename, str) or "/" in filename or "\\" in filename or not filename.endswith(".png"):
                        break
                    image = self._raw_frames.get(filename)
                    if image is None:
                        image = self._load_image(filename)
                    if image is None or image.size() != QSize(*canvas):
                        break
                    self._raw_frames[filename] = image
                    originals.append(image)
                    frames.append(image.scaled(self._image.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                if len(frames) == len(filenames):
                    self._actions[name] = (frames, fps, action.get("loop") is True)
                    self._raw_actions[name] = (originals, fps, action.get("loop") is True)
        except (OSError, ValueError, TypeError, AttributeError):
            return

    def set_pose(self, pose):
        self.pose = pose if pose in self._actions or pose in self._images else "idle"
        self._action_frames, self._action_fps, self._action_loop = self._actions.get(self.pose, ([], 6, False))
        self._action_index, self._action_started = 0, time.monotonic()
        self._render_frame()

    def set_scale(self, scale):
        """Resize from source pixels, preserving the feet anchor whenever the screen allows."""
        scale = max(0.7, min(float(scale), 1.4))
        anchor = self.mapToGlobal(QPoint(50 + self._image.width() // 2, 24 + self._image.height()))
        size = QSize(round(240 * scale), round(360 * scale))
        self.scale_factor = scale
        self._images = {name: image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                        for name, image in self._raw_images.items()}
        self._image = self._images["idle"]
        self._actions = {name: ([image.scaled(self._image.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                                  for image in frames], fps, loop)
                         for name, (frames, fps, loop) in self._raw_actions.items()}
        self.setFixedSize(self._image.width() + 100, self._image.height() + 48)
        self.move(anchor - QPoint(50 + self._image.width() // 2, 24 + self._image.height()))
        self._keep_on_screen()
        self._position_toolbar()
        self.set_pose(self.pose)
        self._save_preferences()

    def _save_preferences(self):
        if self.preferences_path is None or not self._preferences_ready:
            return
        try:
            atomic_write_json(self.preferences_path, {"scale": self.scale_factor,
                              "position": [self.x(), self.y()], "motion": self.motion})
        except OSError:
            if not self._preferences_warning:
                print("桌宠偏好未能保存，本次调整仍已生效。")
                self._preferences_warning = True

    def _render_frame(self):
        frame = QPixmap(self.size())
        frame.fill(Qt.GlobalColor.transparent)
        image = self._images.get(self.pose, self._image) if self.motion else self._image
        if self.motion and self._action_frames:
            image = self._action_frames[self._action_index]
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Every frame shares a canvas and anchor. The whole sticker never rocks.
        painter.drawPixmap(50, 24, image)
        painter.end()
        self._frame = frame
        # The alpha mask limits hit testing to the visible character where the
        # window manager supports it; universal click-through is not promised.
        self.setMask(frame.mask())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._frame)
        painter.end()

    def _tick(self):
        if not self.motion or len(self._action_frames) < 2:
            return
        index = int((time.monotonic() - self._action_started) * self._action_fps)
        if self._action_loop:
            index %= len(self._action_frames)
        elif index >= len(self._action_frames):
            if self._pressed:
                index = len(self._action_frames) - 1
            else:
                self._return_idle()
                return
        if index != self._action_index:
            self._action_index = index
            self._render_frame()

    def _toolbar_regions(self):
        body = QRect(self.mapToGlobal(QPoint(50, 24)), self._image.size()).adjusted(-22, -20, 22, 20)
        # Include the feet-to-toolbar path. Transparent pixels receive no enter
        # events when the native alpha mask is active, so proximity uses QCursor.
        return body.united(self.toolbar.geometry().adjusted(-12, -14, 12, 10))

    def _position_toolbar(self):
        position = self.mapToGlobal(QPoint((self.width() - self.toolbar.width()) // 2,
                                          self._image.height() + 26))
        area = self._screen_area()
        if area is not None:
            position.setX(max(area.left(), min(position.x(), area.right() - self.toolbar.width() + 1)))
            position.setY(max(area.top(), min(position.y(), area.bottom() - self.toolbar.height() + 1)))
        self.toolbar.move(position)

    def _update_toolbar(self, point=None, now=None):
        if self._closing or not self.isVisible():
            return
        self._position_toolbar()
        point = QCursor.pos() if point is None else point
        now = time.monotonic() if now is None else now
        focused = any(dialog is not None and dialog.isVisible() and dialog.isActiveWindow()
                      for dialog in (self.chat_dialog, self.notifications_dialog))
        pinned = self._pressed or focused or bool(self._menu and self._menu.isVisible())
        if pinned or self._toolbar_regions().contains(point):
            self._toolbar_leave_at = None
            self.toolbar.show()
        elif self.toolbar.isVisible():
            if self._toolbar_leave_at is None:
                self._toolbar_leave_at = now
            elif now - self._toolbar_leave_at >= 0.6:
                self.toolbar.hide()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "toolbar"):
            self._position_toolbar()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._pressed, self._dragged = True, False
        self._press_global = event.globalPosition().toPoint()
        self._window_start, self._last_delta = self.pos(), QPoint()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._pressed:
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._press_global
        if delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragged = True
        if self._dragged:
            self.move(self._window_start + delta)
            if self.pose != "drag":
                self.set_pose("drag")
            self._last_delta = delta
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._pressed:
            return super().mouseReleaseEvent(event)
        self._pressed = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self._dragged:
            self._keep_on_screen()
            self._save_preferences()
        self.set_pose("land" if self._dragged else "wave")
        self._update_toolbar(event.globalPosition().toPoint())
        event.accept()

    def _screen_area(self):
        screens = QApplication.screens()
        if not screens:
            return None
        center = self.mapToGlobal(QPoint(50 + self._image.width() // 2, 24 + self._image.height() // 2))

        def distance(screen):
            area = screen.availableGeometry()
            x = max(area.left(), min(center.x(), area.right()))
            y = max(area.top(), min(center.y(), area.bottom()))
            return (center.x() - x) ** 2 + (center.y() - y) ** 2

        # QWidget.screen() may still refer to the primary monitor before show;
        # choose by the saved global coordinates, recovering a detached display.
        return min(screens, key=distance).availableGeometry()

    def _keep_on_screen(self):
        area = self._screen_area()
        if area is None:
            return
        # Keep the visible character and its controls recoverable after a drag.
        x = max(area.left() - 42, min(self.x(), area.right() - self._image.width() - 58))
        y = max(area.top() - 16, min(self.y(), area.bottom() - self._image.height() - 82))
        self.move(x, y)

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())
        event.accept()

    def show_menu(self, position=None):
        menu = QMenu(self)
        sizes = menu.addMenu("人物大小")
        group = QActionGroup(sizes)
        group.setExclusive(True)
        for label, scale in (("小 · 70%", .7), ("紧凑 · 85%", .85), ("标准 · 100%", 1.0),
                             ("大 · 120%", 1.2), ("更大 · 140%", 1.4)):
            action = sizes.addAction(label)
            action.setCheckable(True)
            action.setChecked(abs(self.scale_factor - scale) < .01)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, value=scale: self.set_scale(value))
        menu.addAction("刷新连接状态", self.refresh_connection)
        manage = menu.addMenu("打开工作台")
        for label, path in (("设置", "/setup"), ("体检", "/checkup"), ("运行状态", "/"), ("让 Agent 帮忙", "/iterate")):
            manage.addAction(label, lambda checked=False, target=path: self.open_panel(target))
        action = menu.addAction("角色动作反馈")
        action.setCheckable(True)
        action.setChecked(self.motion)
        action.toggled.connect(self.set_motion)
        menu.addSeparator()
        menu.addAction("退出桌宠", self.close)
        self._menu = menu
        menu.popup(position or QCursor.pos())

    def set_motion(self, enabled):
        self.motion = bool(enabled)
        self._render_frame()
        self._save_preferences()

    def open_panel(self, path):
        webbrowser.open(self.client.management_url(path))

    def show_chat(self):
        if self.chat_dialog is None:
            self.chat_dialog = ChatDialog(self)
            self._place_dialog(self.chat_dialog)
        self.chat_dialog.show()
        self.chat_dialog.raise_()
        self.chat_dialog.activateWindow()
        self.chat_dialog.entry.setFocus()

    def show_notifications(self):
        if self.notifications_dialog is None:
            self.notifications_dialog = NotificationsDialog(self)
            self._place_dialog(self.notifications_dialog)
        self.notifications_dialog.refresh()
        self.notifications_dialog.show()
        self.notifications_dialog.raise_()
        self.notifications_dialog.activateWindow()
        self._unread = 0
        self.toolbar.notifications.set_badge(0)

    def _place_dialog(self, dialog):
        area = self._screen_area()
        if area is None:
            return
        x = max(area.left() + 12, min(self.x() - dialog.width() + 24,
                                     area.right() - dialog.width() - 12))
        y = max(area.top() + 12, min(self.y() + 20, area.bottom() - dialog.height() - 12))
        dialog.move(x, y)

    def connection_text(self):
        return "本机对话已就绪 · " + ("QQ 已连接" if self.qq_connected else "QQ 待确认") if self.available else self.reason

    def _notify(self, text):
        self.notifications.append(f"{time.strftime('%H:%M')} · {text}")
        if self.notifications_dialog is None or not self.notifications_dialog.isVisible():
            self._unread += 1
        self.toolbar.notifications.set_badge(self._unread)
        if self.notifications_dialog is not None:
            self.notifications_dialog.refresh()

    def _submit(self, key, operation):
        if key in self._jobs or self._closing:
            return
        job = _Request(key, operation)
        job.signals.finished.connect(self._finished)
        self._jobs[key] = job
        if key == "chat":
            # Leaving the pet must not wait for the model's bounded HTTP read.
            # A sent request can still finish in the bot; it is never repeated.
            job.setAutoDelete(False)
            threading.Thread(target=job.run, name="regular-pet-chat", daemon=True).start()
        else:
            self.pool.start(job)

    def refresh_connection(self):
        self._submit("session", self.client.session)

    def _return_idle(self):
        if not self._closing and not self._pressed:
            target = "thinking" if self.chat_busy else "idle" if self.available else "sleeping"
            if target == self.pose:
                self._action_frames = []
            else:
                self.set_pose(target)

    @Slot(str, object)
    def _finished(self, key, result):
        self._jobs.pop(key, None)
        if self._closing:
            return
        if key == "session":
            state = result.get("value") or {}
            self.csrf = state.get("csrf", "") if result["ok"] else ""
            self.available = bool(result["ok"] and state.get("available"))
            self.qq_connected = bool(result["ok"] and state.get("qq_connected"))
            self.reason = str(state.get("reason") or "本机对话尚未就绪。") if result["ok"] else result["message"]
            current = (self.available, self.qq_connected)
            if self._last_connection is not None and current != self._last_connection:
                self._notify(self.connection_text())
            if current != self._last_connection:
                self._return_idle()
            self._last_connection = current
            self.setToolTip(self.connection_text() + "\n靠近显示聊天和设置，拖动移动。")
        elif key == "chat":
            self.chat_busy = False
            if result["ok"]:
                if self.chat_dialog is not None:
                    _append_text(self.chat_dialog.history, "老群友", result["value"])
                self._notify("本机回复已到达。")
                self.set_pose("celebrate")
                QTimer.singleShot(1600, self._return_idle)
            else:
                if self.chat_dialog is not None:
                    _append_text(self.chat_dialog.history, "本机提示", result["message"])
                self._notify(result["message"])
                self._return_idle()
        if self.chat_dialog is not None:
            self.chat_dialog.update_status()

    def closeEvent(self, event):
        self._save_preferences()
        self._closing = True
        self.poll.stop()
        self.animation.stop()
        self.proximity.stop()
        self.toolbar.close()
        self.pool.clear()
        for dialog in (self.chat_dialog, self.notifications_dialog):
            if dialog is not None:
                dialog.close()
        event.accept()
        if self.quit_on_close and QApplication.instance() is not None:
            QApplication.instance().quit()


def run(*, port, token, preferences_path=None):
    app = QApplication.instance() or QApplication(["regular-pet"])
    app.setApplicationName("The Regular")
    app.setApplicationDisplayName("老群友")
    pet = PetWidget(PanelClient(port, token), preferences_path=preferences_path)
    app._regular_pet = pet
    interrupted = False

    def request_stop(signum, frame):
        nonlocal interrupted
        # Raising inside a Qt slot is swallowed by PySide. Leave the event loop
        # first; the launcher must receive the interruption to clean its child.
        interrupted = True
        app.quit()

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
        pet.show()
        QTimer.singleShot(0, pet.refresh_connection)
        result = app.exec() if not interrupted else 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if not pet._closing:
            pet.close()
    if interrupted:
        raise KeyboardInterrupt
    return result
