# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The app icon: the committed files match their source and every size is there."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot
from release.make_icon import ICO, ICONS, SIZES, SOURCE, ico_bytes

from ponos_coach.ui.app_icon import ICON_SIZES, app_icon

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_the_window_icon_and_the_generator_have_the_same_sizes() -> None:
    assert ICON_SIZES == SIZES


def test_every_png_has_its_size_and_a_transparent_corner(qtbot: QtBot) -> None:
    del qtbot  # a QApplication for QImage
    for size in SIZES:
        image = QImage(str(ICONS / f"ponos-coach-{size}.png"))
        assert (image.width(), image.height()) == (size, size)
        assert image.pixelColor(0, 0).alpha() == 0  # rounded tile: the corner shows through


def test_the_ico_holds_every_size_as_the_committed_png() -> None:
    data = ICO.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, len(SIZES))
    pngs = {size: (ICONS / f"ponos-coach-{size}.png").read_bytes() for size in SIZES}
    assert data == ico_bytes(pngs)  # written by the generator from these very PNGs
    for png in pngs.values():
        assert png.startswith(_PNG_SIGNATURE)


def test_the_app_icon_offers_every_size(qtbot: QtBot) -> None:
    del qtbot
    icon = app_icon()
    assert not icon.isNull()
    assert sorted(size.width() for size in icon.availableSizes()) == sorted(SIZES)


def test_the_executable_uses_the_icon_and_the_package_carries_the_pngs() -> None:
    spec = (Path(__file__).resolve().parents[2] / "release" / "ponos_coach.spec").read_text(
        encoding="utf-8"
    )
    assert 'icon=str(ROOT / "release" / "ponos-coach.ico")' in spec
    assert '"ponos_coach/ui/icons"' in spec
    assert SOURCE.is_file()
