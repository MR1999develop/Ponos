# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-05 (keyboard and accessible names), T-UI-06 (languages) and T-UI-07 (settings) of phase 3.

The new dialogs (New profile, Edit profile, Sport weights, Activity detail) are
exercised on their own, without the window: Tab path, accessible name read from Qt's
accessibility interface, texts in Italian from the real catalogue and with a "pseudo" translator
that lengthens
every text by half. The settings are exercised with values corrupted by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import date
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QSettings, Qt, QTranslator
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import parse_command_line, setup_translations
from ponos_coach.activities.domain import Sport
from ponos_coach.i18n.catalog import Message, MessageStatus, parse_catalog
from ponos_coach.load.domain import default_weight
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.ui.activity_detail import DetailUnavailable
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.measurements_form import MeasurementsForm
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import NewProfileForm
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.sport_weights_dialog import SportWeightsDialog
from ponos_coach.ui.sport_weights_form import SportWeightsForm
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.tokens import Theme
from ponos_coach.ui.translations import (
    Language,
    TranslationReport,
    choose_language,
    qt_translations_dir,
    save_language,
    saved_language,
)
from tests.ui.test_measurements_form import TODAY, make_history
from tests.ui.test_translations import build_real_catalog
from tests.ui.workspace_support import close_window, compose, language_form, make_settings

TS_FILE = Path(__file__).resolve().parents[2] / "src" / "ponos_coach" / "i18n" / "ponos_it.ts"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


# --- The four new dialogs ----------------------------------------------------------------------


def edit_dialog(*, fixed_missing: bool = False) -> QWidget:
    history = make_history(fixed=None if fixed_missing else make_history().fixed)
    return ProfileEditDialog(
        MeasurementsForm(history, TODAY, "Atleta di prova"), history, Theme.LIGHT
    )


def weights_dialog() -> QWidget:
    return SportWeightsDialog(SportWeightsForm({s: default_weight(s) for s in Sport}))


def new_profile_dialog() -> QWidget:
    return NewProfileDialog(
        NewProfileForm("Europe/Rome"), date(2026, 6, 30), lambda _p: None, lambda: "", Theme.LIGHT
    )


def detail_dialog() -> QWidget:
    return ActivityDetailDialog(ChartEngine(lambda _name: None), ZoneInfo("Europe/Rome"))


DIALOGS: dict[str, Callable[[], QWidget]] = {
    "edit": edit_dialog,
    "edit_fixed_missing": lambda: edit_dialog(fixed_missing=True),
    "weights": weights_dialog,
    "new_profile": new_profile_dialog,
    "detail": detail_dialog,
}


def shown(qtbot: QtBot, name: str) -> QWidget:
    dialog = DIALOGS[name]()
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def interactive(dialog: QWidget) -> list[QWidget]:
    """The controls the user acts with, visible and enabled (not Qt's internals)."""
    found: list[QWidget] = []
    for widget in dialog.findChildren(QWidget):
        if widget.objectName().startswith("qt_") or isinstance(widget, QHeaderView):
            continue
        if not isinstance(
            widget, QLineEdit | QAbstractSpinBox | QAbstractButton | QAbstractItemView
        ):
            continue
        if widget.isVisibleTo(dialog) and widget.isEnabled():
            found.append(widget)
    return found


def accessible_name(widget: QWidget) -> str:
    return QAccessible.queryAccessibleInterface(widget).text(QAccessible.Text.Name)


def tab_order(dialog: QWidget) -> list[QWidget]:
    """The controls in the order the Tab visits them (Qt's focus chain)."""
    allowed = {id(w) for w in interactive(dialog)}
    order: list[QWidget] = []
    seen: set[int] = set()
    widget: QWidget | None = dialog.nextInFocusChain()
    while widget is not None and id(widget) not in seen and widget is not dialog:
        seen.add(id(widget))
        if id(widget) in allowed and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
            order.append(widget)
        widget = widget.nextInFocusChain()
    return order


@pytest.mark.parametrize("name", list(DIALOGS))
def test_t_ui_05_every_control_of_the_new_dialogs_can_take_the_focus_with_the_tab_key(
    qtbot: QtBot, name: str
) -> None:
    dialog = shown(qtbot, name)

    controls = interactive(dialog)
    reached = {id(w) for w in tab_order(dialog)}

    assert controls
    unreachable = [w.objectName() or type(w).__name__ for w in controls if id(w) not in reached]
    assert unreachable == []
    for widget in controls:
        assert widget.focusPolicy() & Qt.FocusPolicy.TabFocus, widget.objectName()


