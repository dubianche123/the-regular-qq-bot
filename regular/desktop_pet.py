"""透明人物桌宠的 Qt 外壳：只负责画、窗口和输入。

行为、物理和 agent 状态映射都在 regular/pet_brain.py（纯计算、可测）。这里只连接已有面板：
不加载机器人、不管理其进程；聊天和 presence 都走面板的本机接口。
"""
from __future__ import annotations

from collections import deque
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import random
import signal
import threading
import time
import webbrowser

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QIcon, QImageReader, QPainter,
                           QPainterPath, QPen, QPixmap, QRegion, QTransform)
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from regular.desktop import CompanionError, PanelClient
from regular.desktop_voice import NativeVoiceSession, request_microphone_permission
from regular.pet_brain import CLIPS, PetBrain
from storage import atomic_write_json

INK = QColor("#173b32")
PAPER = QColor("#fffdf7")
SAGE = QColor("#dfe8dc")
LILAC = QColor("#b9a3e3")
MUTED = QColor("#5b7066")
SCALES = ((0.6, "迷你 · 60%"), (0.8, "小 · 80%"), (1.0, "标准 · 100%"), (1.2, "大 · 120%"), (1.5, "更大 · 150%"))
FLOATING = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)


# ── preferences ──
def _read_preferences(path):
    """{"scale", "x", "quiet", "motion"}; the previous {"scale","position","motion"} form migrates."""
    if path is None:
        return {}
    try:
        if path.stat().st_size > 4096:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if "position" in data and "x" not in data:
        position = data.get("position")
        data = {"scale": data.get("scale"), "motion": data.get("motion"), "quiet": False,
                "x": position[0] if isinstance(position, list) and position else None}
    scale, x = data.get("scale"), data.get("x")
    if (type(scale) not in (int, float) or not 0.5 <= scale <= 1.6
            or type(data.get("motion")) is not bool or type(data.get("quiet")) is not bool
            or type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 100000):
        return {}
    return {"scale": float(scale), "x": float(x), "quiet": data["quiet"], "motion": data["motion"]}


# ── background requests ──
class _Signals(QObject):
    finished = Signal(str, object)


class _VoiceSignals(QObject):
    event = Signal(int, object)
    permission = Signal(int, bool, str)


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


# ── sprites ──
class SpriteBank:
    """WebP strips from tools/pet_sprites.py, cut once and cached per size and direction."""
    def __init__(self, root=None):
        if b"webp" not in [bytes(f) for f in QImageReader.supportedImageFormats()]:
            raise ValueError("Qt WebP image support is unavailable")
        root = root or files("regular").joinpath("assets", "pet")
        spec = json.loads(root.joinpath("sprites.json").read_text(encoding="utf-8"))
        frame, anchor = spec.get("frame"), spec.get("anchor")
        if (spec.get("schema") != 2 or not isinstance(frame, list) or len(frame) != 2
                or not isinstance(anchor, list) or len(anchor) != 2):
            raise ValueError("unsupported pet sprite manifest")
        self.width, self.height = (int(v) for v in frame)
        self.anchor = (float(anchor[0]), float(anchor[1]))
        self.figure = float(spec.get("figure_height") or 320)
        self.frames = {}
        for name, action in spec.get("actions", {}).items():
            count, filename = action.get("frames"), action.get("file")
            if not isinstance(filename, str) or "/" in filename or type(count) is not int or not 1 <= count <= 32:
                raise ValueError(f"invalid strip {name}")
            strip = QPixmap()
            if not strip.loadFromData(root.joinpath(filename).read_bytes()) or strip.size() != QSize(self.width * count, self.height):
                raise ValueError(f"unreadable strip {name}")
            self.frames[name] = [strip.copy(i * self.width, 0, self.width, self.height) for i in range(count)]
        for clip, (strip, sequence, *_rest) in CLIPS.items():
            if strip not in self.frames or max(sequence) >= len(self.frames[strip]):
                raise ValueError(f"clip {clip} needs missing frames")
        self._cache, self._regions = {}, {}

    def points(self, scale):
        """Source pixels are 2x of the on-screen size at 100%."""
        return 0.5 * scale

    def pixmap(self, strip, index, mirrored, scale, dpr):
        key = (strip, index, mirrored, round(scale, 3), round(dpr, 2))
        cached = self._cache.get(key)
        if cached is None:
            source = self.frames[strip][index]
            if mirrored:
                source = source.transformed(QTransform().scale(-1, 1))
            factor = self.points(scale) * dpr
            cached = source.scaled(max(1, round(self.width * factor)), max(1, round(self.height * factor)),
                                   Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            cached.setDevicePixelRatio(dpr)
            if len(self._cache) > 400:
                self._cache.clear()
            self._cache[key] = cached
        return cached

    def region(self, strip, index, mirrored, scale):
        """Hit-test region in points, grown a little so breathing never clips the outline."""
        key = (strip, index, mirrored, round(scale, 3))
        region = self._regions.get(key)
        if region is None:
            pixmap = self.pixmap(strip, index, mirrored, scale, 1.0)
            base = QRegion(pixmap.mask())
            region = QRegion(base)
            for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3)):
                region = region.united(base.translated(dx, dy))
            self._regions[key] = region
        return region


# ── painting helpers ──
def _clear(painter, rect):
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(rect, Qt.GlobalColor.transparent)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _soft_card(painter, rect, radius, fill=PAPER, border=QColor(23, 59, 50, 30)):
    painter.setPen(Qt.PenStyle.NoPen)
    for spread in range(6, 0, -1):
        painter.setBrush(QColor(20, 47, 40, 4 + (6 - spread) * 2))
        painter.drawRoundedRect(rect.adjusted(-spread, -spread + 3, spread, spread + 3), radius + spread, radius + spread)
    painter.setBrush(fill)
    painter.setPen(QPen(border, 1))
    painter.drawRoundedRect(rect, radius, radius)


def _floating(window, *, input=True):
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # Qt::Tool otherwise hides on macOS when another application becomes active.
    window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setAutoFillBackground(False)
    if not input:
        window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)


class FxLayer(QWidget):
    """Particles float above the character in a click-through layer."""
    def __init__(self, pet):
        super().__init__(None, FLOATING)
        _floating(self, input=False)
        self.pet = pet
        self.setWindowTitle("老群友 · 特效")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clear(painter, self.rect())
        brain, now = self.pet.brain, self.pet.now()
        origin = self.mapFromGlobal(QPoint(round(brain.x), round(brain.y)))
        f = brain.figure
        for p in brain.particles:
            age = (now - p.born) / p.life
            alpha = max(0.0, min(1.0, 1.4 - age * 1.4))
            x, y = origin.x() + p.x * f, origin.y() + p.y * f
            painter.save()
            painter.translate(x, y)
            painter.setOpacity(alpha)
            _particle(painter, p.kind, f, age)
            painter.restore()
        painter.end()


