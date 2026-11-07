# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-CALC-01...07 della fase 3: carico con le zone e i pesi salvati, ricalcolo e coerenza.

Il collaudatore ricalcola ogni risultato in modo indipendente (oracoli scritti qui, senza usare le
funzioni sotto prova) e confronta la panoramica incrementale, quella che l'utente vede dopo una
serie di modifiche, con il calcolo da zero su un profilo nuovo con la cache vuota.
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import FileFormat, HeartRateSeries, Sport
from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.load.domain import (
    ZONE_COUNT,
    HeartRateLimits,
    LoadSource,
    Physiology,
    PhysiologyEntry,
    Sex,
    SportWeight,
    ThresholdHeartRate,
    compute_activity_load,
    default_weight,
    load_fingerprint,
    threshold_for_sport,
)
from ponos_coach.load.use_cases import ComputeProfileOverview, SetSportWeights
from ponos_coach.profiles.domain import Weight
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementUpdate,
    UpdateMeasurements,
)
from tests.builders import physiology as make_physiology
from tests.builders import physiology_entry
from tests.collaudo.fase3_support import (
    CHANGE_DAYS,
    TODAY,
    MixedActivity,
    assert_same_overview,
    profile_with_mixed,
    scratch_overview,
    write_mixed,
)
from tests.collaudo.support import RealProfile, create_profile, open_profile_from_folder
from tests.fixtures.generators import fit_writer as fw

PHYSIOLOGY = make_physiology(170)  # FC 50-190, soglia 170, uomo
SECONDS_PER_ZONE_TEST = 3600
WEIGHT_SAMPLES = (0.0, 0.05, 0.5, 1.0, 1.1, 2.0, 5.0)


def constant_series(bpm: float, seconds: int) -> HeartRateSeries:
    return HeartRateSeries(np.arange(seconds, dtype=float), np.full(seconds, float(bpm)))


# --- T-CALC-01 con i pesi e le zone --------------------------------------------------------


@pytest.mark.parametrize("sport", list(Sport))
@pytest.mark.parametrize("weight", WEIGHT_SAMPLES)
def test_t_calc_01_an_hour_at_the_threshold_of_each_sport_is_hrtss_100_all_in_zone_5(
    sport: Sport, weight: float
) -> None:
    threshold = threshold_for_sport(PHYSIOLOGY, sport)  # in bici: soglia della corsa - 4
    series = constant_series(threshold, SECONDS_PER_ZONE_TEST)

    result = compute_activity_load(
        sport, SECONDS_PER_ZONE_TEST, series, PHYSIOLOGY, SportWeight(weight)
    )

    assert result.source is LoadSource.HEART_RATE
    assert result.hr_tss == pytest.approx(100.0, rel=1e-9)
    assert result.load == pytest.approx(100.0 * weight, rel=1e-9, abs=1e-12)
    assert result.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, float(SECONDS_PER_ZONE_TEST))


