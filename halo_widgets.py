"""Small shared widgets: colour swatches, the palette picker, tool icons."""
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QColorDialog, QFrame, QGridLayout, QLabel, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)


def paint_swatch(p, r, hexcol, selected=False):
    if hexcol is None:
        p.fillRect(r, Qt.white)
        p.setPen(QPen(QColor("#d33"), 2))
        p.drawLine(r.bottomLeft(), r.topRight())
    else:
        p.fillRect(r, QColor(hexcol))
    p.setPen(QPen(QColor("#eceff4") if selected else QColor(0, 0, 0, 120), 2 if selected else 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))


class SwatchGrid(QWidget):
    """A wrapping grid of colour squares."""
    clicked = Signal(int, object)      # index, Qt.KeyboardModifiers
    rightClicked = Signal(int, object)  # index, global pos

    def __init__(self, cell=22, parent=None):
        super().__init__(parent)
        self.colors = []
        self.cell = cell
        self.marked = set()
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_colors(self, colors, marked=()):
        self.colors = list(colors)
        self.marked = set(marked)
        self.updateGeometry()
        self.update()

    def _cols(self):
        return max(1, (self.width() - 2) // (self.cell + 3))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        cols = max(1, (w - 2) // (self.cell + 3))
        rows = max(1, math.ceil(len(self.colors) / cols))
        return rows * (self.cell + 3) + 3

    def sizeHint(self):
        return QSize(200, self.heightForWidth(200))

    def minimumSizeHint(self):
        return QSize(60, self.cell + 6)

    def resizeEvent(self, ev):
        self.setMinimumHeight(self.heightForWidth(self.width()))

    def _rect(self, i):
        cols = self._cols()
        r, c = divmod(i, cols)
        return QRectF(2 + c * (self.cell + 3), 2 + r * (self.cell + 3), self.cell, self.cell)

    def index_at(self, pos):
        for i in range(len(self.colors)):
            if self._rect(i).contains(pos):
                return i
        return -1

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        if not self.colors:
            p.setPen(QColor("#7b8394"))
            p.drawText(self.rect().adjusted(4, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, "No colours yet")
        for i, c in enumerate(self.colors):
            paint_swatch(p, self._rect(i), c, i in self.marked)

    def mousePressEvent(self, ev):
        i = self.index_at(ev.position())
        if i < 0:
            return
        if ev.button() == Qt.RightButton:
            self.rightClicked.emit(i, ev.globalPosition().toPoint())
        else:
            self.clicked.emit(i, ev.modifiers())

    def mouseMoveEvent(self, ev):
        i = self.index_at(ev.position())
        self.setToolTip(self.colors[i] if i >= 0 else "")


class PalettePopup(QFrame):
    """Pick a colour: from the locked palette only, or the active palette
    plus any colour at all when the piece isn't locked."""
    picked = Signal(object)  # hex or None

    def __init__(self, colors, locked, allow_none=True, parent=None, current=None):
        super().__init__(parent, Qt.Popup)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Locked palette" if locked else "Palette")
        title.setStyleSheet("color:#9aa3b5")
        lay.addWidget(title)
        grid = SwatchGrid(22)
        grid.set_colors(colors, [i for i, c in enumerate(colors) if c == current])
        grid.setFixedWidth(8 * 25 + 4)
        grid.clicked.connect(lambda i, m: self._done(colors[i]))
        lay.addWidget(grid)
        row = QWidget()
        rl = QGridLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        if allow_none:
            b = QPushButton("None")
            b.clicked.connect(lambda: self._done(None))
            rl.addWidget(b, 0, 0)
        more = QPushButton("Any colour…")
        more.setEnabled(not locked)
        if locked:
            more.setToolTip("The piece is locked to a palette (Halo panel)")
        more.clicked.connect(self._more)
        rl.addWidget(more, 0, 1)
        lay.addWidget(row)
        self.current = current

    def _done(self, c):
        self.picked.emit(c)
        self.close()

    def _more(self):
        self.hide()
        c = QColorDialog.getColor(QColor(self.current or "#888888"), self.parentWidget(), "Colour")
        if c.isValid():
            self.picked.emit(c.name())
        self.close()


class ColorButton(QToolButton):
    """A swatch that opens the palette picker."""
    colorChosen = Signal(object)

    def __init__(self, provider, allow_none=True, parent=None):
        """provider() -> (colors, locked)"""
        super().__init__(parent)
        self.provider = provider
        self.allow_none = allow_none
        self.color = None
        self.setFixedSize(30, 22)
        self.clicked.connect(self.open_picker)

    def set_color(self, c):
        self.color = c
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        paint_swatch(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.color)

    def open_picker(self):
        colors, locked = self.provider()
        pop = PalettePopup(colors, locked, self.allow_none, self, self.color)
        pop.picked.connect(self.colorChosen.emit)
        pop.adjustSize()
        pop.move(self.mapToGlobal(self.rect().bottomLeft()))
        pop.show()


class FillStroke(QWidget):
    """Illustrator's overlapping fill/stroke squares."""
    fillClicked = Signal()
    strokeClicked = Signal()
    swapClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fill = None
        self.stroke = None
        self.setFixedSize(52, 52)
        self.setToolTip("Fill (front) and stroke (back). Click to choose; X swaps them.")

    def set_colors(self, fill, stroke):
        self.fill, self.stroke = fill, stroke
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        sr = QRectF(18, 18, 30, 30)
        if self.stroke is None:
            paint_swatch(p, sr, None)
        else:
            p.fillRect(sr, QColor(self.stroke))
            p.fillRect(sr.adjusted(7, 7, -7, -7), QColor("#3b4252"))
            p.setPen(QColor(0, 0, 0, 140))
            p.drawRect(sr)
        paint_swatch(p, QRectF(2, 2, 30, 30), self.fill)
        p.setPen(QColor("#d8dee9"))
        p.drawText(QRectF(36, 0, 16, 14), Qt.AlignCenter, "⇄")

    def mousePressEvent(self, ev):
        x, y = ev.position().x(), ev.position().y()
        if x > 34 and y < 16:
            self.swapClicked.emit()
        elif x < 32 and y < 32:
            self.fillClicked.emit()
        else:
            self.strokeClicked.emit()


# ---------------------------------------------------------------- icons

def asset_path(name):
    import os
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def _asset_icon(fname, color):
    """A line-art PNG (white on transparent) tinted to `color`."""
    import os
    path = asset_path(fname)
    if not os.path.exists(path):
        return None
    src = QPixmap(path)
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawPixmap(QRectF(3, 3, 42, 42), src, QRectF(src.rect()))
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), QColor(color))
    p.end()
    return QIcon(pm)


