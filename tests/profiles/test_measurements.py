# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dati personali alla creazione e misure datate: peso, FC minima, FC massima, soglia."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import (
    BirthDate,
    FixedProfileData,
    Height,
    ProfileId,
    ProfileName,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.measurements import (
    CompleteFixedProfileData,
    InvalidMeasurementsError,
    MeasurementProblem,
    MeasurementUpdate,
    ReadMeasurementHistory,
    UpdateMeasurements,
)
from ponos_coach.profiles.ports import FixedDataAlreadySetError
from ponos_coach.profiles.use_cases import (
    CreateProfile,
    NewProfileRequest,
    ProfilePorts,
    ProfileSession,
)
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryDisk, InMemoryProfileStorage
from tests.fakes.profiles import InMemoryProfileFolders, InMemoryProfileRegistry
from tests.load.test_profile_overview import World as LoadWorld
from tests.load.test_profile_overview import two_runs

TODAY = date(2026, 3, 1)
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
FIXED = FixedProfileData(Height(172), BirthDate(date(1990, 5, 17)))
WEIGHT = Weight(705)
FEMALE = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.FEMALE)


def create_profile(request: NewProfileRequest | None = None, *, number: int = 1) -> ProfileSession:
    disk = InMemoryDisk()
    ports = ProfilePorts(
        InMemoryProfileRegistry(), InMemoryProfileFolders(disk), InMemoryProfileStorage(disk)
    )
    create = CreateProfile(
        ports, lambda: NOW, lambda: ProfileId(uuid.UUID(f"00000000-0000-4000-8000-{number:012d}"))
    )
    return create(
        request
        or NewProfileRequest(
            ProfileName.parse("Atleta di prova"),
            absolute_path("misure", f"p{number}"),
            "Europe/Rome",
            FEMALE,
            TODAY,
            FIXED,
            WEIGHT,
        )
    )


def updater(session: ProfileSession, today: date = TODAY) -> UpdateMeasurements:
    return UpdateMeasurements(
        session.activities, session.physiology, session.weights, lambda: today
    )


def entries(session: ProfileSession) -> list[PhysiologyEntry]:
    return list(session.physiology.list_entries())


# --- Creazione --------------------------------------------------------------------------------


def test_create_stores_height_birth_date_and_the_first_weight_valid_from_today() -> None:
    session = create_profile()

    assert session.fixed_data.get() == FIXED
    assert list(session.weights.list_entries()) == [WeightEntry(TODAY, WEIGHT)]
    assert entries(session) == [PhysiologyEntry(TODAY, FEMALE)]


def test_create_without_personal_data_leaves_the_profile_without_them() -> None:
    session = create_profile(
        NewProfileRequest(
            ProfileName.parse("Atleta di prova"),
            absolute_path("misure", "senza"),
            "UTC",
            FEMALE,
            TODAY,
        )
    )

    assert session.fixed_data.get() is None
    assert list(session.weights.list_entries()) == []


# --- Aggiornare le misure ---------------------------------------------------------------------


def test_new_threshold_from_a_day_keeps_the_other_values_of_that_day() -> None:
    session = create_profile()
    day = TODAY - timedelta(days=10)

    updater(session)(MeasurementUpdate(day, threshold_bpm=165))

    assert entries(session) == [
        PhysiologyEntry(
            day, Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(165), Sex.FEMALE)
        ),
        PhysiologyEntry(TODAY, FEMALE),
    ]
    assert list(session.weights.list_entries()) == [WeightEntry(TODAY, WEIGHT)]  # peso intatto


def test_values_not_given_are_the_ones_valid_on_that_day_not_the_latest() -> None:
    session = create_profile()
    updater(session)(
        MeasurementUpdate(date(2026, 1, 1), min_bpm=48, max_bpm=188, threshold_bpm=168)
    )
    updater(session)(MeasurementUpdate(date(2026, 2, 1), max_bpm=195, threshold_bpm=172))

    updater(session)(MeasurementUpdate(date(2026, 1, 15), min_bpm=52))

    january_15 = [e for e in entries(session) if e.valid_from == date(2026, 1, 15)]
    (entry,) = january_15
    assert (entry.physiology.limits.min_bpm, entry.physiology.limits.max_bpm) == (52, 188)
    assert entry.physiology.threshold.bpm == 168  # quella di gennaio, non quella di febbraio
    assert entry.physiology.sex is Sex.FEMALE  # il sesso non cambia