@pytest.mark.parametrize("sport", list(Sport))
@pytest.mark.parametrize("weight", WEIGHT_SAMPLES)
def test_t_calc_01_an_hour_at_the_resting_rate_is_zero_load_and_all_zone_1(
    sport: Sport, weight: float
) -> None:
    series = constant_series(PHYSIOLOGY.limits.min_bpm, SECONDS_PER_ZONE_TEST)

    result = compute_activity_load(
        sport, SECONDS_PER_ZONE_TEST, series, PHYSIOLOGY, SportWeight(weight)
    )

    assert (result.trimp, result.hr_tss, result.load) == (0.0, 0.0, 0.0)
    assert result.zone_seconds.values == (float(SECONDS_PER_ZONE_TEST), 0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("weight", WEIGHT_SAMPLES)
def test_t_calc_03_the_weight_scales_the_load_and_nothing_else(weight: float) -> None:
    series = constant_series(150, 1800)
    reference = compute_activity_load(Sport.TRAIL, 1800, series, PHYSIOLOGY, SportWeight(1.0))

    result = compute_activity_load(Sport.TRAIL, 1800, series, PHYSIOLOGY, SportWeight(weight))

    assert (result.trimp, result.hr_tss) == (reference.trimp, reference.hr_tss)
    assert result.zone_seconds == reference.zone_seconds
    assert result.load == pytest.approx(reference.hr_tss * weight, rel=1e-12, abs=1e-12)
    assert (result.used_samples, result.discarded_samples) == (
        reference.used_samples,
        reference.discarded_samples,
    )


def test_t_calc_03_an_estimate_keeps_the_weight_and_the_measured_zone_seconds() -> None:
    result = compute_activity_load(Sport.CYCLING, 3600, None, PHYSIOLOGY, SportWeight(2.0))

    assert result.source is LoadSource.ESTIMATE
    assert result.zone_seconds.total == 0.0
    assert result.load == pytest.approx(result.hr_tss * 2.0)
    assert result.hr_tss == pytest.approx(100.0 * 0.65**2)  # durata_h * 100 * IF^2


@st.composite
def random_series(draw: st.DrawFn) -> tuple[HeartRateSeries, float]:
    """Secondi crescenti con buchi anche oltre 10 s e bpm anche fuori limiti."""
    steps = draw(st.lists(st.integers(1, 15), min_size=1, max_size=60))
    seconds = np.cumsum([0, *steps[:-1]]).astype(float)
    bpm = np.array(
        draw(st.lists(st.integers(10, 260), min_size=len(steps), max_size=len(steps))),
        dtype=float,
    )
    moving = float(sum(steps))
    return HeartRateSeries(seconds, bpm), moving


def naive_zone_seconds(
    series: HeartRateSeries, limits: HeartRateLimits, threshold: int
) -> tuple[list[float], float]:
    """Oracolo ingenuo: ciclo per campione, confini in interi (percentuale * soglia)."""
    seconds = series.seconds.tolist()
    gaps = [b - a for a, b in pairwise(seconds)]
    ordered = sorted(gaps)
    middle = len(ordered) // 2
    median = (
        0.0
        if not ordered
        else ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    validity = [*gaps, median] if gaps else [1.0]
    zones = [0.0] * ZONE_COUNT
    counted = 0.0
    for bpm, valid_s in zip(series.bpm.tolist(), validity, strict=True):
        if not limits.min_bpm <= bpm <= limits.max_bpm or valid_s > 10.0:
            continue
        counted += valid_s
        scaled = bpm * 100
        zone = sum(1 for percent in (85, 90, 95, 100) if scaled >= percent * threshold)
        zones[zone] += valid_s
    return zones, counted


@given(random_series(), st.sampled_from(list(Sport)), st.floats(0.0, 5.0))
def test_t_calc_03_zone_seconds_equal_the_naive_oracle_and_never_exceed_the_counted_time(
    drawn: tuple[HeartRateSeries, float], sport: Sport, weight: float
) -> None:
    series, moving = drawn
    result = compute_activity_load(sport, moving, series, PHYSIOLOGY, SportWeight(weight))
    expected, counted = naive_zone_seconds(
        series, PHYSIOLOGY.limits, threshold_for_sport(PHYSIOLOGY, sport)
    )

    assert result.zone_seconds.values == pytest.approx(tuple(expected), abs=1e-9)
    assert result.zone_seconds.total == pytest.approx(counted, abs=1e-9)
    assert result.load >= 0.0
    assert result.load == pytest.approx(result.hr_tss * weight, rel=1e-12, abs=1e-12)


# --- Impronta dei carichi ---------------------------------------------------------------------


def test_t_calc_05_the_fingerprint_depends_on_every_input_of_the_load_and_on_nothing_else() -> None:
    base = load_fingerprint(PHYSIOLOGY, {})
    different = {
        "min": Physiology(HeartRateLimits(51, 190), ThresholdHeartRate(170), Sex.MALE),
        "max": Physiology(HeartRateLimits(50, 191), ThresholdHeartRate(170), Sex.MALE),
        "threshold": Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(169), Sex.MALE),
        "sex": Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.FEMALE),
    }

    assert len({load_fingerprint(p, {}) for p in different.values()} | {base}) == 5
    assert load_fingerprint(PHYSIOLOGY, {Sport.RUNNING: SportWeight(1.5)}) != base
    one = {Sport.RUNNING: SportWeight(1.5), Sport.CYCLING: SportWeight(0.5)}
    swapped = {Sport.CYCLING: SportWeight(0.5), Sport.RUNNING: SportWeight(1.5)}
    assert load_fingerprint(PHYSIOLOGY, one) == load_fingerprint(PHYSIOLOGY, swapped)
    assert load_fingerprint(PHYSIOLOGY, {Sport.RUNNING: SportWeight(1.5)}) != load_fingerprint(
        PHYSIOLOGY, {Sport.RUNNING: SportWeight(1.5000001)}
    )
    assert load_fingerprint(PHYSIOLOGY, {Sport.RUNNING: SportWeight(1.5)}) != load_fingerprint(
        PHYSIOLOGY, {Sport.CYCLING: SportWeight(1.5)}
    )


# --- Cache, panoramica e dettaglio sulla pila reale ------------------------------------------


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = profile_with_mixed(tmp_path, "pila")
    yield opened
    opened.close()  # ripetibile


