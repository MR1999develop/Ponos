# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Righe dei moduli: l'etichetta punta al campo e il campo prende il suo nome accessibile."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.ui.form_rows import add_field_row


def test_the_label_is_the_buddy_of_the_field_and_not_of_its_container(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    layout = QFormLayout(host)
    field = QLineEdit(host)
    container = QWidget(host)

    add_field_row(layout, "Name", field, container)

    label = layout.labelForField(container)
    assert isinstance(label, QLabel)
    assert label.text() == "Name"
    assert label.buddy() is field
    assert field.accessibleName() == "Name"


def test_without_a_container_the_field_itself_is_the_row(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    layout = QFormLayout(host)
    field = QLineEdit(host)

    add_field_row(layout, "Height", field)

    label = layout.labelForField(field)
    assert isinstance(label, QLabel)
    assert label.buddy() is field


def test_a_name_the_field_already_has_is_kept(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    layout = QFormLayout(host)
    field = QLineEdit(host)
    field.setAccessibleName("Date of birth")

    add_field_row(layout, "Born on", field)

    assert field.accessibleName() == "Date of birth"