def test_the_same_day_replaces_the_entry() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(TODAY, threshold_bpm=160))
    updater(session)(MeasurementUpdate(TODAY, max_bpm=185))

    (entry,) = entries(session)
    assert (entry.physiology.limits.max_bpm, entry.physiology.threshold.bpm) == (185, 160)


def test_a_weight_alone_adds_a_weight_entry_and_no_physiology_entry() -> None:
    session = create_profile()
    later = TODAY - timedelta(days=1)

    updater(session)(MeasurementUpdate(later, weight=Weight(698)))

    assert list(session.weights.list_entries()) == [
        WeightEntry(later, Weight(698)),
        WeightEntry(TODAY, WEIGHT),
    ]
    assert entries(session) == [PhysiologyEntry(TODAY, FEMALE)]


def test_weight_and_heart_rate_together_are_saved_together() -> None:
    session = create_profile()

    updater(session)(MeasurementUpdate(TODAY, Weight(690), min_bpm=55))

    assert list(session.weights.list_entries()) == [WeightEntry(TODAY, Weight(690))]
    assert entries(session)[0].physiology.limits.min_bpm == 55


@pytest.mark.parametrize(
    "changes",
    [
        {"min_bpm": 170},  # minima pari alla soglia
        {"min_bpm": 191},  # minima sopra la massima
        {"max_bpm": 169},  # massima sotto la soglia
        {"threshold_bpm": 50},  # soglia pari alla minima (deve stare sopra)
        {"threshold_bpm": 191},  # soglia sopra la massima
        {"min_bpm": 19},  # fuori intervallo (minimo 20)
        {"max_bpm": 251},  # fuori intervallo (massimo 250)
        {"threshold_bpm": 39},  # fuori intervallo (minimo 40)
    ],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
def test_inconsistent_heart_rate_is_refused_and_nothing_is_written(
    changes: dict[str, int],
) -> None:
    session = create_profile()
    before = (entries(session), list(session.weights.list_entries()))

    with pytest.raises(InvalidMeasurementsError) as error:
        updater(session)(MeasurementUpdate(TODAY, Weight(690), **changes))

    assert error.value.problem is MeasurementProblem.INVALID_HEART_RATE
    assert (entries(session), list(session.weights.list_entries())) == before  # nemmeno il peso


def test_the_constraints_are_checked_on_the_result_so_two_values_can_move_together() -> None:
    session = create_profile()

    updater(session)(MeasurementUpdate(TODAY, min_bpm=171, max_bpm=200, threshold_bpm=180))

    assert entries(session)[0].physiology.limits.min_bpm == 171


def test_on_point_of_the_heart_rate_constraints_is_accepted() -> None:
    session = create_profile()

    updater(session)(
        MeasurementUpdate(TODAY, min_bpm=169, threshold_bpm=170)
    )  # soglia = minima + 1
    updater(session)(MeasurementUpdate(TODAY, max_bpm=170))  # soglia = massima

    (entry,) = entries(session)
    assert (entry.physiology.limits.min_bpm, entry.physiology.threshold.bpm) == (169, 170)
    assert entry.physiology.limits.max_bpm == 170


def test_a_day_in_the_future_is_refused_but_today_is_accepted() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(TODAY, Weight(700)))  # on point: oggi

    with pytest.raises(InvalidMeasurementsError) as error:
        updater(session)(MeasurementUpdate(TODAY + timedelta(days=1), Weight(700)))  # off point

    assert error.value.problem is MeasurementProblem.DATE_IN_FUTURE


def test_a_day_before_1900_is_refused() -> None:
    session = create_profile()

    with pytest.raises(InvalidMeasurementsError) as error:
        updater(session)(MeasurementUpdate(date(1899, 12, 31), Weight(700)))

    assert error.value.problem is MeasurementProblem.DATE_TOO_OLD


