# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-QUAL-02 e T-DATI-01 della fase 3: partizioni e confini dei dati personali, migrazione.

Altezza, peso, data di nascita, FC, soglia, pesi degli sport e giorno di validità: ogni valore si
prova nelle partizioni della specifica e ai confini (valore al limite e appena oltre), dal testo
dell'utente fino alla riga nel database. In più la migrazione v1/v2 -> v3 uccisa a ogni istruzione
con un processo vero, il profilo v2 aperto da due istanze e i dati fissi completati due volte
insieme.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PySide6.QtCore import QDate
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    SportWeight,
    ThresholdHeartRate,
    customised_weights,
    default_weight,
)
from ponos_coach.load.use_cases import SetSportWeights
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import JsonProfileRegistry
from ponos_coach.profiles.domain import (
    HEIGHT_MAX_CM,
    HEIGHT_MIN_CM,
    MIN_BIRTH_DATE,
    PROFILE_DATABASE_NAME,
    BirthDate,
    FixedProfileData,
    FolderKind,
    Height,
    Weight,
)
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementProblem,
    MeasurementUpdate,
    UpdateMeasurements,
)
from ponos_coach.profiles.ports import FixedDataAlreadySetError, ProfileInUseError
from ponos_coach.profiles.use_cases import OpenProfileFromFolder, ProfilePorts
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from ponos_coach.ui.birth_date_edit import BirthDateEdit
from ponos_coach.ui.body_input import BodyProblem, latest_birth_date, read_height, read_weight
from ponos_coach.ui.heart_rate_rules import (
    MAX_ALLOWED_BPM,
    MIN_ALLOWED_BPM,
    MIN_THRESHOLD_BPM,
    heart_rate_problems,
)
from ponos_coach.ui.sport_weights_form import SportWeightsForm
from tests.collaudo.support import create_profile, fixed_now
from tests.collaudo.test_database_del_profilo import rows, write_frozen_v1
from tests.collaudo.test_fase3_migrazione import table_names, user_version, write_frozen_v2
from tests.fixtures.generators.collaudo_fase3_worker import DIED

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase3_worker.py"
)
TODAY = date(2026, 7, 1)


# --- Peso: partizioni e confini ---------------------------------------------------------------

VALID_WEIGHTS = [
    ("20", 200), ("20.0", 200), ("20,0", 200), ("20.1", 201), ("20,1", 201), ("70.5", 705),
    ("70,5", 705), ("  70.5  ", 705), ("299.9", 2999), ("300", 3000), ("300.0", 3000),
    ("300,0", 3000), ("70.50", 705), ("70.", 700), ("+70", 700),
]  # fmt: skip
INVALID_WEIGHTS = [
    "", "   ", "19.9", "19,9", "19.99", "0", "-0", "-70", "-20", "300.1", "300,01", "301", "1000",
    "70.55", "70.05", "70.0.1", "70,5,1", "7 0", "abc", "70kg", "NaN", "nan", "Infinity",
    "-Infinity", "1e400", "1e-1000", "0x46", "9" * 17, "0" * 17 + "70", "--70", "70-",
    "\u0667\u0660\u066b\u0665",
]  # fmt: skip
NON_CANONICAL_WEIGHTS = [
    "7_0.5", "\u0667\u0660", "\uff17\uff10", "7e1", ".5e2", "7E1", "700e-1", "7 0.5", "70 .5",
    "70. 5", "70.5\u00a0", "\u00a070.5", "70.\uff15", "70,5e0", "+-70", "0x46", ".5", "70..5",
    "70,.5", "7_0",
]  # fmt: skip


@pytest.mark.parametrize(("text", "tenths"), VALID_WEIGHTS)
def test_t_qual_02_weight_text_valid_partitions_and_limits(text: str, tenths: int) -> None:
    assert Weight.parse(text) == Weight(tenths)
    assert read_weight(text) == Weight(tenths)


@pytest.mark.parametrize("text", INVALID_WEIGHTS)
def test_t_qual_02_weight_text_invalid_partitions_and_just_beyond_the_limits(text: str) -> None:
    with pytest.raises(ValueError, match="peso"):
        Weight.parse(text)
    expected = BodyProblem.WEIGHT_EMPTY if not text.strip() else BodyProblem.WEIGHT_INVALID
    assert read_weight(text) is expected


