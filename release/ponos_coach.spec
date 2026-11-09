# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# PyInstaller spec for Windows and Ubuntu (one folder, no console window).
# Build with `uv run --locked nox -s dist`; do not run it from another directory's state.
#
# Everything the app reads at run time through `importlib.resources` or a path relative to its
# own package must be listed under `datas` with the same package-relative destination:
#   - the food table, the compiled Italian catalogue (`nox -s i18n` makes it);
#   - the whole web resources folder of the interface (chart page, scripts and, later, the
#     map libraries), so that new files in it need no change here;
#   - plotly's package data (its script and templates) comes from PyInstaller's own plotly hook.
# No absolute path is written anywhere: the spec only uses paths derived from its own location.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is provided by PyInstaller
PACKAGE = ROOT / "src" / "ponos_coach"
CATALOG = PACKAGE / "i18n" / "ponos_it.qm"
if not CATALOG.is_file():
    raise SystemExit("ponos_it.qm is missing: run `uv run --locked nox -s i18n` first")

datas = [
    (str(PACKAGE / "nutrition" / "foods.json"), "ponos_coach/nutrition"),
    (str(CATALOG), "ponos_coach/i18n"),
    (str(PACKAGE / "ui" / "web"), "ponos_coach/ui/web"),
    (str(PACKAGE / "ui" / "icons"), "ponos_coach/ui/icons"),  # window icon (app_icon.py)
]
datas += collect_data_files("tzdata")  # IANA time zones: Windows has no system copy
datas += collect_data_files("desktop_notifier")  # its icon and platform backends
datas += copy_metadata("keyring")  # keyring finds its backends through entry points

# Modules found only at run time (plug-in style loading): the self-check imports them.
hidden = [
    *collect_submodules("ponos_coach"),
    *collect_submodules("desktop_notifier"),
    *collect_submodules("keyring"),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "release" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
# The project's own metadata comes along (the map's User-Agent reads the version from it), and with
# an editable install it holds `direct_url.json`: the path of the project folder. Never shipped.
a.datas = [entry for entry in a.datas if Path(entry[0]).name != "direct_url.json"]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ponos-coach",
    console=False,
    icon=str(ROOT / "release" / "ponos-coach.ico"),  # made by release/make_icon.py
    upx=False,
    strip=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="PonosCoach",
    upx=False,
    strip=False,
)
