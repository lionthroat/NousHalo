"""The Timeline: frame animation in the style of Photoshop's frame mode.

Each frame remembers, per layer: shown or hidden, an offset, opacity and a
hue spin. New Frame copies the current one; Tween fills in frames between
the current frame and the next; the Frame Move tool (N) drags a layer within
the current frame only.
"""
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QListView, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from halo_render import render_artboard

THUMB = 72


class TimelinePanel(QWidget):
    def __init__(self, editor, export_cb, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.export_cb = export_cb
        self._sync = False
        self.playing = False
        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.timeout.connect(self._tick)
        self._thumbs = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        left = QVBoxLayout()
        bar = QHBoxLayout()
        bar.setSpacing(2)

        def btn(text, tip, cb):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            bar.addWidget(b)
            return b
        btn("⏮", "First frame", lambda: self.ed.go_frame(0))
        btn("◀", "Previous frame", lambda: self.step(-1))
        self.play_btn = btn("▶", "Play / stop (Space in this panel)", self.toggle_play)
        btn("▶|", "Next frame", lambda: self.step(1))
        btn("⏭", "Last frame", lambda: self.ed.go_frame(len(self.ed.doc.frames) - 1))
        bar.addSpacing(8)
        self.loop = QCheckBox("Loop")
        self.loop.toggled.connect(self._loop)
        bar.addWidget(self.loop)
        self.onion = QCheckBox("Onion skin")
        self.onion.setToolTip("Show the frames before and after, faintly")
        self.onion.toggled.connect(self._onion)
        bar.addWidget(self.onion)
        bar.addSpacing(8)
        btn("+ Frame", "New frame (a copy of the current one)", self.ed.add_frame)
        btn("Tween…", "Fill in frames between this frame and the next", self.tween)
        btn("Delete", "Delete the selected frames", self.delete)
        bar.addSpacing(8)
        bar.addWidget(QLabel("Delay"))
        self.delay = QSpinBox()
        self.delay.setRange(10, 10000)
        self.delay.setSingleStep(10)
        self.delay.setSuffix(" ms")
        self.delay.setKeyboardTracking(False)
        self.delay.setToolTip("How long the selected frames show")
        self.delay.valueChanged.connect(self._delay)
        bar.addWidget(self.delay)
        self.fps = QLabel("")
        self.fps.setStyleSheet("color:#7b8394")
        bar.addWidget(self.fps)
        bar.addStretch(1)
        btn("Export…", "Export frames as PNGs and/or a sprite sheet", lambda: self.export_cb())
        left.addLayout(bar)

        self.stack = QStackedWidget()
        empty = QWidget()
        el = QVBoxLayout(empty)
        msg = QLabel("No frames yet. “+ Frame” captures how the layers look now; add more frames, "
                     "then hide/show layers, nudge them with the Frame Move tool (N), fade them or spin "
                     "their hue per frame. Tween fills in the in-betweens.")
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#9aa3b5")
        el.addWidget(msg)
        el.addStretch(1)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setFlow(QListView.LeftToRight)
        self.list.setWrapping(False)
        self.list.setMovement(QListView.Static)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSpacing(2)
        self.list.setGridSize(QSize(THUMB + 34, THUMB + 26))
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setFixedHeight(THUMB + 40)
        self.list.itemSelectionChanged.connect(self._list_sel)
        self.stack.addWidget(empty)
        self.stack.addWidget(self.list)
        left.addWidget(self.stack)
        lay.addLayout(left, 1)

        # the current layer in the current frame
        self.side = QWidget()
        sf = QFormLayout(self.side)
        sf.setContentsMargins(8, 0, 0, 0)
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._layer_picked)
        sf.addRow("Layer", self.layer_combo)
        self.vis = QCheckBox("Visible in this frame")
        self.vis.toggled.connect(lambda v: self._state(v=v))
        sf.addRow(self.vis)
        self.dx = QDoubleSpinBox()
        self.dy = QDoubleSpinBox()
        for sb in (self.dx, self.dy):
            sb.setRange(-100000, 100000)
            sb.setDecimals(1)
            sb.setKeyboardTracking(False)
        r = QHBoxLayout()
        r.addWidget(self.dx)
        r.addWidget(self.dy)
        sf.addRow("Offset", r)
        self.dx.valueChanged.connect(lambda v: self._state(dx=v))
        self.dy.valueChanged.connect(lambda v: self._state(dy=v))
        self.op = QSpinBox()
        self.op.setRange(0, 100)
        self.op.setSuffix("%")
        self.op.setKeyboardTracking(False)
        self.op.valueChanged.connect(lambda v: self._state(op=v / 100.0))
        sf.addRow("Opacity", self.op)
        self.hue = QSpinBox()
        self.hue.setRange(-720, 720)
        self.hue.setSuffix("°")
        self.hue.setKeyboardTracking(False)
        self.hue.setToolTip("Spin this layer's colours around the wheel in this frame.\n"
                            "Tween from 0° to 360° for a full colour cycle.")
        self.hue.valueChanged.connect(lambda v: self._state(hue=float(v)))
        sf.addRow("Hue spin", self.hue)
        lay.addWidget(self.side)

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(300)
        self._thumb_timer.timeout.connect(self.refresh_thumbs)
        editor.changed.connect(self._changed)
        self.refresh()

    # ------------------------------------------------------------ sync
    def _changed(self, kind):
        if kind in ("doc", "file", "frame", "selection"):
            self.refresh(thumbs=kind != "frame" and kind != "selection")

    def refresh(self, thumbs=True):
        self._sync = True
        frames = self.ed.doc.frames
        self.stack.setCurrentIndex(1 if frames else 0)
        self.loop.setChecked(self.ed.doc.loop)
        self.onion.setChecked(self.ed.onion)
        if self.list.count() != len(frames):
            self.list.clear()
            for i in range(len(frames)):
                self.list.addItem(QListWidgetItem(""))
            thumbs = True
        for i, f in enumerate(frames):
            it = self.list.item(i)
            it.setText("%d · %dms" % (i + 1, f.delay))
        fi = self.ed.frame_index
        if fi is not None and 0 <= fi < self.list.count():
            if not self.list.item(fi).isSelected():
                self.list.clearSelection()
                self.list.item(fi).setSelected(True)
            self.list.scrollToItem(self.list.item(fi))
        f = self.ed.frame()
        self.delay.setEnabled(f is not None)
        if f is not None:
            self.delay.setValue(f.delay)
            self.fps.setText("≈ %.1f fps" % (1000.0 / max(f.delay, 1)))
        else:
            self.fps.setText("")
        self.layer_combo.clear()
        for l in reversed(self.ed.doc.layers):
            self.layer_combo.addItem(l.name, l.id)
        cur = self.ed.current_layer()
        self.layer_combo.setCurrentIndex(max(0, self.layer_combo.findData(cur.id)))
        self.side.setEnabled(f is not None)
        if f is not None:
            s = f.state(cur)
            self.vis.setChecked(s["v"])
            self.dx.setValue(s["dx"])
            self.dy.setValue(s["dy"])
            self.op.setValue(int(round(s["op"] * 100)))
            self.hue.setValue(int(round(s["hue"])))
        self._sync = False
        if thumbs and frames:
            self._thumb_timer.start()

    def refresh_thumbs(self):
        frames = self.ed.doc.frames
        if not frames or self.list.count() != len(frames):
            return
        ab = self.ed.current_artboard()
        s = THUMB / max(ab.rect.width(), ab.rect.height())
        for i, f in enumerate(frames):
            img = render_artboard(self.ed.doc, ab, s, frame=f, transparent=False)
            pm = QPixmap(THUMB, THUMB)
            pm.fill(QColor("#3b4252"))
            p = QPainter(pm)
            p.drawImage((THUMB - img.width()) // 2, (THUMB - img.height()) // 2, img)
            p.end()
            self.list.item(i).setIcon(QIcon(pm))

    # ------------------------------------------------------------ actions
    def _list_sel(self):
        if self._sync:
            return
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        if rows and self.ed.frame_index not in rows:
            self.ed.go_frame(rows[-1])
        elif len(rows) == 1:
            self.ed.go_frame(rows[0])

    def selected_rows(self):
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        return rows or ([self.ed.frame_index] if self.ed.frame_index is not None else [])

    def step(self, d):
        n = len(self.ed.doc.frames)
        if not n:
            return
        i = (self.ed.frame_index or 0) + d
        if self.ed.doc.loop:
            i %= n
        self.ed.go_frame(max(0, min(n - 1, i)))

    def toggle_play(self):
        if self.playing:
            self.playing = False
            self.play_timer.stop()
            self.play_btn.setText("▶")
            return
        if len(self.ed.doc.frames) < 2:
            return
        self.playing = True
        self.play_btn.setText("■")
        self._tick(first=True)

    def _tick(self, first=False):
        if not self.playing:
            return
        frames = self.ed.doc.frames
        if not frames:
            self.toggle_play()
            return
        if not first:
            i = (self.ed.frame_index or 0) + 1
            if i >= len(frames):
                if not self.ed.doc.loop:
                    self.toggle_play()
                    return
                i = 0
            self.ed.go_frame(i)
        self.play_timer.start(max(10, self.ed.frame().delay))

    def _loop(self, v):
        if self._sync:
            return
        self.ed.doc.loop = v
        self.ed.dirty = True

    def _onion(self, v):
        if self._sync:
            return
        self.ed.onion = v
        self.ed.notify("frame")

    def _delay(self, v):
        if self._sync:
            return
        self.ed.checkpoint()
        for i in self.selected_rows():
            self.ed.doc.frames[i].delay = v
        self.ed.notify("doc")

    def delete(self):
        self.ed.delete_frames(self.selected_rows())

    def _layer_picked(self):
        if self._sync:
            return
        lid = self.layer_combo.currentData()
        if lid:
            self.ed.current_layer_id = lid
            self.ed.changed.emit("selection")

    def _state(self, **kw):
        if self._sync:
            return
        f = self.ed.frame()
        if f is None:
            return
        self.ed.checkpoint()
        f.set_state(self.ed.current_layer(), **kw)
        self.ed.notify("doc")

    def tween(self):
        frames = self.ed.doc.frames
        i = self.ed.frame_index
        if i is None or len(frames) < 2:
            return
        if i >= len(frames) - 1:
            i -= 1
        d = QDialog(self)
        d.setWindowTitle("Tween")
        f = QFormLayout(d)
        f.addRow(QLabel("Between frame %d and frame %d" % (i + 1, i + 2)))
        n = QSpinBox()
        n.setRange(1, 240)
        n.setValue(5)
        f.addRow("Frames to add", n)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() == QDialog.Accepted:
            self.ed.tween(i, i + 1, n.value())

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self.toggle_play()
            return
        super().keyPressEvent(ev)
