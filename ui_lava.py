"""Helper's original luminous orb style, with soft violet fluid motion."""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageFilter
from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget


@lru_cache(maxsize=96)
def _fluid_light(frame: int) -> QImage:
    """A small premultiplied layer: smooth flowing light, without hard masks."""
    seconds = frame / 60
    size = 128
    layer = QImage(size, size, QImage.Format.Format_RGBA8888_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    painter = QPainter(layer)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(size / 2, size / 2)
    painter.scale(size / 2, size / 2)
    painter.setPen(Qt.PenStyle.NoPen)

    # Wide, translucent folds drift through each other. Their blurred edges
    # blend into the sphere's shading, like the original Helper wisps.
    for i in range(3):
        phase = seconds * .48 + i * 2.1
        y = math.sin(phase) * .48
        bend = math.sin(phase * .73 + .8) * .48
        ribbon = QPainterPath()
        ribbon.moveTo(-1.3, y - .28)
        ribbon.cubicTo(-.6, y - .7 + bend, .15, y + .65, 1.3, y - .16)
        ribbon.lineTo(1.3, y + .18)
        ribbon.cubicTo(.3, y + .95, -.4, y - .25 + bend, -1.3, y + .18)
        ribbon.closeSubpath()
        shade = QLinearGradient(-.7, y - .4, .5, y + .5)
        if i == 1:
            shade.setColorAt(0, QColor(255, 255, 255, 8))
            shade.setColorAt(.5, QColor(255, 255, 255, 106))
            shade.setColorAt(1, QColor(255, 255, 255, 12))
        else:
            shade.setColorAt(0, QColor(156, 95, 224, 8))
            shade.setColorAt(.5, QColor(156, 95, 224, 58))
            shade.setColorAt(1, QColor(156, 95, 224, 10))
        painter.fillPath(ribbon, shade)

    for i in range(7):
        phase = seconds * (.36 + i * .018) + i * 1.83
        x = .52 * math.sin(phase) + .12 * math.sin(phase * 1.7 + i)
        y = .58 * math.cos(phase * .81 + i * .45)
        radius = .44 + .12 * math.sin(phase * .67 + .4)
        light = QRadialGradient(x, y, radius)
        rgb = (255, 255, 255) if i % 3 else (171, 113, 233)
        light.setColorAt(0, QColor(*rgb, 100 if i % 3 else 68))
        light.setColorAt(.45, QColor(*rgb, 34))
        light.setColorAt(1, QColor(*rgb, 0))
        painter.setBrush(light)
        painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
    painter.end()

    # Blur premultiplied channels together to avoid fringes at transparent
    # edges. Copy the result so Qt owns it after the Python buffer is freed.
    pointer = layer.constBits()
    pointer.setsize(layer.sizeInBytes())
    pixels = Image.frombytes('RGBA', (size, size), bytes(pointer))
    softened = pixels.filter(ImageFilter.GaussianBlur(4.2)).tobytes()
    return QImage(softened, size, size, size * 4, QImage.Format.Format_RGBA8888_Premultiplied).copy()


def _sphere_outline(seconds: float, energy: float) -> QPainterPath:
    """A nearly circular, gently breathing liquid surface."""
    points = []
    count = 32
    for i in range(count):
        angle = i * math.tau / count
        amplitude = .004 + energy * .004
        radius = 1 + amplitude * math.sin(3 * angle + seconds * .75) + .003 * math.sin(5 * angle - seconds * .5)
        points.append(QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
    path = QPainterPath(points[0])
    for i in range(count):
        a, b, c, d = (points[(i + offset) % count] for offset in (-1, 0, 1, 2))
        path.cubicTo(b + (c - a) / 6, c - (d - b) / 6, c)
    path.closeSubpath()
    return path


def paint_lava_orb(painter: QPainter, rect: QRectF, seconds: float, *, energy: float = 0.0) -> None:
    """Original white-to-color glass shading, recolored violet and animated."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    center = rect.center()
    radius = min(rect.width(), rect.height()) / 2
    painter.translate(center)
    painter.scale(radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)

    halo = QRadialGradient(0, .06, 1.23)
    halo.setColorAt(.75, QColor(183, 132, 241, 26 + int(energy * 22)))
    halo.setColorAt(1, QColor(183, 132, 241, 0))
    painter.setBrush(halo)
    painter.drawEllipse(QRectF(-1.23, -1.17, 2.46, 2.46))

    glass = _sphere_outline(seconds, energy)
    painter.save()
    painter.setClipPath(glass)
    body = QLinearGradient(-.12, -1, .16, 1)
    body.setColorAt(0, QColor('#ffffff'))
    body.setColorAt(.22, QColor('#f7efff'))
    body.setColorAt(.52, QColor('#dec4fa'))
    body.setColorAt(.80, QColor('#c59aee'))
    body.setColorAt(1, QColor('#af7de4'))
    painter.fillPath(glass, body)
    painter.drawImage(QRectF(-1, -1, 2, 2), _fluid_light(round(seconds * 60)))

    # Rounded volume at the lower edge, followed by a broad moving softbox
    # reflection. White remains part of the liquid, rather than a flat field.
    volume = QRadialGradient(-.23, -.38, 1.65)
    volume.setColorAt(0, QColor(129, 67, 193, 0))
    volume.setColorAt(.65, QColor(129, 67, 193, 0))
    volume.setColorAt(1, QColor(129, 67, 193, 48))
    painter.fillPath(glass, volume)
    spec_x = -.28 + math.sin(seconds * .23) * .15
    spec_y = -.52 + math.cos(seconds * .19) * .06
    reflection = QRadialGradient(spec_x, spec_y, .88)
    reflection.setColorAt(0, QColor(255, 255, 255, 190))
    reflection.setColorAt(.3, QColor(255, 255, 255, 95))
    reflection.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillPath(glass, reflection)
    sheen = QLinearGradient(0, -1, 0, -.35)
    sheen.setColorAt(0, QColor(255, 255, 255, 150))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillPath(glass, sheen)
    painter.restore()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rim = QLinearGradient(0, -1, 0, 1)
    rim.setColorAt(0, QColor(255, 255, 255, 245))
    rim.setColorAt(.6, QColor(248, 237, 255, 205))
    rim.setColorAt(1, QColor(238, 220, 255, 180))
    painter.setPen(QPen(rim, .038))
    painter.drawPath(glass)
    painter.restore()


class LavaOrb(QWidget):
    """A decorative orb. Hidden surfaces do not consume animation frames."""
    def __init__(self, size: int = 80, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet('background: transparent; border: none;')
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        padding = self.width() * .12
        paint_lava_orb(painter, QRectF(self.rect()).adjusted(padding, padding, -padding, -padding),
                       self._clock.elapsed() / 1000)