@pytest.mark.parametrize("text", NON_CANONICAL_WEIGHTS)
def test_t_qual_02_weight_text_with_hostile_numerals_is_refused_not_reinterpreted(
    text: str,
) -> None:
    with pytest.raises(ValueError, match="peso"):
        Weight.parse(text)


@given(st.decimals(min_value=Decimal("-1000"), max_value=Decimal("1000"), places=1))
def test_t_qual_02_weight_in_tenths_follows_the_limits_exactly(kg: Decimal) -> None:
    tenths = int(kg * 10)
    in_range = 200 <= tenths <= 3000

    if in_range:
        assert Weight.from_kg(kg) == Weight(tenths)
        assert Weight.parse(format(kg, "f")) == Weight(tenths)
    else:
        with pytest.raises(ValueError, match="peso"):
            Weight.from_kg(kg)


# --- Altezza ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "centimetres"),
    [("100", 100), ("101", 101), ("172", 172), ("249", 249), ("250", 250), (" 172 ", 172)],
)
def test_t_qual_02_height_text_valid_partitions_and_limits(text: str, centimetres: int) -> None:
    assert read_height(text) == Height(centimetres)


@pytest.mark.parametrize(
    "text",
    ["99", "98", "0", "1", "251", "260", "999", "-1", "+172", "1e2", "172.0", "172,5", "17 2",
     "\uff11\uff17\uff12", "\u0663\u0662\u0661", "abc", "12345", "0100", "0172", "²00", "1_0"],
)  # fmt: skip
def test_t_qual_02_height_text_invalid_partitions_and_just_beyond_the_limits(text: str) -> None:
    assert read_height(text) is BodyProblem.HEIGHT_INVALID


@pytest.mark.parametrize("text", ["", " ", "\t"])
def test_t_qual_02_an_empty_height_is_its_own_problem(text: str) -> None:
    assert read_height(text) is BodyProblem.HEIGHT_EMPTY


@pytest.mark.parametrize(
    ("centimetres", "valid"),
    [(HEIGHT_MIN_CM - 1, False), (HEIGHT_MIN_CM, True), (HEIGHT_MAX_CM, True),
     (HEIGHT_MAX_CM + 1, False), (-1, False), (10**9, False)],
)  # fmt: skip
def test_t_qual_02_height_type_limits_on_and_off_point(centimetres: int, valid: bool) -> None:
    if valid:
        assert Height(centimetres).cm == centimetres
    else:
        with pytest.raises(ValueError, match="altezza"):
            Height(centimetres)


# --- Data di nascita --------------------------------------------------------------------------


def five_years_before(today: date) -> date:
    """Oracolo: lo stesso giorno cinque anni prima (il 29 febbraio diventa il 28)."""
    day = 28 if (today.month, today.day) == (2, 29) else today.day
    return date(today.year - 5, today.month, day)


@given(
    st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)),
    st.dates(min_value=date(1850, 1, 1), max_value=date(2100, 12, 31)),
)
def test_t_qual_02_birth_date_is_valid_exactly_between_1900_and_five_years_ago(
    today: date, value: date
) -> None:
    valid = MIN_BIRTH_DATE <= value <= five_years_before(today)

    if valid:
        assert BirthDate.on(value, today).value == value
    else:
        with pytest.raises(ValueError, match="data di nascita"):
            BirthDate.on(value, today)


@pytest.mark.parametrize(
    "today",
    [date(2026, 2, 28), date(2028, 2, 29), date(2028, 3, 1), date(2024, 2, 29), date(2100, 3, 1),
     date(2026, 12, 31), date(2026, 1, 1), date(2000, 2, 29), date(2005, 3, 1)],
)  # fmt: skip
def test_t_qual_02_the_latest_birth_date_is_the_on_point_and_the_next_day_is_the_off_point(
    today: date,
) -> None:
    latest = latest_birth_date(today)

    assert latest == five_years_before(today)
    assert BirthDate.on(latest, today).value == latest
    with pytest.raises(ValueError, match="data di nascita"):
        BirthDate.on(latest + timedelta(days=1), today)


