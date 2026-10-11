"""Nous Halo: vector drawing with layers and artboards, Illustrator .ai import,
a colour wheel for hue shifting, palette locking, and frame animation."""
import os
import sys

from PySide6.QtCore import QByteArray, QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QTextBrowser, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from halo_canvas import Canvas
from halo_editor import Editor
from halo_export import export_animation, export_artboards, export_png, export_svg, safe
from halo_import import ImportError_, import_file, import_image
from halo_model import Document
from halo_panels import ArtboardsPanel, LayersPanel, PalettesPanel, PropertiesPanel, size_dialog
from halo_timeline import TimelinePanel
from halo_wheel import HaloPanel
from halo_widgets import FillStroke, PalettePopup, app_icon, tool_icon

APP = "Nous Halo"
HERE = os.path.dirname(os.path.abspath(__file__))


def version():
    for base in (getattr(sys, "_MEIPASS", ""), HERE):
        try:
            with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return "0.1.0"


TOOLS = [
    ("select", "Selection", "V"), ("direct", "Direct Selection (anchors and handles)", "A"),
    ("pen", "Pen", "P"), ("rect", "Rectangle", "M"), ("ellipse", "Ellipse", "L"),
    ("line", "Line", "\\"), ("text", "Type", "T"), ("brush", "Paintbrush (raster)", "B"),
    ("eraser", "Eraser (raster)", "E"), ("eyedropper", "Eyedropper", "I"),
    ("framemove", "Frame Move: drag a layer in the current frame", "N"),
    ("artboard", "Artboard", "Shift+O"), ("hand", "Hand", "H"), ("zoom", "Zoom", "Z"),
]

OPEN_FILTER = ("Artwork (*.halo *.ai *.pdf *.svg);;Nous Halo (*.halo);;Illustrator (*.ai);;"
               "PDF (*.pdf);;SVG (*.svg);;All files (*)")