def raising_decoder(*_args: object) -> None:
    raise AssertionError("la cache avrebbe dovuto bastare: nessun file va riletto")


def test_t_calc_05_a_cached_overview_is_bit_identical_with_zones_and_saved_weights(
    tmp_path: Path, profile: RealProfile
) -> None:
    weights = {
        Sport.RUNNING: SportWeight(1.35),
        Sport.STRENGTH: SportWeight(0.0),
        Sport.CYCLING: SportWeight(2.5),
    }
    SetSportWeights(profile.session.activities, profile.session.sport_weights)(weights)
    first = profile.overview()

    cached = profile.overview(decode=raising_decoder)  # type: ignore[arg-type]

    assert_same_overview(first, cached, TODAY)
    assert any(a.load.zone_seconds.total > 0 for a in first.activities)
    by_sport = {a.sport: a for a in first.activities}
    assert by_sport[Sport.STRENGTH].load.load == 0.0
    assert by_sport[Sport.STRENGTH].load.hr_tss > 0.0  # lo zero è del peso, non dell'attività
    folder = profile.folder
    profile.close()
    reopened = open_profile_from_folder(profile.ports, folder)
    try:
        assert_same_overview(first, reopened.overview(decode=raising_decoder), TODAY)  # type: ignore[arg-type]
    finally:
        reopened.close()


def test_t_calc_05_the_detail_of_every_activity_gives_the_same_load_as_the_overview(
    profile: RealProfile,
) -> None:
    session = profile.session
    SetSportWeights(session.activities, session.sport_weights)(
        {Sport.TRAIL: SportWeight(1.9), Sport.CYCLING: SportWeight(0.3)}
    )
    session.physiology.put(physiology_entry(date(2026, 2, 1), 160))
    overview = profile.overview()
    detail = GetActivityDetail(
        session.activities,
        profile.archive,
        session.physiology,
        session.sport_weights,
        decode_stored_activity,
        timezone=ZoneInfo("Europe/Rome"),
    )

    for item in overview.activities:
        result = detail(item.identity)
        assert isinstance(result, ActivityDetail)
        assert result.load == item.load
        assert result.local_day == item.local_day
        run_threshold = 160  # la prima voce (1 febbraio) vale anche per i giorni che la precedono
        bike_offset = 4 if item.sport is Sport.CYCLING else 0
        assert result.threshold_bpm == run_threshold - bike_offset
        assert result.zone_starts_bpm == tuple(
            result.threshold_bpm * percent / 100 for percent in (85, 90, 95, 100)
        )


# --- Ricalcolo dopo una serie di modifiche = calcolo da zero ---------------------------------


def apply_random_step(rng: random.Random, profile: RealProfile, history: list[str]) -> None:
    """Un'operazione a caso sul profilo; i rifiuti non cambiano nulla (verificato dal chiamante)."""
    session = profile.session
    update = UpdateMeasurements(
        session.activities,
        session.physiology,
        session.weights,
        lambda: TODAY,
        session.fixed_data,
    )
    day = rng.choice(CHANGE_DAYS)
    kind = rng.choice(("threshold", "limits", "weight_only", "sport_weights", "future", "both"))
    history.append(f"{kind}@{day}")
    if kind == "threshold":
        update(MeasurementUpdate(day, threshold_bpm=rng.randint(60, 190)))
    elif kind == "limits":
        update(MeasurementUpdate(day, min_bpm=rng.randint(30, 59), max_bpm=rng.randint(191, 210)))
    elif kind == "weight_only":
        update(MeasurementUpdate(day, weight=Weight(rng.randint(200, 3000))))
    elif kind == "both":
        update(MeasurementUpdate(day, weight=Weight(700), threshold_bpm=rng.randint(60, 190)))
    elif kind == "future":
        with pytest.raises(InvalidMeasurementsError):
            update(MeasurementUpdate(TODAY + timedelta(days=1), threshold_bpm=150))
    else:
        chosen = {
            sport: SportWeight(round(rng.uniform(0, 5), 2))
            for sport in rng.sample(list(Sport), rng.randint(0, len(Sport)))
        }
        SetSportWeights(session.activities, session.sport_weights)(chosen)


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_t_calc_05_every_step_of_a_random_sequence_of_updates_equals_the_calculation_from_scratch(
    tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)  # deterministico: la sequenza è nel nome del test
    profile = profile_with_mixed(tmp_path, "incrementale")
    history: list[str] = []
    try:
        for step in range(8):
            apply_random_step(rng, profile, history)
            incremental = profile.overview()  # cache riempita dai passi precedenti
            scratch, scratch_profile = scratch_overview(
                tmp_path,
                f"zero{seed}_{step}",
                profile.session.physiology.list_entries(),
                profile.session.sport_weights.load(),
            )
            scratch_profile.close()

            try:
                assert_same_overview(incremental, scratch, TODAY)
            except AssertionError as error:
                error.add_note(f"operazioni fin qui: {history}")
                raise
    finally:
        profile.close()


