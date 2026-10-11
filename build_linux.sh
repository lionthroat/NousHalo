#!/usr/bin/env bash
# Builds nous-halo_<version>_amd64.deb for Linux Mint / Ubuntu / Debian.
# Run on Linux from the project folder, with the venv set up
# (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt):
#   bash build_linux.sh
# GitHub builds it on version tags (see .github/workflows/build.yml).
#
# The package installs the app to /opt/nous-halo, a `nous-halo` command,
# a menu entry, icons, a file type for .halo documents (which open in it),
# and offers it for .ai and .svg files in Open With.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
VERSION=$(tr -d '[:space:]' < VERSION)
PKG=nous-halo

# --strip drops debug symbols (libpython alone goes from 32 MB to a few)
"$PY" -m PyInstaller --noconfirm --clean --windowed --name NousHalo --strip \
    --add-data "$PWD/icon.ico:." --add-data "$PWD/VERSION:." --add-data "$PWD/assets:assets" \
    --distpath dist --workpath build --specpath build \
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml \
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf \
    "$PWD/noushalo.py"

# Qt drags in pieces this app never uses (Wayland, EGLFS/framebuffer/VNC
# platforms, networking, SVG, OpenGL helpers, translations). Drop them, as
# build.ps1 does on Windows. Kept: the X11 (xcb) platform, the GTK and portal
# themes (native file dialogs), input methods (compose key, IBus), ICU (Qt Core
# needs it) and the image formats Place image reads (JPEG, GIF, WebP, ICO).
# Under a Wayland session Qt falls back to X11 (XWayland).
INT=dist/NousHalo/_internal
QT=$INT/PySide6/Qt
rm -rf "$QT/translations" \
    "$QT"/lib/libQt6{Network,OpenGL,Svg,WaylandClient,WlShellIntegration}.so* \
    "$QT"/lib/libQt6{EglFS,EglFs,Quick,Qml,Pdf,VirtualKeyboard}*.so* \
    "$QT"/plugins/{egldeviceintegrations,generic,iconengines,tls,networkinformation,wayland-*}
find "$QT/plugins/platforms" -type f ! -name libqxcb.so -delete
find "$QT/plugins/imageformats" -type f ! -name libqico.so ! -name libqjpeg.so ! -name libqgif.so ! -name libqwebp.so -delete

# PyInstaller also copies the build machine's own system libraries (GTK,
# GLib, X11, fontconfig, OpenSSL, the C++ runtime...). Use the user's instead
# (they're in Depends below): smaller, and an old bundled GLib or C++ runtime
# can clash with a newer system's GTK modules or graphics drivers.
find "$INT" -maxdepth 1 -name 'lib*.so*' ! -name 'libpython3*' -delete

# Nothing left behind may need something that was just deleted
MISSING=$(find dist/NousHalo -type f \( -name '*.so*' -o -name NousHalo \) -print0 |
    xargs -0 ldd 2>/dev/null | awk '/:$/ { f = $0 } /not found/ { print f, $0 }' || true)
if [ -n "$MISSING" ]; then echo "$MISSING"; echo "Pruned too much"; exit 1; fi

STAGE=build/deb
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/mime/packages"
cp -a dist/NousHalo "$STAGE/opt/$PKG"
ln -s "/opt/$PKG/NousHalo" "$STAGE/usr/bin/$PKG"

# Icons at the sizes desktops look for
QT_QPA_PLATFORM=offscreen "$PY" - "$STAGE" "$PKG" <<'EOF'
import os, sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
app = QGuiApplication(sys.argv[:1])
stage, pkg = sys.argv[1], sys.argv[2]
src = QImage("icon.png")
for size in (48, 64, 128, 256, 512):
    d = os.path.join(stage, f"usr/share/icons/hicolor/{size}x{size}/apps")
    os.makedirs(d, exist_ok=True)
    src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(os.path.join(d, pkg + ".png"))
EOF

# .halo is a file type of its own (the system mime database picks this up)
cat > "$STAGE/usr/share/mime/packages/$PKG.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-nous-halo">
    <comment>Nous Halo document</comment>
    <glob pattern="*.halo"/>
    <sub-class-of type="application/zip"/>
    <icon name="$PKG"/>
  </mime-type>
</mime-info>
EOF

cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Nous Halo
GenericName=Vector drawing
Comment=Vector drawing with a hue-shifting colour wheel, palettes and frame animation; opens Illustrator files
Exec=$PKG %f
Icon=$PKG
Terminal=false
Categories=Graphics;VectorGraphics;
MimeType=application/x-nous-halo;application/illustrator;image/svg+xml;
Keywords=ai;illustrator;svg;vector;palette;hue;animation;sprite;
StartupWMClass=NousHalo
EOF

SIZE_KB=$(du -sk "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: lionthroat <lionthroat@users.noreply.github.com>
Homepage: https://noushalo.lionthroat.com
Installed-Size: $SIZE_KB
Depends: libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1, libxcb-util1, libxcb-shm0, libxcb-sync1, libxcb-xfixes0, libxcb-render0, libxcb1, libx11-6, libx11-xcb1, libxkbcommon0, libxkbcommon-x11-0, libegl1, libgl1, libfontconfig1, libfreetype6, libdbus-1-3, libglib2.0-0, libgtk-3-0, libstdc++6, libssl3, libffi8, zlib1g, libzstd1, libbz2-1.0, liblzma5
Description: Vector drawing with a hue-shifting colour wheel
 Nous Halo opens Illustrator (.ai) files with their artboards, layers, groups
 and text, and puts hue shifting front and centre: move one colour or spin the
 whole palette around a wheel, lock a piece to a palette, trace scans, and
 animate with a frame timeline. Free and open source (AGPL-3.0).
EOF

# Files root-owned and world-readable, whatever the build machine's umask was
chmod -R u+rwX,go+rX,go-w "$STAGE"
OUT="${PKG}_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"