@pytest.mark.parametrize("name", list(DIALOGS))
def test_t_ui_05_every_control_of_the_new_dialogs_has_an_accessible_name(
    qtbot: QtBot, name: str
) -> None:
    dialog = shown(qtbot, name)

    unnamed = [
        f"{type(w).__name__}:{w.objectName()}"
        for w in interactive(dialog)
        if not accessible_name(w)
    ]

    assert unnamed == []


@pytest.mark.parametrize("name", ["weights", "edit", "new_profile"])
def test_t_ui_05_the_accessible_names_of_a_dialog_are_distinct(qtbot: QtBot, name: str) -> None:
    dialog = shown(qtbot, name)

    names = [accessible_name(w) for w in interactive(dialog) if accessible_name(w)]

    assert len(set(names)) == len(names), names


def y_of(widget: QWidget, dialog: QWidget) -> int:
    return widget.mapTo(dialog, QPoint(0, 0)).y()


@pytest.mark.parametrize("name", ["edit_fixed_missing", "edit", "weights", "new_profile"])
def test_t_ui_05_the_tab_order_follows_the_visual_order_from_top_to_bottom(
    qtbot: QtBot, name: str
) -> None:
    dialog = shown(qtbot, name)

    positions = [y_of(w, dialog) for w in tab_order(dialog)]

    tolerance = 12  # a button next to a field may sit a few pixels higher
    assert all(b >= a - tolerance for a, b in pairwise(positions)), positions


def test_t_ui_05_the_save_button_is_off_with_a_reason_until_something_changes_and_then_reachable(
    qtbot: QtBot,
) -> None:
    dialog = shown(qtbot, "edit")
    assert isinstance(dialog, ProfileEditDialog)

    assert not dialog.save_button.isEnabled()
    assert dialog.save_button.toolTip()  # the reason
    assert dialog.save_button not in tab_order(dialog)  # a disabled button does not take focus
    dialog.threshold_box.setValue(dialog.threshold_box.value() - 1)
    assert dialog.save_button.isEnabled()
    assert dialog.save_button in tab_order(dialog)


# --- Languages ------------------------------------------------------------------------------------


@pytest.fixture
def italian(tmp_path: Path, qapp: QCoreApplication) -> Iterator[TranslationReport]:
    build_real_catalog(tmp_path)
    report = setup_translations(
        qapp, parse_command_line([]), saved=Language.ITALIAN, app_dir=tmp_path,
        qt_dir=qt_translations_dir(),
    )  # fmt: skip
    assert report.app_loaded
    yield report
    for translator in report.translators:
        qapp.removeTranslator(translator)


def catalog_messages() -> tuple[Message, ...]:
    return tuple(
        m
        for m in parse_catalog(TS_FILE.read_bytes())
        if m.status is MessageStatus.FINISHED and not m.plural
    )


def texts_of(dialog: QWidget) -> set[str]:
    """Every text the user sees or hears: labels, buttons, titles, hints, names."""
    texts = {dialog.windowTitle()}
    for widget in dialog.findChildren(QWidget):
        for value in (widget.toolTip(), widget.accessibleName()):
            texts.add(value)
        if isinstance(widget, QLabel):
            texts.add(widget.text())
        if isinstance(widget, QAbstractButton):
            texts.add(widget.text())
        if isinstance(widget, QLineEdit):
            texts.add(widget.placeholderText())
        if isinstance(widget, QAbstractSpinBox):
            texts.add(widget.specialValueText())
        title = getattr(widget, "title", None)
        if callable(title):
            texts.add(str(title()))
    return {t for t in texts if t}


@pytest.mark.parametrize("name", list(DIALOGS))
def test_t_ui_06_in_italian_no_visible_text_of_the_new_dialogs_is_left_in_english(
    qtbot: QtBot, italian: TranslationReport, name: str
) -> None:
    translated = {m.source for m in catalog_messages() if m.forms[0] != m.source}
    dialog = shown(qtbot, name)

    left_in_english = sorted(texts_of(dialog) & translated)

    assert left_in_english == []


def test_t_ui_06_in_italian_the_standard_buttons_come_from_qt_and_are_translated(
    qtbot: QtBot, italian: TranslationReport
) -> None:
    assert italian.qt_loaded
    dialog = shown(qtbot, "new_profile")
    buttons = {w.text() for w in dialog.findChildren(QAbstractButton)}

    assert "Annulla" in buttons
    assert "Cancel" not in buttons


