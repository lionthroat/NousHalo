# Nous Halo

A desktop drawing program for leaving Adobe Illustrator: it opens `.ai` files
with their layers, artboards, vector shapes, text and images, puts **hue
shifting** front and centre, keeps a piece **locked to a palette**, and has a
**frame timeline** for simple animations exported as PNGs and sprite sheets.

Built with Python, Qt (PySide6), PyMuPDF and NumPy.

## Opening Illustrator files

| What's in the file | How Nous Halo reads it |
|---|---|
| Normal `.ai` (saved with "Create PDF Compatible File", Illustrator's default) | The PDF copy inside: every artboard, layer (hidden layers stay hidden), path, clipping mask, image and text line |
| `.ai` that is really an EPS (Adobe Stock downloads), and `.eps` saved by Illustrator | Illustrator's own drawing data, carried inside the EPS |
| `.ai` exported from Photopea | Illustrator's own drawing data (the PDF page is empty) |
| `.pdf`, `.svg` | Same path as a normal `.ai` |

Groups, clipping groups and sublayers come in too. For a normal `.ai` they're
read from Illustrator's own data (which the PDF copy lacks) and matched to
the PDF shapes by size and position; if a page can't be matched it comes in
ungrouped, looking the same.

Use **File → Open** to start a new document from a file, or **File → Place
artwork** to add a file's artboards to the open document. Save as `.halo` to
keep your work (a zip of JSON + PNGs + any fonts carried in from imports).

What doesn't come across:

- **Gradients and mesh fills** (none of the files checked so far use them). In a
  normal `.ai` the shape is missing; in EPS/Photopea data it comes in grey.
  The import report says how many.
- **Blend modes and effects** (drop shadows, blurs) aren't applied.
- **Fonts that aren't installed**: text stays editable and keeps its original
  letter shapes until you edit it, then switches to a stand-in (pick a font in
  Properties). Each line of imported text is its own text object.

## The Halo panel (hue shifting)

Every colour in scope is a dot on a colour wheel; bigger dots are used more.

- **Drag a dot** to change that one colour: around the ring for hue, in and
  out for saturation. Shift-click to move several. Scroll over a dot for lightness.
- **Drag empty space** (or the **Spin all** slider) to turn the whole palette
  around the wheel. With **Linked** on, dragging any dot spins them all.
- **Saturation** and **Lightness** sliders adjust everything at once.
- **Scope**: whole document, current artboard, current layer or selection.
- **Perceptual** mode (OKLCH) keeps each colour's lightness as its hue turns;
  **Classic HSB** behaves like Illustrator's wheel.
- **Images & paint** are included: their main colours show as ringed dots;
  moving one shifts that band of hues in the pixels (**Image reach** sets how wide).
- The canvas previews live; nothing changes until **Apply** (one undo step).

### Palettes

- **Lock piece to this palette** (Halo panel): colour pickers offer only the
  palette, Halo results snap to it, the eyedropper snaps, and a counter shows
  colours that are off-palette.
- **Conform…** snaps everything in scope to the palette, including a pixel by
  pixel remap of images and paint, with optional dithering for soft gradients.
- The **Palettes** panel builds palettes from the document, the selection,
  an image's dominant colours, or a harmony of the fill colour (complementary,
  analogous, triad, split, square, monochrome, and a **hue-shifted ramp** that
  leans cool in the shadows and warm in the highlights).
- Imports/exports Adobe `.ase` swatches, GIMP `.gpl`, `.hex` lists, and a PNG strip.
  The library lives in `%APPDATA%\NousHalo\palettes`.

## Timeline

Frame animation like Photoshop's frame mode:

- **+ Frame** copies the current frame. In each frame, every layer can be
  shown or hidden, offset, faded, and hue-spun (set them in the Timeline's side
  panel, toggle the eye in Layers, or drag with the **Frame Move** tool, `N`).
- **Tween…** fills in frames between this frame and the next, easing offset,
  opacity and hue (a layer that appears or vanishes fades). Tween a hue from 0°
  to 360° for a colour cycle.
- Per-frame delay, loop, onion skin, play.
- **Export animation**: one PNG per frame and/or a sprite sheet PNG with a JSON
  file listing each frame's rectangle and duration (and Godot's hframes/vframes).

## Tools

| Key | Tool |
|---|---|
| V | Selection: click, Shift-click, marquee; drag to move (Alt duplicates); handles scale (Shift keeps proportions, Alt from centre); the round handle rotates |
| A | Direct Selection: drag anchors and handles; Alt-drag breaks a handle; Delete removes anchors |
| P | Pen: click for corners, drag for curves, click the first anchor to close, Enter to finish. On a selected path: click a segment to add an anchor, an anchor to remove it, Alt-click to toggle smooth/corner, click an open end to continue it |
| M / L / \ | Rectangle / Ellipse / Line (Shift constrains, Alt from centre) |
| T | Type (double-click text with Selection to edit; Esc or Ctrl+Enter finishes) |
| B / E | Paintbrush / Eraser on raster paint: square monoline, hard, medium and soft round tips; `[` `]` size; pen pressure on a tablet |
| I | Eyedropper (Shift for stroke) |
| N | Frame Move |
| Shift+O | Artboard: click to pick, drag to move (artwork comes along; Alt leaves it), drag edges to resize, drag on the pasteboard for a new one |
| H / Z / Space | Hand / Zoom / temporary hand; Ctrl+wheel zooms |
| X | Swap fill and stroke |

Groups: Ctrl+G / Ctrl+Shift+G; a click selects a whole group, double-click
steps inside it (everything else dims; Esc steps back out), Direct Selection
reaches any path at any depth. Ctrl+7 / Ctrl+Alt+7 make and release clipping
masks. In the Layers panel, groups and sublayers nest with thumbnails; drag
objects onto a group or layer row to move them in.

Rulers (Ctrl+R) measure from the current artboard's corner. Drag out of a
ruler to make a guide, drag a guide to move it or back onto the ruler to delete
it, double-click one to type its position. Shapes, the pen, moved objects and
dragged anchors snap to guides and to artboard edges and centres; View has
toggles for guides (Ctrl+;), locking them (Ctrl+Alt+;) and snapping
(Ctrl+Shift+;). Guides are saved in the .halo.

Also: arrange (Ctrl+[ ]), flip, text to outlines (Ctrl+Shift+O), rasterize,
copy/paste/duplicate, unlimited panel docking. PNG export at any scale with
transparency; SVG export of an artboard.

## Running and building

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python noushalo.py [file]
powershell -ExecutionPolicy Bypass -File build.ps1   # dist\NousHalo\NousHalo.exe
```

Tests (`tests\`) run offscreen with their own settings and palette library.
They use private sample files in `tests\fixtures\` (not in the repository) and
only ever touch temporary copies.

## Licence

AGPL-3.0, because PyMuPDF is AGPL.
