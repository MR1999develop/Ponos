# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The app icon for the windows and the taskbar, from the PNGs made by `release/make_icon.py`.

PNGs rather than the SVG: they need no SVG plug-in at run time, and each size was rendered for its
own pixel grid. The executable's own icon (Explorer, Start menu) is `release/ponos-coach.ico`.
"""

from __future__ import annotations

from importlib import resources
from typing import Final

from PySide6.QtGui import QIcon, QPixmap

ICON_SIZES: Final = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def app_icon() -> QIcon:
    """The icon with every size; an empty icon if the files are missing (never an error)."""
    icon = QIcon()
    folder = resources.files("ponos_coach.ui") / "icons"
    for size in ICON_SIZES:
        resource = folder / f"ponos-coach-{size}.png"
        if not resource.is_file():
            continue
        pixmap = QPixmap()
        if pixmap.loadFromData(resource.read_bytes(), "PNG"):
            icon.addPixmap(pixmap)
    return icon