def test_t_ui_06_every_finished_message_translates_in_its_own_context_through_the_compiled_catalog(
    italian: TranslationReport,
) -> None:
    wrong = [
        m.source
        for m in catalog_messages()
        if QCoreApplication.translate(m.context, m.source, m.comment or None) != m.forms[0]
    ]

    assert wrong == []


def plural_messages() -> list[Message]:
    return [
        m
        for m in parse_catalog(TS_FILE.read_bytes())
        if m.plural and m.status is MessageStatus.FINISHED
    ]


COUNTS = (0, 1, 2, 3, 5, 11, 21, 22, 100, 101, 1_000_000)


def test_t_ui_06_the_catalog_has_plural_messages_to_check() -> None:
    assert len(plural_messages()) >= 5


@pytest.mark.parametrize("count", COUNTS)
def test_t_ui_06_italian_plurals_carry_the_number_and_use_the_singular_only_for_one(
    italian: TranslationReport, count: int
) -> None:
    for message in plural_messages():
        text = QCoreApplication.translate(
            message.context, message.source, message.comment or None, count
        )

        assert str(count) in text, message.source
        assert "%n" not in text
        assert not re.search(r"\(\w{1,3}\)", text), text  # no "(i)" / "(e)" left
        form = message.forms[0 if count == 1 else 1]  # Italian: singular only for one
        assert text == form.replace("%n", str(count)), message.source


@pytest.mark.parametrize("count", [1, 2, 5])
def test_t_ui_06_english_plurals_do_not_use_the_optional_ending_form(count: int) -> None:
    for message in plural_messages():
        text = QCoreApplication.translate(
            message.context, message.source, message.comment or None, count
        )

        assert "%n" not in text
        assert not re.search(r"\w\((?:s|es|ies|i)\)", text), text


