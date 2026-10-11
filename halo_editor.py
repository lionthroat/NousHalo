"""The editing session: one document, its selection, undo, and the settings
the tools share (fill, stroke, brush, current layer/artboard/frame)."""
import math

from PySide6.QtCore import QObject, QPointF, QRectF, Signal
from PySide6.QtGui import QImage, QTransform, Qt

from halo_color import Recolor, nearest_hex, norm_hex, remap_pixels
from halo_model import (Document, Frame, GroupItem, Layer, PathItem, RasterItem, TextItem,
                        leaf_items, walk_items)
from halo_palette import Palette

UNDO_LIMIT = 80


class Editor(QObject):
    changed = Signal(str)   # "doc", "selection", "frame", "tool", "preview", "file"

    def __init__(self, doc=None):
        super().__init__()
        self.doc = doc or Document.blank()
        self.path = None
        self.dirty = False
        self.version = 0
        self.undo_stack = []
        self.redo_stack = []
        self.selection = []        # item ids, in click order
        self.node_sel = set()      # (item id, subpath index, node index)
        self.current_layer_id = self.doc.layers[-1].id
        self.current_artboard_id = self.doc.artboards[0].id
        self.frame_index = None
        self.onion = False
        self.clipboard = []
        self.isolation = None      # id of the group being edited on its own
        # view aids (the window keeps these between sessions)
        self.show_rulers = False
        self.show_guides = True
        self.lock_guides = False
        self.snap_on = True
        # shared tool settings
        self.fill = "#d08770"
        self.stroke = "#2e3440"
        self.stroke_width = 2.0
        self.font_family = "Segoe UI"
        self.font_size = 48.0
        self.brush_kind = "hard"      # square, hard, medium, soft
        self.brush_size = 24.0        # document units
        self.brush_opacity = 1.0
        self.raster_res = 2.0         # paint pixels per document unit
        # Halo preview
        self.preview = None           # Recolor
        self.preview_ids = None
        self.raster_previews = {}

    # ------------------------------------------------------------ document
    def set_doc(self, doc, path=None):
        self.doc = doc
        self.path = path
        self.dirty = False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selection = []
        self.node_sel = set()
        self.isolation = None
        self.current_layer_id = doc.layers[-1].id
        self.current_artboard_id = doc.artboards[0].id
        self.frame_index = 0 if doc.frames else None
        self.clear_preview(emit=False)
        self.notify("file")

    def notify(self, kind="doc"):
        self.version += 1
        self.changed.emit(kind)

    def checkpoint(self):
        snap = self.doc.snapshot()
        self.undo_stack.append((snap, list(self.selection), self.frame_index))
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append((self.doc.snapshot(), list(self.selection), self.frame_index))
        snap, sel, fi = self.undo_stack.pop()
        self._restore(snap, sel, fi)

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.doc.snapshot(), list(self.selection), self.frame_index))
        snap, sel, fi = self.redo_stack.pop()
        self._restore(snap, sel, fi)

    def _restore(self, snap, sel, fi):
        self.doc.restore(snap)
        self.dirty = True
        ids = {it.id for _, _, it in self.doc.walk()}
        self.selection = [i for i in sel if i in ids]
        if self.isolation not in ids:
            self.isolation = None
        self.node_sel = set()
        if self.doc.frames:
            self.frame_index = min(fi if fi is not None else 0, len(self.doc.frames) - 1)
        else:
            self.frame_index = None
        if not self.doc.layer_by_id(self.current_layer_id):
            self.current_layer_id = self.doc.layers[-1].id
        if not any(a.id == self.current_artboard_id for a in self.doc.artboards):
            self.current_artboard_id = self.doc.artboards[0].id
        self.notify("doc")
        self.changed.emit("selection")

    # ------------------------------------------------------------ lookups
    def current_layer(self):
        l = self.doc.layer_by_id(self.current_layer_id)
        if l is None:
            l = self.doc.layers[-1]
            self.current_layer_id = l.id
        return l

    def current_artboard(self):
        for a in self.doc.artboards:
            if a.id == self.current_artboard_id:
                return a
        self.current_artboard_id = self.doc.artboards[0].id
        return self.doc.artboards[0]

    def artboard_at(self, p):
        for a in reversed(self.doc.artboards):
            if a.rect.contains(p):
                return a
        return None

    def frame(self):
        if self.frame_index is None or not self.doc.frames:
            return None
        return self.doc.frames[max(0, min(self.frame_index, len(self.doc.frames) - 1))]

    def selected_items(self):
        out = []
        for i in self.selection:
            l, it = self.doc.find(i)
            if it is not None:
                out.append(it)
        return out

    def selected_with_layers(self):
        out = []
        for i in self.selection:
            l, it = self.doc.find(i)
            if it is not None:
                out.append((l, it))
        return out

    def selected_leaves(self):
        """Drawable objects in the selection, looking inside selected groups."""
        out, seen = [], set()
        for it in self.selected_items():
            for leaf in (it.leaves() if isinstance(it, GroupItem) else [it]):
                if leaf.id not in seen:
                    seen.add(leaf.id)
                    out.append(leaf)
        return out

    def layer_visible(self, layer):
        f = self.frame()
        return f.state(layer)["v"] if f is not None else layer.visible

    def set_layer_visible(self, layer, v):
        self.checkpoint()
        f = self.frame()
        if f is not None:
            f.set_state(layer, v=v)
        else:
            layer.visible = v
        self.notify("doc")

    def layer_offset(self, layer):
        f = self.frame()
        if f is None:
            return QPointF(0, 0)
        s = f.state(layer)
        return QPointF(s["dx"], s["dy"])

    def editable_layers(self):
        return [l for l in self.doc.layers if self.layer_visible(l) and not l.locked]

    # ------------------------------------------------------------ isolation
    def isolated_group(self):
        if self.isolation is None:
            return None
        _, g = self.doc.find(self.isolation)
        if not isinstance(g, GroupItem):
            self.isolation = None
            return None
        return g

    def enter_isolation(self, group):
        """Edit inside a group: clicks pick its contents, the rest dims."""
        l, _ = self.doc.find(group.id)
        self.isolation = group.id
        if l is not None:
            self.current_layer_id = l.id
        self.select([])
        self.notify("frame")

    def exit_isolation(self):
        if self.isolation is None:
            return False
        g = self.isolated_group()
        parent = self.doc.parent_of(g.id) if g is not None else None
        # step out one level at a time, like Illustrator
        self.isolation = parent.id if parent is not None else None
        self.select([g.id] if g is not None else [])
        self.notify("frame")
        return True

    def context_lists(self):
        """(layer, list) pairs that clicks and Select All work on."""
        g = self.isolated_group()
        if g is not None:
            l, _ = self.doc.find(g.id)
            return [(l, g.children)]
        return [(l, l.items) for l in self.editable_layers()]

    def target_list(self):
        """Where new objects go: the isolated group, or the current layer."""
        g = self.isolated_group()
        if g is not None:
            return g.children
        return self.current_layer().items

    # ------------------------------------------------------------ selection
    def select(self, ids, add=False, toggle=False):
        if toggle:
            cur = list(self.selection)
            for i in ids:
                if i in cur:
                    cur.remove(i)
                else:
                    cur.append(i)
            self.selection = cur
        elif add:
            self.selection += [i for i in ids if i not in self.selection]
        else:
            self.selection = list(ids)
        self.node_sel = {n for n in self.node_sel if n[0] in self.selection}
        if self.selection:
            l, _ = self.doc.find(self.selection[-1])
            if l is not None:
                self.current_layer_id = l.id
        self.changed.emit("selection")

    def select_all(self):
        ids = [it.id for _, lst in self.context_lists() for it in lst if it.visible and not it.locked]
        self.select(ids)

    def selection_bounds(self):
        r = QRectF()
        for it in self.selected_items():
            r = r.united(it.bounds())
        return r

    # ------------------------------------------------------------ editing
    def add_item(self, it, layer=None, checkpoint=True, select=True):
        if checkpoint:
            self.checkpoint()
        if layer is None:
            target = self.target_list()
            layer = self.current_layer()
        else:
            target = layer.items
        if layer.locked:
            layer.locked = False
        target.append(it)
        self.notify("doc")
        if select:
            self.select([it.id])
        return it

    def remove_item(self, item_id):
        """Take an object out of whatever holds it. Returns it (or None)."""
        _, cont, it = self.doc.locate(item_id)
        if it is not None:
            cont.remove(it)
        return it

    def transform_selection(self, t, checkpoint=True):
        if checkpoint:
            self.checkpoint()
        for it in self._outermost(self.selected_items()):
            it.apply_transform(t)
        self.notify("doc")

    def _outermost(self, items):
        """Drop objects whose group is also in the list (so nothing moves twice)."""
        ids = {it.id for it in items}
        out = []
        for it in items:
            p = self.doc.parent_of(it.id)
            inside = False
            while p is not None:
                if p.id in ids:
                    inside = True
                    break
                p = self.doc.parent_of(p.id)
            if not inside:
                out.append(it)
        return out

    def delete_selection(self):
        items = self._outermost(self.selected_items())
        if not items:
            return
        self.checkpoint()
        for it in items:
            self.remove_item(it.id)
            if self.isolation == it.id:
                self.isolation = None
        self.selection = []
        self.node_sel = set()
        self.notify("doc")
        self.changed.emit("selection")

    def copy(self):
        order = self.doc.paint_order()
        items = sorted(self._outermost(self.selected_items()), key=lambda it: order.get(it.id, 0))
        self.clipboard = [it.clone(keep_id=False) for it in items]

    def cut(self):
        self.copy()
        self.delete_selection()

    def paste(self, in_place=False, offset=10.0):
        if not self.clipboard:
            return
        self.checkpoint()
        target = self.target_list()
        new = []
        for it in self.clipboard:
            c = it.clone(keep_id=False)
            if not in_place:
                c.apply_transform(QTransform.fromTranslate(offset, offset))
            target.append(c)
            new.append(c.id)
        self.clipboard = [it.clone(keep_id=False) for it in self.clipboard]
        self.notify("doc")
        self.select(new)

    def duplicate(self):
        self.copy()
        self.paste()

    def arrange(self, how):
        """front, forward, backward, back: within the list that holds each object."""
        items = self._outermost(self.selected_items())
        if not items:
            return
        self.checkpoint()
        by_list = {}
        for it in items:
            _, cont, _ = self.doc.locate(it.id)
            by_list.setdefault(id(cont), (cont, set()))[1].add(it.id)
        for cont, ids in by_list.values():
            if how == "front":
                cont[:] = [i for i in cont if i.id not in ids] + [i for i in cont if i.id in ids]
            elif how == "back":
                cont[:] = [i for i in cont if i.id in ids] + [i for i in cont if i.id not in ids]
            elif how == "forward":
                for i in range(len(cont) - 2, -1, -1):
                    if cont[i].id in ids and cont[i + 1].id not in ids:
                        cont[i], cont[i + 1] = cont[i + 1], cont[i]
            elif how == "backward":
                for i in range(1, len(cont)):
                    if cont[i].id in ids and cont[i - 1].id not in ids:
                        cont[i], cont[i - 1] = cont[i - 1], cont[i]
        self.notify("doc")

    def move_selection_to_layer(self, layer):
        order = self.doc.paint_order()
        items = sorted(self._outermost(self.selected_items()), key=lambda it: order.get(it.id, 0))
        if not items:
            return
        self.checkpoint()
        for it in items:
            self.remove_item(it.id)
            layer.items.append(it)
        self.current_layer_id = layer.id
        self.notify("doc")

    def move_items_to(self, ids, owner, index):
        """Move objects (keeping their order) into a layer or group so the
        first lands at stacking position `index` (0 = bottom)."""
        target = owner.items if isinstance(owner, Layer) else owner.children
        order = self.doc.paint_order()
        moving = sorted(self._outermost([it for i in ids for it in [self.doc.find(i)[1]] if it is not None]),
                        key=lambda it: order.get(it.id, 0))
        # a group can't go inside itself
        if isinstance(owner, GroupItem):
            def inside(g, oid):
                return any(c.id == oid or (isinstance(c, GroupItem) and inside(c, oid)) for c in g.children)
            moving = [it for it in moving if it is not owner and
                      not (isinstance(it, GroupItem) and inside(it, owner.id))]
        if not moving:
            return
        self.checkpoint()
        for it in moving:
            _, cont, _ = self.doc.locate(it.id)
            if cont is target and cont.index(it) < index:
                index -= 1
            cont.remove(it)
        index = max(0, min(index, len(target)))
        target[index:index] = moving
        layer = owner if isinstance(owner, Layer) else self.doc.find(owner.id)[0]
        if layer is not None:
            if layer.locked:
                layer.locked = False
            self.current_layer_id = layer.id
        self.notify("doc")

    def move_layer_to(self, layer, index):
        i = self.doc.layers.index(layer)
        index = max(0, min(index, len(self.doc.layers) - 1))
        if i == index:
            return
        self.checkpoint()
        self.doc.layers.insert(index, self.doc.layers.pop(i))
        self.notify("doc")

    # ------------------------------------------------------------ groups
    def group_selection(self):
        """Ctrl+G: the selected objects become one group, placed where the
        topmost of them was."""
        order = self.doc.paint_order()
        items = sorted(self._outermost(self.selected_items()), key=lambda it: order.get(it.id, 0))
        if not items:
            return None
        self.checkpoint()
        top = items[-1]
        layer, cont, _ = self.doc.locate(top.id)
        pos = cont.index(top)
        for it in items:
            _, c, _ = self.doc.locate(it.id)
            if c is cont and c.index(it) < pos:
                pos -= 1
            c.remove(it)
        g = GroupItem(items)
        cont.insert(pos, g)
        self.current_layer_id = layer.id
        self.notify("doc")
        self.select([g.id])
        return g

    def ungroup_selection(self):
        """Ctrl+Shift+G: selected groups give their contents back to the
        list they sit in. Group opacity and clipping pass to the contents."""
        groups = [it for it in self._outermost(self.selected_items()) if isinstance(it, GroupItem)]
        if not groups:
            return 0
        self.checkpoint()
        freed = []
        for g in groups:
            _, cont, _ = self.doc.locate(g.id)
            i = cont.index(g)
            for c in g.children:
                c.opacity *= g.opacity
                if g.clip and not c.clip:
                    c.clip = [dict(nodes=[list(n) for n in s["nodes"]], closed=s.get("closed"))
                              for s in g.clip]
                c.touch()
            cont[i:i + 1] = g.children
            freed += [c.id for c in g.children]
            if self.isolation == g.id:
                self.isolation = None
        self.notify("doc")
        self.select(freed)
        return len(groups)

    def make_clipping_mask(self):
        """Ctrl+7: the topmost selected path clips the other selected objects."""
        order = self.doc.paint_order()
        items = sorted(self._outermost(self.selected_items()), key=lambda it: order.get(it.id, 0))
        if len(items) < 2 or not isinstance(items[-1], PathItem):
            return None
        mask = items[-1]
        g = self.group_selection()
        if g is None:
            return None
        # (group_selection made its own undo step; fold the rest into it)
        g.children = [c for c in g.children if c.id != mask.id]
        g.clip = [dict(nodes=[list(n) for n in s["nodes"]], closed=True) for s in mask.subpaths]
        g.name = mask.name or ""
        g.touch()
        self.notify("doc")
        self.select([g.id])
        return g

    def release_clipping_mask(self):
        """Ctrl+Alt+7: the clip turns back into an (unpainted) path on top."""
        groups = [it for it in self.selected_items() if isinstance(it, GroupItem) and it.clip]
        if not groups:
            return 0
        self.checkpoint()
        for g in groups:
            p = PathItem([dict(nodes=[list(n) for n in s["nodes"]], closed=s.get("closed"))
                          for s in g.clip], None, None)
            p.name = "Clipping path"
            g.children.append(p)
            g.clip = None
            g.touch()
        self.notify("doc")
        return len(groups)

    def flip(self, horizontal=True):
        r = self.selection_bounds()
        if r.isNull():
            return
        c = r.center()
        t = QTransform.fromTranslate(-c.x(), -c.y()) * \
            QTransform.fromScale(-1 if horizontal else 1, 1 if horizontal else -1) * \
            QTransform.fromTranslate(c.x(), c.y())
        self.transform_selection(t)

    def text_to_outlines(self):
        texts = [it for it in self.selected_leaves() if isinstance(it, TextItem)]
        if not texts:
            return 0
        self.checkpoint()
        from halo_model import qpath_to_subpaths
        new_ids = []
        for it in texts:
            subs = qpath_to_subpaths(it.qpath())
            p = PathItem(subs, it.color, it.stroke, it.stroke_width or 1.0)
            p.name = it.text[:30]
            p.opacity = it.opacity
            p.clip = it.clip
            _, cont, _ = self.doc.locate(it.id)
            cont[cont.index(it)] = p
            new_ids.append(p.id)
        gone = {t.id for t in texts}
        self.selection = [i for i in self.selection if i not in gone] + new_ids
        self.notify("doc")
        self.changed.emit("selection")
        return len(texts)

    def rasterize_selection(self):
        from halo_render import draw_item
        from PySide6.QtGui import QPainter
        order = self.doc.paint_order()
        items = sorted(self._outermost(self.selected_items()), key=lambda it: order.get(it.id, 0))
        if not items:
            return
        r = self.selection_bounds()
        if r.isEmpty():
            return
        self.checkpoint()
        s = self.raster_res
        img = QImage(max(1, int(math.ceil(r.width() * s))), max(1, int(math.ceil(r.height() * s))),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.scale(s, s)
        p.translate(-r.x(), -r.y())
        for it in items:  # bottom to top
            draw_item(p, it)
        p.end()
        ri = RasterItem(img)
        ri.xf = QTransform.fromScale(1 / s, 1 / s) * QTransform.fromTranslate(r.x(), r.y())
        ri.name = "Rasterized"
        top = items[-1]
        _, cont, _ = self.doc.locate(top.id)
        pos = cont.index(top)
        for it in items:
            _, c, _ = self.doc.locate(it.id)
            if c is cont and c.index(it) < pos:
                pos -= 1
            c.remove(it)
        cont.insert(min(pos, len(cont)), ri)
        self.selection = [ri.id]
        self.notify("doc")
        self.changed.emit("selection")

    # ------------------------------------------------------------ colour
    def set_fill(self, hexcol, apply=True):
        self.fill = norm_hex(hexcol) if hexcol else None
        if apply and self.selected_items():
            self.checkpoint()
            for it in self.selected_leaves():
                if isinstance(it, PathItem):
                    it.fill = self.fill
                elif isinstance(it, TextItem):
                    it.color = self.fill
                it.touch()
            self.notify("doc")
        self.changed.emit("tool")

    def set_stroke(self, hexcol, apply=True):
        self.stroke = norm_hex(hexcol) if hexcol else None
        if apply and self.selected_items():
            self.checkpoint()
            for it in self.selected_leaves():
                if isinstance(it, PathItem):
                    it.stroke = self.stroke
                elif isinstance(it, TextItem):
                    it.stroke = self.stroke
                    if self.stroke and not it.stroke_width:
                        it.stroke_width = 1.0
                it.touch()
            self.notify("doc")
        self.changed.emit("tool")

    def lock_colors(self):
        """The palette the piece is locked to, as a list of hex, or None."""
        if not self.doc.lock_palette:
            return None
        p = self.doc.palette_by_name(self.doc.lock_palette)
        return p.colors if p and p.colors else None

    def lock_to(self, palette):
        self.checkpoint()
        if palette is None:
            self.doc.lock_palette = None
        else:
            existing = self.doc.palette_by_name(palette.name)
            if existing is None:
                self.doc.palettes.append(palette.copy())
            else:
                existing.colors = list(palette.colors)
                existing.names = list(palette.names)
            self.doc.lock_palette = palette.name
        self.notify("doc")
        self.changed.emit("tool")

    def snap(self, hexcol):
        pal = self.lock_colors()
        return nearest_hex(hexcol, pal) if pal and hexcol else hexcol

    def scope_items(self, scope):
        """Objects a colour command reaches: selection, layer, artboard, document."""
        if scope == "selection":
            return self.selected_leaves()
        if scope == "layer":
            return list(leaf_items(self.current_layer().items))
        if scope == "artboard":
            r = self.current_artboard().rect
            return [it for _, it in self.doc.all_items() if r.intersects(it.bounds())]
        return [it for _, it in self.doc.all_items()]

    def off_palette_count(self, items=None):
        pal = self.lock_colors()
        if not pal:
            return 0
        s = set(pal)
        cols = set()
        for it in (items if items is not None else [i for _, i in self.doc.all_items()]):
            for c in it.colors():
                if c not in s:
                    cols.add(c)
        return len(cols)

    def apply_recolor(self, rc, items, rasters=True):
        """Bake a Recolor into objects (the Halo wheel's Apply)."""
        from halo_render import qimage_to_rgba, rgba_to_qimage
        from halo_color import recolor_pixels
        self.checkpoint()
        cache = {}

        def m(c):
            if c is None:
                return None
            if c not in cache:
                cache[c] = rc.map_hex(c)
            return cache[c]
        for it in items:
            if isinstance(it, PathItem):
                it.fill, it.stroke = m(it.fill), m(it.stroke)
            elif isinstance(it, TextItem):
                it.color, it.stroke = m(it.color), m(it.stroke)
            elif isinstance(it, RasterItem) and rasters:
                it.image = rgba_to_qimage(recolor_pixels(qimage_to_rgba(it.image), rc))
            it.touch()
        self.clear_preview(emit=False)
        self.notify("doc")

    def conform(self, items, dither=0.0, rasters=True):
        """Snap every colour in `items` to the locked palette."""
        from halo_render import qimage_to_rgba, rgba_to_qimage
        pal = self.lock_colors()
        if not pal:
            return 0
        self.checkpoint()
        n = 0
        for it in items:
            if isinstance(it, PathItem):
                nf, ns = self.snap(it.fill), self.snap(it.stroke)
                n += (nf != it.fill) + (ns != it.stroke)
                it.fill, it.stroke = nf, ns
            elif isinstance(it, TextItem):
                nf, ns = self.snap(it.color), self.snap(it.stroke)
                n += (nf != it.color) + (ns != it.stroke)
                it.color, it.stroke = nf, ns
            elif isinstance(it, RasterItem) and rasters:
                it.image = rgba_to_qimage(remap_pixels(qimage_to_rgba(it.image), pal, dither))
                n += 1
            it.touch()
        self.notify("doc")
        return n

    def set_preview(self, rc, ids, raster_previews):
        self.preview = rc
        self.preview_ids = ids
        self.raster_previews = raster_previews
        self.changed.emit("preview")

    def clear_preview(self, emit=True):
        self.preview = None
        self.preview_ids = None
        self.raster_previews = {}
        if emit:
            self.changed.emit("preview")

    # ------------------------------------------------------------ layers
    def new_layer(self, name=None, above=None):
        self.checkpoint()
        l = Layer(name or "Layer %d" % (len(self.doc.layers) + 1), self.doc.next_layer_tag())
        cur = above or self.current_layer()
        idx = self.doc.layers.index(cur) + 1 if cur in self.doc.layers else len(self.doc.layers)
        self.doc.layers.insert(idx, l)
        self.current_layer_id = l.id
        self.notify("doc")
        return l

    def new_raster_layer(self):
        l = self.new_layer("Paint %d" % (len(self.doc.layers) + 1))
        ab = self.current_artboard()
        self.make_raster(l, ab.rect)
        self.notify("doc")
        return l

    def make_raster(self, layer, rect):
        s = self.raster_res
        img = QImage(max(1, int(math.ceil(rect.width() * s))), max(1, int(math.ceil(rect.height() * s))),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        it = RasterItem(img)
        it.name = "Paint"
        it.xf = QTransform.fromScale(1 / s, 1 / s) * QTransform.fromTranslate(rect.x(), rect.y())
        (self.target_list() if layer is self.current_layer() else layer.items).append(it)
        return it

    def delete_layer(self, layer):
        if len(self.doc.layers) <= 1:
            return
        self.checkpoint()
        idx = self.doc.layers.index(layer)
        self.doc.layers.remove(layer)
        ids = {it.id for _, it in walk_items(layer.items)}
        self.selection = [i for i in self.selection if i not in ids]
        if self.isolation in ids:
            self.isolation = None
        for f in self.doc.frames:
            f.states.pop(layer.id, None)
        self.current_layer_id = self.doc.layers[max(0, idx - 1)].id
        self.notify("doc")
        self.changed.emit("selection")

    def duplicate_layer(self, layer):
        self.checkpoint()
        c = layer.clone(keep_ids=False)
        c.name = layer.name + " copy"
        self.doc.layers.insert(self.doc.layers.index(layer) + 1, c)
        for f in self.doc.frames:
            if layer.id in f.states:
                f.states[c.id] = dict(f.states[layer.id])
        self.current_layer_id = c.id
        self.notify("doc")

    def move_layer(self, layer, delta):
        i = self.doc.layers.index(layer)
        j = max(0, min(len(self.doc.layers) - 1, i + delta))
        if i == j:
            return
        self.checkpoint()
        self.doc.layers.insert(j, self.doc.layers.pop(i))
        self.notify("doc")

    def merge_down(self, layer):
        i = self.doc.layers.index(layer)
        if i == 0:
            return
        self.checkpoint()
        below = self.doc.layers[i - 1]
        below.items.extend(layer.items)
        self.doc.layers.remove(layer)
        self.current_layer_id = below.id
        self.notify("doc")

    # ------------------------------------------------------------ frames
    def add_frame(self):
        self.checkpoint()
        cur = self.frame()
        if cur is None:
            f = Frame(100)
            for l in self.doc.layers:
                f.set_state(l, v=l.visible)
            self.doc.frames.append(f)
            self.frame_index = 0
        else:
            f = cur.clone()
            self.doc.frames.insert(self.frame_index + 1, f)
            self.frame_index += 1
        self.notify("doc")
        self.changed.emit("frame")

    def delete_frames(self, indexes):
        if not self.doc.frames:
            return
        self.checkpoint()
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(self.doc.frames):
                del self.doc.frames[i]
        if self.doc.frames:
            self.frame_index = min(self.frame_index or 0, len(self.doc.frames) - 1)
        else:
            self.frame_index = None
        self.notify("doc")
        self.changed.emit("frame")

    def go_frame(self, i):
        if not self.doc.frames:
            self.frame_index = None
        else:
            self.frame_index = max(0, min(i, len(self.doc.frames) - 1))
        self.notify("frame")

    def tween(self, a, b, count):
        """Insert `count` in-between frames from frame a to frame b, easing
        offsets, opacity and hue; a layer that appears or vanishes fades."""
        if count < 1 or a == b:
            return
        if a > b:
            a, b = b, a
        self.checkpoint()
        fa, fb = self.doc.frames[a], self.doc.frames[b]
        new = []
        for k in range(1, count + 1):
            t = k / (count + 1)
            f = Frame(int(round(fa.delay + (fb.delay - fa.delay) * t)))
            for l in self.doc.layers:
                sa, sb = fa.state(l), fb.state(l)
                oa = sa["op"] if sa["v"] else 0.0
                ob = sb["op"] if sb["v"] else 0.0
                op = oa + (ob - oa) * t
                f.states[l.id] = {"v": op > 0.001,
                                  "dx": sa["dx"] + (sb["dx"] - sa["dx"]) * t,
                                  "dy": sa["dy"] + (sb["dy"] - sa["dy"]) * t,
                                  "op": op,
                                  "hue": sa["hue"] + (sb["hue"] - sa["hue"]) * t}
            new.append(f)
        self.doc.frames[b:b] = new
        self.frame_index = b + count
        self.notify("doc")
        self.changed.emit("frame")
