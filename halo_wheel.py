"""The Halo panel: every colour in the piece on one wheel.

Drag a dot to move that colour (hue around the ring, saturation in and
out). Drag empty space, or the Spin slider, to turn the whole palette around
the wheel at once. With Linked on, dragging any dot spins them all.
Lock to a palette and every result snaps to the palette's nearest colour.
Nothing changes in the artwork until Apply; the canvas shows a live preview.
"""
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QBrush
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QSizePolicy, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from halo_color import (Recolor, extract_palette, hex_wheel, nearest_hex, recolor_pixels,
                        rgb_to_hex, wheel_hex, wheel_to_rgb, hue_dist)
from halo_model import RasterItem
from halo_render import qimage_to_rgba, rgba_to_qimage
from halo_widgets import PalettePopup, SwatchGrid, paint_swatch

PREVIEW_MAX = 900


class Wheel(QWidget):
    changed = Signal()
    selectionChanged = Signal()

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.entries = []        # dict(src, base=(H,R,L), target=[H,R,L], count, raster)
        self.selected = set()
        self.markers = []        # palette colours shown as small squares
        self.mode = "perceptual"
        self._bg = None
        self._bg_key = None
        self.drag = None
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(300, 300)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return w

    # geometry
    def _geom(self):
        s = min(self.width(), self.height())
        R = s / 2 - 14
        c = QPointF(self.width() / 2, self.height() / 2)
        return c, R

    def pos_of(self, H, Rr):
        c, R = self._geom()
        a = math.radians(H)
        r = min(Rr, 1.08) * R
        # hue 0 at the right, counter-clockwise like a colour wheel
        return QPointF(c.x() + math.cos(a) * r, c.y() - math.sin(a) * r)

    def wheel_at(self, p):
        c, R = self._geom()
        dx, dy = p.x() - c.x(), c.y() - p.y()
        return math.degrees(math.atan2(dy, dx)) % 360, math.hypot(dx, dy) / R

    def shown(self, e):
        rc = self.panel.recolor
        H = (e["target"][0] + rc.spin) % 360
        Rr = e["target"][1] * rc.sat
        return H, Rr

    def final_hex(self, e):
        rc = self.panel.recolor
        t, b = e["target"], e["base"]
        moved = abs(hue_dist(b[0], t[0])) > 0.01 or abs(b[1] - t[1]) > 1e-3 or abs(b[2] - t[2]) > 1e-3
        if not moved and abs(rc.spin) < 1e-6 and abs(rc.sat - 1) < 1e-6 and abs(rc.light) < 1e-6:
            out = e["src"]
        else:
            H, Rr = self.shown(e)
            out = wheel_hex((H, Rr, min(1, max(0, t[2] + rc.light))), self.mode)
        return nearest_hex(out, rc.palette) if rc.palette else out

    # painting
    def _background(self):
        c, R = self._geom()
        key = (int(R), self.mode, self.width(), self.height())
        if self._bg_key == key:
            return self._bg
        n = int(2 * R) + 2
        ys, xs = np.mgrid[0:n, 0:n].astype(np.float64)
        dx, dy = xs - n / 2, n / 2 - ys
        rr = np.hypot(dx, dy) / R
        H = np.degrees(np.arctan2(dy, dx)) % 360
        L = np.full_like(H, 0.78 if self.mode == "perceptual" else 1.0)
        rgb = wheel_to_rgb(np.stack([H, np.minimum(rr, 1.0), L], -1), self.mode)
        # fade a little so the dots stand out
        rgb = rgb * 0.82 + 0.18 * 0.2
        a = np.clip((1.0 - rr) * R + 0.5, 0, 1)
        rgba = np.zeros((n, n, 4), np.uint8)
        rgba[..., :3] = np.round(rgb * 255)
        rgba[..., 3] = np.round(a * 255)
        img = rgba_to_qimage(rgba)
        self._bg, self._bg_key = img, key
        return img

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c, R = self._geom()
        bg = self._background()
        p.drawImage(QPointF(c.x() - bg.width() / 2, c.y() - bg.height() / 2), bg)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, R, R)
        p.drawEllipse(c, R / 2, R / 2)
        # palette markers
        for m in self.markers:
            H, Rr, L = hex_wheel(m, self.mode)
            q = self.pos_of(H, Rr)
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.setBrush(QColor(m))
            p.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
        if not self.entries:
            p.setPen(QColor("#d8dee9"))
            p.drawText(QRectF(0, c.y() + R * 0.55, self.width(), 24), Qt.AlignCenter, "No colours in scope")
            return
        maxc = max(e["count"] for e in self.entries) or 1
        rc = self.panel.recolor
        for i, e in enumerate(self.entries):
            H, Rr = self.shown(e)
            q = self.pos_of(H, Rr)
            moved = (abs(hue_dist(e["base"][0], H)) > 0.5 or abs(e["base"][1] - Rr) > 0.01)
            if moved:
                b = self.pos_of(e["base"][0], e["base"][1])
                p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DotLine))
                p.drawLine(b, q)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(b, 3, 3)
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawLine(c, q)
        for i, e in enumerate(self.entries):
            H, Rr = self.shown(e)
            q = self.pos_of(H, Rr)
            size = 6 + 7 * math.sqrt(e["count"] / maxc)
            fin = QColor(self.final_hex(e))
            sel = i in self.selected
            if e["raster"]:
                p.setPen(QPen(fin, 4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(q, size, size)
                p.setPen(QPen(QColor("#eceff4") if sel else QColor(0, 0, 0, 170), 2 if sel else 1, Qt.DashLine))
                p.drawEllipse(q, size + 2.5, size + 2.5)
            else:
                p.setPen(QPen(QColor("#eceff4") if sel else QColor(0, 0, 0, 170), 3 if sel else 1.2))
                p.setBrush(fin)
                p.drawEllipse(q, size, size)

    # interaction
    def entry_at(self, pos):
        best, bd = -1, 1e9
        for i, e in enumerate(self.entries):
            q = self.pos_of(*self.shown(e))
            d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
            if d < 16 and d < bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, ev):
        pos = ev.position()
        i = self.entry_at(pos)
        H0, R0 = self.wheel_at(pos)
        rc = self.panel.recolor
        if i >= 0:
            if ev.modifiers() & Qt.ShiftModifier:
                self.selected ^= {i}
            elif i not in self.selected:
                self.selected = {i}
            self.selectionChanged.emit()
            linked = self.panel.linked.isChecked()
            self.drag = {"kind": "spin" if linked else "dots", "H0": H0, "R0": R0, "i": i,
                         "spin0": rc.spin, "sat0": rc.sat,
                         "orig": {j: list(self.entries[j]["target"]) for j in range(len(self.entries))}}
        else:
            self.drag = {"kind": "spin", "H0": H0, "R0": R0, "spin0": rc.spin, "sat0": rc.sat, "i": -1,
                         "orig": {}}
        self.update()

    def mouseMoveEvent(self, ev):
        if not self.drag:
            i = self.entry_at(ev.position())
            if i >= 0:
                e = self.entries[i]
                self.setToolTip("%s → %s  (%d use%s)%s" % (
                    e["src"], self.final_hex(e), e["count"], "" if e["count"] == 1 else "s",
                    "  · from images/paint" if e["raster"] else ""))
            else:
                self.setToolTip("Drag here to spin every colour around the wheel")
            return
        H, Rr = self.wheel_at(ev.position())
        d = self.drag
        dh = float(hue_dist(d["H0"], H))
        hue_only = bool(ev.modifiers() & Qt.ShiftModifier)
        rc = self.panel.recolor
        if d["kind"] == "spin":
            rc.spin = (d["spin0"] + dh + 180) % 360 - 180
            if d["i"] >= 0 and not hue_only and d["R0"] > 0.05:
                rc.sat = max(0.0, min(2.0, d["sat0"] * Rr / d["R0"]))
        else:
            for j in self.selected:
                o = d["orig"][j]
                t = self.entries[j]["target"]
                t[0] = (o[0] + dh) % 360
                if not hue_only:
                    # radius moves by the same amount the cursor did
                    t[1] = max(0.0, min(1.1, o[1] + (Rr - d["R0"]) / max(rc.sat, 0.05)))
        self.panel.on_wheel_changed()
        self.update()

    def mouseReleaseEvent(self, ev):
        if self.drag and self.panel.recolor.palette and self.drag["kind"] == "dots":
            # palette lock: moved dots land on the nearest palette colour
            for j in self.selected:
                e = self.entries[j]
                fin = self.final_hex(e)
                rc = self.panel.recolor
                H, Rr, L = hex_wheel(fin, self.mode)
                e["target"] = [(H - rc.spin) % 360, Rr / max(rc.sat, 0.05), L - rc.light]
            self.panel.on_wheel_changed()
        self.drag = None
        self.update()

    def mouseDoubleClickEvent(self, ev):
        i = self.entry_at(ev.position())
        if i >= 0:
            self.selected = {i}
            self.selectionChanged.emit()
            self.panel.pick_target()

    def wheelEvent(self, ev):
        i = self.entry_at(ev.position())
        targets = self.selected if (i in self.selected or i < 0) else {i}
        if not targets:
            return
        step = 0.02 * (1 if ev.angleDelta().y() > 0 else -1)
        for j in targets:
            t = self.entries[j]["target"]
            t[2] = max(0.0, min(1.0, t[2] + step))
        self.panel.on_wheel_changed()
        self.update()


class HaloPanel(QWidget):
    SCOPES = [("document", "Whole document"), ("artboard", "Current artboard"),
              ("layer", "Current layer"), ("selection", "Selection")]

    def __init__(self, editor, palettes_provider, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.palettes_provider = palettes_provider  # -> list of Palette
        self.recolor = Recolor("perceptual")
        self.items = []
        self.small = {}  # raster id -> (H, W, 4) preview pixels
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._push_preview)
        self._stale = False
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(250)
        self._rebuild_timer.timeout.connect(self._maybe_rebuild)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)
        top = QHBoxLayout()
        self.scope = QComboBox()
        for k, label in self.SCOPES:
            self.scope.addItem(label, k)
        self.scope.setToolTip("Which colours the wheel shows and changes")
        self.model = QComboBox()
        self.model.addItem("Perceptual", "perceptual")
        self.model.addItem("Classic HSB", "classic")
        self.model.setToolTip("Perceptual (OKLCH) keeps a colour's lightness as its hue turns.\n"
                              "Classic HSB matches Illustrator's wheel.")
        top.addWidget(self.scope, 2)
        top.addWidget(self.model, 1)
        lay.addLayout(top)

        self.wheel = Wheel(self)
        lay.addWidget(self.wheel, 1)

        row = QHBoxLayout()
        self.linked = QCheckBox("Linked")
        self.linked.setToolTip("Linked: dragging any colour spins them all together.\n"
                               "Unlinked: drag one colour (Shift-click for several) on its own.")
        self.rasters = QCheckBox("Images && paint")
        self.rasters.setChecked(True)
        self.rasters.setToolTip("Include raster objects. Their colour regions show as ringed dots;\n"
                                "moving one shifts that hue range in the pixels.")
        row.addWidget(self.linked)
        row.addWidget(self.rasters)
        row.addStretch(1)
        lay.addLayout(row)

        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(3)
        self.spin = self._slider(-180, 180, 0)
        self.spin_box = QSpinBox()
        self.spin_box.setRange(-180, 180)
        self.spin_box.setSuffix("°")
        self.sat = self._slider(0, 200, 100)
        self.sat_box = QSpinBox()
        self.sat_box.setRange(0, 200)
        self.sat_box.setSuffix("%")
        self.light = self._slider(-50, 50, 0)
        self.light_box = QSpinBox()
        self.light_box.setRange(-50, 50)
        for r, (lab, s, b) in enumerate((("Spin all", self.spin, self.spin_box),
                                          ("Saturation", self.sat, self.sat_box),
                                          ("Lightness", self.light, self.light_box))):
            g.addWidget(QLabel(lab), r, 0)
            g.addWidget(s, r, 1)
            g.addWidget(b, r, 2)
            s.valueChanged.connect(b.setValue)
            b.valueChanged.connect(s.setValue)
            s.valueChanged.connect(self._sliders_changed)
        self.reach = self._slider(5, 90, 20)
        self.reach.setToolTip("How wide a band of hues an image colour dot moves")
        self.reach.valueChanged.connect(self._sliders_changed)
        g.addWidget(QLabel("Image reach"), 3, 0)
        g.addWidget(self.reach, 3, 1, 1, 2)
        lay.addLayout(g)

        sel = QHBoxLayout()
        self.sel_label = QLabel("Click a dot to pick a colour")
        self.sel_label.setStyleSheet("color:#9aa3b5")
        self.sel_label.setWordWrap(True)
        self.pick_btn = QPushButton("Set…")
        self.pick_btn.setToolTip("Choose exactly what the selected colour becomes")
        self.pick_btn.clicked.connect(self.pick_target)
        sel.addWidget(self.sel_label, 1)
        sel.addWidget(self.pick_btn)
        lay.addLayout(sel)

        btns = QHBoxLayout()
        self.reset_btn = QPushButton("Reset")
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setDefault(True)
        self.apply_btn.setStyleSheet("QPushButton{font-weight:600}")
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)

        # palette lock
        box = QWidget()
        bl = QGridLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.addWidget(QLabel("Palette"), 0, 0)
        self.pal_combo = QComboBox()
        self.pal_combo.setToolTip("Shown on the wheel as small squares")
        bl.addWidget(self.pal_combo, 0, 1, 1, 2)
        self.lock = QCheckBox("Lock piece to this palette")
        self.lock.setToolTip("Pickers offer only this palette's colours, and Halo results snap to it")
        bl.addWidget(self.lock, 1, 0, 1, 3)
        self.off_label = QLabel("")
        self.off_label.setStyleSheet("color:#ebcb8b")
        bl.addWidget(self.off_label, 2, 0, 1, 2)
        self.conform_btn = QPushButton("Conform…")
        self.conform_btn.setToolTip("Snap every colour in scope to the locked palette, images included")
        bl.addWidget(self.conform_btn, 2, 2)
        lay.addWidget(box)

        self.scope.currentIndexChanged.connect(lambda: self.rebuild(force=True))
        self.model.currentIndexChanged.connect(self._mode_changed)
        self.rasters.toggled.connect(lambda: self.rebuild(force=True))
        self.reset_btn.clicked.connect(lambda: self.rebuild(force=True))
        self.apply_btn.clicked.connect(self.apply)
        self.wheel.selectionChanged.connect(self._sel_changed)
        self.pal_combo.currentIndexChanged.connect(self._palette_changed)
        self.lock.toggled.connect(self._lock_toggled)
        self.conform_btn.clicked.connect(self.conform_dialog)
        editor.changed.connect(self._editor_changed)
        self.refresh_palettes()
        self.rebuild(force=True)

    def _slider(self, lo, hi, v):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(v)
        return s

    # ------------------------------------------------------------ state
    def pending(self):
        rc = self.recolor
        moved = any(abs(hue_dist(e["base"][0], e["target"][0])) > 0.01 or
                    abs(e["base"][1] - e["target"][1]) > 1e-3 or abs(e["base"][2] - e["target"][2]) > 1e-3
                    for e in self.wheel.entries)
        return moved or abs(rc.spin) > 1e-6 or abs(rc.sat - 1) > 1e-6 or abs(rc.light) > 1e-6

    def _editor_changed(self, kind):
        if self._busy:
            return
        if kind in ("doc", "file", "selection"):
            if kind == "file":
                self.refresh_palettes()
            if kind == "selection" and self.scope.currentData() not in ("selection", "layer", "artboard"):
                self._update_lock_ui()
                return
            self._stale = True
            self._rebuild_timer.start()
        elif kind == "tool":
            self._update_lock_ui()

    def _maybe_rebuild(self):
        if not self.isVisible():
            return  # done when the panel is shown again
        if self._stale and not self.pending() and not self.wheel.drag:
            self._stale = False
            self.rebuild()
        self._update_lock_ui()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._stale:
            self._rebuild_timer.start()

    def _mode_changed(self):
        self.recolor.mode = self.model.currentData()
        self.wheel.mode = self.recolor.mode
        self.wheel._bg_key = None
        self.rebuild(force=True)

    def rebuild(self, force=False):
        """Collect the colours in scope and reset every adjustment."""
        mode = self.model.currentData()
        self.recolor = Recolor(mode)
        self.recolor.tolerance = float(self.reach.value())
        self.recolor.palette = self.ed.lock_colors() if self.lock.isChecked() else None
        for s in (self.spin, self.sat, self.light):
            s.blockSignals(True)
        self.spin.setValue(0)
        self.sat.setValue(100)
        self.light.setValue(0)
        self.spin_box.setValue(0)
        self.sat_box.setValue(100)
        self.light_box.setValue(0)
        for s in (self.spin, self.sat, self.light):
            s.blockSignals(False)
        items = self.ed.scope_items(self.scope.currentData())
        self.items = items
        counts = self.ed.doc.used_colors(items)
        entries = []
        for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:200]:
            b = hex_wheel(c, mode)
            entries.append({"src": c, "base": b, "target": list(b), "count": n, "raster": False})
        self.small = {}
        if self.rasters.isChecked():
            for it in items:
                if isinstance(it, RasterItem):
                    img = it.image
                    if max(img.width(), img.height()) > PREVIEW_MAX:
                        img = img.scaled(PREVIEW_MAX, PREVIEW_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    arr = qimage_to_rgba(img)
                    self.small[it.id] = arr
                    for c in extract_palette(arr, 6):
                        b = hex_wheel(c, mode)
                        if b[1] < 0.04:
                            continue  # greys have no hue to move
                        entries.append({"src": c, "base": b, "target": list(b), "count": 1, "raster": True})
        self.wheel.entries = entries
        self.wheel.selected = set()
        self.wheel.mode = mode
        self.wheel.markers = self._marker_colors()
        self.wheel.update()
        self._sel_changed()
        self.ed.clear_preview()

    def _marker_colors(self):
        p = self.current_palette()
        return p.colors if p else []

    def build_recolor(self):
        rc = self.recolor
        rc.mode = self.model.currentData()
        rc.spin = float(self.spin.value()) if not self.wheel.drag else rc.spin
        rc.tolerance = float(self.reach.value())
        rc.falloff = rc.tolerance * 1.2
        rc.moves = {}
        for e in self.wheel.entries:
            if e["raster"]:
                continue
            t, b = e["target"], e["base"]
            if abs(hue_dist(b[0], t[0])) > 0.01 or abs(b[1] - t[1]) > 1e-3 or abs(b[2] - t[2]) > 1e-3:
                rc.moves[e["src"]] = tuple(t)
        rc.raster_moves = {e["src"]: tuple(e["target"]) for e in self.wheel.entries if e["raster"]
                           and (abs(hue_dist(e["base"][0], e["target"][0])) > 0.01 or
                                abs(e["base"][1] - e["target"][1]) > 1e-3 or
                                abs(e["base"][2] - e["target"][2]) > 1e-3)}
        rc.palette = self.ed.lock_colors() if self.lock.isChecked() else None
        return rc

    def raster_recolor(self):
        """The same plan, with image colour dots as the moves."""
        rc = self.build_recolor()
        r = Recolor(rc.mode)
        r.spin, r.sat, r.light = rc.spin, rc.sat, rc.light
        r.tolerance, r.falloff = rc.tolerance, rc.falloff
        r.moves = dict(rc.raster_moves)
        r.palette = rc.palette
        return r

    def on_wheel_changed(self):
        rc = self.recolor
        # mirror wheel drags into the sliders
        for s, b, v in ((self.spin, self.spin_box, int(round(rc.spin))),
                        (self.sat, self.sat_box, int(round(rc.sat * 100)))):
            s.blockSignals(True)
            b.blockSignals(True)
            s.setValue(v)
            b.setValue(v)
            s.blockSignals(False)
            b.blockSignals(False)
        self._sel_changed()
        self._timer.start()

    def _sliders_changed(self):
        rc = self.recolor
        rc.spin = float(self.spin.value())
        rc.sat = self.sat.value() / 100.0
        rc.light = self.light.value() / 100.0
        self.wheel.update()
        self._timer.start()

    def _push_preview(self):
        rc = self.build_recolor()
        if not self.pending() and not rc.palette:
            self.ed.clear_preview()
            return
        ids = {it.id for it in self.items}
        previews = {}
        if self.rasters.isChecked() and self.small:
            rr = self.raster_recolor()
            for rid, arr in self.small.items():
                previews[rid] = rgba_to_qimage(recolor_pixels(arr, rr))
        self._busy = True
        self.ed.set_preview(rc, ids, previews)
        self._busy = False

    def _sel_changed(self):
        sel = sorted(self.wheel.selected)
        if not sel:
            self.sel_label.setText("Drag a dot to move one colour; drag empty space to spin them all")
            self.pick_btn.setEnabled(False)
            return
        self.pick_btn.setEnabled(True)
        e = self.wheel.entries[sel[0]]
        fin = self.wheel.final_hex(e)
        more = " (+%d more)" % (len(sel) - 1) if len(sel) > 1 else ""
        self.sel_label.setText("%s → %s%s%s" % (e["src"], fin, more, "  · image colour" if e["raster"] else ""))

    def pick_target(self):
        sel = sorted(self.wheel.selected)
        if not sel:
            return
        e = self.wheel.entries[sel[0]]
        locked = bool(self.lock.isChecked() and self.ed.lock_colors())
        p = self.current_palette()
        colors = self.ed.lock_colors() if locked else (p.colors if p else [])
        pop = PalettePopup(colors, locked, False, self, self.wheel.final_hex(e))

        def chosen(c):
            if not c:
                return
            rc = self.recolor
            H, Rr, L = hex_wheel(c, self.recolor.mode)
            for j in sel:
                self.wheel.entries[j]["target"] = [(H - rc.spin) % 360, Rr / max(rc.sat, 0.05), L - rc.light]
            self.on_wheel_changed()
            self.wheel.update()
        pop.picked.connect(chosen)
        pop.adjustSize()
        pop.move(self.pick_btn.mapToGlobal(self.pick_btn.rect().bottomLeft()))
        pop.show()

    def apply(self):
        rc = self.build_recolor()
        if not self.pending() and not rc.palette:
            return
        self._busy = True
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.WaitCursor)
            vec = [it for it in self.items if not isinstance(it, RasterItem)]
            ras = [it for it in self.items if isinstance(it, RasterItem)] if self.rasters.isChecked() else []
            self.ed.checkpoint()
            undo_len = len(self.ed.undo_stack)
            self.ed.apply_recolor(rc, vec, rasters=False)
            if ras:
                rr = self.raster_recolor()
                for it in ras:
                    it.image = rgba_to_qimage(recolor_pixels(qimage_to_rgba(it.image), rr))
                    it.touch()
            # one undo step for the whole Apply
            while len(self.ed.undo_stack) > undo_len:
                self.ed.undo_stack.pop()
            self.ed.notify("doc")
        finally:
            from PySide6.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            self._busy = False
        self.rebuild(force=True)

    # ------------------------------------------------------------ palettes
    def refresh_palettes(self):
        cur = self.pal_combo.currentText()
        self.pal_combo.blockSignals(True)
        self.pal_combo.clear()
        self.pal_combo.addItem("(none)")
        names = []
        for p in self.palettes_provider():
            names.append(p.name)
            self.pal_combo.addItem(p.name)
        lockname = self.ed.doc.lock_palette
        if lockname and lockname not in names:
            self.pal_combo.addItem(lockname)
        want = lockname or cur
        i = self.pal_combo.findText(want) if want else 0
        self.pal_combo.setCurrentIndex(max(0, i))
        self.pal_combo.blockSignals(False)
        self._update_lock_ui()
        self.wheel.markers = self._marker_colors()
        self.wheel.update()

    def current_palette(self):
        name = self.pal_combo.currentText()
        if not name or name == "(none)":
            return None
        for p in self.palettes_provider():
            if p.name == name:
                return p
        return self.ed.doc.palette_by_name(name)

    def select_palette(self, name):
        i = self.pal_combo.findText(name)
        if i >= 0:
            self.pal_combo.setCurrentIndex(i)

    def _palette_changed(self):
        p = self.current_palette()
        if self.ed.doc.lock_palette and (p is None or p.name != self.ed.doc.lock_palette):
            # switching palettes while locked moves the lock
            if p is None:
                self.ed.lock_to(None)
            else:
                self.ed.lock_to(p)
        self.wheel.markers = self._marker_colors()
        self.wheel.update()
        self._update_lock_ui()
        self._timer.start()

    def _lock_toggled(self, on):
        if self._busy:
            return
        p = self.current_palette()
        if on and p is None:
            QMessageBox.information(self, "Nous Halo", "Choose a palette first (or make one in Palettes).")
            self.lock.setChecked(False)
            return
        self.ed.lock_to(p if on else None)
        self._timer.start()

    def _update_lock_ui(self):
        locked = self.ed.doc.lock_palette
        self._busy = True
        self.lock.setChecked(bool(locked))
        self._busy = False
        self.conform_btn.setEnabled(bool(locked))
        if locked:
            n = self.ed.off_palette_count()
            self.off_label.setText("%d colour%s off palette" % (n, "" if n == 1 else "s") if n else "All vector colours on palette")
            self.off_label.setStyleSheet("color:%s" % ("#ebcb8b" if n else "#a3be8c"))
        else:
            self.off_label.setText("")

    def conform_dialog(self):
        if not self.ed.lock_colors():
            return
        d = QDialog(self)
        d.setWindowTitle("Conform to palette")
        f = QFormLayout(d)
        scope = QComboBox()
        for k, label in self.SCOPES:
            scope.addItem(label, k)
        scope.setCurrentIndex(self.scope.currentIndex())
        f.addRow("Colours in", scope)
        ras = QCheckBox("Remap images and paint, pixel by pixel")
        ras.setChecked(True)
        f.addRow(ras)
        dither = QSlider(Qt.Horizontal)
        dither.setRange(0, 100)
        dither.setValue(0)
        f.addRow("Dithering", dither)
        note = QLabel("Dithering keeps soft gradients feeling soft by mixing neighbouring\n"
                      "palette colours in a fine pattern. 0 gives clean flat regions.")
        note.setStyleSheet("color:#9aa3b5")
        f.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._busy = True
            n = self.ed.conform(self.ed.scope_items(scope.currentData()), dither.value() / 100.0, ras.isChecked())
        finally:
            self._busy = False
            QApplication.restoreOverrideCursor()
        self.rebuild(force=True)
        self._update_lock_ui()