class PseudoTranslator(QTranslator):
    """Lengthens every text by half with words at the end (real words, so it can wrap)."""

    def isEmpty(self) -> bool:  # noqa: N802 - Qt
        return False

    def translate(
        self, context: str, source_text: str, disambiguation: str | None = None, n: int = -1
    ) -> str:
        del context, disambiguation
        text = source_text.replace("%n", str(max(n, 0)))
        extra = " ".join(["lunghissimo"] * max(1, len(text.split()) // 2))
        return f"{text} {extra}"


@pytest.fixture
def pseudo(qapp: QCoreApplication) -> Iterator[PseudoTranslator]:
    translator = PseudoTranslator(qapp)
    qapp.installTranslator(translator)
    yield translator
    qapp.removeTranslator(translator)


def inside_state_page(widget: QWidget) -> bool:
    """Message pages have a separate test (D-F3-11)."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, StatePage):
            return True
        parent = parent.parentWidget()
    return False


def clipped(dialog: QWidget) -> list[str]:
    """Labels and buttons whose text does not fit in the space they have."""
    dialog.resize(dialog.sizeHint().expandedTo(dialog.size()))  # never below the usable size
    QCoreApplication.processEvents()
    problems: list[str] = []
    for widget in dialog.findChildren(QWidget):
        if not widget.isVisibleTo(dialog) or widget.objectName().startswith("qt_"):
            continue
        if isinstance(widget, QLabel) and widget.text() and not inside_state_page(widget):
            if widget.wordWrap():
                if widget.heightForWidth(widget.width()) > widget.height() + 1:
                    problems.append(f"label cut off in height: {widget.text()[:40]}")
            elif widget.fontMetrics().horizontalAdvance(widget.text()) > widget.width() + 1:
                problems.append(f"label tagliata in larghezza: {widget.text()[:40]}")
        if isinstance(widget, QAbstractButton) and widget.text():
            text = widget.text().replace("&", "")
            if widget.fontMetrics().horizontalAdvance(text) > widget.width():
                problems.append(f"bottone tagliato: {text[:40]}")
    return problems


@pytest.mark.parametrize("name", ["edit", "edit_fixed_missing", "weights", "new_profile", "detail"])
def test_t_ui_06_the_layout_holds_texts_half_as_long_again(
    qtbot: QtBot, pseudo: PseudoTranslator, name: str
) -> None:
    dialog = shown(qtbot, name)

    assert clipped(dialog) == []
    hint = dialog.sizeHint()
    assert hint.width() <= 1280  # fits in a laptop screen
    assert hint.height() <= 1000


@pytest.mark.parametrize("name", ["edit", "edit_fixed_missing", "weights", "new_profile", "detail"])
def test_t_ui_06_the_layout_holds_the_italian_texts(
    qtbot: QtBot, italian: TranslationReport, name: str
) -> None:
    dialog = shown(qtbot, name)

    assert clipped(dialog) == []
    assert dialog.sizeHint().width() <= 1100


def test_t_ui_06_the_message_pages_of_the_detail_window_show_every_line_of_their_text(
    qtbot: QtBot,
) -> None:
    dialog = shown(qtbot, "detail")
    assert isinstance(dialog, ActivityDetailDialog)
    cut: list[str] = []

    def check(state: str) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()
        cut.extend(
            f"{state}: {label.text()[:30]}"
            for label in dialog.findChildren(QLabel)
            if label.isVisibleTo(dialog)
            and label.text()
            and label.wordWrap()
            and label.height() < label.heightForWidth(label.width())
        )

    check("loading")
    for reason in ExclusionReason:
        dialog.show_outcome(DetailUnavailable(reason))
        check(reason.value)

    assert cut == []


# --- T-UI-07: settings remembered and corrupted -----------------------------------------------

HOSTILE_VALUES: list[object] = [
    "", " ", "abc", "-1", "0", "1.0", "1e3", "99999999999999999999", "9" * 5000, "\x00", "\u202e",
    "../../etc", "C:\\no\\such\\folder", "it\x00", "IT", " it", "it" * 2000,
    b"\xff\xfe\x00", b"", ["a", "b"], [], 3.5, float("nan"), True, False, 2**62, -(2**62),
    {"a": 1},
]  # fmt: skip
ALL_KEYS = (
    "language/code", "window/geometry", "window/section", "import/lastFolder",
    "import/lastFilesFolder", "profiles/lastOpenFolder", "overview/lastExportFolder",
    "garmin/autoSyncOnOpen/00000000-0000-4000-8000-000000000001",
)  # fmt: skip


@pytest.mark.parametrize("value", HOSTILE_VALUES, ids=[f"v{n}" for n in range(len(HOSTILE_VALUES))])
def test_t_ui_07_a_window_starts_in_english_and_usable_whatever_every_setting_holds(
    qtbot: QtBot, tmp_path: Path, value: object
) -> None:
    path = tmp_path / "rovinate.ini"
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    for key in ALL_KEYS:
        settings.setValue(key, value)
    settings.sync()
    again = QSettings(str(path), QSettings.Format.IniFormat)

    window = compose(tmp_path, again)
    qtbot.addWidget(window)
    window.start()
    window.show()

    assert window.isVisible()
    assert saved_language(again) is None
    assert language_form(window).button(Language.ENGLISH).isChecked()
    assert window.windowTitle()
    close_window(qtbot, window)


def test_t_ui_07_language_section_and_geometry_come_back_in_the_next_session(
    qtbot: QtBot, tmp_path: Path
) -> None:
    settings = QSettings(str(tmp_path / "ricordi.ini"), QSettings.Format.IniFormat)
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    window.show()
    window.resize(800, 711)
    language_form(window).button(Language.ITALIAN).click()
    sidebar_row = 1
    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None
    sidebar.setCurrentRow(sidebar_row)
    size_before = window.size()  # the headless screen may limit the requested size
    close_window(qtbot, window)

    later = QSettings(str(tmp_path / "ricordi.ini"), QSettings.Format.IniFormat)
    assert saved_language(later) is Language.ITALIAN
    assert choose_language(None, saved_language(later)) is Language.ITALIAN
    reopened = compose(tmp_path / "secondo", later)
    qtbot.addWidget(reopened)
    reopened.start()
    reopened.show()
    sidebar_again = reopened.findChild(QListWidget, "sidebar")
    assert sidebar_again is not None
    assert sidebar_again.currentRow() == sidebar_row
    assert reopened.height() == size_before.height() == 711
    close_window(qtbot, reopened)


def test_t_ui_07_the_forced_language_wins_over_the_saved_one_and_does_not_overwrite_it(
    tmp_path: Path,
) -> None:
    settings = QSettings(str(tmp_path / "lingua.ini"), QSettings.Format.IniFormat)
    save_language(settings, Language.ITALIAN)

    assert choose_language(Language.ENGLISH, saved_language(settings)) is Language.ENGLISH
    assert saved_language(settings) is Language.ITALIAN
