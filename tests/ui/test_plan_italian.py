# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-06 for the plan: the real Italian catalog translates the new texts of the goals, the
training settings and the plan, in plain words, with the placeholders and the plurals right."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QLocale
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import parse_command_line, setup_translations
from ponos_coach.goals.domain import SessionChoices, SessionType
from ponos_coach.planning.domain import (
    CloseARaces,
    Phase,
    PlanRefusal,
    RecoveryInserted,
    RefusalReason,
    RetestInUnloadWeek,
    TargetClass,
)
from ponos_coach.ui.calendar_model import build_month, first_of_month, plan_layer_of
from ponos_coach.ui.calendar_texts import cell_text, day_detail_text
from ponos_coach.ui.goal_dialog import GoalDialog
from ponos_coach.ui.goal_draft import GoalField, GoalFormKind, default_draft
from ponos_coach.ui.plan_presentation import (
    NoticeAction,
    PlanAvailable,
    PlanRefusedNow,
    Severity,
    notices_of,
)
from ponos_coach.ui.plan_texts import (
    goal_label,
    notice_prefix,
    notice_texts,
    phase_text,
    retest_proposal_text,
    session_type_text,
    target_class_text,
)
from ponos_coach.ui.training_dialog import TrainingSettingsDialog
from ponos_coach.ui.training_draft import draft_of
from ponos_coach.ui.translations import Language, TranslationReport, qt_translations_dir
from tests.ui.plan_support import MARATHON_DAY, PLAN_TODAY, marathon, plan_for
from tests.ui.test_translations import build_real_catalog


@pytest.fixture
def italian(tmp_path: Path, qapp: QCoreApplication) -> Iterator[None]:
    build_real_catalog(tmp_path)
    previous = QLocale()
    report: TranslationReport = setup_translations(
        qapp,
        parse_command_line([]),
        saved=Language.ITALIAN,
        app_dir=tmp_path,
        qt_dir=qt_translations_dir(),
    )
    assert report.app_loaded
    QLocale.setDefault(QLocale(QLocale.Language.Italian, QLocale.Country.Italy))
    yield
    for translator in report.translators:
        qapp.removeTranslator(translator)
    QLocale.setDefault(previous)


def label_of(goal_id: str) -> str:
    return f"obiettivo {goal_id}"


def test_the_names_of_the_categories_are_in_italian(italian: None) -> None:
    assert session_type_text(SessionType.THRESHOLD) == "Soglia"
    assert session_type_text(SessionType.ALTERNATIVE) == "Attività alternativa"
    assert phase_text(Phase.TAPER) == "Scarico"
    assert phase_text(Phase.RACE) == "Giorno di gara"
    assert target_class_text(TargetClass.AMBITIOUS) == "Ambizioso"
    assert target_class_text(TargetClass.NOT_ESTIMABLE) == "Non stimabile"
    assert notice_prefix(True) == "Nessun piano"
    assert notice_prefix(False) == "Avviso"


def test_a_refusal_and_its_remedy_are_in_plain_italian_without_formal_address(
    italian: None,
) -> None:
    state = PlanRefusedNow(PlanRefusal(RefusalReason.NO_AVAILABILITY))

    (notice,) = notices_of(state)
    headline, remedy = notice_texts(notice, label_of)

    assert notice.severity is Severity.NO_PLAN
    assert headline == "Non è impostato nessun tempo per allenarsi."
    assert (
        remedy
        == "Scegli i giorni in cui puoi allenarti e le ore a settimana nelle Impostazioni di "
        "allenamento."
    )


def test_a_warning_names_the_goal_in_italian(italian: None) -> None:
    plan = plan_for(marathon(), types=frozenset({SessionType.EASY, SessionType.THRESHOLD}))

    (notice,) = [
        n
        for n in notices_of(PlanAvailable(plan))
        if n.action is NoticeAction.TRAINING_SETTINGS and "lunga" in notice_texts(n, label_of)[1]
    ]
    headline, remedy = notice_texts(notice, label_of)

    assert (
        headline
        == "Non è scelta la seduta lunga per obiettivo marathon: la resistenza ne risentirà."
    )
    assert remedy == "Scegli la seduta lunga in Impostazioni di allenamento."


def test_numbers_of_days_keep_their_placeholder_in_both_plural_forms(italian: None) -> None:
    plan = plan_for()
    for days in (1, 14):
        state = PlanAvailable(replace(plan, warnings=(CloseARaces("a", "b", days),)))
        headline, _ = notice_texts(notices_of(state)[0], label_of)
        assert f"giorni tra le due: {days})" in headline

    state = PlanAvailable(replace(plan, warnings=(RecoveryInserted(-35.0, 90.0, 60.0, 4),)))
    assert notice_texts(notices_of(state)[0], label_of)[0].endswith("all'inizio del piano: 4.")


def test_the_race_and_the_session_read_in_italian_in_the_calendar(italian: None) -> None:
    plan = plan_for(marathon(target_s=12_000))
    layer = plan_layer_of(plan, [], [], [marathon(target_s=12_000)])
    session_day = next(d for d in plan.days if d.session is not None and d.session.blocks)

    race_cell = next(
        c
        for c in build_month(first_of_month(MARATHON_DAY), PLAN_TODAY, [], [], layer).days
        if c.day == MARATHON_DAY
    )
    day_cell = next(
        c
        for c in build_month(first_of_month(session_day.day), PLAN_TODAY, [], [], layer).days
        if c.day == session_day.day
    )

    assert "Gara A" in cell_text(race_cell).split("\n")
    assert "Giorno di gara" in cell_text(race_cell).split("\n")
    assert any(line.startswith("Piano: ") for line in cell_text(day_cell).split("\n"))
    detail = day_detail_text(race_cell)
    assert "Il piano prevede" in detail
    assert "Tempo obiettivo:" in detail
    assert "Carico stimato" in day_detail_text(day_cell)
    assert "zona" in day_detail_text(day_cell)
    assert goal_label(marathon()).startswith("Gara A, Maratona, ")


def test_the_dialogs_are_in_italian_and_a_problem_is_a_sentence(
    qtbot: QtBot, italian: None
) -> None:
    dialog = GoalDialog(default_draft(GoalFormKind.RACE, date(2026, 7, 1)), [], date(2026, 7, 1))
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Aggiungi gara"
    assert dialog.save_button.text() == "Aggiungi obiettivo"
    assert (
        dialog.error_text(GoalField.DATE) == "Scegli un giorno dopo oggi: il piano parte da domani."
    )
    assert [dialog.priority_combo.itemText(i) for i in range(3)] == [
        "A: l'obiettivo della stagione",
        "B: una gara importante",
        "C: una gara di allenamento",
    ]
    training = TrainingSettingsDialog(
        draft_of(None, SessionChoices.of({})),
        suggested=True,
    )
    qtbot.addWidget(training)
    assert training.save_blocked_reason() == "Non è cambiato nulla."
    assert training.long_day_combo.itemText(0) == "lunedì"


def test_the_retest_proposal_reads_in_italian(italian: None) -> None:
    text = retest_proposal_text(RetestInUnloadWeek(date(2026, 10, 5)))

    assert text.startswith("Piano: fai il test di soglia nella settimana facile da ")
