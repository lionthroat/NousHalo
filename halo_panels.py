"""Layers, Properties, Artboards and Palettes panels."""
import os

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSlider, QSpinBox,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from halo_color import HARMONIES, extract_palette, harmony, sort_colors
from halo_model import GroupItem, PathItem, RasterItem, TextItem
from halo_palette import (Palette, delete_from_library, load_library, read_palette_file,
                          save_to_library, write_palette_file)
from halo_widgets import ColorButton, SwatchGrid


def _eye_icon(on):
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("#d8dee9") if on else QColor("#4c566a"))
    p.drawEllipse(2, 5, 12, 7)
    if on:
        p.setBrush(QColor("#d8dee9"))
        p.drawEllipse(6, 6, 4, 5)
    p.end()
    return QIcon(pm)


def _lock_icon(on):
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    if on:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#ebcb8b"))
        p.drawRoundedRect(4, 2, 8, 8, 3, 3)
        p.setBrush(QColor("#ebcb8b"))
        p.drawRect(3, 7, 10, 7)
        p.end()
    return QIcon(pm)


def _tag_icon(color):
    pm = QPixmap(10, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.fillRect(2, 2, 5, 12, QColor(color))
    p.end()
    return QIcon(pm)


# ================================================================ Layers

THUMB = 28


def _thumb_for(items, frame_rect, border, bg=QColor("#f4f6fa")):
    """A small picture of some objects. frame_rect None = fit their bounds."""
    from PySide6.QtCore import QRectF
    from halo_render import draw_item
    pm = QPixmap(THUMB, THUMB)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.fillRect(QRectF(1, 1, THUMB - 2, THUMB - 2), bg)
    r = frame_rect
    if r is None:
        r = QRectF()
        for it in items:
            r = r.united(it.bounds())
    if not r.isEmpty() or (r.width() > 0 or r.height() > 0):
        side = max(r.width(), r.height(), 1e-6)
        s = (THUMB - 6) / side
        p.save()
        p.translate(THUMB / 2, THUMB / 2)
        p.scale(s, s)
        p.translate(-r.center().x(), -r.center().y())
        p.setClipRect(r.adjusted(-side, -side, side, side))
        from halo_model import PathItem as _P
        for it in items:
            # hairlines stay visible at thumbnail size
            if isinstance(it, _P) and it.stroke and it.width * s < 1.3:
                it = it.clone()
                it.width = 1.3 / s
            draw_item(p, it)
        p.restore()
    p.setPen(QPen(border, 2 if border != QColor("#4c566a") else 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(1, 1, THUMB - 2, THUMB - 2))
    p.end()
    return QIcon(pm)


class LayerTree(QTreeWidget):
    """Drag objects between layers or within one; drag layers to reorder.
    The drop is turned into an editor command and the tree is rebuilt."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, ev):
        target = self.itemAt(ev.position().toPoint())
        pos = self.dropIndicatorPosition()
        dragged = self.selectedItems()
        ev.setDropAction(Qt.IgnoreAction)  # we move things in the document, not rows
        ev.accept()
        if not dragged:
            return
        self.panel.handle_drop(dragged, target, pos)


class LayersPanel(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.tree = LayerTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(3)
        # indentation goes in the name column, so the eye and lock columns
        # line up for layers and objects alike
        self.tree.setTreePosition(2)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(THUMB, THUMB))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        hdr.setMinimumSectionSize(10)
        self.tree.setColumnWidth(0, 24)
        self.tree.setColumnWidth(1, 20)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.buttons = {}
        for key, text, tip in (("new", "+", "New layer"), ("paint", "+🖌", "New paint (raster) layer"),
                               ("dup", "⧉", "Duplicate layer"), ("up", "▲", "Move layer up"),
                               ("down", "▼", "Move layer down"), ("merge", "⤓", "Merge down"),
                               ("del", "🗑", "Delete layer")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            row.addWidget(b)
            self.buttons[key] = b
        row.addStretch(1)
        lay.addLayout(row)
        self.op = QSlider(Qt.Horizontal)
        self.op.setRange(0, 100)
        self.op.setToolTip("Layer opacity")
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Opacity"))
        orow.addWidget(self.op, 1)
        self.op_label = QLabel("100%")
        orow.addWidget(self.op_label)
        lay.addLayout(orow)

        self.buttons["new"].clicked.connect(lambda: self.ed.new_layer())
        self.buttons["paint"].clicked.connect(lambda: self.ed.new_raster_layer())
        self.buttons["dup"].clicked.connect(lambda: self.ed.duplicate_layer(self.ed.current_layer()))
        self.buttons["up"].clicked.connect(lambda: self.ed.move_layer(self.ed.current_layer(), 1))
        self.buttons["down"].clicked.connect(lambda: self.ed.move_layer(self.ed.current_layer(), -1))
        self.buttons["merge"].clicked.connect(lambda: self.ed.merge_down(self.ed.current_layer()))
        self.buttons["del"].clicked.connect(self._delete)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._double)
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.tree.itemExpanded.connect(self._expanded_changed)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.op.sliderReleased.connect(self._opacity_done)
        self.op.valueChanged.connect(lambda v: self.op_label.setText("%d%%" % v))
        self._syncing = False
        self._expanded = set()
        self._thumbs = {}       # (id(obj), rev) -> QIcon
        self._layer_thumbs = {}  # layer id -> (key, QIcon)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.refresh)
        editor.changed.connect(self._changed)
        self.refresh()

    def _changed(self, kind):
        if kind in ("doc", "file", "selection", "frame"):
            self._timer.start()

    # ------------------------------------------------------------ thumbnails
    def item_thumb(self, it):
        key = it.deep_rev() if isinstance(it, GroupItem) else (id(it), it.rev)
        icon = self._thumbs.get(key)
        if icon is None:
            icon = _thumb_for([it], None, QColor("#4c566a"))
            if len(self._thumbs) > 6000:
                self._thumbs.clear()
            self._thumbs[key] = icon
        return icon

    def layer_thumb(self, l):
        ab = self.ed.current_artboard()
        key = (tuple(it.deep_rev() if isinstance(it, GroupItem) else (id(it), it.rev, it.visible)
                     for it in l.items), ab.rect.getRect())
        cached = self._layer_thumbs.get(l.id)
        if cached and cached[0] == key:
            return cached[1]
        icon = _thumb_for([it for it in l.items if it.visible], ab.rect, QColor(l.tag))
        self._layer_thumbs[l.id] = (key, icon)
        return icon

    # ------------------------------------------------------------ building
    def refresh(self):
        self._syncing = True
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            ti = stack.pop()
            oid = ti.data(2, Qt.UserRole)
            if oid is None:
                continue
            if ti.isExpanded():
                self._expanded.add(oid)
            elif ti.childCount():
                self._expanded.discard(oid)
            stack += [ti.child(k) for k in range(ti.childCount())]
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        sel = set(self.ed.selection)
        cur = self.ed.current_layer()
        for l in reversed(self.ed.doc.layers):
            ti = QTreeWidgetItem()
            ti.setIcon(0, _eye_icon(self.ed.layer_visible(l)))
            ti.setIcon(1, _lock_icon(l.locked))
            ti.setIcon(2, self.layer_thumb(l))
            ti.setText(2, l.name)
            ti.setToolTip(2, "%s · %d object%s" % (l.name, len(l.items), "" if len(l.items) == 1 else "s"))
            ti.setData(2, Qt.UserRole, l.id)
            ti.setData(0, Qt.UserRole, "layer")
            ti.setFlags(ti.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            f = ti.font(2)
            f.setBold(l is cur)
            ti.setFont(2, f)
            self.tree.addTopLevelItem(ti)
            open_ids = self._open_ids(sel)
            expand = l.id in self._expanded or l.id in open_ids
            self._add_rows(ti, l.items, expand, sel, open_ids, l.tag)
            if expand:
                ti.setExpanded(True)
        if not sel:
            for i in range(self.tree.topLevelItemCount()):
                ti = self.tree.topLevelItem(i)
                if ti.data(2, Qt.UserRole) == cur.id:
                    ti.setSelected(True)
        self.tree.verticalScrollBar().setValue(scroll)
        self.op.blockSignals(True)
        self.op.setValue(int(round(cur.opacity * 100)))
        self.op_label.setText("%d%%" % self.op.value())
        self.op.blockSignals(False)
        self._syncing = False

    def _open_ids(self, sel):
        """Layers and groups that must be open so the selection shows."""
        if getattr(self, "_open_cache", None) and self._open_cache[0] == (self.ed.version, tuple(sel)):
            return self._open_cache[1]
        out = set()
        for i in sel:
            l, _ = self.ed.doc.find(i)
            if l is None:
                continue
            out.add(l.id)
            g = self.ed.doc.parent_of(i)
            while g is not None:
                out.add(g.id)
                g = self.ed.doc.parent_of(g.id)
        self._open_cache = ((self.ed.version, tuple(sel)), out)
        return out

    def _add_rows(self, parent, items, shown, sel, open_ids, tag):
        """Rows for a layer's or group's contents (top of the stack first).
        Thumbnails are only made for rows you can see; big lists wait until
        they're opened."""
        if not shown and len(items) > 400:
            ph = QTreeWidgetItem(parent)
            ph.setText(2, "…")
            ph.setData(0, Qt.UserRole, "placeholder")
            return
        for it in reversed(items):
            ci = QTreeWidgetItem(parent)
            ci.setIcon(0, _eye_icon(it.visible))
            ci.setIcon(1, _lock_icon(it.locked))
            ci.setText(2, it.label())
            ci.setData(2, Qt.UserRole, it.id)
            ci.setData(0, Qt.UserRole, "item")
            if shown:
                if isinstance(it, GroupItem) and it.sublayer:
                    ci.setIcon(2, _thumb_for(list(it.children), self.ed.current_artboard().rect, QColor(tag)))
                else:
                    ci.setIcon(2, self.item_thumb(it))
            if isinstance(it, GroupItem):
                ci.setFlags(ci.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                if it.sublayer:
                    f = ci.font(2)
                    f.setBold(True)
                    ci.setFont(2, f)
                gopen = it.id in self._expanded or it.id in open_ids
                self._add_rows(ci, it.children, shown and gopen, sel, open_ids, tag)
                if gopen:
                    ci.setExpanded(True)
            else:
                ci.setFlags((ci.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            if it.id in sel:
                ci.setSelected(True)

    def _expanded_changed(self, ti):
        """Thumbnails (and rows, for big lists) are made when a row opens."""
        if self._syncing or ti.data(0, Qt.UserRole) not in ("layer", "item"):
            return
        self._expanded.add(ti.data(2, Qt.UserRole))
        self._timer.start(0)

    # ------------------------------------------------------------ clicks
    def _clicked(self, ti, col):
        kind = ti.data(0, Qt.UserRole)
        oid = ti.data(2, Qt.UserRole)
        if kind == "layer":
            l = self.ed.doc.layer_by_id(oid)
            if col == 0:
                self.ed.set_layer_visible(l, not self.ed.layer_visible(l))
            elif col == 1:
                self.ed.checkpoint()
                l.locked = not l.locked
                self.ed.notify("doc")
            else:
                self.ed.current_layer_id = l.id
                self.ed.changed.emit("selection")
        elif kind == "item":
            l, it = self.ed.doc.find(oid)
            if it is None:
                return
            if col == 0:
                self.ed.checkpoint()
                it.visible = not it.visible
                self.ed.notify("doc")
            elif col == 1:
                self.ed.checkpoint()
                it.locked = not it.locked
                self.ed.notify("doc")

    def _tree_selection(self):
        if self._syncing:
            return
        ids = [ti.data(2, Qt.UserRole) for ti in self.tree.selectedItems() if ti.data(0, Qt.UserRole) == "item"]
        layers = [ti.data(2, Qt.UserRole) for ti in self.tree.selectedItems() if ti.data(0, Qt.UserRole) == "layer"]
        if layers and not ids:
            self.ed.current_layer_id = layers[-1]
            self.ed.select([])
            return
        if ids != self.ed.selection:
            self.ed.select(ids)

    def _double(self, ti, col):
        if col < 2:
            return
        kind = ti.data(0, Qt.UserRole)
        oid = ti.data(2, Qt.UserRole)
        if kind == "layer":
            l = self.ed.doc.layer_by_id(oid)
            name, ok = QInputDialog.getText(self, "Rename layer", "Name", text=l.name)
            if ok and name.strip():
                self.ed.checkpoint()
                l.name = name.strip()
                self.ed.notify("doc")
        elif kind == "item":
            l, it = self.ed.doc.find(oid)
            name, ok = QInputDialog.getText(self, "Rename object", "Name (empty = describe the shape)",
                                            text=it.name)
            if ok:
                self.ed.checkpoint()
                it.name = name.strip()
                self.ed.notify("doc")

    # ------------------------------------------------------------ drag and drop
    def handle_drop(self, dragged, target, pos):
        DIV = QAbstractItemView.DropIndicatorPosition
        items = [ti.data(2, Qt.UserRole) for ti in dragged if ti.data(0, Qt.UserRole) == "item"]
        layers = [ti.data(2, Qt.UserRole) for ti in dragged if ti.data(0, Qt.UserRole) == "layer"]
        n = len(self.ed.doc.layers)
        if items:
            if target is None:
                # empty space below everything: bottom layer, at the bottom
                layer = self.ed.doc.layers[0]
                index = 0
            elif target.data(0, Qt.UserRole) == "layer":
                layer = self.ed.doc.layer_by_id(target.data(2, Qt.UserRole))
                index = len(layer.items)  # onto a layer: on top of its stack
            elif target.data(0, Qt.UserRole) == "item":
                layer, cont, tgt = self.ed.doc.locate(target.data(2, Qt.UserRole))
                if tgt is None or tgt.id in items:
                    return
                if isinstance(tgt, GroupItem) and pos == DIV.OnItem:
                    # onto a group: inside it, on top
                    self.ed.move_items_to(set(items), tgt, len(tgt.children))
                    return
                i = cont.index(tgt)
                # the panel lists the top of the stack first
                index = i + 1 if pos == DIV.AboveItem else i
                owner = self.ed.doc.parent_of(tgt.id) or layer
                self.ed.move_items_to(set(items), owner, index)
                return
            else:
                return
            self.ed.move_items_to(set(items), layer, index)
            return
        if layers and len(layers) == 1:
            layer = self.ed.doc.layer_by_id(layers[0])
            if target is None:
                self.ed.move_layer_to(layer, 0)
                return
            if target.data(0, Qt.UserRole) != "layer":
                target = target.parent()
                pos = DIV.AboveItem
            if target is None:
                return
            tl = self.ed.doc.layer_by_id(target.data(2, Qt.UserRole))
            if tl is None or tl is layer:
                return
            j = self.ed.doc.layers.index(tl)
            i = self.ed.doc.layers.index(layer)
            new = j + 1 if pos == DIV.AboveItem else j
            if i < new:
                new -= 1
            self.ed.move_layer_to(layer, max(0, min(n - 1, new)))

    # ------------------------------------------------------------ misc
    def _delete(self):
        l = self.ed.current_layer()
        if l.items and QMessageBox.question(self, "Delete layer", "Delete \"%s\" and its %d object%s?" % (
                l.name, len(l.items), "" if len(l.items) == 1 else "s")) != QMessageBox.Yes:
            return
        self.ed.delete_layer(l)

    def _opacity_done(self):
        l = self.ed.current_layer()
        self.ed.checkpoint()
        l.opacity = self.op.value() / 100.0
        self.ed.notify("doc")

    def _menu(self, pos):
        ti = self.tree.itemAt(pos)
        m = QMenu(self)
        if ti is not None and ti.data(0, Qt.UserRole) == "layer":
            l = self.ed.doc.layer_by_id(ti.data(2, Qt.UserRole))
            m.addAction("Select all on this layer", lambda: self.ed.select([i.id for i in l.items]))
            m.addAction("Rename…", lambda: self._double(ti, 2))
            m.addAction("Hide others", lambda: self._solo(l))
            m.addAction("Show all layers", self._show_all)
            tags = m.addMenu("Layer colour")
            from halo_model import LAYER_TAGS
            for c in LAYER_TAGS:
                tags.addAction(_tag_icon(c), c, lambda c=c: self._tag(l, c))
        if self.ed.selection:
            mv = m.addMenu("Move selection to layer")
            for l in reversed(self.ed.doc.layers):
                mv.addAction(l.name, lambda l=l: self.ed.move_selection_to_layer(l))
        if not m.isEmpty():
            m.exec(self.tree.viewport().mapToGlobal(pos))

    def _solo(self, layer):
        self.ed.checkpoint()
        f = self.ed.frame()
        for l in self.ed.doc.layers:
            if f is not None:
                f.set_state(l, v=l is layer)
            else:
                l.visible = l is layer
        self.ed.notify("doc")

    def _show_all(self):
        self.ed.checkpoint()
        f = self.ed.frame()
        for l in self.ed.doc.layers:
            if f is not None:
                f.set_state(l, v=True)
            else:
                l.visible = True
        self.ed.notify("doc")

    def _tag(self, l, c):
        self.ed.checkpoint()
        l.tag = c
        self.ed.notify("doc")


# ================================================================ Properties

class PropertiesPanel(QWidget):
    def __init__(self, editor, color_provider, parent=None):
        super().__init__(parent)
        self.ed = editor
        self._sync = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("Nothing selected")
        self.title.setStyleSheet("font-weight:600")
        lay.addWidget(self.title)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        lay.addLayout(form)

        self.fill = ColorButton(color_provider)
        self.stroke = ColorButton(color_provider)
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 500)
        self.width.setDecimals(2)
        self.width.setSingleStep(0.5)
        self.width.setSuffix(" pt")
        frow = QHBoxLayout()
        frow.addWidget(self.fill)
        frow.addSpacing(10)
        frow.addWidget(QLabel("Stroke"))
        frow.addWidget(self.stroke)
        frow.addWidget(self.width)
        frow.addStretch(1)
        form.addRow("Fill", frow)
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix("%")
        form.addRow("Opacity", self.opacity)
        self.caps = QComboBox()
        self.caps.addItems(["Butt caps", "Round caps", "Square caps"])
        self.joins = QComboBox()
        self.joins.addItems(["Miter joins", "Round joins", "Bevel joins"])
        crow = QHBoxLayout()
        crow.addWidget(self.caps)
        crow.addWidget(self.joins)
        self.caps_row = QWidget()
        self.caps_row.setLayout(crow)
        crow.setContentsMargins(0, 0, 0, 0)
        form.addRow("", self.caps_row)

        geo = QGridLayout()
        self.geo = {}
        for i, k in enumerate(("X", "Y", "W", "H")):
            sb = QDoubleSpinBox()
            sb.setRange(-100000, 100000)
            sb.setDecimals(1)
            sb.setKeyboardTracking(False)
            self.geo[k] = sb
            geo.addWidget(QLabel(k), i // 2, (i % 2) * 2)
            geo.addWidget(sb, i // 2, (i % 2) * 2 + 1)
        self.geo_box = QWidget()
        self.geo_box.setLayout(geo)
        geo.setContentsMargins(0, 0, 0, 0)
        form.addRow("", self.geo_box)

        # text
        self.text_box = QWidget()
        tf = QFormLayout(self.text_box)
        tf.setContentsMargins(0, 0, 0, 0)
        self.font = QFontComboBox()
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 2000)
        self.size.setDecimals(1)
        self.size.setKeyboardTracking(False)
        self.bold = QCheckBox("Bold")
        self.italic = QCheckBox("Italic")
        self.align = QComboBox()
        self.align.addItems(["left", "center", "right"])
        self.tracking = QDoubleSpinBox()
        self.tracking.setRange(-200, 2000)
        self.tracking.setDecimals(2)
        self.tracking.setKeyboardTracking(False)
        self.leading = QDoubleSpinBox()
        self.leading.setRange(0.5, 5)
        self.leading.setSingleStep(0.05)
        self.leading.setKeyboardTracking(False)
        tf.addRow("Font", self.font)
        r = QHBoxLayout()
        r.addWidget(self.size)
        r.addWidget(self.bold)
        r.addWidget(self.italic)
        tf.addRow("Size", r)
        tf.addRow("Align", self.align)
        tf.addRow("Letter spacing", self.tracking)
        tf.addRow("Line height", self.leading)
        self.font_note = QLabel("")
        self.font_note.setWordWrap(True)
        self.font_note.setStyleSheet("color:#ebcb8b")
        tf.addRow(self.font_note)
        lay.addWidget(self.text_box)

        # brush
        self.brush_box = QWidget()
        bf = QFormLayout(self.brush_box)
        bf.setContentsMargins(0, 8, 0, 0)
        self.brush_kind = QComboBox()
        for k, label in (("square", "Square (monoline)"), ("hard", "Hard round"),
                         ("medium", "Medium round"), ("soft", "Soft round")):
            self.brush_kind.addItem(label, k)
        self.brush_size = QDoubleSpinBox()
        self.brush_size.setRange(0.5, 2000)
        self.brush_size.setDecimals(1)
        self.brush_size.setSuffix(" pt")
        self.brush_op = QSpinBox()
        self.brush_op.setRange(1, 100)
        self.brush_op.setSuffix("%")
        self.res = QComboBox()
        for v in (1, 2, 4):
            self.res.addItem("%dx" % v, float(v))
        self.res.setToolTip("Pixels per point in new paint layers. 2x looks sharp in a 2x export.")
        lab = QLabel("Brush")
        lab.setStyleSheet("font-weight:600")
        bf.addRow(lab)
        bf.addRow("Tip", self.brush_kind)
        bf.addRow("Size  [ ]", self.brush_size)
        bf.addRow("Opacity", self.brush_op)
        bf.addRow("New paint resolution", self.res)
        lay.addWidget(self.brush_box)
        lay.addStretch(1)

        self.fill.colorChosen.connect(lambda c: self.ed.set_fill(c))
        self.stroke.colorChosen.connect(lambda c: self.ed.set_stroke(c))
        self.width.valueChanged.connect(self._width)
        self.opacity.editingFinished.connect(self._opacity)
        self.caps.currentIndexChanged.connect(lambda i: self._set_attr("cap", i, PathItem))
        self.joins.currentIndexChanged.connect(lambda i: self._set_attr("join", i, PathItem))
        for k, sb in self.geo.items():
            sb.valueChanged.connect(self._geo)
        self.font.currentFontChanged.connect(lambda f: self._text_attr("family", f.family()))
        self.size.valueChanged.connect(lambda v: self._text_attr("size", v))
        self.bold.toggled.connect(lambda v: self._text_attr("bold", v))
        self.italic.toggled.connect(lambda v: self._text_attr("italic", v))
        self.align.currentTextChanged.connect(lambda v: self._text_attr("align", v))
        self.tracking.valueChanged.connect(lambda v: self._text_attr("tracking", v))
        self.leading.valueChanged.connect(lambda v: self._text_attr("leading", v))
        self.brush_kind.currentIndexChanged.connect(self._brush)
        self.brush_size.valueChanged.connect(self._brush)
        self.brush_op.valueChanged.connect(self._brush)
        self.res.currentIndexChanged.connect(self._brush)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.refresh)
        editor.changed.connect(lambda k: self._timer.start())
        self.refresh()

    def refresh(self):
        self._sync = True
        items = self.ed.selected_items()
        leaves = self.ed.selected_leaves()
        if not items:
            self.title.setText("Nothing selected · new objects use these")
            self.fill.set_color(self.ed.fill)
            self.stroke.set_color(self.ed.stroke)
            self.width.setValue(self.ed.stroke_width)
            self.geo_box.setEnabled(False)
            self.opacity.setEnabled(False)
            self.caps_row.setVisible(False)
            self.text_box.setVisible(False)
        else:
            self.title.setText(items[-1].label() if len(items) == 1 else "%d objects" % len(items))
            it = leaves[-1] if leaves else items[-1]
            if isinstance(it, PathItem):
                self.fill.set_color(it.fill)
                self.stroke.set_color(it.stroke)
                self.width.setValue(it.width)
                self.caps.setCurrentIndex(it.cap)
                self.joins.setCurrentIndex(it.join)
            elif isinstance(it, TextItem):
                self.fill.set_color(it.color)
                self.stroke.set_color(it.stroke)
                self.width.setValue(it.stroke_width)
            self.caps_row.setVisible(isinstance(it, PathItem))
            self.opacity.setEnabled(True)
            self.opacity.setValue(int(round(items[-1].opacity * 100)))
            self.geo_box.setEnabled(True)
            r = self.ed.selection_bounds()
            for k, v in (("X", r.x()), ("Y", r.y()), ("W", r.width()), ("H", r.height())):
                self.geo[k].setValue(v)
            texts = [i for i in leaves if isinstance(i, TextItem)]
            self.text_box.setVisible(bool(texts))
            if texts:
                t = texts[-1]
                self.font.setCurrentFont(QFont(t.family))
                self.size.setValue(t.size)
                self.bold.setChecked(t.bold)
                self.italic.setChecked(t.italic)
                self.align.setCurrentText(t.align)
                self.tracking.setValue(t.tracking)
                self.leading.setValue(t.leading)
                if t.missing_font:
                    self.font_note.setText(
                        "“%s” isn't installed. %s" % (t.missing_font,
                            "Showing its original letter shapes until you edit the text." if t.uses_outline()
                            else "Showing a stand-in font."))
                else:
                    self.font_note.setText("")
        i = self.brush_kind.findData(self.ed.brush_kind)
        self.brush_kind.setCurrentIndex(max(0, i))
        self.brush_size.setValue(self.ed.brush_size)
        self.brush_op.setValue(int(round(self.ed.brush_opacity * 100)))
        self.res.setCurrentIndex(max(0, self.res.findData(self.ed.raster_res)))
        self._sync = False

    def _width(self, v):
        if self._sync:
            return
        self.ed.stroke_width = v
        items = self.ed.selected_leaves()
        if items:
            self.ed.checkpoint()
            for it in items:
                if isinstance(it, PathItem):
                    it.width = v
                elif isinstance(it, TextItem):
                    it.stroke_width = v
                it.touch()
            self.ed.notify("doc")

    def _opacity(self):
        if self._sync:
            return
        items = self.ed.selected_items()
        if not items:
            return
        self.ed.checkpoint()
        for it in items:
            it.opacity = self.opacity.value() / 100.0
        self.ed.notify("doc")

    def _set_attr(self, name, v, cls):
        if self._sync:
            return
        items = [i for i in self.ed.selected_leaves() if isinstance(i, cls)]
        if not items:
            return
        self.ed.checkpoint()
        for it in items:
            setattr(it, name, v)
            it.touch()
        self.ed.notify("doc")

    def _text_attr(self, name, v):
        if self._sync:
            return
        if name == "family":
            self.ed.font_family = v
        if name == "size":
            self.ed.font_size = v
        items = [i for i in self.ed.selected_leaves() if isinstance(i, TextItem)]
        if not items:
            return
        self.ed.checkpoint()
        for it in items:
            setattr(it, name, v)
            it.outline = None  # restyled: draw from the font from now on
            it.touch()
        self.ed.notify("doc")

    def _geo(self):
        if self._sync:
            return
        r = self.ed.selection_bounds()
        if r.isNull():
            return
        x, y, w, h = (self.geo[k].value() for k in ("X", "Y", "W", "H"))
        sx = w / r.width() if r.width() > 1e-6 else 1
        sy = h / r.height() if r.height() > 1e-6 else 1
        t = QTransform.fromTranslate(-r.x(), -r.y()) * QTransform.fromScale(sx, sy) * QTransform.fromTranslate(x, y)
        self.ed.transform_selection(t)

    def _brush(self):
        if self._sync:
            return
        self.ed.brush_kind = self.brush_kind.currentData()
        self.ed.brush_size = self.brush_size.value()
        self.ed.brush_opacity = self.brush_op.value() / 100.0
        self.ed.raster_res = self.res.currentData()


# ================================================================ Artboards

class ArtboardsPanel(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Artboard", "Size"])
        self.tree.setRootIsDecorated(False)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for key, text in (("add", "New…"), ("rename", "Rename"), ("size", "Size…"), ("del", "Delete")):
            b = QPushButton(text)
            row.addWidget(b)
            setattr(self, "b_" + key, b)
        lay.addLayout(row)
        self.b_add.clicked.connect(self.add)
        self.b_rename.clicked.connect(self.rename)
        self.b_size.clicked.connect(self.resize_ab)
        self.b_del.clicked.connect(self.delete)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(lambda *a: self.zoomRequested.emit())
        editor.changed.connect(lambda k: k in ("doc", "file", "selection") and self.refresh())
        self.refresh()

    zoomRequested = Signal()

    def refresh(self):
        self.tree.clear()
        cur = self.ed.current_artboard()
        for i, ab in enumerate(self.ed.doc.artboards):
            ti = QTreeWidgetItem([ab.name, "%d × %d" % (round(ab.rect.width()), round(ab.rect.height()))])
            ti.setData(0, Qt.UserRole, ab.id)
            self.tree.addTopLevelItem(ti)
            if ab is cur:
                ti.setSelected(True)
        self.tree.resizeColumnToContents(0)

    def _clicked(self, ti, col):
        self.ed.current_artboard_id = ti.data(0, Qt.UserRole)
        self.ed.changed.emit("selection")

    def add(self):
        w, h, name = size_dialog(self, "New artboard", 1024, 1024,
                                 "Artboard %d" % (len(self.ed.doc.artboards) + 1))
        if w is None:
            return
        from halo_model import Artboard
        self.ed.checkpoint()
        x = max(a.rect.right() for a in self.ed.doc.artboards) + 72
        ab = Artboard(name, x, 0, w, h)
        self.ed.doc.artboards.append(ab)
        self.ed.current_artboard_id = ab.id
        self.ed.notify("doc")
        self.zoomRequested.emit()

    def rename(self):
        ab = self.ed.current_artboard()
        name, ok = QInputDialog.getText(self, "Rename artboard", "Name", text=ab.name)
        if ok and name.strip():
            self.ed.checkpoint()
            ab.name = name.strip()
            self.ed.notify("doc")

    def resize_ab(self):
        ab = self.ed.current_artboard()
        w, h, name = size_dialog(self, "Artboard size", ab.rect.width(), ab.rect.height(), ab.name,
                                 background=ab.background)
        if w is None:
            return
        self.ed.checkpoint()
        ab.rect.setWidth(w)
        ab.rect.setHeight(h)
        ab.name = name
        bg = getattr(self, "_last_bg", ab.background)
        ab.background = bg
        self.ed.notify("doc")

    def delete(self):
        if len(self.ed.doc.artboards) <= 1:
            return
        self.ed.checkpoint()
        ab = self.ed.current_artboard()
        self.ed.doc.artboards.remove(ab)
        self.ed.current_artboard_id = self.ed.doc.artboards[0].id
        self.ed.notify("doc")


PRESETS = [("Square 512", 512, 512), ("Square 1024", 1024, 1024), ("Square 2048", 2048, 2048),
           ("HD 1920 × 1080", 1920, 1080), ("4K 3840 × 2160", 3840, 2160), ("Sprite 64", 64, 64),
           ("Sprite 128", 128, 128), ("Sprite 256", 256, 256), ("Phone 1080 × 1920", 1080, 1920)]


def size_dialog(parent, title, w, h, name="", background="unset"):
    d = QDialog(parent)
    d.setWindowTitle(title)
    f = QFormLayout(d)
    ne = QLineEdit(name)
    f.addRow("Name", ne)
    preset = QComboBox()
    preset.addItem("Custom")
    for label, pw, ph in PRESETS:
        preset.addItem(label, (pw, ph))
    f.addRow("Preset", preset)
    ws, hs = QSpinBox(), QSpinBox()
    for sb, v in ((ws, w), (hs, h)):
        sb.setRange(1, 30000)
        sb.setValue(int(round(v)))
        sb.setSuffix(" px")
    f.addRow("Width", ws)
    f.addRow("Height", hs)
    preset.currentIndexChanged.connect(lambda i: preset.currentData() and (ws.setValue(preset.currentData()[0]),
                                                                          hs.setValue(preset.currentData()[1])))
    bgbox = None
    if background != "unset":
        bgbox = QComboBox()
        bgbox.addItem("Transparent", None)
        bgbox.addItem("White", "#ffffff")
        bgbox.addItem("Black", "#000000")
        if background and background not in ("#ffffff", "#000000"):
            bgbox.addItem(background, background)
        bgbox.setCurrentIndex(max(0, bgbox.findData(background)))
        f.addRow("Background", bgbox)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    f.addRow(bb)
    if d.exec() != QDialog.Accepted:
        return None, None, None
    if bgbox is not None:
        parent._last_bg = bgbox.currentData()
    return float(ws.value()), float(hs.value()), ne.text().strip() or name or "Artboard"


# ================================================================ Palettes

class PalettesPanel(QWidget):
    """The palette library: build, edit, save and load palettes."""
    librarySaved = Signal()
    activeChanged = Signal(str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.library = load_library()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)
        top = QHBoxLayout()
        self.combo = QComboBox()
        top.addWidget(self.combo, 1)
        menu_btn = QToolButton()
        menu_btn.setText("☰")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        pm = QMenu(menu_btn)
        pm.addAction("New palette…", self.new_palette)
        pm.addAction("Duplicate", self.duplicate)
        pm.addAction("Rename…", self.rename)
        pm.addAction("Delete", self.delete)
        pm.addSeparator()
        pm.addAction("Import swatches (.ase, .gpl, .hex)…", self.import_file)
        pm.addAction("Export palette…", self.export_file)
        menu_btn.setMenu(pm)
        top.addWidget(menu_btn)
        lay.addLayout(top)
        self.grid = SwatchGrid(24)
        lay.addWidget(self.grid)
        hint = QLabel("Click: fill · Shift-click: stroke · Right-click: edit")
        hint.setStyleSheet("color:#7b8394")
        lay.addWidget(hint)

        add = QHBoxLayout()
        b_add = QPushButton("+ Fill colour")
        b_add.setToolTip("Add the current fill colour")
        b_add.clicked.connect(self.add_fill)
        add.addWidget(b_add)
        b_from = QToolButton()
        b_from.setText("Build from ▾")
        b_from.setPopupMode(QToolButton.InstantPopup)
        fm = QMenu(b_from)
        fm.addAction("Colours in the document", lambda: self.from_doc("document"))
        fm.addAction("Colours in the current artboard", lambda: self.from_doc("artboard"))
        fm.addAction("Colours in the selection", lambda: self.from_doc("selection"))
        fm.addAction("Selected image (dominant colours)…", self.from_selected_image)
        fm.addAction("Image file…", self.from_image_file)
        hm = fm.addMenu("Harmony from fill colour")
        for name in HARMONIES:
            hm.addAction(name + ("…" if HARMONIES[name] is None else ""), lambda n=name: self.from_harmony(n))
        b_from.setMenu(fm)
        add.addWidget(b_from)
        b_sort = QToolButton()
        b_sort.setText("Sort ▾")
        b_sort.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(b_sort)
        sm.addAction("By hue", lambda: self.sort("hue"))
        sm.addAction("By lightness", lambda: self.sort("lightness"))
        b_sort.setMenu(sm)
        add.addWidget(b_sort)
        add.addStretch(1)
        lay.addLayout(add)
        lay.addStretch(1)

        self.combo.currentIndexChanged.connect(self._show)
        self.grid.clicked.connect(self._swatch_clicked)
        self.grid.rightClicked.connect(self._swatch_menu)
        editor.changed.connect(self._ed_changed)
        self.reload_combo()

    # library helpers
    def palettes(self):
        out = list(self.library)
        names = {p.name for p in out}
        for p in self.ed.doc.palettes:
            if p.name not in names:
                out.append(p)
        return out

    def current(self):
        name = self.combo.currentText()
        for p in self.palettes():
            if p.name == name:
                return p
        return None

    def reload_combo(self, select=None):
        cur = select or self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        lib = {p.name for p in self.library}
        for p in self.palettes():
            self.combo.addItem(p.name + ("" if p.name in lib else "  (in document)"), p.name)
            self.combo.setItemText(self.combo.count() - 1, p.name)
        i = self.combo.findText(cur) if cur else -1
        if i < 0 and self.ed.doc.lock_palette:
            i = self.combo.findText(self.ed.doc.lock_palette)
        self.combo.setCurrentIndex(max(0, i))
        self.combo.blockSignals(False)
        self._show()

    def _ed_changed(self, kind):
        if kind == "file":
            self.reload_combo()
        elif kind in ("tool", "doc"):
            self._show()

    def _show(self):
        p = self.current()
        if p is None:
            self.grid.set_colors([])
            return
        marked = [i for i, c in enumerate(p.colors) if c in (self.ed.fill, self.ed.stroke)]
        self.grid.set_colors(p.colors, marked)
        self.activeChanged.emit(p.name)

    def save(self, p, old_name=None):
        if p not in self.library:
            self.library.append(p)
        save_to_library(p, old_name)
        # a locked document keeps its own copy in step
        if self.ed.doc.lock_palette in (p.name, old_name):
            self.ed.lock_to(p)
        self.reload_combo(p.name)
        self.librarySaved.emit()

    # actions
    def new_palette(self, colors=None, name=None):
        if name is None:
            name, ok = QInputDialog.getText(self, "New palette", "Name", text="New palette")
            if not ok or not name.strip():
                return
            name = name.strip()
        name = self._unique(name)
        p = Palette(name, colors or [])
        self.save(p)
        return p

    def _unique(self, name):
        names = {p.name for p in self.palettes()}
        if name not in names:
            return name
        k = 2
        while "%s %d" % (name, k) in names:
            k += 1
        return "%s %d" % (name, k)

    def duplicate(self):
        p = self.current()
        if p:
            q = p.copy()
            q.name = self._unique(p.name + " copy")
            self.save(q)

    def rename(self):
        p = self.current()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Rename palette", "Name", text=p.name)
        if ok and name.strip() and name.strip() != p.name:
            old = p.name
            p.name = self._unique(name.strip())
            if self.ed.doc.lock_palette == old:
                self.ed.doc.lock_palette = p.name
                dp = self.ed.doc.palette_by_name(old)
                if dp:
                    dp.name = p.name
            self.save(p, old)

    def delete(self):
        p = self.current()
        if not p:
            return
        if QMessageBox.question(self, "Delete palette", "Delete the palette “%s” from the library?" % p.name) != QMessageBox.Yes:
            return
        if p in self.library:
            self.library.remove(p)
            delete_from_library(p.name)
        self.reload_combo()
        self.librarySaved.emit()

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import swatches", "",
                                              "Swatch files (*.ase *.gpl *.hex *.txt *.json);;All files (*)")
        if not path:
            return
        try:
            p = read_palette_file(path)
        except Exception as e:
            QMessageBox.warning(self, "Import swatches", "Couldn't read that file:\n%s" % e)
            return
        if not p.colors:
            QMessageBox.information(self, "Import swatches", "No colours found in that file.")
            return
        p.name = self._unique(p.name)
        self.save(p)

    def export_file(self):
        p = self.current()
        if not p:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export palette", p.name + ".ase",
                                              "Adobe swatches (*.ase);;GIMP palette (*.gpl);;Hex list (*.hex);;PNG strip (*.png)")
        if path:
            write_palette_file(p, path)

    def add_fill(self):
        p = self.current() or self.new_palette(name="New palette")
        if p and self.ed.fill and p.add(self.ed.fill):
            self.save(p)

    def from_doc(self, scope):
        counts = self.ed.doc.used_colors(self.ed.scope_items(scope))
        cols = [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
        if not cols:
            QMessageBox.information(self, "Palette", "No vector colours there.")
            return
        self.new_palette(sort_colors(cols[:64]), name={"document": "Document colours", "artboard": "Artboard colours",
                                                       "selection": "Selection colours"}[scope])

    def _count(self, title, default=8):
        n, ok = QInputDialog.getInt(self, title, "How many colours?", default, 2, 64)
        return n if ok else None

    def from_selected_image(self):
        from halo_render import qimage_to_rgba
        imgs = [it for it in self.ed.selected_items() if isinstance(it, RasterItem)]
        if not imgs:
            QMessageBox.information(self, "Palette", "Select an image or paint object first.")
            return
        n = self._count("Palette from image")
        if not n:
            return
        img = imgs[-1].image
        if max(img.width(), img.height()) > 800:
            img = img.scaled(800, 800, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.new_palette(sort_colors(extract_palette(qimage_to_rgba(img), n)), name=imgs[-1].name or "Image colours")

    def from_image_file(self):
        from halo_render import qimage_to_rgba
        from PySide6.QtGui import QImage
        path, _ = QFileDialog.getOpenFileName(self, "Palette from image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            return
        n = self._count("Palette from image")
        if not n:
            return
        if max(img.width(), img.height()) > 800:
            img = img.scaled(800, 800, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.new_palette(sort_colors(extract_palette(qimage_to_rgba(img), n)),
                         name=os.path.splitext(os.path.basename(path))[0])

    def from_harmony(self, name):
        base = self.ed.fill or "#d08770"
        steps, shift = 5, 25.0
        if HARMONIES[name] is None:
            d = QDialog(self)
            d.setWindowTitle(name)
            f = QFormLayout(d)
            st = QSpinBox()
            st.setRange(2, 16)
            st.setValue(5)
            f.addRow("Steps", st)
            sh = QSpinBox()
            sh.setRange(0, 90)
            sh.setValue(25)
            sh.setSuffix("°")
            if name == "Hue-shifted ramp":
                f.addRow("Hue shift", sh)
                note = QLabel("Shadows lean cool, highlights lean warm.")
                note.setStyleSheet("color:#9aa3b5")
                f.addRow(note)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            f.addRow(bb)
            if d.exec() != QDialog.Accepted:
                return
            steps, shift = st.value(), float(sh.value())
        cols = harmony(base, name, "perceptual", steps, shift)
        self.new_palette(cols, name="%s %s" % (name, base))

    def sort(self, key):
        p = self.current()
        if p:
            pairs = dict(zip(p.colors, p.names))
            p.colors = sort_colors(p.colors, key)
            p.names = [pairs.get(c, "") for c in p.colors]
            self.save(p)

    def _swatch_clicked(self, i, mods):
        p = self.current()
        if not p:
            return
        if mods & Qt.ShiftModifier:
            self.ed.set_stroke(p.colors[i])
        else:
            self.ed.set_fill(p.colors[i])

    def _swatch_menu(self, i, gpos):
        p = self.current()
        if not p:
            return
        m = QMenu(self)
        m.addAction("Use as fill", lambda: self.ed.set_fill(p.colors[i]))
        m.addAction("Use as stroke", lambda: self.ed.set_stroke(p.colors[i]))
        m.addSeparator()
        m.addAction("Change…", lambda: self._change(p, i))
        m.addAction("Replace with fill colour", lambda: self._replace(p, i, self.ed.fill))
        if i > 0:
            m.addAction("Move left", lambda: self._move(p, i, -1))
        if i < len(p.colors) - 1:
            m.addAction("Move right", lambda: self._move(p, i, 1))
        m.addAction("Remove", lambda: (p.remove(i), self.save(p)))
        m.exec(gpos)

    def _change(self, p, i):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(p.colors[i]), self, "Swatch")
        if c.isValid():
            self._replace(p, i, c.name())

    def _replace(self, p, i, c):
        if c:
            p.colors[i] = c
            self.save(p)

    def _move(self, p, i, d):
        j = i + d
        p.colors[i], p.colors[j] = p.colors[j], p.colors[i]
        p.names[i], p.names[j] = p.names[j], p.names[i]
        self.save(p)
