"""Native Illustrator data (EPS / Photopea .ai): import each private fixture,
render it, and put the file's own embedded thumbnail beside it."""
from helpers import *
import base64, re
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import Qt
from halo_import import import_file
from halo_render import render_artboard

names = fixtures("native")
ok = True
for n in names:
    src = os.path.join(FIX, n)
    if not os.path.exists(src):
        print("skip (no fixture)", n); continue
    doc = import_file(fixture(n))
    from halo_model import GroupItem
    items = sum(1 for _ in doc.all_items())
    groups = sum(1 for _, _, it in doc.walk() if isinstance(it, GroupItem))
    ab = doc.artboards[0]
    s = 500 / max(ab.rect.width(), ab.rect.height())
    ours = render_artboard(doc, ab, s, transparent=False)
    thumb = QImage()
    m = re.search(rb"<xmpGImg:image>([^<]+)</xmpGImg:image>", open(src, "rb").read())
    if m:
        thumb.loadFromData(base64.b64decode(m.group(1).replace(b"&#xA;", b"")))
    out = QImage(1020, 520, QImage.Format_RGB32); out.fill(QColor("#555"))
    p = QPainter(out); p.drawImage(5, 5, ours)
    if not thumb.isNull():
        p.drawImage(515, 5, thumb.scaled(500, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    p.end()
    out.save(os.path.join(OUT, "native_" + os.path.splitext(n)[0][:30] + ".png"))
    print("%-45s artboard %5.0fx%-5.0f layers %d objects %5d groups %4d  %s" % (n[:45], ab.rect.width(), ab.rect.height(), len(doc.layers), items, groups, " | ".join(doc.import_notes)))
    ok = ok and items > 0
print("OK" if ok else "FAIL")