def _particle(painter, kind, figure, age):
    size = figure * 0.11
    if kind in ("z", "note", "!"):
        font = QFont()
        font.setBold(True)
        font.setPixelSize(round(size * (1.2 if kind == "z" and age > 0.4 else 1.0)))
        painter.setFont(font)
        painter.setPen(QColor("#8b6fd0") if kind == "z" else QColor("#3d7a5c") if kind == "note" else QColor("#e0565b"))
        painter.drawText(QPointF(-size * 0.3, 0), {"z": "z", "note": "♪", "!": "!"}[kind])
    elif kind == "heart":
        path = QPainterPath()
        s = size * 0.55
        path.moveTo(0, s * 0.9)
        path.cubicTo(-s * 1.4, -s * 0.1, -s * 0.5, -s * 1.1, 0, -s * 0.35)
        path.cubicTo(s * 0.5, -s * 1.1, s * 1.4, -s * 0.1, 0, s * 0.9)
        painter.fillPath(path, QColor("#f08aa8"))
    elif kind == "sparkle":
        s = size * 0.5
        path = QPainterPath()
        path.moveTo(0, -s)
        for a, b in ((s * .2, -s * .2), (s, 0), (s * .2, s * .2), (0, s), (-s * .2, s * .2), (-s, 0), (-s * .2, -s * .2)):
            path.lineTo(a, b)
        path.closeSubpath()
        painter.fillPath(path, QColor("#f2c94c"))
    elif kind == "sweat":
        s = size * 0.45
        path = QPainterPath()
        path.moveTo(0, -s)
        path.cubicTo(s * 0.9, 0, s * 0.7, s, 0, s)
        path.cubicTo(-s * 0.7, s, -s * 0.9, 0, 0, -s)
        painter.fillPath(path, QColor("#8cc8f0"))
    elif kind == "crumb":
        painter.setBrush(QColor("#c99a5b"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, figure * 0.25 * age), size * 0.12, size * 0.12)
    elif kind == "puff":
        pen = QPen(QColor("#e0565b"), max(1.5, size * 0.12), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        s = size * 0.4
        for angle in (0, 90, 180, 270):
            painter.save()
            painter.rotate(angle)
            painter.drawArc(QRectF(s * 0.2, s * 0.2, s, s), 90 * 16, 90 * 16)
            painter.restore()


class SpeechBubble(QWidget):
    """Comic bubble above her head; a click dismisses it."""
    MAX_WIDTH = 248

    def __init__(self, pet):
        super().__init__(None, FLOATING)
        _floating(self)
        self.pet = pet
        self.setWindowTitle("老群友 · 说话")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.text, self.kind, self.serial, self.started = "", "say", 0, 0.0
        self.streaming_serial = None
        self.painted = ""       # what the last paint actually showed (the typewriter is ahead of it)
        self.font_ = QFont()
        self.font_.setPixelSize(14)

    def show_bubble(self, bubble, now):
        same_line = bubble.serial == self.serial
        if same_line and bubble.text == self.text and bubble.kind == self.kind:
            return
        self.serial, self.text, self.kind = bubble.serial, bubble.text, bubble.kind
        if not same_line:
            self.started = now
            self.painted = ""
        self.setAccessibleName("老群友说：" + self.text)
        metrics = QFontMetricsF(self._font())
        if self.kind == "think":
            size = QSize(64, 48)
        else:
            box = metrics.boundingRect(QRectF(0, 0, self.MAX_WIDTH - 32, 2000),
                                       int(Qt.TextFlag.TextWordWrap), self.text)
            # card inset 7 + text inset 14 per side; 5+10 above, 8+14 below (tail).
            size = QSize(max(56, math.ceil(box.width()) + 44), math.ceil(box.height()) + 39)
        self.resize(size)
        self.show()
        self.update()

    def _font(self):
        font = QFont(self.font_)
        if self.kind == "status":
            font.setPixelSize(12)
        return font

    def visible_text(self, now):
        if self.serial == self.streaming_serial or self.kind != "say" or not self.pet.brain.motion:
            return self.text
        return self.text[:max(1, int((now - self.started) * 38))]

    def needs_paint(self, now):
        """Typing (or the thinking dots) still has something to show. Compared with what was last painted,
        not with the full line: the frame that completes the line must still be painted ("谢谢投" stuck)."""
        return self.kind == "think" or self.visible_text(now) != self.painted

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clear(painter, self.rect())
        card = QRectF(self.rect()).adjusted(7, 5, -7, -14)
        fill = PAPER if self.kind != "status" else QColor("#f1f3ee")
        if self.kind == "think":
            _soft_card(painter, card, card.height() / 2, fill)
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(card.center().x() - 4, card.bottom() + 6), 4, 4)
            painter.drawEllipse(QPointF(card.center().x() - 8, card.bottom() + 11), 2.4, 2.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(MUTED)
            phase = int(self.pet.now() * 3) % 3
            for i in range(3):
                painter.setOpacity(1.0 if i == phase else 0.35)
                painter.drawEllipse(QPointF(card.center().x() + (i - 1) * 10, card.center().y()), 3.2, 3.2)
            painter.end()
            return
        _soft_card(painter, card, 16, fill)
        tail = QPainterPath()
        cx = min(max(self._tail_x(), card.left() + 20), card.right() - 20)
        tail.moveTo(cx - 8, card.bottom() - 1)
        tail.lineTo(cx + 2, card.bottom() + 11)
        tail.lineTo(cx + 9, card.bottom() - 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(tail)
        painter.setFont(self._font())
        painter.setPen(INK if self.kind == "say" else MUTED)
        self.painted = self.visible_text(self.pet.now())
        painter.drawText(card.adjusted(14, 10, -14, -8), int(Qt.TextFlag.TextWordWrap), self.painted)
        painter.end()

    def _tail_x(self):
        head = self.pet.head_global()
        return self.mapFromGlobal(head).x()

    def mousePressEvent(self, event):
        self.pet.brain.bubble = None
        self.hide()


class _RoundButton(QPushButton):
    def __init__(self, icon, label):
        super().__init__()
        self.icon_kind, self.badge = icon, 0
        self.setFixedSize(38, 38)
        self.setFlat(True)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_badge(self, count):
        self.badge = count
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hot = self.underMouse() or self.hasFocus() or self.isDown()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2f5a49") if hot else QColor(0, 0, 0, 0))
        painter.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        pen = QPen(PAPER, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.translate(self.width() / 2, self.height() / 2)
        kind = self.icon_kind
        if kind == "chat":
            painter.drawRoundedRect(QRectF(-8, -7, 16, 11), 4, 4)
            painter.drawLine(QPointF(-3, 4), QPointF(-6, 8))
            painter.drawLine(QPointF(-6, 8), QPointF(1, 4))
        elif kind == "feed":
            painter.drawEllipse(QRectF(-7.5, -7.5, 15, 15))
            for x, y in ((-3, -2), (2.5, -3), (0, 2.5)):
                painter.drawPoint(QPointF(x, y))
        elif kind == "voice":
            painter.drawRoundedRect(QRectF(-3, -9, 6, 12), 3, 3)
            painter.drawArc(QRectF(-6, -6, 12, 12), 180 * 16, 180 * 16)
            painter.drawLine(QPointF(0, 6), QPointF(0, 9))
            painter.drawLine(QPointF(-3, 9), QPointF(3, 9))
        elif kind == "activity":
            # a short timeline: what she has been up to (a bell read as "notifications")
            painter.drawLine(QPointF(-5, -7), QPointF(-5, 7))
            for y in (-5, 0, 5):
                painter.setBrush(PAPER)
                painter.drawEllipse(QPointF(-5, y), 1.6, 1.6)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawLine(QPointF(-1, y), QPointF(7 if y != 0 else 4, y))
        else:
            for x, y in ((-5, -3), (0, 3), (5, -1)):
                painter.drawLine(QPointF(x, -7), QPointF(x, 7))
                painter.setBrush(INK)
                painter.drawEllipse(QPointF(x, y), 2.2, 2.2)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d8ed9a"))
            painter.drawEllipse(QPointF(9, -9), 4, 4)
        painter.end()


class PetToolbar(QWidget):
    """四个按钮绕成一圈贴在她旁边；不再是一条竖着的长方形。

    只留日常真正会点的四件事：聊 / 听 / 她在忙啥 / 更多。投喂、大小、
    不乱跑这些设置全部收进「更多」，右键原生菜单也不再存在。
    """
    SIZE = 108

    def __init__(self, pet):
        super().__init__(None, FLOATING)
        _floating(self)
        self.setWindowTitle("老群友 · 快捷操作")
        self.setAccessibleName("老群友快捷操作：聊天、语音、她在忙啥、更多")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.chat = _RoundButton("chat", "和老群友聊两句")
        self.voice = _RoundButton("voice", "和老群友语音聊聊")
        self.activity = _RoundButton("activity", "她最近在忙什么")
        self.more = _RoundButton("settings", "更多设置")
        # 顺时针：聊 → 听 → 她在忙啥 → 更多，四个按钮挨着排，圆盘只比它们大一圈。
        spots = ((13, 13), (57, 13), (57, 57), (13, 57))
        callbacks = (pet.show_chat, pet.show_voice, pet.show_activity, lambda: pet.show_menu())
        for button, callback, (x, y) in zip((self.chat, self.voice, self.activity, self.more),
                                            callbacks, spots):
            button.setParent(self)
            button.move(x, y)
            button.show()
            button.clicked.connect(callback)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clear(painter, self.rect())
        painter.setPen(QPen(QColor("#436356"), 1))
        painter.setBrush(QColor(23, 59, 50, 232))
        painter.drawEllipse(QRectF(self.rect()).adjusted(3, 3, -3, -3))
        painter.end()


class PetMoreMenu(QWidget):
    """自绘的「更多」面板：原来 Qt QMenu 的长方形和「打开工作台」五连子菜单都在这里收掉。"""
    radius = 22

    def __init__(self, pet):
        super().__init__(None, FLOATING)
        _floating(self)
        self.pet = pet
        self.setWindowTitle("老群友 · 更多")
        self.setAccessibleName("老群友更多设置")
        self.setFixedSize(216, 320)
        self.setStyleSheet("""
            QLabel { color:#173b32; font-size:14px; font-weight:600; background:transparent; }
            QPushButton { color:#173b32; background:transparent; border:0; border-radius:11px;
                          padding:7px 10px; font-size:13px; text-align:left; }
            QPushButton:hover { background:#e8efe4; }
            QPushButton:checked { color:#fffdf7; background:#2f5a49; }
            QPushButton#danger { color:#8b4a3e; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 18)
        layout.setSpacing(3)
        layout.addWidget(QLabel("更多"))
        self.scale = self._button("人物大小", self._next_scale)
        self.quiet = self._button("待在原地", None, checkable=True)
        self.motion = self._button("动作和特效", None, checkable=True)
        self.quiet.toggled.connect(pet.set_quiet)
        self.motion.toggled.connect(pet.set_motion)
        self._button("投喂一块小饼干", pet.feed)
        self._button("刷新状态", pet.refresh_connection)
        self._button("打开工作台", lambda: pet.open_panel("/"))
        self._button("退出桌宠", pet.close, danger=True)
        self._scale_index = 2
        self.sync()

    def _button(self, label, callback, *, checkable=False, danger=False):
        button = QPushButton(label)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(label)
        button.setObjectName("danger" if danger else "more-item")
        if callback is not None:
            button.clicked.connect(callback)
        self.layout().addWidget(button)
        return button

    def sync(self):
        brain = self.pet.brain
        self._scale_index = min(range(len(SCALES)), key=lambda i: abs(SCALES[i][0] - brain.scale))
        self.scale.setText(f"人物大小 · {SCALES[self._scale_index][1]}")
        for button, enabled in ((self.quiet, brain.quiet), (self.motion, brain.motion)):
            button.blockSignals(True)
            button.setChecked(enabled)
            button.setText(("待在原地" if button is self.quiet else "动作和特效") + ("：开" if enabled else "：关"))
            button.blockSignals(False)

    def _next_scale(self):
        self._scale_index = (self._scale_index + 1) % len(SCALES)
        self.pet.set_scale(SCALES[self._scale_index][0])
        self.scale.setText(f"人物大小 · {SCALES[self._scale_index][1]}")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clear(painter, self.rect())
        _soft_card(painter, QRectF(self.rect()).adjusted(6, 6, -6, -8), self.radius)
        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.pet.surface_closed(self)


class _Card(QWidget):
    """Rounded paper surface with real transparency at the corners."""
    radius = 22

    def __init__(self, pet, title, flags=Qt.WindowType.Dialog):
        super().__init__(None, flags | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.pet = pet
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("""
            QLineEdit { background: transparent; color:#173b32; border:0; font-size:14px; padding:4px 2px; }
            QPushButton#send { color:#fffdf7; background:#173b32; border:0; border-radius:17px; font-size:15px; }
            QPushButton#send:hover { background:#2f5a49; }
            QPushButton#send:disabled { background:#cfd8cc; color:#6a7d70; }
            QPushButton#close { color:#526b5d; background:transparent; border:0; border-radius:13px; font-size:16px; }
            QPushButton#close:hover { background:#e8e7ef; }
        """)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clear(painter, self.rect())
        _soft_card(painter, QRectF(self.rect()).adjusted(8, 6, -8, -10), self.radius)
        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.pet.surface_closed(self)


class ChatBar(_Card):
    """A pill above her head; replies come back as her speech bubble."""
    radius = 26

    def __init__(self, pet):
        super().__init__(pet, "和老群友聊两句")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 14, 18, 18)
        layout.setSpacing(8)
        self.entry = QLineEdit()
        self.entry.setMaxLength(2000)
        self.entry.setAccessibleName("想和老群友说的话")
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("send")
        self.send_button.setFixedSize(34, 34)
        self.send_button.setAccessibleName("发送")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send)
        self.entry.returnPressed.connect(self.send)
        self.entry.textChanged.connect(lambda _: self.update_status())
        layout.addWidget(self.entry, 1)
        layout.addWidget(self.send_button)
        self.resize(340, 72)
        self.update_status()

    def update_status(self):
        pet = self.pet
        ready = pet.available and bool(pet.csrf) and not pet._voice_started
        placeholder = ("正在语音聊，结束后再发文字。" if pet._voice_started else
                       "说点什么…（Enter 发送，Esc 收起）" if ready else pet.reason)
        self.entry.setPlaceholderText(placeholder)
        self.send_button.setEnabled(ready and not pet.chat_busy and bool(self.entry.text().strip()))

    def send(self):
        text = self.entry.text().strip()
        pet = self.pet
        if pet.chat_busy or pet._voice_started or not pet.available or not pet.csrf or not text:
            return
        self.entry.clear()
        pet.send_chat(text)
        self.update_status()


class _TalkButton(QPushButton):
    def __init__(self, pet, parent):
        super().__init__("静音麦克风", parent)
        self.pet = pet
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("静音麦克风")
        self.setFlat(True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background:transparent; border:0;")
        self.clicked.connect(pet.toggle_voice_mute)

    def paintEvent(self, event):
        # Paint the pill ourselves: a native macOS push-button can ignore a
        # stylesheet's rounded background and fall back to a rectangular bezel.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        fill = SAGE if not enabled else QColor("#8a655e") if self.isDown() else QColor("#2f5a49") if self.underMouse() else INK
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        font = QFont()
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(PAPER if enabled else MUTED)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self.text())
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(LILAC, 1.5))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), (rect.height() - 6) / 2, (rect.height() - 6) / 2)
        painter.end()

class VoiceCard(_Card):
    """Open to talk continuously, mute if needed, close to end the call."""
    def __init__(self, pet):
        super().__init__(pet, "和老群友语音聊聊")
        self.setStyleSheet(self.styleSheet() + """
            QLabel { color:#5b7066; font-size:12px; background:transparent; }
            QLabel#voice-title { color:#173b32; font-size:14px; font-weight:600; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 16, 26, 23)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("语音聊聊", self)
        title.setObjectName("voice-title")
        self.close_button = QPushButton("×", self)
        self.close_button.setObjectName("close")
        self.close_button.setFixedSize(26, 26)
        self.close_button.setAccessibleName("结束并关闭语音")
        self.close_button.setToolTip("结束语音")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.close_button)
        self.status = QLabel("正在连接…", self)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("语音状态")
        self.caption = QLabel("说完自动回应，也可以直接插话", self)
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setStyleSheet("color:#7b8a80; font-size:11px;")
        self.talk = _TalkButton(pet, self)
        self.talk.setFixedHeight(44)
        layout.addLayout(header)
        layout.addWidget(self.status)
        layout.addWidget(self.talk)
        layout.addWidget(self.caption)
        self.resize(310, 168)
        self.update_status()

    def update_status(self):
        pet = self.pet
        self.status.setText(pet.voice_message)
        ready = pet.voice_state in ("idle", "listening", "thinking", "speaking", "muted")
        retry = not pet._voice_started
        self.talk.setEnabled(retry or ready)
        words = ("重新连接" if retry else "取消静音" if pet._voice_muted else
                 "静音麦克风" if ready else "正在连接…")
        self.talk.setText(words)
        self.talk.setAccessibleName(words)

    def closeEvent(self, event):
        self.pet.end_voice()
        super().closeEvent(event)


class ProposalCard(_Card):
    """One thing the manager suggested in chat. Nothing changes until the first button is pressed."""
    def __init__(self, pet, action):
        super().__init__(pet, "老群友 · 确认一下")
        self.action = action
        self.setStyleSheet(self.styleSheet() + """
            QLabel { color:#173b32; font-size:14px; }
            QPushButton { border-radius:15px; padding:6px 16px; font-size:13px; }
            QPushButton#yes { color:#fffdf7; background:#173b32; border:0; }
            QPushButton#no { color:#173b32; background:transparent; border:1px solid #d7dfcf; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 24, 24)
        layout.setSpacing(12)
        if action["kind"] == "toggle":
            words = f"{'打开' if action['enabled'] else '关掉'}「{action['label']}」？"
            yes_text = "打开" if action["enabled"] else "关掉"
        else:
            words, yes_text = "这件事需要 Agent 帮忙，打开交接页？", "打开交接页"
        # Parent every child explicitly: a button first added to a loose layout stays Python-owned,
        # and Qt deleting it again with the card crashed the process at exit.
        self.label = QLabel(words, self)
        self.label.setWordWrap(True)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.yes = QPushButton(yes_text, self)
        self.yes.setObjectName("yes")
        self.no = QPushButton("算了", self)
        self.no.setObjectName("no")
        for button in (self.yes, self.no):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch(1)
        self.yes.clicked.connect(lambda: pet.confirm_proposal(self.action))
        self.yes.setEnabled(action["kind"] != "toggle" or "toggle" not in pet._jobs)
        self.no.clicked.connect(self.close)
        layout.addWidget(self.label)
        layout.addLayout(row)
        self.resize(286, 118)


class ActivityCard(_Card):
    """What she has been up to: agent signals, local replies and connection changes."""
    def __init__(self, pet):
        super().__init__(pet, "老群友 · 动态")
        self.resize(320, 380)
        close = QPushButton("×", self)
        close.setObjectName("close")
        close.setFixedSize(26, 26)
        close.setAccessibleName("关闭动态")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.close)
        close.move(self.width() - 46, 20)
        self.close_button = close

    EMPTY = ("", "还没有新动态。她最近的消息和动态会留在这里。")

    def entries(self):
        return list(self.pet.activity)[-9:][::-1] or [self.EMPTY]

    def fit(self):
        """Grow with the entries instead of leaving a tall empty card."""
        body = QFont()
        body.setPixelSize(13)
        metrics = QFontMetricsF(body)
        height = 98.0
        for stamp, text in self.entries():
            width = self.width() - (124 if stamp else 80)
            used = metrics.boundingRect(QRectF(0, 0, width, 400), int(Qt.TextFlag.TextWordWrap), text)
            height += max(26.0, used.height() + 10)
        self.resize(self.width(), round(max(190.0, min(420.0, height + 34))))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pet = self.pet
        title = QFont()
        title.setPixelSize(17)
        title.setBold(True)
        painter.setFont(title)
        painter.setPen(INK)
        painter.drawText(QPointF(28, 42), "她最近在忙什么")
        small = QFont()
        small.setPixelSize(12)
        painter.setFont(small)
        painter.setPen(MUTED)
        painter.drawText(QRectF(28, 52, self.width() - 56, 36), int(Qt.TextFlag.TextWordWrap), pet.status_summary())
        y = 98.0
        body = QFont()
        body.setPixelSize(13)
        for stamp, text in self.entries():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(SAGE)
            painter.drawEllipse(QPointF(33, y + 8), 3.5, 3.5)
            painter.setFont(small)
            painter.setPen(MUTED)
            painter.drawText(QPointF(44, y + 12), stamp)
            painter.setFont(body)
            painter.setPen(INK)
            box = QRectF(88 if stamp else 44, y, self.width() - (124 if stamp else 80), 60)
            used = painter.boundingRect(box, int(Qt.TextFlag.TextWordWrap), text)
            painter.drawText(box, int(Qt.TextFlag.TextWordWrap), text)
            y += max(26.0, used.height() + 10)
            if y > self.height() - 40:
                break
        painter.end()


# ── the character ──
class PetWidget(QWidget):
    def __init__(self, client, *, quit_on_close=True, preferences_path=None, rng=None, clock=None, sprites=None,
                 voice_factory=NativeVoiceSession, permission_request=request_microphone_permission):
        super().__init__(None, FLOATING)
        _floating(self)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Tool windows do not always count as primary windows. Closing a card
        # must not terminate the still-visible character.
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.client, self.quit_on_close = client, quit_on_close
        self.clock = clock or time.monotonic
        self.preferences_path = Path(preferences_path) if preferences_path is not None else None
        self._preferences_warning = False
        preferences = _read_preferences(self.preferences_path)
        motion = (preferences.get("motion", True)
                  and os.environ.get("REGULAR_REDUCE_MOTION", "").lower() not in ("1", "true"))
        self.sprites = sprites or SpriteBank()
        self.brain = PetBrain(rng=rng or random.Random(), now=self.now(), motion=motion,
                              quiet=preferences.get("quiet", False), scale=preferences.get("scale", 1.0))
        self.setObjectName("regular-desktop-pet")
        self.setWindowTitle("The Regular · 老群友")
        self.setAccessibleName("老群友：靠近显示聊天和设置，拖起来会晃，戳一下有反应")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.csrf, self.available, self.qq_connected = "", False, False
        self.reason = "正在连接…"
        self.chat_busy = False
        self.activity = deque(maxlen=40)
        self._unread = 0
        self._last_connection = None
        self._presence_since = None
        self._closing = False
        self._jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._menu = None
        self._menu_leave_at = None
        self._toolbar_leave_at = None
        self._mask_key = None
        self._paint_key = None
        self._fx_dirty = False
        self.chat_bar = self.activity_card = self.proposal_card = self.voice_card = None
        self.voice_factory, self.permission_request = voice_factory, permission_request
        self.voice_session = None
        self.voice_state, self.voice_message = "closed", "正在连接…"
        self._voice_started = False
        self._voice_muted = False
        self._voice_generation = 0
        self._voice_problem = ""
        self._voice_transcript_serial = None
        self.voice_signals = _VoiceSignals(self)
        self.voice_signals.event.connect(self._voice_event)
        self.voice_signals.permission.connect(self._voice_permission)
        self._pending_toggle = None
        self.fx = FxLayer(self)
        self.bubble = SpeechBubble(self)
        self.toolbar = PetToolbar(self)
        self._apply_size()
        self._place_initial(preferences)
        self.animation = QTimer(self)
        self.animation.setInterval(16)
        self.animation.timeout.connect(self.step)
        self.animation.start()
        self.poll = QTimer(self)
        self.poll.setInterval(15000)
        self.poll.timeout.connect(self.refresh_connection)
        self.poll.start()
        self.presence_poll = QTimer(self)
        self.presence_poll.setInterval(3000)
        self.presence_poll.timeout.connect(self.refresh_presence)
        self.presence_poll.start()

    # ── clock & geometry ──
    def now(self):
        return self.clock()

    def _apply_size(self):
        scale = self.brain.scale
        canvas = self.sprites.width * self.sprites.points(scale)
        self.pad_x, self.pad_top, self.pad_bottom = canvas * 0.3, canvas * 0.3, canvas * 0.06
        self.anchor_pt = (self.sprites.anchor[0] * self.sprites.points(scale),
                          self.sprites.anchor[1] * self.sprites.points(scale))
        width = round(canvas + 2 * self.pad_x)
        height = round(self.pad_top + canvas + self.pad_bottom)
        self.setFixedSize(width, height)
        self.fx.setFixedSize(width, height + round(canvas * 0.5))
        self._mask_key = self._paint_key = None

    def window_anchor(self):
        """Feet anchor inside this window, in points."""
        return QPointF(self.width() / 2, self.pad_top + self.anchor_pt[1])

    def _screen_for(self, x, y):
        screens = QApplication.screens()
        if not screens:
            return None

        def distance(screen):
            area = screen.availableGeometry()
            cx = max(area.left(), min(x, area.right()))
            cy = max(area.top(), min(y, area.bottom()))
            return (cx - x) ** 2 + (cy - y) ** 2
        return min(screens, key=distance).availableGeometry()

    def _ground_from(self, x, y):
        area = self._screen_for(x, y)
        if area is not None:
            self.brain.set_ground(area.left(), area.right() + 1, area.bottom() + 1, area.top())

    def _place_initial(self, preferences):
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
        x = preferences.get("x", area.right() - 220)
        self._ground_from(x, area.bottom())
        self.brain.place(x)
        # Entrance: drop in from above and land on the floor.
        self.brain.enter(self.now(), min(320.0, area.height() * 0.4))
        self._sync_windows(force=True)

    def head_global(self):
        left, top, right, bottom = self.brain.body_rect()
        return QPoint(round((left + right) / 2), round(top))

    # ── frame loop ──
    def step(self, now=None):
        if self._closing:
            return
        now = self.now() if now is None else now
        brain = self.brain
        if brain.mode == "drag":
            self._ground_from(QCursor.pos().x(), QCursor.pos().y())
        else:
            self._ground_from(brain.x, brain.y - 10)
        cursor = QCursor.pos()
        brain.hover(now, cursor.x(), cursor.y(), near=self._near(cursor))
        brain.set_attention(self._attention(), now)
        brain.tick(now)
        # Smooth 60 fps only while she moves; idle poses and breathing are fine at 30 fps.
        moving = brain.mode != "ground" or brain.clip in ("walk", "hop", "land") or brain.fading(now)
        self.animation.setInterval(16 if moving else 33)
        self._sync_windows()
        self._update_toolbar(cursor, now)
        if brain.bubble is not None:
            self.bubble.show_bubble(brain.bubble, now)
            self._place_bubble()
            if self.bubble.needs_paint(now):
                self.bubble.update()
        elif self.bubble.isVisible():
            self.bubble.hide()
            self.bubble.serial = 0

    def _near(self, cursor):
        left, top, right, bottom = self.brain.body_rect()
        zone = QRect(round(left - 36), round(top - 30), round(right - left + 72), round(bottom - top + 50))
        if self.toolbar.isVisible():
            zone = zone.united(self.toolbar.geometry().adjusted(-14, -14, 14, 14))
        return zone.contains(cursor)

    def _attention(self):
        return bool(self.toolbar.isVisible()
                    or any(card is not None and card.isVisible()
                           for card in (self.chat_bar, self.activity_card, self.proposal_card, self.voice_card))
                    or (self._menu and self._menu.isVisible()))

    def _sync_windows(self, force=False):
        brain = self.brain
        anchor = self.window_anchor()
        top_left = QPoint(round(brain.x - anchor.x()), round(brain.y - anchor.y()))
        if force or top_left != self.pos():
            self.move(top_left)
            self.fx.move(top_left.x(), top_left.y() - (self.fx.height() - self.height()))
            if self.toolbar.isVisible():
                self._place_toolbar()
            if self.chat_bar is not None and self.chat_bar.isVisible():
                self._place_chat()
            if self.proposal_card is not None and self.proposal_card.isVisible():
                self._place_proposal()
            if self.voice_card is not None and self.voice_card.isVisible():
                self._place_voice()
        strip, frame, mirrored, transform = brain.pose()
        fade = transform["fade"]
        key = (strip, frame, mirrored, round(transform["sx"], 3), round(transform["sy"], 3),
               round(transform["rot"], 1), round(transform["dy"], 1), brain.mode,
               fade and (fade[0], fade[1], fade[2], round(fade[3], 2)))
        if force or key != self._paint_key:
            self._paint_key = key
            self._update_mask(strip, frame, mirrored, transform)
            self.update()
        if brain.particles or self._fx_dirty:
            if brain.particles and not self.fx.isVisible() and self.isVisible():
                self.fx.show()
            self.fx.update()
            self._fx_dirty = bool(brain.particles)

    def _update_mask(self, strip, frame, mirrored, transform):
        brain = self.brain
        # While moving or dissolving between two frames the silhouette changes every paint.
        if brain.mode != "ground" or transform["rot"] or transform["dy"] or transform["fade"]:
            if self._mask_key is not None:
                self.clearMask()
                self._mask_key = None
            return
        key = (strip, frame, mirrored, brain.scale)
        if key == self._mask_key:
            return
        self._mask_key = key
        anchor = self.window_anchor()
        origin = QPoint(round(anchor.x() - self.anchor_pt[0]), round(anchor.y() - self.anchor_pt[1]))
        region = self.sprites.region(strip, frame, mirrored, brain.scale).translated(origin)
        shadow = QRect(round(anchor.x() - brain.figure * 0.22), round(anchor.y() - 6), round(brain.figure * 0.44), 12)
        self.setMask(region.united(QRegion(shadow, QRegion.RegionType.Ellipse)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        _clear(painter, self.rect())
        brain = self.brain
        strip, frame, mirrored, transform = brain.pose()
        anchor = self.window_anchor()
        figure = brain.figure
        if brain.mode == "ground":
            lift = min(1.0, -transform["dy"] / (figure * 0.32)) if transform["dy"] else 0.0
            width = figure * (0.5 if brain.posture() == "lie" else 0.36) * (1 - 0.35 * lift)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 40, 35, round(46 * (1 - 0.5 * lift))))
            painter.drawEllipse(QPointF(anchor.x(), anchor.y() - 1), width / 2, figure * 0.028)
        pixmap = self.sprites.pixmap(strip, frame, mirrored, brain.scale, self.devicePixelRatioF())
        painter.translate(anchor.x(), anchor.y() + transform["dy"])
        if transform["rot"]:
            pivot = -figure * 0.98 if transform["pivot"] == "hands" else 0.0
            painter.translate(0, pivot)
            painter.rotate(transform["rot"])
            painter.translate(0, -pivot)
        painter.scale(transform["sx"], transform["sy"])
        fade = transform["fade"]
        if fade is not None:
            # The outgoing frame fades while the new one comes up a little faster, so the
            # figure never turns see-through halfway.
            old_strip, old_frame, old_mirrored, opacity = fade
            painter.setOpacity(opacity)
            painter.drawPixmap(QPointF(-self.anchor_pt[0], -self.anchor_pt[1]),
                               self.sprites.pixmap(old_strip, old_frame, old_mirrored, brain.scale,
                                                   self.devicePixelRatioF()))
            painter.setOpacity(1 - opacity * opacity)
        painter.drawPixmap(QPointF(-self.anchor_pt[0], -self.anchor_pt[1]), pixmap)
        painter.end()

    # ── input ──
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.globalPosition()
        self.brain.press(self.now(), point.x(), point.y())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(event)
        point = event.globalPosition()
        self.brain.drag(self.now(), point.x(), point.y())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        point = event.globalPosition()
        result = self.brain.release(self.now(), point.x(), point.y())
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if result == "click":
            # React on release; waiting out the double-click interval felt laggy.
            self.brain.poke(self.now())
        elif result == "drop":
            self._save_preferences()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.brain.release(self.now(), event.globalPosition().x(), event.globalPosition().y())
        self.show_chat()
        event.accept()

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPosition().toPoint())
        event.accept()

    def show_menu(self, position=None):
        """右键和环形菜单的「更多」是同一个自绘面板，不再有 Qt 原生 QMenu。"""
        if self._menu is None:
            self._menu = PetMoreMenu(self)
        self._menu.sync()
        menu = self._menu
        if isinstance(position, QPoint):
            rect = QRect(position.x(), position.y(), menu.width(), menu.height())
        elif self.toolbar.isVisible():
            ring = self.toolbar.geometry()
            x = ring.right() + 8 if self._side() > 0 else ring.left() - 8 - menu.width()
            rect = QRect(x, ring.center().y() - menu.height() // 2, menu.width(), menu.height())
        else:
            point = QCursor.pos()
            rect = QRect(point.x(), point.y(), menu.width(), menu.height())
        self._menu.move(self._clamp(rect))
        self._menu.show()
        self._menu.raise_()
        self._menu_leave_at = None

    # ── settings ──
    def set_scale(self, scale):
        scale = max(0.5, min(float(scale), 1.6))
        self.brain.scale = scale
        self._apply_size()
        self.brain.set_ground(self.brain.left, self.brain.right, self.brain.floor)
        self._sync_windows(force=True)
        self._save_preferences()

    def set_quiet(self, enabled):
        self.brain.quiet = bool(enabled)
        if enabled:
            self.brain.settle(self.now())
        self._save_preferences()

    def set_motion(self, enabled):
        self.brain.motion = bool(enabled)
        if not enabled:
            self.brain.settle(self.now())
        self.brain.particles.clear()
        self._sync_windows(force=True)
        self._save_preferences()

    def _save_preferences(self):
        if self.preferences_path is None:
            return
        try:
            atomic_write_json(self.preferences_path, {"scale": self.brain.scale, "x": round(self.brain.x, 1),
                                                      "quiet": self.brain.quiet, "motion": self.brain.motion})
        except OSError:
            if not self._preferences_warning:
                print("桌宠偏好未能保存，本次调整仍已生效。")
                self._preferences_warning = True

    # ── floating surfaces ──
    def _side(self):
        area = self._screen_for(self.brain.x, self.brain.y - 10)
        return 1 if area is None or self.brain.x < area.center().x() else -1

    def _clamp(self, rect):
        area = self._screen_for(rect.center().x(), rect.center().y())
        if area is None:
            return rect.topLeft()
        x = max(area.left() + 6, min(rect.left(), area.right() - rect.width() - 6))
        y = max(area.top() + 6, min(rect.top(), area.bottom() - rect.height() - 6))
        return QPoint(x, y)

    def _place_toolbar(self):
        left, top, right, bottom = self.brain.body_rect()
        side = self._side()
        x = right + 2 if side > 0 else left - 2 - self.toolbar.width()
        y = (top + bottom) / 2 - self.toolbar.height() / 2
        self.toolbar.move(self._clamp(QRect(round(x), round(y), self.toolbar.width(), self.toolbar.height())))

    def _place_bubble(self):
        head = self.head_global()
        top = head.y() - 8
        if self.chat_bar is not None and self.chat_bar.isVisible():
            top = self.chat_bar.y() + 4
        rect = QRect(round(head.x() - self.bubble.width() / 2), round(top - self.bubble.height()),
                     self.bubble.width(), self.bubble.height())
        self.bubble.move(self._clamp(rect))

    def _place_chat(self):
        head = self.head_global()
        bar = self.chat_bar
        rect = QRect(round(head.x() - bar.width() / 2), round(head.y() - bar.height() - 4), bar.width(), bar.height())
        if self.toolbar.isVisible() and rect.intersects(self.toolbar.geometry()):
            # Never cover the side palette: lift the pill above it.
            rect.moveBottom(self.toolbar.geometry().top() - 4)
        bar.move(self._clamp(rect))

    def _place_proposal(self):
        card = self.proposal_card
        left, top, right, bottom = self.brain.body_rect()
        x = right + 40 if self._side() > 0 else left - 40 - card.width()
        card.move(self._clamp(QRect(round(x), round(top), card.width(), card.height())))

    def _place_voice(self):
        card = self.voice_card
        head = self.head_global()
        card.move(self._clamp(QRect(round(head.x() - card.width() / 2),
                                   round(head.y() - card.height() - 8), card.width(), card.height())))

    def _update_toolbar(self, cursor, now):
        if self._closing or not self.isVisible():
            return
        if self.brain.mode != "ground":
            self.toolbar.hide()
            return
        # 自绘面板没有 QMenu 那种「点外面自动收起」，这里补上。
        if self._menu is not None and self._menu.isVisible():
            zone = self._menu.geometry().adjusted(-24, -24, 24, 24)
            if not zone.contains(cursor) and not self._near(cursor):
                if self._menu_leave_at is None:
                    self._menu_leave_at = now
                elif now - self._menu_leave_at >= 1.2:
                    self._menu.hide()
                    self._menu_leave_at = None
            else:
                self._menu_leave_at = None
        focused = any(card is not None and card.isVisible() and card.isActiveWindow()
                      for card in (self.chat_bar, self.activity_card, self.proposal_card, self.voice_card))
        if self._near(cursor) or bool(self._menu and self._menu.isVisible()) or focused:
            self._toolbar_leave_at = None
            if not self.toolbar.isVisible():
                self._place_toolbar()
                self.toolbar.show()
                if self.chat_bar is not None and self.chat_bar.isVisible():
                    self._place_chat()
        elif self.toolbar.isVisible():
            if self._toolbar_leave_at is None:
                self._toolbar_leave_at = now
            elif now - self._toolbar_leave_at >= 0.7:
                self.toolbar.hide()

    def show_chat(self):
        if self.chat_bar is None:
            self.chat_bar = ChatBar(self)
        self.chat_bar.update_status()
        self._place_chat()
        self.chat_bar.show()
        self.chat_bar.raise_()
        self.chat_bar.activateWindow()
        self.chat_bar.entry.setFocus()

    def show_voice(self):
        if self.voice_card is None:
            self.voice_card = VoiceCard(self)
        self.voice_card.update_status()
        self._place_voice()
        self.voice_card.show()
        self.voice_card.raise_()
        self.voice_card.activateWindow()
        self.start_voice()

    def _voice_status(self, state, message):
        self.voice_state, self.voice_message = state, message
        if state in ("idle", "closed", "listening", "muted") and not self.chat_busy:
            self.brain.chat_pending = False
            if self.brain.bubble is not None and self.brain.bubble.kind == "think":
                self.brain.bubble = None
        if self.voice_card is not None:
            self.voice_card.update_status()
        if self.chat_bar is not None:
            self.chat_bar.update_status()

    def start_voice(self):
        if self._voice_started or self._closing:
            return
        if not self.csrf:
            self._voice_status("closed", "还没连上本机面板，请刷新连接后再试。")
            return
        if self.chat_busy:
            self._voice_status("closed", "等这一句聊完，再开始语音吧。")
            return
        self._voice_started = True
        self._voice_muted = False
        self._voice_problem = ""
        self._voice_generation += 1
        generation = self._voice_generation
        # 语音会话真的在持续，状态一直演到结束，不是靠长超时「假装在忙」。
        self.brain.set_state("listen", self.now(), seconds=3600.0)
        self._voice_status("permission", "请允许麦克风，连好后就能直接说话。")
        self.permission_request(lambda granted, message:
                                self.voice_signals.permission.emit(generation, granted, message))

    @Slot(int, bool, str)
    def _voice_permission(self, generation, granted, message):
        if generation != self._voice_generation or not self._voice_started or self._closing:
            return
        if not granted:
            self._voice_started = False
            self.brain.clear_state(self.now())
            self._voice_status("closed", message)
            return
        self._voice_status("connecting", "正在连接语音…")
        self.voice_session = self.voice_factory(
            self.client, self.csrf, lambda event: self.voice_signals.event.emit(generation, event), continuous=True)
        self.voice_session.start()

    def end_voice(self):
        self._voice_generation += 1
        self._voice_started = False
        self.brain.clear_state(self.now())
        if self.voice_session is not None:
            self.voice_session.close()
            self.voice_session = None
        self._voice_status("closed", "语音已结束，麦克风已关闭。")

    def toggle_voice_mute(self):
        if not self._voice_started:
            self.start_voice()
            return
        muted = not self._voice_muted
        if self.voice_session is not None and self.voice_session.set_muted(muted):
            self._voice_muted = muted
            self._voice_status("muted" if muted else "listening", "麦克风已静音。" if muted else "我在听，直接说就好。")

    @Slot(int, object)
    def _voice_event(self, generation, event):
        if generation != self._voice_generation or self._closing:
            return
        kind = event.get("type")
        if kind == "ready":
            self._voice_problem = ""
            self._voice_transcript_serial = None
            self._voice_status("listening", "我在听，直接说就好。")
        elif kind == "state":
            state = event.get("state")
            if state in ("listening", "thinking"):
                self._voice_transcript_serial = None
            words = {"connecting": "正在连接语音…", "listening": "我在听…",
                     "thinking": "正在想怎么接…", "speaking": "她在说话…",
                     "idle": "我在听，直接说就好。", "muted": "麦克风已静音。"}
            if state in words:
                if state == "thinking" and not self.brain.chat_pending:
                    self.brain.chat_sent(self.now())
                self._voice_status(state, words[state])
        elif kind == "transcript" and event.get("text"):
            text = str(event["text"])
            if event.get("role") == "assistant":
                self._show_voice_transcript(text, final=bool(event.get("final")))
            if event.get("final"):
                self._record(("你：" if event.get("role") == "user" else "她：") + text[:80], unread=False)
        elif kind == "proposal":
            from regular.local_chat import proposal
            action = proposal(event.get("action"))
            if action:
                self._voice_status("idle", "确认一下这项调整，再继续聊。")
                self.propose(action)
        elif kind == "error":
            message = str(event.get("message") or "这次语音没接上；没有自动重试。")
            self._voice_problem = message
            self._voice_status("idle", message)
            if not self.chat_busy:
                self.brain.chat_failed(self.now(), message)
        elif kind == "closed":
            self._voice_started = False
            self.voice_session = None
            self.brain.clear_state(self.now())
            self._voice_status("closed", self._voice_problem or "语音已结束，麦克风已关闭。")

    def _show_voice_transcript(self, text, *, final):
        """One reply owns one bubble; cumulative audio text is already streamed."""
        now = self.now()
        bubble = self.brain.bubble
        if (self._voice_transcript_serial is None
                or (bubble is not None and bubble.serial != self._voice_transcript_serial)):
            self.brain.chat_reply(now, text)
            bubble = self.brain.bubble
            self._voice_transcript_serial = bubble.serial
        elif bubble is None:
            return  # Clicking the bubble dismisses this reply, including later chunks.
        bubble.text = text
        bubble.until = now + (max(6.0, min(30.0, 4 + len(text) * .16)) if final else 20)
        self.bubble.streaming_serial = bubble.serial

    def show_activity(self):
        if self.activity_card is None:
            self.activity_card = ActivityCard(self)
        left, top, right, bottom = self.brain.body_rect()
        card = self.activity_card
        card.fit()
        side = self._side()
        x = right + 70 if side > 0 else left - 70 - card.width()
        self.activity_card.move(self._clamp(QRect(round(x), round(bottom - card.height()), card.width(), card.height())))
        card.show()
        card.raise_()
        card.activateWindow()
        self._unread = 0
        self.toolbar.activity.set_badge(0)

    def surface_closed(self, surface):
        if not self._closing:
            self.brain.set_attention(False, self.now())

    def feed(self):
        self.brain.feed(self.now())

    def propose(self, action):
        """Show the manager's proposal beside her; it only runs on confirm."""
        if self.proposal_card is not None:
            self.proposal_card.close()
            self.proposal_card.deleteLater()
        card = self.proposal_card = ProposalCard(self, action)
        self.brain.set_attention(True, self.now())
        self._place_proposal()
        card.show()
        card.raise_()

    def confirm_proposal(self, action):
        card = self.proposal_card
        # A dismissed/replaced card must not execute a queued old click. Keep
        # one mutation in flight so its result cannot be assigned to a newer one.
        if self._closing or card is None or not card.isVisible() or card.action is not action:
            return
        if action["kind"] == "toggle" and "toggle" in self._jobs:
            card.yes.setEnabled(False)
            self.brain.say("上一项调整还在处理中，请稍等一下。", now=self.now(), seconds=3)
            return
        card.close()
        if action["kind"] == "handoff":
            self.open_panel("/iterate")
            return
        if not self.csrf:
            self.brain.say("还没连上，开关没动。", now=self.now(), seconds=4)
            return
        csrf, self._pending_toggle = self.csrf, action
        self._submit("toggle", lambda: self.client.toggle(action["flag"], action["enabled"], csrf))

    def open_panel(self, path):
        webbrowser.open(self.client.management_url(path))

    # ── local state and requests ──
    def connection_text(self):
        if not self.available:
            return self.reason
        return "可以聊天 · " + ("QQ 已连接" if self.qq_connected else "QQ 暂未连接")

    def status_summary(self):
        p = self.brain.presence
        if not p.known:
            return self.connection_text()
        mood = "心情不错" if p.mood >= 0.3 else "有点烦" if p.mood <= -0.3 else "心情平稳"
        parts = ["在线" if p.bot == "ready" else "还没启动", mood]
        if p.circadian:
            parts.append(p.circadian)
        if p.chatter:
            parts.append(f"群里 5 分钟内 {p.chatter} 条消息")
        return " · ".join(parts)

    def _record(self, text, *, unread=True):
        self.activity.append((time.strftime("%H:%M"), text))
        if unread and (self.activity_card is None or not self.activity_card.isVisible()):
            self._unread += 1
            self.toolbar.activity.set_badge(self._unread)
        if self.activity_card is not None:
            if self.activity_card.isVisible():
                bottom = self.activity_card.geometry().bottom()
                self.activity_card.fit()
                self.activity_card.move(self.activity_card.x(), bottom - self.activity_card.height() + 1)
            self.activity_card.update()

    def _submit(self, key, operation):
        if key in self._jobs or self._closing:
            return
        job = _Request(key, operation)
        job.signals.finished.connect(self._finished)
        self._jobs[key] = job
        if key in ("chat", "toggle"):
            # Leaving the pet must not wait for the model's bounded HTTP read.
            # A sent request can still finish in the bot; it is never repeated.
            job.setAutoDelete(False)
            threading.Thread(target=job.run, name=f"regular-pet-{key}", daemon=True).start()
        else:
            self.pool.start(job)

    def refresh_connection(self):
        self._submit("session", self.client.session)

    def refresh_presence(self):
        since = self._presence_since
        self._submit("presence", lambda: self.client.presence(since))

    def send_chat(self, text):
        if self.chat_busy or self._voice_started or not self.available or not self.csrf:
            return
        self.chat_busy = True
        self.brain.chat_sent(self.now())
        self._record("你：" + text, unread=False)
        csrf = self.csrf
        self._submit("chat", lambda: self.client.send(text, csrf))

    @Slot(str, object)
    def _finished(self, key, result):
        self._jobs.pop(key, None)
        if self._closing:
            return
        now = self.now()
        if key == "session":
            state = result.get("value") or {}
            self.csrf = state.get("csrf", "") if result["ok"] else ""
            self.available = bool(result["ok"] and state.get("available"))
            self.qq_connected = bool(result["ok"] and state.get("qq_connected"))
            self.reason = "还没连上，去首页看看。" if result["ok"] else result["message"]
            current = (self.available, self.qq_connected)
            if self._last_connection is not None and current != self._last_connection:
                self._record(self.connection_text())
            self._last_connection = current
        elif key == "presence":
            if result["ok"]:
                data = result["value"]
                events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
                fresh = [e for e in events if not self.brain.has_seen(e)]
                # 本机语音会话是真实且持续的状态：面板里的群活动别把它覆盖掉。
                if self._voice_started and isinstance(data, dict) and "state" in data:
                    data = {k: v for k, v in data.items() if k != "state"}
                self.brain.observe(now, data)
                for event in fresh:
                    line = _ACTIVITY.get(event.get("kind"))
                    if event.get("kind") == "cue" and isinstance(event.get("text"), str) and event["text"]:
                        line = "Agent：" + event["text"][:60]
                    elif event.get("kind") == "state":
                        line = {"handoff": "你把一段反馈交给了 Agent", "checkup": "在看每日体检",
                                "stuck": "有项能力还没配好"}.get(event.get("state"))
                    if line:
                        self._record(line)
                stamps = [e["ts"] for e in events if type(e.get("ts")) in (int, float) and e.get("kind") != "cue"]
                if stamps:
                    self._presence_since = max(stamps + [self._presence_since or 0.0])
        elif key == "chat":
            self.chat_busy = False
            if result["ok"]:
                self.brain.chat_reply(now, result["value"])
                self._record("她：" + result["value"][:80])
                action = getattr(result["value"], "action", None)
                if action:
                    self.propose(action)
            else:
                self.brain.chat_failed(now, result["message"])
                self._record(result["message"])
        elif key == "toggle":
            action, self._pending_toggle = self._pending_toggle or {}, None
            if self.proposal_card is not None:
                self.proposal_card.yes.setEnabled(True)
            if result["ok"]:
                done = (f"已保存「{action.get('label', '')}」{'开启' if action.get('enabled') else '关闭'}。"
                        + str(result.get("value") or ""))
                self.brain.say(done, now=now, seconds=8)
                self._record(done)
            else:
                self.brain.say(result["message"], now=now, seconds=5)
        if self.chat_bar is not None:
            self.chat_bar.update_status()

    def closeEvent(self, event):
        self._save_preferences()
        self._closing = True
        for timer in (self.poll, self.presence_poll, self.animation):
            timer.stop()
        self.end_voice()
        for surface in (self.toolbar, self.bubble, self.fx, self.chat_bar, self.activity_card,
                        self.proposal_card, self.voice_card, self._menu):
            if surface is not None:
                surface.close()
        self.pool.clear()
        event.accept()
        if self.quit_on_close and QApplication.instance() is not None:
            # exit(), not quit(): on macOS quit() goes through NSApp terminate and
            # ends the process before the launcher can stop its own panel child.
            QApplication.instance().exit(0)


_ACTIVITY = {"mention": "群里有人@了她", "named": "群里有人喊了她的名字", "quoted": "有人引用了她的话",
             "called": "群里有人叫她", "replying": "她在琢磨怎么接话", "said": "她在群里回了一句",
             "chimed": "她在群里插了句嘴", "searching": "她在帮群里查资料", "proactive": "她在群里主动开了个话头", "drawing": "她开始给群里画图", "drew": "她给群里画了张图",
             "singing": "她在录一段语音", "voice": "她在群里发了条语音", "media_end": "这次没发出去"}


def run(*, port, token, preferences_path=None):
    app = QApplication.instance() or QApplication(["regular-pet"])
    app.setApplicationName("The Regular")
    app.setApplicationDisplayName("老群友")
    app.setDesktopFileName("io.github.the-regular")
    icon = QPixmap()
    icon.loadFromData(files("regular").joinpath("assets", "app-icon.png").read_bytes())
    app.setWindowIcon(QIcon(icon))
    pet = PetWidget(PanelClient(port, token), preferences_path=preferences_path)
    app._regular_pet = pet
    interrupted = False

    def request_stop(signum, frame):
        nonlocal interrupted
        # Raising inside a Qt slot is swallowed by PySide. Leave the event loop
        # first; the launcher must receive the interruption to clean its child.
        interrupted = True
        app.exit(0)

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
        pet.show()
        QTimer.singleShot(0, pet.refresh_connection)
        QTimer.singleShot(300, pet.refresh_presence)
        result = app.exec() if not interrupted else 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if not pet._closing:
            pet.close()
    if interrupted:
        raise KeyboardInterrupt
    return result
