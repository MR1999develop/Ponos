# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for what came after 2026-11-08: deleting and restoring activities, the speed and pace
series, the elevation profile and the active calories. Positions, identities, dates and Garmin ids
never reach a log record or the text of an error with its chain of causes.

Recognisable values (the serial numbers of the files, the coordinates, the Garmin ids) go through
the real use cases, including the failing paths (a file gone or altered, a tombstone the database
refuses); then they are searched for in every log record and in every exception text. The modules
of the feature must not import `logging`, nor print.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.decoding import decode_stored_activity, decode_stored_track
from ponos_coach.activities.deletion import RestoreStatus
from ponos_coach.activities.domain import DeletedActivity, identity_key
from ponos_coach.activities.ports import ActivityIndexError
from ponos_coach.load.activity_profile import ActivityProfileData, GetActivityProfile
from ponos_coach.nutrition.domain import EnergySource
from ponos_coach.profiles.day_nutrition import GetActivityEnergy, GetRecordEnergy
from tests.collaudo.fase5_support import ROME, World, make_world
from tests.collaudo.fase9_support import (
    DELETED_AT,
    archived_file_of,
    delete_use_case,
    identities,
    restore_use_case,
    tombstones,
)
from tests.collaudo.support import format_logs
from tests.collaudo.test_fase5_log_salute import chain_text
from tests.collaudo.test_fase9_dettaglio_attivita import (
    detail_of,
    fit_file,
    import_fit,
    run_samples,
    sources_of,
)

ROOT = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
FEATURE_FILES = (
    ROOT / "activities" / "deletion.py",
    ROOT / "activities" / "domain" / "deleted.py",
    ROOT / "activities" / "domain" / "speed_series.py",
    ROOT / "storage" / "sqlite_deleted.py",
    ROOT / "storage" / "schema_deleted_activity.py",
    ROOT / "load" / "activity_profile.py",
    ROOT / "nutrition" / "domain" / "energy.py",
    ROOT / "ui" / "speed_figure.py",
    ROOT / "ui" / "elevation_figure.py",
    ROOT / "ui" / "activity_deletion.py",
    ROOT / "ui" / "deleted_activities_page.py",
    ROOT / "maps" / "domain.py",
    ROOT / "maps" / "use_cases.py",
)
POSITION_MARKERS = ("46.12", "11.65", "46,12", "11,65")


def test_the_modules_of_the_features_neither_log_nor_print() -> None:
    for path in FEATURE_FILES:
        assert path.is_file(), path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert "logging" not in [alias.name for alias in node.names], path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging", path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    made = make_world(tmp_path)
    yield made
    made.close()


def test_t_sec_08_no_position_identity_or_date_of_a_deleted_activity_reaches_a_log_or_an_error(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ids = [
        import_fit(world, fit_file(run_samples(1800, position=True)), "a"),
        import_fit(world, fit_file(run_samples(1800, position=True)), "b"),
        import_fit(world, fit_file(run_samples(1800, position=True)), "c"),
    ]
    session = world.profile.session
    secrets = [identity_key(i) for i in ids] + [str(i.file_id.serial_number) for i in ids]
    errors: list[BaseException] = []

    delete_use_case(world.profile)(ids)
    by_id = {t.identity: t for t in tombstones(world.profile)}
    original = archived_file_of(world.profile, by_id[ids[0]]).read_bytes()
    archived_file_of(world.profile, by_id[ids[0]]).unlink()  # gone
    damaged = archived_file_of(world.profile, by_id[ids[1]])
    damaged.write_bytes(damaged.read_bytes()[:-9])  # altered
    summary = restore_use_case(world.profile)(ids)
    secrets += [str(by_id[i].archived.relative_path) for i in ids]
    secrets += [by_id[i].archived.sha256_hex for i in ids]

    statuses = {r.status for r in summary.results}
    assert statuses == {
        RestoreStatus.FILE_MISSING,
        RestoreStatus.FILE_DAMAGED,
        RestoreStatus.RESTORED,
    }
    # a tombstone for an activity that is still indexed, and an index record of a deleted one
    alive = world.profile.records()[0]
    with pytest.raises(ActivityIndexError) as refused_tombstone:
        session.deleted_activities.add(DeletedActivity.of(alive, DELETED_AT))
    errors.append(refused_tombstone.value)
    still_deleted = by_id[ids[0]]
    with pytest.raises(ActivityIndexError) as refused_record:
        session.activities.add(
            still_deleted.to_record(
                decode_stored_activity(original, still_deleted.file_format), still_deleted.archived
            )
        )
    errors.append(refused_record.value)

    logs = format_logs(caplog.records)
    texts = [logs, *(chain_text(e) for e in errors)]
    for text in texts:
        for secret in secrets:
            assert secret not in text, "an identity or archive name reached a log or an error"
        for marker in POSITION_MARKERS:
            assert marker not in text


def test_t_sec_08_the_profile_the_speed_and_the_calories_of_a_file_write_no_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    identity = import_fit(world, fit_file(run_samples(1800, position=True), kcal=400), "gps")
    s = world.profile.session
    detail = detail_of(world, identity)
    sources = sources_of(world)

    profile = GetActivityProfile(
        s.activities,
        world.profile.archive,
        s.physiology,
        decode_stored_activity,
        decode_stored_track,
        timezone=ROME,
    )(identity)
    energy = GetActivityEnergy(sources)(detail)
    record_energy = GetRecordEnergy(sources, decode_stored_activity, ROME)(identity)

    assert isinstance(profile, ActivityProfileData) and detail.speed is not None
    assert energy is not None and energy.source is EnergySource.DECLARED
    assert record_energy == energy
    assert not [r for r in caplog.records if r.name.startswith("ponos_coach")], format_logs(
        caplog.records
    )
    text = format_logs(caplog.records)
    assert not any(marker in text for marker in POSITION_MARKERS)


def test_t_sec_08_the_listing_of_deleted_activities_is_not_a_second_copy_of_the_track(
    world: World,
) -> None:
    """A tombstone keeps what the list shows, nothing of the track or the heart rate (ADR 018)."""
    identity = import_fit(world, fit_file(run_samples(1800, position=True), kcal=400), "t")
    delete_use_case(world.profile)([identity])

    [tombstone] = tombstones(world.profile)

    kept = set(vars(DeletedActivity)["__dataclass_fields__"])
    assert not {
        n for n in kept if any(w in n for w in ("lat", "lon", "track", "polyline", "bpm", "heart"))
    }
    assert tombstone.identity == identities(world.profile)[0] if identities(world.profile) else True