def test_t_qual_02_the_birth_date_control_stops_exactly_at_the_domain_limit(qtbot: QtBot) -> None:
    for today in (date(2028, 2, 29), date(2026, 7, 1), date(2026, 2, 28)):
        edit = BirthDateEdit(today)
        qtbot.addWidget(edit)
        limit = five_years_before(today)

        assert edit.maximumDate() == QDate(limit.year, limit.month, limit.day)


# --- FC, soglia -------------------------------------------------------------------------------

EDGE_BPM = (18, 19, 20, 21, 38, 39, 40, 41, 49, 50, 51, 169, 170, 171, 189, 190, 191,
            248, 249, 250, 251, 252)  # fmt: skip


def physiology_is_valid(minimum: int, maximum: int, threshold: int) -> bool:
    """Oracolo dalla specifica: 20 <= min < max <= 250, 40 <= soglia <= 250, min < soglia <= max."""
    return (
        20 <= minimum < maximum <= 250 and 40 <= threshold <= 250 and minimum < threshold <= maximum
    )


def test_t_qual_02_heart_rate_triples_are_valid_exactly_as_the_specification_says() -> None:
    checked = 0
    for minimum in EDGE_BPM:
        for maximum in EDGE_BPM:
            for threshold in EDGE_BPM:
                expected = physiology_is_valid(minimum, maximum, threshold)
                try:
                    Physiology(
                        HeartRateLimits(minimum, maximum), ThresholdHeartRate(threshold), Sex.MALE
                    )
                    built = True
                except ValueError:
                    built = False
                assert built is expected, (minimum, maximum, threshold)
                if expected:  # il modulo non deve rifiutare ciò che il dominio accetta
                    assert heart_rate_problems(minimum, maximum, threshold) == ()
                checked += 1
    assert checked == len(EDGE_BPM) ** 3


def test_t_qual_02_the_ranges_of_the_controls_are_those_of_the_domain() -> None:
    HeartRateLimits(MIN_ALLOWED_BPM, MIN_ALLOWED_BPM + 1)
    HeartRateLimits(MAX_ALLOWED_BPM - 1, MAX_ALLOWED_BPM)
    ThresholdHeartRate(MIN_THRESHOLD_BPM)
    ThresholdHeartRate(MAX_ALLOWED_BPM)
    for bad in (MIN_ALLOWED_BPM - 1,):
        with pytest.raises(ValueError, match="FC"):
            HeartRateLimits(bad, 190)
    with pytest.raises(ValueError, match="FC"):
        HeartRateLimits(50, MAX_ALLOWED_BPM + 1)
    with pytest.raises(ValueError, match="FC"):
        ThresholdHeartRate(MIN_THRESHOLD_BPM - 1)
    with pytest.raises(ValueError, match="FC"):
        ThresholdHeartRate(MAX_ALLOWED_BPM + 1)


# --- Pesi degli sport -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [(0.0, True), (-0.0, True), (1e-300, True), (0.05, True), (4.999999999, True), (5.0, True),
     (5.000000001, False), (5.1, False), (-1e-12, False), (-1.0, False), (float("inf"), False),
     (float("-inf"), False), (float("nan"), False), (1e308, False)],
)  # fmt: skip
def test_t_qual_02_sport_weight_partitions_and_limits(value: float, valid: bool) -> None:
    if valid:
        assert SportWeight(value).value == value
    else:
        with pytest.raises(ValueError, match="peso"):
            SportWeight(value)


@pytest.mark.parametrize(
    ("typed", "shown"),
    [(-5.0, 0.0), (-0.004, 0.0), (0.0, 0.0), (0.004, 0.0), (0.005, 0.01), (4.994, 4.99),
     (4.996, 5.0), (5.0, 5.0), (5.001, 5.0), (7.5, 5.0), (float("inf"), 5.0),
     (float("-inf"), 0.0)],
)  # fmt: skip
def test_t_qual_02_the_weights_form_clamps_to_the_limits_with_two_decimals(
    typed: float, shown: float
) -> None:
    form = SportWeightsForm({sport: default_weight(sport) for sport in Sport})

    form.set_value(Sport.RUNNING, typed)

    assert form.value(Sport.RUNNING) == pytest.approx(shown, abs=0.0051)
    assert 0.0 <= form.value(Sport.RUNNING) <= SportWeight.MAX
    assert form.build()[Sport.RUNNING].value == form.value(Sport.RUNNING)


