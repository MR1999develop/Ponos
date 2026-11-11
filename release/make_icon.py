# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Build every size of the app icon from its single source, `ui/icons/ponos-coach.svg`.

Writes the PNGs the window uses (`ui/icons/ponos-coach-<size>.png`) and the Windows icon of the
executable (`release/ponos-coach.ico`, the same sizes as PNG entries). Run it after changing the
SVG: `uv run --locked python -m release.make_icon`. The output is committed, so a build does not
need to render SVG.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "src" / "ponos_coach" / "ui" / "icons"
SOURCE = ICONS / "ponos-coach.svg"
ICO = ROOT / "release" / "ponos-coach.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)  # the sizes Windows asks of an .ico
_ICO_HEADER = struct.Struct("<HHH")  # reserved, type (1 = icon), count
# width, height, colours, reserved, planes, bits per pixel, bytes, offset
_ICO_ENTRY = struct.Struct("<BBBBHHII")
_FULL_SIZE = 256  # written as 0 in an .ico entry


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    """The icon at `size` x `size` pixels, as PNG bytes."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, b"PNG"):
        raise RuntimeError(f"cannot encode the {size} px icon")
    return bytes(data.data())


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    """A Windows .ico holding each PNG as one entry, smallest first."""
    sizes = sorted(pngs)
    offset = _ICO_HEADER.size + _ICO_ENTRY.size * len(sizes)
    entries = []
    for size in sizes:
        side = 0 if size == _FULL_SIZE else size
        entries.append(_ICO_ENTRY.pack(side, side, 0, 0, 1, 32, len(pngs[size]), offset))
        offset += len(pngs[size])
    return b"".join([_ICO_HEADER.pack(0, 1, len(sizes)), *entries, *(pngs[size] for size in sizes)])


def main() -> int:
    """Render the sizes and write the files."""
    app = QGuiApplication.instance() or QGuiApplication(["make_icon"])
    del app
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"make_icon: {SOURCE.name}: not a valid SVG", file=sys.stderr)  # noqa: T201
        return 1
    pngs = {size: render_png(renderer, size) for size in SIZES}
    for size, png in pngs.items():
        (ICONS / f"ponos-coach-{size}.png").write_bytes(png)
    ICO.write_bytes(ico_bytes(pngs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