def test_t_calc_05_the_same_update_repeated_and_the_same_day_replaced_leave_one_entry(
    tmp_path: Path,
) -> None:
    profile = profile_with_mixed(tmp_path)
    session = profile.session
    update = UpdateMeasurements(
        session.activities,
        session.physiology,
        session.weights,
        lambda: TODAY,
        session.fixed_data,
    )
    try:
        for _ in range(3):
            update(MeasurementUpdate(date(2026, 2, 1), threshold_bpm=150))
        update(MeasurementUpdate(date(2026, 2, 1), threshold_bpm=155))
        update(MeasurementUpdate(date(2026, 2, 1), weight=Weight(701)))  # solo peso: FC com'era

        entries = [e for e in session.physiology.list_entries() if e.valid_from == date(2026, 2, 1)]

        assert [e.physiology.threshold.bpm for e in entries] == [155]
        assert [e.valid_from for e in session.physiology.list_entries()] == sorted(
            {e.valid_from for e in session.physiology.list_entries()}
        )
        assert len(session.weights.list_entries()) == 1  # un solo peso, nella data 1 febbraio
    finally:
        profile.close()


# --- Vincolo min < soglia <= max in tutte le combinazioni -----------------------------------


BASE = (50, 190, 170)
MIN_VALUES = (None, 19, 20, 21, 50, 169, 170, 171, 249, 250)
MAX_VALUES = (None, 49, 50, 51, 169, 170, 171, 190, 249, 250, 251)
THRESHOLD_VALUES = (None, 39, 40, 41, 50, 51, 169, 170, 190, 191, 250, 251)
CONSTRAINT_DAY = date(2026, 4, 1)


def expected_triple(
    minimum: int | None, maximum: int | None, threshold: int | None
) -> tuple[int, int, int] | None:
    """Oracolo: il risultato dell'aggiornamento se coerente, altrimenti `None` (rifiuto)."""
    low = BASE[0] if minimum is None else minimum
    high = BASE[1] if maximum is None else maximum
    thr = BASE[2] if threshold is None else threshold
    if 20 <= low < high <= 250 and 40 <= thr <= 250 and low < thr <= high:
        return low, high, thr
    return None


