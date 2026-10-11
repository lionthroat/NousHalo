from helpers import *
import noushalo
from PySide6.QtCore import QTimer
noushalo.apply_theme(app)
w = noushalo.MainWindow()
w.resize(1500, 950)
w.show(); pump()
assert w.open_path(fixture(fixtures("pdf")[0]), ask=False)
pump(10)
w.canvas.fit_artboard(); pump(10)
w.grab().save(os.path.join(OUT, "smoke_window.png"))
print("layers", [l.name for l in w.ed.doc.layers], "items", sum(len(l.items) for l in w.ed.doc.layers))
print("halo dots", len(w.halo.wheel.entries))
w.ed.dirty = False
w.close()
print("OK")
