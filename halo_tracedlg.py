"""The Image Trace dialog: options on the right, a live preview on the left,
then the full-resolution trace goes into the document as a group."""
import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget)

from halo_model import GroupItem, PathItem, RasterItem, map_subpaths, nodes_to_qpath
from halo_render import qimage_to_rgba
from halo_trace import TraceOptions, otsu, to_gray, trace
from halo_widgets import ColorButton

PREVIEW = 900  # longest side of the image the preview traces


class TraceDialog(QDialog):
    def __init__(self, editor, raster, color_provider, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.raster = raster
        self.setWindowTitle("Image Trace")
        img = raster.image
        self.full = qimage_to_rgba(img)
        self.f = min(1.0, PREVIEW / max(img.width(), img.height()))
        small = img if self.f >= 1 else img.scaled(int(img.width() * self.f), int(img.height() * self.f),
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.f = small.width() / img.width()
        self.small = qimage_to_rgba(small)
        self.small_img = small.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.auto_thr = otsu(to_gray(self.small))
        self.shapes = []

        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.view = QLabel()
        self.view.setMinimumSize(520, 520)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background:#3b4252")
        left.addWidget(self.view, 1)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#9aa3b5")
        left.addWidget(self.info)
        lay.addLayout(left, 1)

        right = QWidget()
        right.setMinimumWidth(300)
        f = QFormLayout(right)
        self.auto = QCheckBox("Automatic")
        self.auto.setChecked(True)
        self.thr = self._slider(1, 254, self.auto_thr)
        self.thr.setEnabled(False)
        thr_row = QHBoxLayout()
        thr_row.addWidget(self.auto)
        thr_row.addWidget(self.thr, 1)
        f.addRow("Threshold", thr_row)
        f.addRow(self._hint("Pixels darker than this count as ink."))
        self.invert = QCheckBox("Light marks on a dark background")
        f.addRow("", self.invert)
        self.speck = QSpinBox()
        self.speck.setRange(0, 100000)
        self.speck.setValue(12)
        self.speck.setSuffix(" px")
        f.addRow("Ignore specks under", self.speck)
        f.addRow(self._hint("Dust and paper grain smaller than this (in the image's own pixels) are left out."))
        self.smooth = self._slider(1, 16, 6)
        f.addRow("Smoothness", self.smooth)
        self.corner = self._slider(20, 160, 70)
        f.addRow("Corners", self.corner)
        f.addRow(self._hint("Smoothness evens out wobbles. Corners: lower keeps more points sharp, "
                            "higher rounds them off."))
        self.fill = ColorButton(color_provider, allow_none=False)
        locked = editor.lock_colors()
        start = "#000000"
        if locked:
            start = min(locked, key=lambda c: QColor(c).lightness())
        self.fill.set_color(start)
        self.fill.colorChosen.connect(lambda c: (self.fill.set_color(c), self._schedule()))
        f.addRow("Fill", self.fill)
        self.show_orig = QCheckBox("Show the image under the preview")
        self.show_orig.setChecked(True)
        f.addRow("", self.show_orig)
        self.hide_after = QCheckBox("Hide the image afterwards")
        self.hide_after.setChecked(True)
        f.addRow("", self.hide_after)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Trace")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        lay.addWidget(right)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self.preview)
        self.auto.toggled.connect(lambda on: (self.thr.setEnabled(not on), self._schedule()))
        for w in (self.thr, self.smooth, self.corner):
            w.valueChanged.connect(self._schedule)
        self.speck.valueChanged.connect(self._schedule)
        self.invert.toggled.connect(self._schedule)
        self.show_orig.toggled.connect(self._render)
        self.preview()

    def _slider(self, lo, hi, v):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(v)
        return s

    def _hint(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet("color:#7b8394")
        return l

    def options(self, scale=1.0):
        """Options for an image `scale` times the size of the original."""
        o = TraceOptions()
        o.threshold = None if self.auto.isChecked() else self.thr.value()
        o.invert = self.invert.isChecked()
        o.speck = self.speck.value() * scale * scale
        o.smooth = max(0.4, self.smooth.value() / 4.0 * max(scale, 0.25) ** 0.5)
        o.corners = float(self.corner.value())
        o.tolerance = 0.6
        return o

    def _schedule(self):
        self._timer.start()

    def preview(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.shapes, thr = trace(self.small, self.options(self.f))
        finally:
            QApplication.restoreOverrideCursor()
        if self.auto.isChecked():
            self.thr.blockSignals(True)
            self.thr.setValue(int(thr))
            self.thr.blockSignals(False)
        n = sum(len(sp["nodes"]) for s in self.shapes for sp in s)
        self.info.setText("%d shape%s · %d points (preview at %d%%; the trace uses the full image)" % (
            len(self.shapes), "" if len(self.shapes) == 1 else "s", n, round(self.f * 100)))
        self._render()

    def _render(self):
        w, h = self.small_img.width(), self.small_img.height()
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.white)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        if self.show_orig.isChecked():
            p.setOpacity(0.3)
            p.drawImage(0, 0, self.small_img)
            p.setOpacity(1.0)
        col = QColor(self.fill.color or "#000000")
        for s in self.shapes:
            p.fillPath(nodes_to_qpath(s, even_odd=True), col)
        p.end()
        pm = QPixmap.fromImage(img)
        size = self.view.size() - QSize(8, 8)
        self.view.setPixmap(pm.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.shapes is not None:
            self._render()

    def run(self):
        """Trace at full resolution and add the result. Returns the group."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            shapes, _ = trace(self.full, self.options(1.0))
        finally:
            QApplication.restoreOverrideCursor()
        return add_trace(self.ed, self.raster, shapes, self.fill.color or "#000000", self.hide_after.isChecked())


def add_trace(ed, raster, shapes, fill, hide_image=True):
    """Put traced shapes (pixel coordinates of `raster`) into the document,
    grouped, just above the image. One undo step."""
    if not shapes:
        return None
    ed.checkpoint()
    items = []
    for subs in shapes:
        it = PathItem(map_subpaths(subs, raster.xf), fill, None)
        it.even_odd = True
        items.append(it)
    g = GroupItem(items, name="Traced " + (raster.name or "image"))
    _, cont, _ = ed.doc.locate(raster.id)
    cont.insert(cont.index(raster) + 1, g)
    if hide_image:
        raster.visible = False
    ed.notify("doc")
    ed.select([g.id])
    return g