def test_t_calc_05_the_heart_rate_constraint_holds_for_every_combination_and_refusals_write_nothing(
    tmp_path: Path,
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
    base = PhysiologyEntry(CONSTRAINT_DAY, make_physiology(BASE[2]))
    accepted = refused = 0
    try:
        for minimum in MIN_VALUES:
            for maximum in MAX_VALUES:
                for threshold in THRESHOLD_VALUES:
                    if (minimum, maximum, threshold) == (None, None, None):
                        continue
                    session.physiology.put(base)
                    before_entries = list(session.physiology.list_entries())
                    before_weights = list(session.weights.list_entries())
                    request = MeasurementUpdate(
                        CONSTRAINT_DAY, Weight(777), minimum, maximum, threshold
                    )
                    want = expected_triple(minimum, maximum, threshold)

                    if want is None:
                        with pytest.raises(InvalidMeasurementsError):
                            update(request)
                        assert session.physiology.list_entries() == before_entries
                        assert session.weights.list_entries() == before_weights  # peso incluso
                        refused += 1
                    else:
                        update(request)
                        [entry] = [
                            e for e in session.physiology.list_entries()
                            if e.valid_from == CONSTRAINT_DAY
                        ]  # fmt: skip
                        got = (
                            entry.physiology.limits.min_bpm,
                            entry.physiology.limits.max_bpm,
                            entry.physiology.threshold.bpm,
                        )
                        assert got == want
                        accepted += 1
    finally:
        profile.close()
    assert accepted > 50
    assert refused > 500  # la griglia prova davvero i rifiuti, non solo i casi buoni


def test_t_calc_05_a_stored_database_refuses_incoherent_heart_rate_even_without_the_use_case(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    database = profile.folder / "ponos.sqlite3"
    profile.close()
    statements = (
        "INSERT INTO physiology VALUES ('2026-04-01', 50, 190, 50, 'male')",  # soglia = minima
        "INSERT INTO physiology VALUES ('2026-04-01', 50, 190, 191, 'male')",  # soglia > massima
        "INSERT INTO physiology VALUES ('2026-04-01', 50, 50, 50, 'male')",  # minima = massima
        "INSERT INTO physiology VALUES ('2026-04-01', 19, 190, 170, 'male')",
        "INSERT INTO physiology VALUES ('2026-04-01', 50, 251, 170, 'male')",
        "INSERT INTO physiology VALUES ('2026-04-01', 50, 190, 39, 'male')",
    )
    with closing(sqlite3.connect(database)) as raw:
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(statement)


# --- Fusi orari e ora legale -----------------------------------------------------------------


def starts_near(boundary: datetime) -> tuple[MixedActivity, ...]:
    """Cinque corse identiche attorno a un istante, a +-90 minuti e al minuto stesso."""
    offsets = (-90, -1, 0, 1, 90)
    return tuple(
        MixedActivity(
            f"a{index}", boundary + timedelta(minutes=minutes), fw.Sport.RUNNING,
            fw.SubSport.GENERIC, ((150, 600),),
        )
        for index, minutes in enumerate(offsets)
    )  # fmt: skip


def loads_by_start(profile: RealProfile, zone: ZoneInfo, today: date) -> dict[datetime, float]:
    session = profile.session
    overview = ComputeProfileOverview(
        session.activities,
        profile.archive,
        session.physiology,
        session.load_cache,
        decode_stored_activity,
        sport_weights=session.sport_weights,
        timezone=zone,
        today=lambda: today,
    ).execute()
    return {a.start: a.load.load for a in overview.activities}


@pytest.mark.parametrize(
    ("zone_name", "change_day"),
    [
        ("Europe/Rome", date(2026, 3, 29)),  # giorno in cui salta da 02:00 a 03:00
        ("Europe/Rome", date(2026, 10, 25)),  # giorno in cui 02:00-03:00 si ripete
        ("Africa/Cairo", date(2026, 4, 24)),  # la mezzanotte locale non esiste (00:00 -> 01:00)
        ("America/Havana", date(2026, 3, 8)),  # idem
        ("Pacific/Auckland", date(2026, 3, 29)),
        ("America/Los_Angeles", date(2026, 3, 29)),
        ("Pacific/Kiritimati", date(2026, 6, 15)),  # UTC+14
        ("Pacific/Pago_Pago", date(2026, 6, 15)),  # UTC-11
    ],
)
def test_t_calc_07_the_parameters_of_a_day_apply_by_the_local_day_across_dst_and_far_zones(
    tmp_path: Path, zone_name: str, change_day: date
) -> None:
    zone = ZoneInfo(zone_name)
    boundary = datetime.combine(change_day, time(0, 0), tzinfo=zone).astimezone(UTC)
    specs = starts_near(boundary)
    profile = create_profile(tmp_path)
    folder = write_mixed(tmp_path / "file", specs)
    profile.import_folder(folder)
    session = profile.session
    session.physiology.put(physiology_entry(date(2026, 1, 1), 170))
    session.physiology.put(physiology_entry(change_day, 150))
    expected_thresholds: dict[datetime, int] = {
        spec.start: 150 if spec.start.astimezone(zone).date() >= change_day else 170
        for spec in specs
    }
    try:
        loads = loads_by_start(profile, zone, date(2026, 12, 31))

        assert set(expected_thresholds.values()) == {150, 170}  # il confine taglia le cinque corse
        for spec in specs:
            activity = decode_stored_activity(
                (folder / f"{spec.name}.fit").read_bytes(), FileFormat.FIT
            )
            expected = compute_activity_load(
                Sport.RUNNING,
                activity.timer_s,
                activity.heart_rate,
                make_physiology(expected_thresholds[spec.start]),
                default_weight(Sport.RUNNING),
            ).load
            assert loads[spec.start] == pytest.approx(expected), spec.name
    finally:
        profile.close()


def test_t_calc_07_a_one_second_and_a_twenty_four_hour_activity_with_saved_weights_and_zones() -> (
    None
):
    one_second = constant_series(170, 1)
    day = constant_series(171, 24 * 3600)
    weight = SportWeight(1.7)

    short = compute_activity_load(Sport.RUNNING, 1.0, one_second, PHYSIOLOGY, weight)
    long = compute_activity_load(Sport.RUNNING, 24 * 3600.0, day, PHYSIOLOGY, weight)

    assert short.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 1.0)  # un campione vale 1 s
    assert long.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 24 * 3600.0)
    assert long.hr_tss > 24 * 100  # più di 24 ore alla soglia
    assert long.load == pytest.approx(long.hr_tss * 1.7)
    assert np.isfinite([short.load, long.load]).all()