def test_t_qual_02_the_weights_form_brings_a_nan_to_a_limit_like_any_out_of_range_value() -> None:
    form = SportWeightsForm({sport: default_weight(sport) for sport in Sport})

    form.set_value(Sport.RUNNING, float("nan"))

    assert 0.0 <= form.build()[Sport.RUNNING].value <= SportWeight.MAX


def test_t_qual_02_saved_weights_keep_only_what_differs_from_the_default_and_survive_reopening(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    session = profile.session
    save = SetSportWeights(session.activities, session.sport_weights)
    everything_default = {sport: default_weight(sport) for sport in Sport}
    try:
        save(everything_default)
        assert dict(session.sport_weights.load()) == {}

        mixed = {
            **everything_default,
            Sport.RUNNING: SportWeight(5.0),
            Sport.OTHER: SportWeight(0.0),
        }
        save(mixed)
        assert dict(session.sport_weights.load()) == customised_weights(mixed)

        folder = profile.folder
        profile.close()
        reopened = SqliteProfileStorage().open(folder)
        try:
            assert dict(reopened.sport_weights.load()) == {
                Sport.RUNNING: SportWeight(5.0),
                Sport.OTHER: SportWeight(0.0),
            }
        finally:
            reopened.close()
    finally:
        profile.close()


# --- Giorno di validità -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "problem"),
    [
        (date(1899, 12, 31), MeasurementProblem.DATE_TOO_OLD),
        (date(1, 1, 1), MeasurementProblem.DATE_TOO_OLD),
        (date(1900, 1, 1), None),
        (date(1900, 1, 2), None),
        (TODAY - timedelta(days=1), None),
        (TODAY, None),
        (TODAY + timedelta(days=1), MeasurementProblem.DATE_IN_FUTURE),
        (date(9999, 12, 31), MeasurementProblem.DATE_IN_FUTURE),
    ],
)
def test_t_qual_02_validity_day_partitions_and_limits_through_the_use_case_and_the_database(
    tmp_path: Path, day: date, problem: MeasurementProblem | None
) -> None:
    profile = create_profile(tmp_path)
    session = profile.session
    update = UpdateMeasurements(
        session.activities,
        session.physiology,
        session.weights,
        lambda: TODAY,
        session.fixed_data,
    )
    try:
        request = MeasurementUpdate(day, Weight(700), threshold_bpm=160)
        if problem is None:
            update(request)
            assert [e.valid_from for e in session.weights.list_entries()] == [day]
            assert day in {e.valid_from for e in session.physiology.list_entries()}
        else:
            with pytest.raises(InvalidMeasurementsError) as raised:
                update(request)
            assert raised.value.problem is problem
            assert session.weights.list_entries() == []
            assert [e.valid_from for e in session.physiology.list_entries()] == [TODAY]
    finally:
        profile.close()


# --- T-DATI-01: migrazione uccisa a ogni istruzione --------------------------------------------


def statements_from(version: int) -> int:
    return sum(len(statements) for statements in MIGRATIONS[version:])


