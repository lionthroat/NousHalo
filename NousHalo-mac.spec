# PyInstaller recipe for the macOS app bundle, "Nous Halo.app".
# Built by .github/workflows/build.yml on GitHub's Mac machines:
#   python -m PyInstaller --noconfirm --clean NousHalo-mac.spec
# (needs Pillow, which turns icon.png into the .icns macOS wants)
import os

root = SPECPATH
with open(os.path.join(root, "VERSION")) as f:
    VERSION = f.read().strip()

a = Analysis(
    [os.path.join(root, "noushalo.py")],
    datas=[(os.path.join(root, "icon.ico"), "."), (os.path.join(root, "VERSION"), "."),
           (os.path.join(root, "assets"), "assets")],
    excludes=["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
              "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NousHalo", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="NousHalo")
app = BUNDLE(
    coll,
    name="Nous Halo.app",
    icon=os.path.join(root, "icon.png"),
    bundle_identifier="com.lionthroat.noushalo",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Nous Halo",
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        # .halo is Nous Halo's own document type
        "UTExportedTypeDeclarations": [{
            "UTTypeIdentifier": "com.lionthroat.halo",
            "UTTypeDescription": "Nous Halo document",
            "UTTypeConformsTo": ["public.data", "public.composite-content"],
            "UTTypeTagSpecification": {"public.filename-extension": ["halo"]},
        }],
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Nous Halo document",
                "CFBundleTypeRole": "Editor",
                "LSItemContentTypes": ["com.lionthroat.halo"],
                "LSHandlerRank": "Owner",
            },
            {
                # offered in "Open With" for Illustrator files and SVG, never the default unless chosen
                "CFBundleTypeName": "Artwork",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["com.adobe.illustrator.ai-image", "public.svg-image"],
                "CFBundleTypeExtensions": ["ai", "svg"],
                "LSHandlerRank": "Alternate",
            },
        ],
    },
)
