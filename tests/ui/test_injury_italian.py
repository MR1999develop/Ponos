# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-06 for the injuries: the real Italian catalog translates the texts of the section, with the
placeholders and the plural right, and the words stay plain (a doctor, never a diagnosis)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QLocale

from ponos_coach.__main__ import parse_command_line, setup_translations
from ponos_coach.injury.domain import (
    BodyZone,
    HaltReason,
    IllnessKind,
    Overdue,
    Side,
    Step,
    Verdict,
    WarningSign,
)
from ponos_coach.ui.injury_presentation import (
    HealthNotice,
    NoticeKind,
    ProblemKind,
    ProblemPhase,
    ProblemSummary,
    StatusFacts,
)
from ponos_coach.ui.injury_texts import (
    alert_body,
    alert_title,
    checkin_cell_text,
    detail_lines,
    doctor_invitation,
    halt_texts,
    notice_prefix,
    notice_texts,
    overdue_line,
    problem_title,
    sign_text,
    status_bar_text,
    status_text,
    step_text,
    verdict_line,
    zone_text,
)
from ponos_coach.ui.translations import Language, TranslationReport, qt_translations_dir
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


_BASE = ProblemSummary(
    "a",
    ProblemKind.INJURY,
    ProblemPhase.ACTIVE,
    date(2026, 6, 30),
    None,
    zone=BodyZone.KNEE,
    side=Side.LEFT,
    step=Step.WALKING,
    good_days=1,
    good_days_needed=3,
    checkin_due=True,
    last_verdict=Verdict.BACK,
)


def active(**changes: Any) -> ProblemSummary:
    return replace(_BASE, **changes)


def test_the_vocabulary_is_in_italian(italian: None) -> None:
    assert zone_text(BodyZone.ACHILLES) == "Tendine d'Achille"
    assert step_text(Step.RUN_WALK) == "Corsa e cammino"
    assert problem_title(active()) == "Ginocchio, sinistra"
    assert status_text(active()) == "Camminata"
    assert checkin_cell_text(active()) == "Da fare oggi"


def test_every_warning_sign_is_translated(italian: None) -> None:
    for sign in WarningSign:
        text = sign_text(sign)
        assert text and not any(text.startswith(word.strip()) for word in ("The", "You"))


def test_the_state_of_a_record_reads_in_plain_italian_with_the_numbers_in_place(
    italian: None,
) -> None:
    lines = detail_lines(active())

    assert "Gradino attuale: Camminata." in lines
    assert "Giorni buoni di fila: 1 su 3 per il gradino successivo." in lines
    assert "L'ultimo controllo era peggiore di prima: un gradino indietro." in lines
    assert "Manca il controllo di oggi." in lines


def test_the_doctor_is_invited_and_nothing_is_diagnosed_in_italian(italian: None) -> None:
    assert "visita medica" in doctor_invitation()
    assert "non può dire che cosa significhi" in doctor_invitation()
    assert alert_title() == "Vai da un medico"
    assert "{invitation}" not in alert_body()
    for reason in HaltReason:
        for text in halt_texts(reason):
            assert "{" not in text
    summary = active(phase=ProblemPhase.HALTED)
    headline, remedy = notice_texts(HealthNotice(NoticeKind.WARNING_SIGN, summary))
    assert notice_prefix(HealthNotice(NoticeKind.WARNING_SIGN, summary)) == "Vai da un medico"
    assert headline.startswith("Ginocchio, sinistra: È un segnale che merita una visita medica")
    assert "Visita medica fatta" in remedy
    assert overdue_line(Overdue.TOTAL).endswith("vai da un medico.")
    assert verdict_line(Verdict.UP).startswith("L'ultimo controllo")


def test_an_illness_title_and_the_status_bar_have_their_placeholders_filled(
    italian: None,
) -> None:
    illness = active(
        kind=ProblemKind.ILLNESS,
        zone=None,
        side=None,
        step=None,
        illness_kind=IllnessKind.SYSTEMIC,
    )

    assert problem_title(illness) == "Malattia: febbre, malessere generale o dolori muscolari"
    assert status_bar_text(StatusFacts(see_doctor=True, checkins_due=2)) == (
        "Infortuni: Vai da un medico, Controllo da fare oggi (2)"
    )
    assert status_bar_text(StatusFacts()) == ""
