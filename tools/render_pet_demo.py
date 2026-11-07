#!/usr/bin/env python3
"""Render actual desktop widgets for README; never capture the user's screen.

Requires desktop-build dependencies. All cursor, clock and conversation data
are synthetic. No instance configuration, network or audio device is used.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import random
import sys
import tempfile
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['REGULAR_REDUCE_MOTION'] = ''
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication
from regular import desktop_pet
from regular.desktop_pet import PetWidget

WIDTH, HEIGHT, FLOOR, FPS, SECONDS = 960, 480, 416, 20, 20

def blocked(*args, **kwargs):
    raise AssertionError('The README demo must never make a live request')

class OfflineClient:
    def __getattr__(self, name):
        return blocked

def smooth(start, end, progress):
    p = max(0., min(1., progress))
    return start + (end - start) * p * p * (3 - 2 * p)

def mouse(pet, kind, point, pressed=True):
    event = QMouseEvent(kind, QPointF(10, 10), QPointF(*point),
                        Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pet, event)

def font(size, bold=False):
    f = QFont()
    f.setPixelSize(size)
    f.setBold(bold)
    return f

def background(p, stage, detail):
    p.fillRect(QRect(0, 0, WIDTH, HEIGHT), QColor('#f5f4ed'))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#e7ebe1'))
    p.drawEllipse(QRectF(535, 90, 345, 345))
    p.setBrush(QColor('#ede8f3'))
    p.drawEllipse(QRectF(714, 95, 120, 120))
    p.setPen(QColor('#173b32'))
    p.setFont(font(15, True))
    p.drawText(QPointF(48, 52), 'THE REGULAR · 老群友')
    p.setFont(font(32, True))
    p.drawText(QPointF(48, 105), '住在桌边，也聊得来。')
    p.setPen(QColor('#657566'))
    p.setFont(font(12))
    p.drawText(QRectF(730, 31, 182, 28), int(Qt.AlignmentFlag.AlignRight), '本机交互示意 · 预设对话')
    p.setPen(QColor('#173b32'))
    p.setFont(font(21, True))
    p.drawText(QPointF(49, 207), stage)
    p.setFont(font(14))
    p.setPen(QColor('#657566'))
    p.drawText(QRectF(49, 225, 292, 110), int(Qt.TextFlag.TextWordWrap), detail)
    p.setPen(QPen(QColor('#d6ded1'), 1))
    p.drawLine(QPointF(48, FLOOR + 1), QPointF(WIDTH - 48, FLOOR + 1))
    p.setPen(QColor('#657566'))
    p.setFont(font(12))
    p.drawText(QPointF(49, 450), '靠近就能找到入口，离开后安静陪着你。')

def composite(pet, stage, detail, cursor):
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    background(p, stage, detail)
    for widget in (pet, pet.fx, pet.toolbar, pet.chat_bar, pet.bubble):
        if widget is not None and widget.isVisible():
            p.drawPixmap(widget.pos(), widget.grab())
    x, y = cursor
    path = QPainterPath()
    path.moveTo(x, y)
    for dx, dy in ((1, 20), (6, 15), (11, 23), (15, 21), (10, 13), (18, 12)):
        path.lineTo(x + dx, y + dy)
    path.closeSubpath()
    p.setPen(QPen(QColor('#365142'), 1.1))
    p.setBrush(QColor('#fffdf7'))
    p.drawPath(path)
    p.end()
    return Image.frombytes('RGBA', (WIDTH, HEIGHT), bytes(image.constBits())).convert('RGB')

def render(output, *, preview_dir=None, preview_only=False):
    app = QApplication.instance() or QApplication(['regular-readme-demo'])
    app.setQuitOnLastWindowClosed(False)
    now, cursor = [100.], [QPoint(-500, -500)]
    frames, fired = [], set()
    previews = {2.8: 'ring', 6.8: 'highfive', 10.2: 'drag', 15.7: 'chat', 19.7: 'ring-close'}
    with ExitStack() as stack:
        stack.enter_context(patch.object(PetWidget, '_screen_for', return_value=QRect(0, 0, WIDTH, FLOOR)))
        stack.enter_context(patch.object(desktop_pet.QCursor, 'pos', side_effect=lambda: cursor[0]))
        pet = PetWidget(OfflineClient(), quit_on_close=False, preferences_path=None,
                        rng=random.Random(4), clock=lambda: now[0],
                        permission_request=blocked, voice_factory=blocked)
        # Exercise the send button and thinking state but never run its request.
        pet._submit = lambda key, operation: None if key == 'chat' else blocked()
        for timer in (pet.animation, pet.poll, pet.presence_poll):
            timer.stop()
        pet.brain.scale = 1.15
        pet._apply_size()
        pet.brain.set_ground(0, WIDTH, FLOOR, 0)
        pet.brain.place(680)
        pet.brain.quiet = True
        pet.brain.enter(now[0], 160)
        pet._sync_windows(force=True)
        pet._finished('session', {'ok': True, 'value': {
            'csrf': 'synthetic-demo-csrf-0123456789', 'available': True,
            'reason': '', 'qq_connected': False}})
        pet.show()
        app.processEvents()
        try:
            for index in range(FPS * SECONDS):
                t = index / FPS
                now[0] = 100. + t
                if t < 1.2:
                    stage, detail = '一位桌边的新朋友', '不占一个大窗口，陪你待在屏幕边上。'
                    position = (370, 365)
                elif t < 5.4:
                    stage, detail = '靠近她，圆环就出现', '玩一下 · 聊两句 · 语音\n动态与更多设置，也都在这里。'
                    position = (smooth(370, 655, (t - 1.2) / .7), smooth(365, 305, (t - 1.2) / .7))
                elif t < 8.6:
                    stage, detail = '来，击个掌', '她抬手，你接住。连着点几下，看看能接几次。'
                    position = (681, 307)
                elif t < 12.2:
                    stage, detail = '拎起来，再轻轻放下', '拖动时会晃腿，松开就落回桌边。'
                    position = (smooth(680, 725, (t - 8.6) / 1.3), smooth(340, 115, (t - 8.6) / 1.3))
                elif t < 17.4:
                    stage, detail = '想说话，就聊两句', '点圆环里的聊天，输入一句，她会在气泡里接话。'
                    position = (535, 325)
                else:
                    stage, detail = '入口常在，也懂得留空间', '鼠标移开，圆环自动收起。她还在桌边。'
                    position = (smooth(655, 370, (t - 17.8) / .9), smooth(305, 365, (t - 17.8) / .9))
                if 5.1 <= t < 5.7:
                    point = pet.toolbar.play.mapToGlobal(pet.toolbar.play.rect().center())
                    position = (point.x(), point.y())
                elif 12.2 <= t < 12.6:
                    point = pet.toolbar.chat.mapToGlobal(pet.toolbar.chat.rect().center())
                    position = (point.x(), point.y())
                elif 13.1 <= t < 13.6 and pet.chat_bar is not None:
                    point = pet.chat_bar.send_button.mapToGlobal(pet.chat_bar.send_button.rect().center())
                    position = (point.x(), point.y())
                cursor[0] = QPoint(round(position[0]), round(position[1]))
                def once(name, at, operation):
                    if t >= at and name not in fired:
                        fired.add(name)
                        operation()
                once('play', 5.4, pet.toolbar.play.click)
                for number, at in enumerate((5.9, 6.6, 7.3)):
                    once(f'highfive-{number}', at, lambda: (
                        mouse(pet, QEvent.Type.MouseButtonPress, (pet.brain.x, pet.brain.y - 80)),
                        mouse(pet, QEvent.Type.MouseButtonRelease, (pet.brain.x, pet.brain.y - 80), False)))
                once('drag-start', 8.6, lambda: (pet.brain.cancel_game(now[0]), setattr(pet.brain, 'bubble', None),
                    mouse(pet, QEvent.Type.MouseButtonPress, (pet.brain.x, pet.brain.y - 80))))
                if 8.65 <= t < 10.7:
                    mouse(pet, QEvent.Type.MouseMove, position)
                once('drag-release', 10.7, lambda: mouse(pet, QEvent.Type.MouseButtonRelease, position, False))
                once('chat', 12.4, pet.toolbar.chat.click)
                once('type', 12.6, lambda: pet.chat_bar.entry.setText('今天吃点什么好？'))
                once('thinking', 13.4, lambda: pet.chat_bar.send_button.click())
                once('answer', 14.1, lambda: pet._finished('chat', {
                    'ok': True, 'value': '热乎乎的番茄鸡蛋面，先给你留一碗。'}))
                once('chat-close', 17.4, lambda: (pet.chat_bar.hide(), pet.brain.clear_state(now[0], source='local')))
                once('bubble-close', 17.4, lambda: setattr(pet.brain, 'bubble', None))
                pet.step()
                app.processEvents()
                if index == 2 * FPS:
                    assert pet.toolbar.isVisible(), 'The ring must appear within the first two seconds'
                if index == round(10.2 * FPS):
                    assert pet.brain.mode == 'drag' and pet.brain.y < FLOOR - 60
                image = composite(pet, stage, detail, position)
                if not preview_only:
                    frames.append(image)
                if preview_dir is not None:
                    for moment, name in previews.items():
                        if index == round(moment * FPS):
                            preview_dir.mkdir(parents=True, exist_ok=True)
                            image.save(preview_dir / f'{name}.png')
        finally:
            pet.close()
            app.processEvents()
    if preview_only:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pet-demo-', dir=output.parent) as directory:
        encoded = Path(directory) / 'demo.webp'
        frames[0].save(encoded, format='WEBP', save_all=True, append_images=frames[1:],
                       duration=1000 // FPS, loop=0, quality=82, method=6, minimize_size=True)
        assert encoded.stat().st_size < 2_000_000, 'Keep the README animation below 2 MB'
        encoded.replace(output)
    print(f'Rendered {len(frames)} frames, {SECONDS}s, {output.stat().st_size:,} bytes: {output}')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/readme/pet-demo.webp')
    parser.add_argument('--preview-dir', type=Path, help='optional representative PNG frames for review')
    parser.add_argument('--preview-only', action='store_true', help='review frames without encoding the animation')
    args = parser.parse_args()
    if args.preview_only and args.preview_dir is None:
        parser.error('--preview-only needs --preview-dir')
    render(args.output, preview_dir=args.preview_dir, preview_only=args.preview_only)

if __name__ == '__main__':
    main()
