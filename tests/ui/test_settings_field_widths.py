# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Settings pages: the input fields have the width of their kind (short number, date, choice),
the same in every page, narrow, left in their column, and they show their text in full.

Names and folders are free text: they are left out, and stay wide.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ponos_coach.goals.domain import SessionChoices
from ponos_coach.profiles.measurements import MeasurementHistory
from ponos_coach.ui.form_rows import FieldKind, field_width
from ponos_coach.ui.measurements_form import MeasurementsForm
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.tokens import SPACE_6, Theme
from ponos_coach.ui.training_dialog import TrainingSettingsDialog
from ponos_coach.ui.training_draft import draft_of
from tests.ui.nutrition_support import open_settings, window_with_days
from tests.ui.workspace_support import WAIT_MS, compose, make_settings

_FREE_TEXT = {"profileName", "profileFolder"}
_NARROW_CHARS = 24  # a field of a kind is narrower than this many average characters


def _narrow_limit(widget: QWidget) -> int:
    return _NARROW_CHARS * widget.fontMetrics().averageCharWidth()


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(params=["en", "it"])
def locale(request: pytest.FixtureRequest) -> Iterator[str]:
    previous = QLocale()
    if request.param == "it":
        QLocale.setDefault(QLocale(QLocale.Language.Italian, QLocale.Country.Italy))
    else:
        QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield str(request.param)
    QLocale.setDefault(previous)


def _kind_of(widget: QWidget) -> FieldKind | None:
    """The kind of an input field, or None for what is not one of the sized fields."""
    if widget.objectName() in _FREE_TEXT or widget.objectName().startswith("qt_"):
        return None
    if isinstance(widget, QDateEdit):
        return FieldKind.DATE
    if isinstance(widget, QComboBox):
        return FieldKind.CHOICE
    if isinstance(widget, QAbstractSpinBox | QLineEdit):
        return FieldKind.SHORT
    return None


def _fields(root: QWidget) -> dict[QWidget, FieldKind]:
    found: dict[QWidget, FieldKind] = {}
    for child in root.findChildren(QWidget):
        kind = _kind_of(child)
        if kind is not None and not child.isHidden():
            found[child] = kind
    return found


def _check(root: QWidget, expected_kinds: set[FieldKind]) -> None:
    root.resize(900, 700)
    root.show()
    fields = _fields(root)
    assert set(fields.values()) >= expected_kinds
    for widget, kind in fields.items():
        width = field_width(widget, kind)
        assert width < _narrow_limit(widget), widget.objectName()
        assert widget.width() == width, (widget.objectName(), kind)
        if isinstance(widget, QAbstractSpinBox) and not isinstance(widget, QDateEdit):
            assert widget.fontMetrics().horizontalAdvance(widget.text()) + SPACE_6 <= width
        if isinstance(widget, QComboBox):
            longest = max(widget.itemText(i) for i in range(widget.count()))
            assert widget.fontMetrics().horizontalAdvance(longest) + SPACE_6 <= width


def test_the_kinds_are_narrow_and_a_choice_is_wider_than_a_number(qtbot: QtBot) -> None:
    probe = QWidget()
    qtbot.addWidget(probe)

    assert field_width(probe, FieldKind.SHORT) < _narrow_limit(probe)
    assert field_width(probe, FieldKind.DATE) < _narrow_limit(probe)
    assert field_width(probe, FieldKind.CHOICE) < _narrow_limit(probe)
    assert field_width(probe, FieldKind.CHOICE) > field_width(probe, FieldKind.SHORT)


def test_the_training_page_fields(qtbot: QtBot, locale: str) -> None:
    dialog = TrainingSettingsDialog(draft_of(None, SessionChoices.of({})), suggested=True)
    qtbot.addWidget(dialog)

    _check(dialog, {FieldKind.SHORT, FieldKind.CHOICE})


def test_the_profile_page_fields(qtbot: QtBot, locale: str) -> None:
    history = MeasurementHistory(None, (), (), (), ())
    form = MeasurementsForm(history, date(2026, 6, 30), "Atleta di prova")
    dialog = ProfileEditDialog(form, history, Theme.LIGHT)
    qtbot.addWidget(dialog)

    _check(dialog, {FieldKind.SHORT, FieldKind.DATE})


def test_the_nutrition_page_fields(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, locale: str
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    _check(dialog, {FieldKind.SHORT, FieldKind.DATE, FieldKind.CHOICE})


def test_a_new_profile_has_the_same_widths(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None

    _check(dialog, {FieldKind.SHORT, FieldKind.DATE})