def test_an_update_with_no_measure_is_refused() -> None:
    with pytest.raises(InvalidMeasurementsError) as error:
        MeasurementUpdate(TODAY)

    assert error.value.problem is MeasurementProblem.NOTHING_TO_UPDATE


def test_error_messages_never_contain_the_values() -> None:
    session = create_profile()

    with pytest.raises(InvalidMeasurementsError) as error:
        updater(session)(MeasurementUpdate(TODAY, min_bpm=171))

    assert "171" not in str(error.value)
    assert str(error.value) == "invalid_heart_rate"


# --- T-CALC-05 con il caso d'uso --------------------------------------------------------------


def test_t_calc_05_a_heart_rate_change_from_a_date_recalculates_like_from_scratch() -> None:
    """Cambio di FC massima e soglia a una data, fatto con il caso d'uso: la panoramica
    successiva è uguale a quella di un profilo che aveva i valori giusti fin dall'inizio."""
    world = LoadWorld()
    world.profile.physiology.put(PhysiologyEntry(date(2026, 1, 1), _physiology(170, 190)))
    two_runs(world)  # una corsa il 15 gennaio, una il 10 febbraio
    before_change = world.overview()

    UpdateMeasurements(
        world.profile.activities,
        world.profile.physiology,
        world.profile.weights,
        lambda: date(2026, 3, 1),
    )(MeasurementUpdate(date(2026, 2, 1), max_bpm=180, threshold_bpm=160))
    after_change = world.overview()

    scratch = LoadWorld()
    scratch.profile.physiology.put(PhysiologyEntry(date(2026, 1, 1), _physiology(170, 190)))
    scratch.profile.physiology.put(PhysiologyEntry(date(2026, 2, 1), _physiology(160, 180)))
    two_runs(scratch)
    expected = scratch.overview()

    assert after_change.activities[0] == before_change.activities[0]  # prima del cambio: uguale
    assert after_change.activities[1] != before_change.activities[1]  # dopo: ricalcolata
    assert after_change.activities == expected.activities
    assert np.array_equal(after_change.pmc.ctl, expected.pmc.ctl)


def _physiology(threshold: int, max_bpm: int) -> Physiology:
    return Physiology(HeartRateLimits(50, max_bpm), ThresholdHeartRate(threshold), Sex.MALE)


# --- Storico e dati fissi ---------------------------------------------------------------------


def test_history_lists_fixed_data_weights_and_parameters_by_date() -> None:
    session = create_profile()
    update = updater(session)
    update(MeasurementUpdate(date(2026, 2, 1), Weight(710), threshold_bpm=165))
    update(MeasurementUpdate(date(2026, 1, 1), Weight(720)))

    history = ReadMeasurementHistory(session.fixed_data, session.weights, session.physiology)()

    assert history.fixed == FIXED
    assert [w.valid_from for w in history.weights] == [date(2026, 1, 1), date(2026, 2, 1), TODAY]
    assert [e.valid_from for e in history.physiology] == [date(2026, 2, 1), TODAY]


def test_history_of_a_profile_without_fixed_data_says_so() -> None:
    session = create_profile(
        NewProfileRequest(
            ProfileName.parse("Atleta di prova"),
            absolute_path("misure", "storico"),
            "UTC",
            FEMALE,
            TODAY,
        )
    )

    history = ReadMeasurementHistory(session.fixed_data, session.weights, session.physiology)()

    assert history.fixed is None
    assert history.weights == ()


def test_fixed_data_can_be_completed_once_and_never_changed() -> None:
    session = create_profile(
        NewProfileRequest(
            ProfileName.parse("Atleta di prova"),
            absolute_path("misure", "completa"),
            "UTC",
            FEMALE,
            TODAY,
        )
    )
    complete = CompleteFixedProfileData(session.fixed_data)

    complete(FIXED)
    with pytest.raises(FixedDataAlreadySetError):
        complete(FixedProfileData(Height(180), BirthDate(date(1985, 1, 1))))

    assert session.fixed_data.get() == FIXED