def run_worker_until_death(folder: Path, kill_at: int) -> int:
    done = subprocess.run(  # noqa: S603 - interprete e script fissi
        [sys.executable, "-I", str(WORKER), str(folder), str(kill_at)],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode in {0, DIED}, done.stderr
    return done.returncode


V1_TABLES_ONLY = {"meta", "activities", "physiology", "load_cache"}
_V1_TOTAL = statements_from(1)  # istruzioni da eseguire partendo da un database v1
V1_KILL_POINTS = sorted({1, 2, 3, 5, _V1_TOTAL // 2, statements_from(2), statements_from(2) + 1,
                         _V1_TOTAL - 1, _V1_TOTAL})  # fmt: skip


@pytest.mark.parametrize("kill_at", range(1, statements_from(2) + 1))
def test_t_dati_01_a_v2_database_killed_at_every_migration_statement_is_still_v2_and_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v2(path)
    tables = ("meta", "activities", "physiology", "load_cache", "sync_journal", "sync_cursor")
    before = {table: rows(path, table) for table in tables}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 2  # la transazione non è stata confermata
    assert "profile_body" not in table_names(path)
    assert {table: rows(path, table) for table in tables} == before  # nemmeno la cache svuotata
    opened = SqliteProfileStorage().open(tmp_path)  # il giro buono riesce da dove era
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        assert opened.fixed_data.get() is None
    finally:
        opened.close()
    assert {table: rows(path, table) for table in ("meta", "activities", "physiology")} == {
        table: before[table] for table in ("meta", "activities", "physiology")
    }


@pytest.mark.parametrize("kill_at", V1_KILL_POINTS)
def test_t_dati_01_a_v1_database_killed_midway_through_the_whole_chain_stays_v1(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    tables = ("meta", "activities", "physiology", "load_cache")
    before = {table: rows(path, table) for table in tables}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 1  # né v2 né v3 a metà: la catena è una sola transazione
    assert table_names(path) == V1_TABLES_ONLY
    assert {table: rows(path, table) for table in tables} == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == SCHEMA_VERSION
    assert [row for row in rows(path, "meta") if row[0] == "futuro"] == [("futuro", "conservami")]


# --- Profilo v2 aperto da due istanze, dati fissi completati insieme -------------------------


def real_ports(config_dir: Path) -> ProfilePorts:
    return ProfilePorts(
        JsonProfileRegistry(config_dir),
        LocalProfileFolders(lambda _folder: FolderKind.LOCAL),
        SqliteProfileStorage(),
    )


def test_t_dati_01_two_instances_opening_one_v2_profile_at_once_migrate_once_and_one_is_refused(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "profilo"
    folder.mkdir()
    write_frozen_v2(folder / PROFILE_DATABASE_NAME)
    barrier = threading.Barrier(2, timeout=30)
    outcomes: list[object] = []
    guard = threading.Lock()
    stored = threading.Semaphore(0)
    released = threading.Event()

    def instance(number: int) -> None:
        opening = OpenProfileFromFolder(real_ports(tmp_path / f"config{number}"), fixed_now)
        barrier.wait()
        try:
            result: object = opening(folder)
        except Exception as error:
            result = error
        with guard:
            outcomes.append(result)
        stored.release()
        if not isinstance(result, Exception):
            released.wait(timeout=30)  # tiene il profilo aperto finché l'altra ha finito
            result.close()  # type: ignore[attr-defined]

    threads = [threading.Thread(target=instance, args=(n,)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    assert stored.acquire(timeout=60)
    assert stored.acquire(timeout=60)
    released.set()
    for thread in threads:
        thread.join(30)

    errors = [o for o in outcomes if isinstance(o, Exception)]
    sessions = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(sessions) == 1, outcomes
    assert [type(e) for e in errors] == [ProfileInUseError]
    assert user_version(folder / PROFILE_DATABASE_NAME) == SCHEMA_VERSION
    assert len(list((folder / "backup").glob("*.sqlite3"))) == 1


def fresh_v3(path: Path) -> None:
    write_frozen_v2(path)
    SqliteProfileStorage().open(path.parent).close()


@pytest.mark.parametrize("round_number", range(12))
def test_t_dati_01_fixed_data_completed_twice_at_once_has_one_winner_and_a_clean_refusal(
    tmp_path: Path, round_number: int
) -> None:
    del round_number
    fresh_v3(tmp_path / PROFILE_DATABASE_NAME)
    candidates = (
        FixedProfileData(Height(171), BirthDate(date(1990, 1, 1))),
        FixedProfileData(Height(182), BirthDate(date(1985, 6, 30))),
    )
    barrier = threading.Barrier(2, timeout=30)
    results: dict[int, BaseException | None] = {}
    guard = threading.Lock()

    def complete(number: int) -> None:
        opened = SqliteProfileStorage().open(tmp_path)  # una connessione per thread
        error: BaseException | None = None
        try:
            barrier.wait()
            try:
                opened.fixed_data.set_once(candidates[number])
            except BaseException as raised:
                error = raised
        finally:
            opened.close()
        with guard:
            results[number] = error

    threads = [threading.Thread(target=complete, args=(n,)) for n in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    winners = [n for n, error in results.items() if error is None]
    losers = [error for error in results.values() if error is not None]
    assert len(winners) == 1, results
    assert [type(e) for e in losers] == [FixedDataAlreadySetError]
    final = SqliteProfileStorage().open(tmp_path)
    try:
        assert final.fixed_data.get() == candidates[winners[0]]
    finally:
        final.close()