def tool_icon(name, color="#d8dee9"):
    if name in ("hand", "eyedropper"):
        icon = _asset_icon(name + ".png", color)
        if icon is not None:
            return icon
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(color)
    pen = QPen(col, 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if name in ("select", "direct"):
        poly = QPolygonF([QPointF(14, 8), QPointF(14, 38), QPointF(22, 30), QPointF(28, 42),
                          QPointF(33, 40), QPointF(27, 28), QPointF(37, 28)])
        p.setBrush(col if name == "select" else Qt.NoBrush)
        p.drawPolygon(poly)
    elif name == "pen":
        path = QPainterPath(QPointF(24, 6))
        path.lineTo(36, 26)
        path.lineTo(30, 36)
        path.lineTo(18, 36)
        path.lineTo(12, 26)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(24, 6), QPointF(24, 24))
        p.drawEllipse(QPointF(24, 26), 2.5, 2.5)
        p.drawRect(QRectF(17, 38, 14, 5))
    elif name == "rect":
        p.drawRect(QRectF(9, 13, 30, 22))
    elif name == "ellipse":
        p.drawEllipse(QRectF(8, 12, 32, 24))
    elif name == "line":
        p.drawLine(QPointF(10, 38), QPointF(38, 10))
    elif name == "text":
        f = p.font()
        f.setPixelSize(34)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 48, 48), Qt.AlignCenter, "T")
    elif name == "brush":
        p.drawLine(QPointF(36, 8), QPointF(20, 28))
        path = QPainterPath(QPointF(20, 26))
        path.cubicTo(12, 26, 14, 36, 8, 40)
        path.cubicTo(18, 42, 26, 36, 23, 30)
        p.setBrush(col)
        p.drawPath(path)
    elif name == "eraser":
        poly = QPolygonF([QPointF(10, 30), QPointF(26, 12), QPointF(38, 22), QPointF(22, 40), QPointF(16, 40)])
        p.drawPolygon(poly)
        p.drawLine(QPointF(17, 22), QPointF(30, 33))
        p.drawLine(QPointF(22, 40), QPointF(40, 40))
    elif name == "eyedropper":
        p.drawLine(QPointF(10, 38), QPointF(28, 20))
        p.setBrush(col)
        p.drawEllipse(QPointF(33, 15), 7, 7)
        p.drawLine(QPointF(24, 16), QPointF(32, 24))
    elif name == "hand":
        path = QPainterPath()
        path.addRoundedRect(QRectF(12, 20, 24, 20), 8, 8)
        p.drawPath(path)
        for x in (16, 22, 28, 34):
            p.drawLine(QPointF(x, 22), QPointF(x, 10 if x != 34 else 14))
    elif name == "zoom":
        p.drawEllipse(QPointF(20, 20), 11, 11)
        p.drawLine(QPointF(28, 28), QPointF(40, 40))
    elif name == "framemove":
        for a in range(4):
            ang = a * math.pi / 2
            c, s = math.cos(ang), math.sin(ang)
            tip = QPointF(24 + c * 17, 24 + s * 17)
            p.drawLine(QPointF(24, 24), tip)
            p.drawLine(tip, QPointF(24 + c * 11 - s * 5, 24 + s * 11 + c * 5))
            p.drawLine(tip, QPointF(24 + c * 11 + s * 5, 24 + s * 11 - c * 5))
        p.drawRect(QRectF(18, 18, 12, 12))
    elif name == "artboard":
        p.drawRect(QRectF(13, 13, 22, 22))
        p.drawLine(QPointF(13, 6), QPointF(13, 42))
        p.drawLine(QPointF(35, 6), QPointF(35, 42))
        p.drawLine(QPointF(6, 13), QPointF(42, 13))
        p.drawLine(QPointF(6, 35), QPointF(42, 35))
    elif name == "halo":
        for k in range(12):
            ang = k * math.pi / 6
            p.setPen(QPen(QColor.fromHsvF(k / 12, 0.65, 0.95), 5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(8, 8, 32, 32), int(math.degrees(ang) * 16), int(26 * 16))
    p.end()
    return QIcon(pm)


def app_icon(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 256, size / 256)
    p.setBrush(QColor("#2e3440"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)
    for k in range(24):
        p.setPen(QPen(QColor.fromHsvF(k / 24, 0.6, 1.0), 26, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(QRectF(48, 48, 160, 160), int(k * 15 * 16), int(15.5 * 16))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#eceff4"))
    p.drawEllipse(QPointF(128, 128), 30, 30)
    p.end()
    return pm