def settings():
    return QSettings("NousHalo", os.environ.get("NOUSHALO_SETTINGS", "NousHalo"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ed = Editor()
        self.setWindowIcon(QIcon(app_icon()))
        self.canvas = Canvas(self.ed)
        self.setCentralWidget(self.canvas)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks)

        self.palettes = PalettesPanel(self.ed)
        self.halo = HaloPanel(self.ed, self.palettes.palettes)
        self.layers = LayersPanel(self.ed)
        self.props = PropertiesPanel(self.ed, self.color_choices)
        self.artboards = ArtboardsPanel(self.ed)
        self.timeline = TimelinePanel(self.ed, self.export_animation_dialog)
        self.docks = {}
        self._dock("halo", "Halo", self.halo, Qt.RightDockWidgetArea)
        self._dock("layers", "Layers", self.layers, Qt.RightDockWidgetArea)
        self._dock("palettes", "Palettes", self.palettes, None)
        self._dock("props", "Properties", self.props, None)
        self._dock("artboards", "Artboards", self.artboards, None)
        self._dock("timeline", "Timeline", self.timeline, Qt.BottomDockWidgetArea)
        self.tabifyDockWidget(self.docks["halo"], self.docks["palettes"])
        self.tabifyDockWidget(self.docks["layers"], self.docks["props"])
        self.tabifyDockWidget(self.docks["layers"], self.docks["artboards"])
        self.docks["halo"].raise_()
        self.docks["layers"].raise_()
        self.resizeDocks([self.docks["halo"]], [350], Qt.Horizontal)
        self.resizeDocks([self.docks["halo"], self.docks["layers"]], [560, 340], Qt.Vertical)
        self.resizeDocks([self.docks["timeline"]], [150], Qt.Vertical)

        self._toolbox()
        self._menus()
        sb = self.statusBar()
        self.pos_label = QLabel("")
        self.zoom_label = QLabel("100%")
        sb.addPermanentWidget(self.pos_label)
        sb.addPermanentWidget(self.zoom_label)

        self.canvas.status.connect(self._status)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText("%d%%" % round(z * 100)))
        self.canvas.colorPicked.connect(self._picked)
        self.canvas.toolRequested.connect(self.set_tool)
        self.artboards.zoomRequested.connect(self.canvas.fit_artboard)
        self.palettes.librarySaved.connect(self.halo.refresh_palettes)
        self.palettes.activeChanged.connect(self._palette_activated)
        self.ed.changed.connect(self._ed_changed)
        self.set_tool("select")
        self._ed_changed("file")
        self.restore_layout()
        s = settings()
        for key, default in (("show_rulers", False), ("show_guides", True), ("snap_on", True)):
            v = s.value("view_" + key)
            self.view_toggles[key].setChecked(default if v is None else v == "true")
            setattr(self.ed, key, self.view_toggles[key].isChecked())
        QTimer.singleShot(0, self.canvas.fit_artboard)

    # ------------------------------------------------------------ layout
    def _dock(self, key, title, w, area):
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + key)
        if key in ("halo", "props", "palettes"):
            # let tall panels scroll instead of forcing the window taller than the screen
            sa = QScrollArea()
            sa.setWidget(w)
            sa.setWidgetResizable(True)
            sa.setFrameShape(QScrollArea.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            w = sa
        d.setWidget(w)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        if area is not None:
            self.addDockWidget(area, d)
        self.docks[key] = d

    def _toolbox(self):
        tb = QToolBar("Tools")
        tb.setObjectName("tools")
        tb.setOrientation(Qt.Vertical)
        tb.setIconSize(QSize(22, 22))
        tb.setMovable(False)
        self.addToolBar(Qt.LeftToolBarArea, tb)
        self.tool_actions = {}
        grp = QActionGroup(self)
        for name, label, key in TOOLS:
            a = QAction(tool_icon(name), "%s (%s)" % (label, key), self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda checked=False, n=name: self.set_tool(n))
            grp.addAction(a)
            tb.addAction(a)
            self.tool_actions[name] = a
            if name in ("direct", "line", "eraser", "artboard"):
                tb.addSeparator()
        tb.addSeparator()
        self.fillstroke = FillStroke()
        self.fillstroke.fillClicked.connect(lambda: self._pick_color(False))
        self.fillstroke.strokeClicked.connect(lambda: self._pick_color(True))
        self.fillstroke.swapClicked.connect(self.swap_colors)
        tb.addWidget(self.fillstroke)
        self.toolbox = tb

    def _menus(self):
        mb = self.menuBar()

        def act(menu, text, cb, key=None, tip=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                a.setStatusTip(tip)
            a.triggered.connect(cb)
            menu.addAction(a)
            return a

        m = mb.addMenu("&File")
        act(m, "New…", self.new_doc, QKeySequence.New)
        act(m, "Open…", self.open_dialog, QKeySequence.Open, "Open .halo, or import .ai / .pdf / .svg as a new document")
        self.recent_menu = m.addMenu("Open recent")
        m.addSeparator()
        act(m, "Place artwork (.ai, .pdf, .svg) as new artboards…", self.place_artwork)
        act(m, "Place image…", self.place_image, "Ctrl+Shift+P")
        m.addSeparator()
        act(m, "Save", self.save, QKeySequence.Save)
        act(m, "Save as…", self.save_as, QKeySequence.SaveAs)
        m.addSeparator()
        act(m, "Export PNG…", self.export_png_dialog, "Ctrl+E")
        act(m, "Export SVG…", self.export_svg_dialog)
        act(m, "Export as Illustrator (.ai)…", self.export_ai_dialog)
        act(m, "Export animation…", self.export_animation_dialog, "Ctrl+Alt+E")
        m.addSeparator()
        act(m, "Quit", self.close, QKeySequence.Quit)

        m = mb.addMenu("&Edit")
        self.undo_act = act(m, "Undo", self.ed.undo, QKeySequence.Undo)
        self.redo_act = act(m, "Redo", self.ed.redo, "Ctrl+Shift+Z")
        a = QAction(self)
        a.setShortcut(QKeySequence("Ctrl+Y"))
        a.triggered.connect(self.ed.redo)
        self.addAction(a)
        m.addSeparator()
        act(m, "Cut", self.ed.cut, QKeySequence.Cut)
        act(m, "Copy", self.ed.copy, QKeySequence.Copy)
        act(m, "Paste", lambda: self.ed.paste(), QKeySequence.Paste)
        act(m, "Paste in place", lambda: self.ed.paste(in_place=True), "Ctrl+Shift+V")
        act(m, "Duplicate", self.ed.duplicate, "Ctrl+D")
        act(m, "Delete", self.ed.delete_selection)
        m.addSeparator()
        act(m, "Select all", self.ed.select_all, QKeySequence.SelectAll)
        act(m, "Deselect", lambda: self.ed.select([]), "Ctrl+Shift+A")

        m = mb.addMenu("&Object")
        ar = m.addMenu("Arrange")
        act(ar, "Bring to front", lambda: self.ed.arrange("front"), "Ctrl+Shift+]")
        act(ar, "Bring forward", lambda: self.ed.arrange("forward"), "Ctrl+]")
        act(ar, "Send backward", lambda: self.ed.arrange("backward"), "Ctrl+[")
        act(ar, "Send to back", lambda: self.ed.arrange("back"), "Ctrl+Shift+[")
        act(m, "Group", self.ed.group_selection, "Ctrl+G")
        act(m, "Ungroup", self.ed.ungroup_selection, "Ctrl+Shift+G")
        cm = m.addMenu("Clipping mask")
        act(cm, "Make (top object clips the rest)", self.make_clip, "Ctrl+7")
        act(cm, "Release", self.ed.release_clipping_mask, "Ctrl+Alt+7")
        act(m, "Edit selected group on its own", self.isolate_selected)
        act(m, "Leave group editing", lambda: self.ed.exit_isolation())
        m.addSeparator()
        act(m, "Flip horizontal", lambda: self.ed.flip(True))
        act(m, "Flip vertical", lambda: self.ed.flip(False))
        m.addSeparator()
        act(m, "Text to outlines", self.ed.text_to_outlines, "Ctrl+Shift+O")
        act(m, "Image Trace…", self.image_trace, "Ctrl+Alt+T")
        act(m, "Rasterize", self.ed.rasterize_selection)
        m.addSeparator()
        act(m, "New layer", lambda: self.ed.new_layer(), "Ctrl+L")
        act(m, "New paint layer", self.ed.new_raster_layer, "Ctrl+Shift+L")

        m = mb.addMenu("&Colour")
        act(m, "Halo wheel", lambda: self._show_dock("halo"), "Ctrl+Shift+H")
        act(m, "Palettes", lambda: self._show_dock("palettes"))
        m.addSeparator()
        act(m, "Swap fill and stroke", self.swap_colors, "X")
        act(m, "Fill: none", lambda: self.ed.set_fill(None))
        act(m, "Stroke: none", lambda: self.ed.set_stroke(None))
        m.addSeparator()
        act(m, "Snap selection to locked palette", self.snap_selection)
        act(m, "Conform to palette…", self.halo.conform_dialog)

        m = mb.addMenu("&View")
        act(m, "Zoom in", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25), QKeySequence.ZoomIn)
        a = QAction(self)
        a.setShortcut(QKeySequence("Ctrl+="))
        a.triggered.connect(lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        self.addAction(a)
        act(m, "Zoom out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25), QKeySequence.ZoomOut)
        act(m, "Fit artboard", self.canvas.fit_artboard, "Ctrl+0")
        act(m, "Fit everything", self.canvas.fit_all, "Ctrl+Alt+0")
        act(m, "Actual size", lambda: self.canvas.set_zoom(1.0), "Ctrl+1")
        m.addSeparator()
        self.view_toggles = {}
        for key, label, sc in (("show_rulers", "Rulers", "Ctrl+R"),
                               ("show_guides", "Guides", "Ctrl+;"),
                               ("lock_guides", "Lock guides", "Ctrl+Alt+;"),
                               ("snap_on", "Snap to guides and artboards", "Ctrl+Shift+;")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(sc))
            a.toggled.connect(lambda on, k=key: self.set_view_toggle(k, on))
            m.addAction(a)
            self.view_toggles[key] = a
        act(m, "Clear guides", self.clear_guides)
        m.addSeparator()
        for key in ("halo", "palettes", "layers", "props", "artboards", "timeline"):
            m.addAction(self.docks[key].toggleViewAction())
        m.addSeparator()
        act(m, "Reset panel layout", self.reset_layout)

        m = mb.addMenu("&Help")
        act(m, "Keyboard shortcuts", self.show_shortcuts, "F1")
        act(m, "About Nous Halo", self.about)
        self.refresh_recent()

    def set_view_toggle(self, key, on):
        setattr(self.ed, key, on)
        if key != "lock_guides":
            settings().setValue("view_" + key, "true" if on else "false")
        self.canvas.update()
        if key == "show_rulers" and on:
            self.statusBar().showMessage("Drag from a ruler to make a guide; drag it back to remove it", 6000)

    def clear_guides(self):
        if self.ed.doc.guides:
            self.ed.checkpoint()
            self.ed.doc.guides = []
            self.ed.notify("doc")

    def _show_dock(self, key):
        d = self.docks[key]
        d.show()
        d.raise_()

    def restore_layout(self):
        s = settings()
        self._default_state = self.saveState()
        g = s.value("geometry")
        if isinstance(g, QByteArray):
            self.restoreGeometry(g)
        else:
            scr = QApplication.primaryScreen().availableGeometry()
            self.resize(int(scr.width() * 0.88), int(scr.height() * 0.88))
            self.move(scr.x() + int(scr.width() * 0.06), scr.y() + int(scr.height() * 0.04))
        st = s.value("state")
        if isinstance(st, QByteArray):
            self.restoreState(st)

    def reset_layout(self):
        self.restoreState(self._default_state)
        for d in self.docks.values():
            d.show()

    # ------------------------------------------------------------ state
    def _ed_changed(self, kind):
        if kind in ("tool", "selection", "doc", "file"):
            self.fillstroke.set_colors(self.ed.fill, self.ed.stroke)
        if kind in ("doc", "file"):
            self.update_title()
        if kind == "file":
            self.halo.refresh_palettes()
            self.palettes.reload_combo()

    def update_title(self):
        name = os.path.basename(self.ed.path) if self.ed.path else getattr(self, "_untitled", "Untitled")
        self.setWindowTitle("%s%s — %s" % (name, " •" if self.ed.dirty else "", APP))

    def _status(self, msg):
        if msg.startswith("x "):
            self.pos_label.setText(msg)
        else:
            self.statusBar().showMessage(msg, 4000)

    def set_tool(self, name):
        if name == "framemove" and not self.ed.doc.frames:
            self.statusBar().showMessage("Frame Move works on animation frames: add one with “+ Frame” in the Timeline", 5000)
        self.canvas.set_tool(name)
        self.tool_actions[name].setChecked(True)
        if name in ("brush", "eraser"):
            self._show_dock("props")

    def color_choices(self):
        lock = self.ed.lock_colors()
        if lock:
            return lock, True
        p = self.palettes.current()
        return (p.colors if p else []), False

    def _pick_color(self, stroke):
        colors, locked = self.color_choices()
        cur = self.ed.stroke if stroke else self.ed.fill
        pop = PalettePopup(colors, locked, True, self, cur)
        pop.picked.connect(lambda c: self.ed.set_stroke(c) if stroke else self.ed.set_fill(c))
        pop.adjustSize()
        pop.move(self.fillstroke.mapToGlobal(self.fillstroke.rect().topRight()))
        pop.show()

    def _picked(self, col, stroke):
        col = self.ed.snap(col)
        if stroke:
            self.ed.set_stroke(col, apply=False)
        else:
            self.ed.set_fill(col, apply=False)
        self.statusBar().showMessage("%s set to %s" % ("Stroke" if stroke else "Fill", col), 4000)

    def _palette_activated(self, name):
        if not self.ed.doc.lock_palette:
            self.halo.select_palette(name)

    def swap_colors(self):
        f, s = self.ed.fill, self.ed.stroke
        items = self.ed.selected_items()
        if items:
            from halo_model import PathItem, TextItem
            self.ed.checkpoint()
            for it in items:
                if isinstance(it, PathItem):
                    it.fill, it.stroke = it.stroke, it.fill
                elif isinstance(it, TextItem) and it.stroke:
                    it.color, it.stroke = it.stroke, it.color
                it.touch()
            self.ed.notify("doc")
        self.ed.fill, self.ed.stroke = s, f
        self.ed.changed.emit("tool")

    def image_trace(self):
        from halo_model import RasterItem
        from halo_tracedlg import TraceDialog
        rasters = [it for it in self.ed.selected_leaves() if isinstance(it, RasterItem)]
        if not rasters:
            self.statusBar().showMessage("Select a placed image or paint object to trace (File → Place image…)", 6000)
            return
        dlg = TraceDialog(self.ed, rasters[-1], self.color_choices, self)
        dlg.resize(1100, 680)
        if dlg.exec() == QDialog.Accepted:
            g = dlg.run()
            if g is None:
                self.statusBar().showMessage("Nothing to trace at that threshold", 6000)
            else:
                self.statusBar().showMessage("Traced %d shapes into “%s”" % (len(g.children), g.name), 6000)

    def make_clip(self):
        if self.ed.make_clipping_mask() is None:
            self.statusBar().showMessage("Select two or more objects; the topmost must be a path (it becomes the mask)", 6000)

    def isolate_selected(self):
        from halo_model import GroupItem
        gs = [it for it in self.ed.selected_items() if isinstance(it, GroupItem)]
        if gs:
            self.ed.enter_isolation(gs[-1])

    def snap_selection(self):
        if not self.ed.lock_colors():
            QMessageBox.information(self, APP, "Lock the piece to a palette first (Halo panel).")
            return
        items = self.ed.selected_items()
        if not items:
            return
        n = self.ed.conform(items, rasters=False)
        self.statusBar().showMessage("%d colour%s snapped" % (n, "" if n == 1 else "s"), 4000)

    # ------------------------------------------------------------ files
    def maybe_save(self):
        if not self.ed.dirty:
            return True
        r = QMessageBox.question(self, APP, "Save changes to %s?" % (
            os.path.basename(self.ed.path) if self.ed.path else getattr(self, "_untitled", "Untitled")),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_doc(self):
        if not self.maybe_save():
            return
        w, h, name = size_dialog(self, "New document", 1024, 1024, "Artboard 1")
        if w is None:
            return
        d = Document.blank(w, h)
        d.artboards[0].name = name
        self._untitled = "Untitled"
        self.ed.set_doc(d)
        self.update_title()
        QTimer.singleShot(0, self.canvas.fit_artboard)

    def open_dialog(self):
        if not self.maybe_save():
            return
        start = settings().value("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Open", start, OPEN_FILTER)
        if path:
            self.open_path(path, ask=False)

    def open_path(self, path, ask=True):
        if ask and not self.maybe_save():
            return False
        settings().setValue("last_dir", os.path.dirname(path))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if path.lower().endswith(".halo"):
                doc = Document.load(path)
                self.ed.set_doc(doc, path)
            else:
                doc = import_file(path)
                self._untitled = os.path.splitext(os.path.basename(path))[0] + ".halo"
                self.ed.set_doc(doc, None)
        except (ImportError_, Exception) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP, str(e) if isinstance(e, ImportError_) else
                                "Couldn't open %s:\n%s" % (os.path.basename(path), e))
            return False
        QApplication.restoreOverrideCursor()
        self.add_recent(path)
        self.update_title()
        QTimer.singleShot(0, self.canvas.fit_all if len(self.ed.doc.artboards) > 1 else self.canvas.fit_artboard)
        self.after_import(path, doc)
        return True

    def after_import(self, path, doc):
        """A quiet status line normally; a popup only when something was left out."""
        if not path.lower().endswith(".halo"):
            how = " (%s)" % doc.import_info if doc.import_info else ""
            self.statusBar().showMessage("Opened %s%s. Save keeps it as a .halo file." % (
                os.path.basename(path), how), 8000)
        if doc.import_notes:
            QMessageBox.information(self, "Opened %s" % os.path.basename(path),
                                    "Everything came across except:\n\n• " + "\n\n• ".join(doc.import_notes))
        doc.import_notes = []

    def place_artwork(self):
        start = settings().value("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Place artwork", start, "Artwork (*.ai *.pdf *.svg)")
        if not path:
            return
        self.ed.checkpoint()
        n = len(self.ed.doc.artboards)
        try:
            import_file(path, self.ed.doc)
        except Exception as e:
            self.ed.undo()
            QMessageBox.warning(self, APP, str(e))
            return
        self.ed.current_artboard_id = self.ed.doc.artboards[n].id
        self.ed.notify("doc")
        self.canvas.fit_all()
        self.after_import(path, self.ed.doc)

    def place_image(self):
        start = settings().value("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Place image", start, "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        try:
            it = import_image(path, self.ed.doc, self.ed.current_artboard())
        except Exception as e:
            QMessageBox.warning(self, APP, str(e))
            return
        self.ed.add_item(it)

    def save(self):
        if not self.ed.path:
            return self.save_as()
        return self._write(self.ed.path)

    def save_as(self):
        start = self.ed.path or os.path.join(settings().value("last_dir", os.path.expanduser("~")),
                                             getattr(self, "_untitled", "Untitled.halo"))
        if not start.lower().endswith(".halo"):
            start = os.path.splitext(start)[0] + ".halo"
        path, _ = QFileDialog.getSaveFileName(self, "Save as", start, "Nous Halo (*.halo)")
        if not path:
            return False
        if not path.lower().endswith(".halo"):
            path += ".halo"
        return self._write(path)

    def _write(self, path):
        try:
            self.canvas.finish_text()
            self.ed.doc.save(path)
        except Exception as e:
            QMessageBox.warning(self, APP, "Couldn't save:\n%s" % e)
            return False
        self.ed.path = path
        self.ed.dirty = False
        self.add_recent(path)
        self.update_title()
        self.statusBar().showMessage("Saved " + path, 4000)
        return True

    def add_recent(self, path):
        s = settings()
        rec = [p for p in (s.value("recent") or []) if p != path]
        rec.insert(0, path)
        s.setValue("recent", rec[:10])
        self.refresh_recent()

    def refresh_recent(self):
        self.recent_menu.clear()
        rec = settings().value("recent") or []
        if isinstance(rec, str):
            rec = [rec]
        for p in rec:
            self.recent_menu.addAction(p, lambda p=p: self.open_path(p))
        self.recent_menu.setEnabled(bool(rec))

    # ------------------------------------------------------------ exports
    def export_png_dialog(self):
        d = QDialog(self)
        d.setWindowTitle("Export PNG")
        f = QFormLayout(d)
        lst = QListWidget()
        cur = self.ed.current_artboard()
        for ab in self.ed.doc.artboards:
            it = QListWidgetItem("%s  (%d × %d)" % (ab.name, ab.rect.width(), ab.rect.height()))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if ab is cur else Qt.Unchecked)
            lst.addItem(it)
        lst.setMaximumHeight(140)
        f.addRow("Artboards", lst)
        scale = QDoubleSpinBox()
        scale.setRange(0.05, 16)
        scale.setDecimals(2)
        scale.setValue(float(settings().value("png_scale", 1.0)))
        scale.setSuffix(" ×")
        f.addRow("Scale", scale)
        transp = QCheckBox("Transparent background")
        transp.setChecked(True)
        f.addRow(transp)
        frame = None
        if self.ed.doc.frames:
            frame = QCheckBox("As in the current animation frame (%d)" % ((self.ed.frame_index or 0) + 1))
            frame.setChecked(True)
            f.addRow(frame)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return
        abs_ = [ab for i, ab in enumerate(self.ed.doc.artboards) if lst.item(i).checkState() == Qt.Checked]
        if not abs_:
            return
        settings().setValue("png_scale", scale.value())
        fr = self.ed.frame() if frame is not None and frame.isChecked() else None
        start = settings().value("export_dir", os.path.expanduser("~"))
        try:
            if len(abs_) == 1:
                path, _ = QFileDialog.getSaveFileName(self, "Export PNG", os.path.join(start, safe(abs_[0].name) + ".png"),
                                                      "PNG (*.png)")
                if not path:
                    return
                settings().setValue("export_dir", os.path.dirname(path))
                export_png(self.ed.doc, abs_[0], path, scale.value(), transp.isChecked(), fr)
                files = [path]
            else:
                folder = QFileDialog.getExistingDirectory(self, "Export PNGs to folder", start)
                if not folder:
                    return
                settings().setValue("export_dir", folder)
                files = export_artboards(self.ed.doc, abs_, folder, scale.value(), transp.isChecked(), fr)
        except Exception as e:
            QMessageBox.warning(self, APP, "Export failed:\n%s" % e)
            return
        self.statusBar().showMessage("Exported %d PNG%s" % (len(files), "" if len(files) == 1 else "s"), 5000)

    def export_svg_dialog(self):
        ab = self.ed.current_artboard()
        start = settings().value("export_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getSaveFileName(self, "Export SVG (current artboard)",
                                              os.path.join(start, safe(ab.name) + ".svg"), "SVG (*.svg)")
        if not path:
            return
        settings().setValue("export_dir", os.path.dirname(path))
        try:
            export_svg(self.ed.doc, ab, path)
        except Exception as e:
            QMessageBox.warning(self, APP, "Export failed:\n%s" % e)
            return
        self.statusBar().showMessage("Exported " + path, 5000)

    def export_ai_dialog(self):
        from halo_aiexport import export_ai
        base = os.path.splitext(os.path.basename(self.ed.path or getattr(self, "_untitled", "Untitled")))[0]
        start = settings().value("export_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getSaveFileName(self, "Export as Illustrator (.ai)", os.path.join(start, base + ".ai"),
                                              "Illustrator, PDF compatible (*.ai)")
        if not path:
            return
        if not path.lower().endswith(".ai"):
            path += ".ai"
        settings().setValue("export_dir", os.path.dirname(path))
        self.canvas.finish_text()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            export_ai(self.ed.doc, path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP, "Export failed:\n%s" % e)
            return
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage("Exported %s (every artboard, layers and live text; animation frames "
                                     "and palette locks stay in the .halo)" % os.path.basename(path), 8000)

    def export_animation_dialog(self):
        if not self.ed.doc.frames:
            QMessageBox.information(self, APP, "There are no frames yet. Add some in the Timeline.")
            return
        d = QDialog(self)
        d.setWindowTitle("Export animation")
        f = QFormLayout(d)
        abc = QComboBox()
        for ab in self.ed.doc.artboards:
            abc.addItem(ab.name, ab.id)
        abc.setCurrentIndex(abc.findData(self.ed.current_artboard().id))
        f.addRow("Artboard", abc)
        name = QLineEdit(safe(self.ed.current_artboard().name).replace(" ", "_"))
        f.addRow("File name", name)
        scale = QDoubleSpinBox()
        scale.setRange(0.05, 16)
        scale.setDecimals(2)
        scale.setValue(float(settings().value("anim_scale", 1.0)))
        scale.setSuffix(" ×")
        f.addRow("Scale", scale)
        seq = QCheckBox("One PNG per frame")
        seq.setChecked(settings().value("anim_seq", "true") == "true")
        sheet = QCheckBox("Sprite sheet PNG + JSON")
        sheet.setChecked(settings().value("anim_sheet", "true") == "true")
        f.addRow(seq)
        f.addRow(sheet)
        cols = QSpinBox()
        cols.setRange(0, 256)
        cols.setSpecialValueText("auto (square-ish)")
        cols.setValue(int(settings().value("anim_cols", 0)))
        f.addRow("Sheet columns", cols)
        pad = QSpinBox()
        pad.setRange(0, 64)
        pad.setSuffix(" px")
        pad.setValue(int(settings().value("anim_pad", 0)))
        f.addRow("Padding", pad)
        transp = QCheckBox("Transparent background")
        transp.setChecked(True)
        f.addRow(transp)
        info = QLabel("%d frames · %.2f s" % (len(self.ed.doc.frames), sum(fr.delay for fr in self.ed.doc.frames) / 1000))
        info.setStyleSheet("color:#9aa3b5")
        f.addRow(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted or not (seq.isChecked() or sheet.isChecked()):
            return
        s = settings()
        s.setValue("anim_scale", scale.value())
        s.setValue("anim_seq", "true" if seq.isChecked() else "false")
        s.setValue("anim_sheet", "true" if sheet.isChecked() else "false")
        s.setValue("anim_cols", cols.value())
        s.setValue("anim_pad", pad.value())
        folder = QFileDialog.getExistingDirectory(self, "Export animation to folder",
                                                  s.value("export_dir", os.path.expanduser("~")))
        if not folder:
            return
        s.setValue("export_dir", folder)
        ab = next(a for a in self.ed.doc.artboards if a.id == abc.currentData())
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            files = export_animation(self.ed.doc, ab, folder, safe(name.text().strip() or "animation"),
                                     scale.value(), seq.isChecked(), sheet.isChecked(), cols.value(),
                                     pad.value(), transp.isChecked())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP, "Export failed:\n%s" % e)
            return
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage("Exported %d files to %s" % (len(files), folder), 6000)

    # ------------------------------------------------------------ help
    def show_shortcuts(self):
        rows = "".join("<tr><td><b>%s</b></td><td>%s</td></tr>" % (k, label) for _, label, k in TOOLS)
        more = [("Space-drag / middle-drag", "Pan"), ("Ctrl + wheel", "Zoom at the cursor"),
                ("[  ]", "Brush size"), ("X", "Swap fill and stroke"), ("Shift", "Constrain (drag, rotate, shapes, lines)"),
                ("Alt-drag (Selection)", "Duplicate while moving"), ("Alt (scale handles)", "Scale from the centre"),
                ("Pen: click / drag", "Corner / smooth anchor; click the first anchor to close; Enter ends"),
                ("Pen on a selected path", "Click a segment to add an anchor, an anchor to remove it, Alt-click to toggle smooth"),
                ("Direct Selection", "Drag anchors/handles; Alt-drag a handle breaks it; Delete removes anchors"),
                ("Double-click text", "Edit it (Esc or Ctrl+Enter to finish)"),
                ("Ctrl+G / Ctrl+Shift+G", "Group / ungroup"),
                ("Double-click a group", "Edit inside it (others dim); Esc steps back out"),
                ("Ctrl+7 / Ctrl+Alt+7", "Make / release clipping mask (top path clips the rest)"),
                ("Halo wheel", "Drag a dot: one colour. Drag empty space: spin all. Shift: hue only. Wheel over a dot: lightness"),
                ("Ctrl+0 / Ctrl+1", "Fit artboard / actual size"),
                ("Ctrl+R", "Rulers; drag from one to make a guide, drag a guide back to remove it"),
                ("Ctrl+; / Ctrl+Alt+; / Ctrl+Shift+;", "Show guides / lock guides / snapping"),
                ("Double-click a guide", "Type its exact position, or delete it")]
        rows += "".join("<tr><td><b>%s</b></td><td>%s</td></tr>" % kv for kv in more)
        d = QDialog(self)
        d.setWindowTitle("Keyboard shortcuts")
        lay = QVBoxLayout(d)
        tb = QTextBrowser()
        tb.setHtml("<table cellspacing=6>%s</table>" % rows)
        lay.addWidget(tb)
        d.resize(620, 560)
        d.exec()

    def about(self):
        QMessageBox.about(self, "About Nous Halo",
                          "<b>Nous Halo</b> %s<br>Vector drawing, a colour wheel for hue shifting and "
                          "palettes, and frame animation.<br><br>Opens Illustrator files saved with PDF "
                          "compatibility (the default). Built with Qt and PyMuPDF." % version())

    def closeEvent(self, ev):
        self.canvas.finish_text()
        if not self.maybe_save():
            ev.ignore()
            return
        s = settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState())
        ev.accept()


def apply_theme(app):
    app.setStyle("Fusion")
    p = QPalette()
    c = {"bg": "#2e3440", "base": "#272c36", "alt": "#3b4252", "text": "#e5e9f0", "mid": "#434c5e",
         "hi": "#5e81ac", "dis": "#6b7385"}
    p.setColor(QPalette.Window, QColor(c["bg"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["base"]))
    p.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["alt"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.Button, QColor(c["alt"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.Highlight, QColor(c["hi"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Mid, QColor(c["mid"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["dis"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["dis"]))
    app.setPalette(p)
    app.setStyleSheet("QDockWidget::title{background:#3b4252;padding:4px 6px}"
                      "QToolBar{background:#2e3440;border:none;spacing:2px;padding:3px}"
                      "QToolButton:checked{background:#4c566a;border-radius:4px}"
                      "QToolTip{border:1px solid #4c566a}")


class HaloApp(QApplication):
    """On macOS a file opened from Finder (double-click, Open With, drag onto
    the Dock icon) arrives as a FileOpen event rather than an argument."""
    window = None
    pending = None

    def event(self, e):
        if e.type() == QEvent.FileOpen and e.file():
            if self.window is not None:
                self.window.open_path(e.file())
                self.window.raise_()
                self.window.activateWindow()
            else:
                self.pending = e.file()
            return True
        return super().event(e)


def main():
    app = HaloApp(sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName("NousHalo")
    apply_theme(app)
    w = MainWindow()
    app.window = w
    w.show()
    start = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else app.pending
    if start:
        QTimer.singleShot(50, lambda: w.open_path(start, ask=False))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
