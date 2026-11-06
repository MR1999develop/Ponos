# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caso d'uso "sincronizza": elenco completo e incrementale, abbinamento, formati, atomicità.

T-SYNC-01 (completa poi incrementale), T-SYNC-05 (attività già importate da file, verifica
completa), GPX e TCX originali, ZIP corrotti. Garmin, archivio e indice sono finti; orologio e
attesa non dormono.
"""

from __future__ import annotations

import logging
from collections import Counter

import pytest

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    ActivityOrigin,
    FailureReason,
    FileFormat,
    ListingCursor,
    ListingKind,
    RemoteActivity,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.ports import (
    ArchiveWriteError,
    DefinitiveSourceError,
    DownloadTooLargeError,
)
from ponos_coach.activities.sync import (
    EntryFailure,
    OriginalExtractionError,
    StopReason,
    SyncMode,
    SyncPhase,
    SyncProgress,
    listing_kind_for,
)
from ponos_coach.activities.use_cases import import_activities
from tests.activities.import_fakes import (
    FixedClock,
    InMemoryImportSource,
    ObservedArchive,
    ObservedIndex,
    OrderLog,
)
from tests.fakes.garmin import FIRST_ACTIVITY_ID, Call, OriginalKind
from tests.fixtures.generators.import_files import fit_run, gpx_run, run_start
from tests.fixtures.generators.sync_harness import SyncRig, in_memory_rig, remote_id

FIRST_PAUSE_S = 1.375  # pausa minima 1 s più metà del jitter massimo (0,75 s)


def list_offsets(rig: SyncRig) -> list[int | None]:
    return [c.offset for c in rig.fake.calls if c.call is Call.LIST_PAGE]


def downloaded_numbers(rig: SyncRig) -> list[int]:
    return [i.value - FIRST_ACTIVITY_ID for i in rig.fake.downloads]


def import_files(rig: SyncRig, files: dict[str, bytes]) -> None:
    import_activities(InMemoryImportSource(files), rig.archive, rig.index, FixedClock())


# --- Quale elenco ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "full", "expected"),
    [
        (SyncMode.SYNC, None, ListingKind.FULL),
        (SyncMode.SYNC, ListingCursor(40, 200, False), ListingKind.FULL),
        (SyncMode.SYNC, ListingCursor(200, 200, True), ListingKind.INCREMENTAL),
        (SyncMode.VERIFY, None, ListingKind.VERIFY),
        (SyncMode.VERIFY, ListingCursor(200, 200, True), ListingKind.VERIFY),
    ],
)
def test_listing_kind_follows_the_mode_and_the_full_cursor(
    mode: SyncMode, full: ListingCursor | None, expected: ListingKind
) -> None:
    assert listing_kind_for(mode, full) is expected


def test_an_extraction_error_must_have_a_definitive_reason() -> None:
    with pytest.raises(ValueError, match="definitiva"):
        OriginalExtractionError(FailureReason.TRANSIENT_ERROR)


# --- T-SYNC-01: completa e poi incrementale ---------------------------------------------------


def test_t_sync_01_the_first_sync_lists_everything_and_downloads_every_activity() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))

    outcome = rig.run()

    assert outcome.kind is ListingKind.FULL
    assert outcome.completed
    assert (outcome.new_entries, outcome.downloaded, outcome.matched) == (25, 25, 0)
    assert outcome.failures == ()
    assert outcome.status_counts[SyncStatus.DOWNLOADED] == 25
    assert list_offsets(rig) == [0, 10, 20]
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(25, 25, True)
    assert rig.journal.last_completed_at() is not None
    records = rig.records()
    assert len(records) == 25
    assert {r.origin for r in records} == {ActivityOrigin.GARMIN}
    assert {r.garmin_activity_id for r in records} == {FIRST_ACTIVITY_ID + n for n in range(25)}
    assert len(rig.memory.files) == 25
    assert downloaded_numbers(rig) == list(range(24, -1, -1))  # dalla più recente


def test_t_sync_01_a_second_sync_is_incremental_and_downloads_only_the_new_ones() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))
    rig.run()
    first_downloads = len(rig.fake.downloads)
    rig.fake.calls.clear()
    for number in range(25, 28):
        rig.fake.add_activity(
            RemoteActivity(remote_id(number), run_start(number), 1800.0, "running", True)
        )

    outcome = rig.run()

    assert outcome.kind is ListingKind.INCREMENTAL
    assert (outcome.new_entries, outcome.downloaded) == (3, 3)
    assert list_offsets(rig) == [0, 10]  # la seconda pagina è tutta di id noti: ci si ferma
    assert downloaded_numbers(rig)[first_downloads:] == [27, 26, 25]
    assert len(rig.records()) == 28


def test_t_sync_01_with_nothing_new_one_page_is_enough() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))
    rig.run()
    rig.fake.calls.clear()
    downloads = len(rig.fake.downloads)

    outcome = rig.run()

    assert outcome.completed
    assert (outcome.new_entries, outcome.downloaded) == (0, 0)
    assert list_offsets(rig) == [0]
    assert len(rig.fake.downloads) == downloads


def test_the_listing_pauses_between_requests_with_the_minimum_plus_jitter() -> None:
    rig = in_memory_rig(3)

    rig.run()

    requests = len(rig.fake.calls)
    assert requests == 4  # una pagina e tre download
    assert rig.wait.waits == [FIRST_PAUSE_S] * (requests - 1)  # mai prima della prima richiesta


def test_progress_goes_from_the_listing_to_the_downloads_with_an_estimate() -> None:
    rig = in_memory_rig(4, latency_s=2.0)
    reports: list[SyncProgress] = []

    rig.run(report=reports.append)

    phases = [p.phase for p in reports]
    assert phases == sorted(phases, key=lambda p: p is SyncPhase.DOWNLOADING)  # prima l'elenco
    downloading = [p for p in reports if p.phase is SyncPhase.DOWNLOADING]
    assert [p.done for p in downloading] == [0, 1, 2, 3, 4]
    assert all(p.total == 4 for p in downloading)
    assert downloading[0].remaining_s is None  # nessuna misura prima della prima voce
    entry_s = FIRST_PAUSE_S + 2.0
    assert downloading[1].remaining_s == pytest.approx(entry_s * 3)
    assert downloading[-1].remaining_s == 0.0


# --- T-SYNC-05: attività già importate da file ------------------------------------------------


def test_t_sync_05_activities_imported_from_files_are_matched_without_downloading() -> None:
    rig = in_memory_rig(10)
    import_files(rig, {f"corsa-{n}.fit": fit_run(n) for n in range(5)})

    outcome = rig.run()

    assert (outcome.matched, outcome.downloaded) == (5, 5)
    assert sorted(downloaded_numbers(rig)) == [5, 6, 7, 8, 9]  # nessun download per le prime 5
    records = rig.records()
    assert len(records) == 10  # nessun duplicato
    imported = [r for r in records if r.origin is ActivityOrigin.IMPORT]
    assert len(imported) == 5
    assert all(r.garmin_activity_id is not None for r in imported)  # id Garmin collegato
    assert {rig.status_of(n) for n in range(5)} == {SyncStatus.MATCHED}
    assert {rig.status_of(n) for n in range(5, 10)} == {SyncStatus.DOWNLOADED}
    assert len(rig.memory.files) == 10


def test_t_sync_05_the_same_file_already_imported_is_linked_not_duplicated() -> None:
    # Garmin dice "ciclismo": lo sport non coincide, quindi si scarica; ma il file è lo stesso.
    rig = in_memory_rig(3, sport_key="cycling")
    import_files(rig, {f"corsa-{n}.fit": fit_run(n) for n in range(3)})

    outcome = rig.run()

    # già nel profilo (stesso file): collegate, mai contate come scaricate (R6)
    assert (outcome.downloaded, outcome.matched, outcome.already_present) == (0, 0, 3)
    assert len(rig.fake.downloads) == 3
    records = rig.records()
    assert len(records) == 3
    assert {r.origin for r in records} == {ActivityOrigin.IMPORT}
    assert {r.garmin_activity_id for r in records} == {FIRST_ACTIVITY_ID + n for n in range(3)}
    assert len(rig.memory.files) == 3  # nessun file in più


def test_t_sync_05_the_same_activity_imported_in_another_format_is_matched_after_download() -> None:
    rig = in_memory_rig(1, sport_key="cycling")
    import_files(rig, {"corsa-0.gpx": gpx_run(0)})

    outcome = rig.run()

    assert (outcome.matched, outcome.downloaded) == (1, 0)
    (record,) = rig.records()
    assert record.file_format is FileFormat.GPX
    assert record.garmin_activity_id == FIRST_ACTIVITY_ID
    assert rig.status_of(0) is SyncStatus.MATCHED
    assert len(rig.memory.files) == 1  # il FIT scaricato non è stato archiviato


def test_t_sync_05_a_full_verification_finds_activities_uploaded_late_with_old_dates() -> None:
    rig = in_memory_rig(20, policy=SyncPolicy(page_size=10))
    rig.run()
    for number in (-1, -2):  # caricate in ritardo, più vecchie di tutto lo storico
        rig.fake.add_activity(
            RemoteActivity(remote_id(number), run_start(number), 1800.0, "running", True)
        )

    incremental = rig.run()
    assert (incremental.new_entries, len(rig.records())) == (0, 20)  # non le trova

    before = len(rig.fake.downloads)
    rig.fake.calls.clear()
    verify = rig.run(SyncMode.VERIFY)

    assert verify.kind is ListingKind.VERIFY
    assert (verify.new_entries, verify.downloaded) == (2, 2)
    assert list_offsets(rig) == [0, 10, 20]  # rielenca tutto
    assert downloaded_numbers(rig)[before:] == [-1, -2]  # ma scarica solo le nuove
    assert len(rig.records()) == 22


# --- Formati e file rovinati ------------------------------------------------------------------


def test_a_gpx_original_is_read_like_an_imported_one() -> None:
    rig = in_memory_rig(4)
    rig.fake.set_kind(remote_id(2), OriginalKind.GPX)

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 4)
    gpx = [r for r in rig.records() if r.file_format is FileFormat.GPX]
    assert len(gpx) == 1
    assert gpx[0].garmin_activity_id == FIRST_ACTIVITY_ID + 2
    assert gpx[0].origin is ActivityOrigin.GARMIN
    assert gpx[0].archived.relative_path.suffix == ".gpx"


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (OriginalKind.TCX, FailureReason.UNSUPPORTED_FORMAT),
        (OriginalKind.CORRUPT_FIT, FailureReason.CORRUPT_FILE),
        (OriginalKind.TRUNCATED_ZIP, FailureReason.ARCHIVE_INVALID),
        (OriginalKind.NOT_A_ZIP, FailureReason.ARCHIVE_INVALID),
        (OriginalKind.EMPTY_ZIP, FailureReason.NOT_AN_ACTIVITY),
    ],
)
def test_an_unusable_original_fails_that_entry_for_good_and_the_sync_goes_on(
    kind: OriginalKind, reason: FailureReason
) -> None:
    rig = in_memory_rig(5)
    rig.fake.set_kind(remote_id(2), kind)

    outcome = rig.run()

    assert outcome.completed
    assert outcome.downloaded == 4
    assert outcome.failures == (EntryFailure(remote_id(2), reason),)
    assert dict(outcome.failures_by_reason()) == {reason: 1}
    entry = rig.journal.get(remote_id(2))
    assert entry is not None
    assert (entry.status, entry.failure) == (SyncStatus.FAILED, reason)
    assert len(rig.records()) == 4

    rig.run()  # una voce fallita in modo definitivo non si riprova
    assert Counter(rig.fake.downloads)[remote_id(2)] == 1


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DownloadTooLargeError("troppo grande"), FailureReason.FILE_TOO_LARGE),
        (DefinitiveSourceError("assente"), FailureReason.REMOTE_REJECTED),
    ],
)
def test_a_definitive_source_error_fails_the_entry_with_its_reason(
    error: Exception, reason: FailureReason
) -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(1), error)

    outcome = rig.run()

    assert outcome.failures == (EntryFailure(remote_id(1), reason),)
    assert outcome.downloaded == 2


@pytest.mark.parametrize(
    ("file_reason", "reason"),
    [
        (ActivityFileReason.TOO_MANY_RECORDS, FailureReason.FILE_TOO_LARGE),
        (ActivityFileReason.TOO_LARGE, FailureReason.FILE_TOO_LARGE),
        (ActivityFileReason.NOT_AN_ACTIVITY, FailureReason.NOT_AN_ACTIVITY),
        (ActivityFileReason.EXTENSION_MISMATCH, FailureReason.CORRUPT_FILE),
        (ActivityFileReason.TOO_LONG, FailureReason.CORRUPT_FILE),
    ],
)
def test_a_decoding_refusal_maps_to_a_failure_reason(
    file_reason: ActivityFileReason, reason: FailureReason
) -> None:
    rig = in_memory_rig(1)

    def refuse(_content: bytes, _file_format: FileFormat) -> None:
        raise ActivityFileError(file_reason)

    rig.decode = refuse  # type: ignore[assignment]

    outcome = rig.run()

    assert outcome.failures == (EntryFailure(remote_id(0), reason),)


def test_an_unexpected_error_in_a_helper_fails_only_that_entry_and_logs_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = in_memory_rig(2)

    def explode(_zipped: bytes) -> None:
        raise RuntimeError("percorso-riservato-123")

    rig.extract = explode  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR):
        outcome = rig.run()

    assert {f.reason for f in outcome.failures} == {FailureReason.ARCHIVE_INVALID}
    assert len(outcome.failures) == 2
    assert "RuntimeError" in caplog.text
    assert "percorso-riservato-123" not in caplog.text


# --- Scrittura: archivio prima, record poi, una transazione -----------------------------------


def test_the_archive_is_written_before_the_short_transaction_of_the_record() -> None:
    rig = in_memory_rig(1)
    log = OrderLog()
    rig.archive = ObservedArchive(rig.archive, log)  # type: ignore[arg-type]
    rig.index = ObservedIndex(rig.index, log)

    rig.run()

    # La prima transazione è quella della pagina dell'elenco; poi l'archivio (R13) e,
    # solo dopo, la transazione breve del record e della voce.
    assert log.events == ["begin", "commit", "store", "begin", "add", "commit"]


def test_a_failing_record_write_rolls_back_the_entry_and_a_rerun_finishes_without_duplicates() -> (
    None
):
    rig = in_memory_rig(2)
    real_index = rig.index
    rig.index = ObservedIndex(real_index, OrderLog(), fail_add_with=RuntimeError("guasto"))

    with pytest.raises(RuntimeError, match="guasto"):
        rig.run()
    assert rig.status_of(1) is SyncStatus.PENDING  # la voce è com'era
    assert rig.records() == []

    rig.index = real_index
    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 2)
    assert len(rig.records()) == 2
    assert len(rig.memory.files) == 2  # il file scritto prima del guasto non si raddoppia


def test_a_storage_error_stops_the_sync_and_leaves_the_entry_untouched() -> None:
    rig = in_memory_rig(3)
    real_archive = rig.archive
    rig.archive = ObservedArchive(
        real_archive,  # type: ignore[arg-type]
        OrderLog(),
        fail_with=ArchiveWriteError("disco pieno"),
    )

    outcome = rig.run()

    assert outcome.stop is StopReason.STORAGE_ERROR
    assert outcome.failures == ()  # non è colpa della voce
    assert outcome.status_counts[SyncStatus.PENDING] == 3
    assert rig.journal.last_completed_at() is None

    rig.archive = real_archive
    assert rig.run().downloaded == 3
