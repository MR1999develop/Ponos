# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case "import activities" with in-memory source, archive and index.

T-IMP-01 (outcomes per file), T-IMP-03 (idempotence), T-GPX-02 (same activity in FIT and GPX).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.activities import use_cases
from ponos_coach.activities.decoding import decode_activity_file
from ponos_coach.activities.domain import ActivityOrigin, FileFormat
from ponos_coach.activities.ports import (
    ArchiveWriteError,
    DuplicateActivityError,
    ImportEntry,
    ImportIssue,
    ImportLimit,
    ImportLimitExceededError,
    SkippedEntry,
)
from ponos_coach.activities.use_cases import (
    FileImportResult,
    ImportStatus,
    ImportSummary,
    import_activities,
)
from tests.activities.import_fakes import (
    FixedClock,
    InMemoryImportSource,
    ObservedArchive,
    ObservedIndex,
    OrderLog,
    new_index,
)
from tests.builders import IMPORTED_AT
from tests.fakes.archive import InMemoryActivityArchive
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import import_files as imp

DATA = Path(__file__).resolve().parents[1] / "fixtures" / "data"
ARCHIVE_PATH = re.compile(r"\d{4}/[0-9a-f]{64}\.fit")


class Setup:
    """Fake archive and index with a frozen clock, plus the import command."""

    def __init__(self) -> None:
        self.archive = InMemoryActivityArchive()
        self.index = new_index()

    def run(self, source: InMemoryImportSource, **options: object) -> ImportSummary:
        return import_activities(
            source,
            self.archive,
            self.index,
            FixedClock(),
            **options,  # type: ignore[arg-type]
        )


def statuses(summary: ImportSummary) -> dict[str, ImportStatus]:
    return {r.name: r.status for r in summary.results}


def test_t_imp_01_each_file_gets_its_outcome() -> None:
    fit_bad_crc = imp.fit_run(3)[:-1] + b"\x00"
    gpx = b'<?xml version="1.0"?><gpx version="1.1"><trk/></gpx>'
    tcx = b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="urn:x"/>'
    files = {
        "a_run.fit": imp.fit_run(1),
        "b_walk.gpx": gpx,
        "c_copy.fit": imp.fit_run(1),  # stessa attività di a_run.fit
        "d_broken.fit": fit_bad_crc,
        "e_garmin.tcx": tcx,
        "f_gpx_named_fit.fit": gpx,
        "g_empty.fit": b"",
    }
    setup = Setup()

    summary = setup.run(InMemoryImportSource(files))

    assert [(r.name, r.status, r.issue) for r in summary.results] == [
        ("a_run.fit", ImportStatus.IMPORTED, None),
        ("b_walk.gpx", ImportStatus.FAILED, ImportIssue.UNSUPPORTED_FORMAT),
        ("c_copy.fit", ImportStatus.ALREADY_PRESENT, None),
        ("d_broken.fit", ImportStatus.FAILED, ImportIssue.CORRUPT),
        ("e_garmin.tcx", ImportStatus.FAILED, ImportIssue.UNSUPPORTED_FORMAT),
        ("f_gpx_named_fit.fit", ImportStatus.FAILED, ImportIssue.CORRUPT),
        ("g_empty.fit", ImportStatus.FAILED, ImportIssue.EMPTY),
    ]
    assert not summary.cancelled
    assert summary.count(ImportStatus.IMPORTED) == 1
    assert len(setup.index.list_by_start()) == 1


def test_records_and_archive_hold_what_was_imported() -> None:
    fit = imp.fit_run(1)
    setup = Setup()

    setup.run(InMemoryImportSource({"x.fit": fit}))

    (fit_record,) = setup.index.list_by_start()
    assert fit_record.file_format is FileFormat.FIT
    assert fit_record.origin is ActivityOrigin.IMPORT
    assert fit_record.imported_at == IMPORTED_AT
    assert fit_record.start == decode_activity_file(fit, "x.fit").start
    # Nell'archivio finisce il file originale, con un nome generato dal programma.
    assert setup.archive.read(fit_record.archived, 10**9) == fit
    assert all(ARCHIVE_PATH.fullmatch(name) for name in map(str, setup.archive.files))


def test_t_imp_03_importing_again_changes_nothing() -> None:
    files = {"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2), "c.fit": imp.fit_run(3)}
    setup = Setup()
    setup.run(InMemoryImportSource(files))
    records, stored = setup.index.list_by_start(), dict(setup.archive.files)

    again = setup.run(InMemoryImportSource(files))

    assert set(statuses(again).values()) == {ImportStatus.ALREADY_PRESENT}
    assert setup.index.list_by_start() == records
    assert setup.archive.files == stored


def test_same_file_under_two_names_in_one_run_is_imported_once() -> None:
    fit = (DATA / "fit" / "run_constant_160.fit").read_bytes()
    copy = (DATA / "fit" / "copy_of_run_constant_160.fit").read_bytes()
    setup = Setup()

    summary = setup.run(InMemoryImportSource({"a.fit": fit, "b.fit": copy}))

    assert [r.status for r in summary.results] == [
        ImportStatus.IMPORTED,
        ImportStatus.ALREADY_PRESENT,
    ]
    (record,) = setup.index.list_by_start()
    assert summary.results[1].matches == record.identity
    assert len(setup.archive.files) == 1


def test_two_different_activities_in_the_same_format_at_the_same_time_are_both_kept() -> None:
    start = imp.run_start(1)
    samples = [fw.Sample(fw.datetime_to_fit(start) + i, 140) for i in range(60)]
    twin = fw.build_activity(
        fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=9)
    )
    other = fw.build_activity(
        fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=10)
    )
    setup = Setup()

    summary = setup.run(InMemoryImportSource({"a.fit": twin, "b.fit": other}))

    assert [r.status for r in summary.results] == [ImportStatus.IMPORTED, ImportStatus.IMPORTED]


def test_a_broken_file_does_not_stop_the_others() -> None:
    files = {"a.fit": b"garbage", "b.fit": imp.fit_run(1), "c.fit": b"<not-xml"}
    setup = Setup()

    summary = setup.run(InMemoryImportSource(files))

    assert statuses(summary) == {
        "a.fit": ImportStatus.FAILED,
        "b.fit": ImportStatus.IMPORTED,
        "c.fit": ImportStatus.FAILED,
    }


def test_files_skipped_by_the_source_are_reported_first_with_their_reason() -> None:
    skipped = [
        SkippedEntry("big.fit", ImportIssue.TOO_LARGE),
        SkippedEntry("link.fit", ImportIssue.NOT_REGULAR_FILE),
    ]
    setup = Setup()

    summary = setup.run(InMemoryImportSource({"a.fit": imp.fit_run(1)}, skipped=skipped))

    assert [(r.name, r.status, r.issue) for r in summary.results] == [
        ("big.fit", ImportStatus.FAILED, ImportIssue.TOO_LARGE),
        ("link.fit", ImportStatus.FAILED, ImportIssue.NOT_REGULAR_FILE),
        ("a.fit", ImportStatus.IMPORTED, None),
    ]


def test_a_file_that_cannot_be_read_is_an_error_with_the_sources_reason() -> None:
    source = InMemoryImportSource(
        {"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2)},
        failing={"a.fit": ImportIssue.UNREADABLE},
    )
    setup = Setup()

    summary = setup.run(source)

    assert [(r.status, r.issue) for r in summary.results] == [
        (ImportStatus.FAILED, ImportIssue.UNREADABLE),
        (ImportStatus.IMPORTED, None),
    ]


def test_a_source_over_a_limit_fails_before_importing_anything() -> None:
    error = ImportLimitExceededError(ImportLimit.ACTIVITY_FILES, 50_000)
    setup = Setup()

    with pytest.raises(ImportLimitExceededError) as caught:
        setup.run(InMemoryImportSource({"a.fit": imp.fit_run(1)}, scan_error=error))

    assert caught.value.limit is ImportLimit.ACTIVITY_FILES
    assert not setup.archive.files
    assert not setup.index.list_by_start()


def test_the_file_is_archived_before_the_record_and_both_in_one_transaction() -> None:
    log = OrderLog()
    archive = ObservedArchive(InMemoryActivityArchive(), log)
    index = ObservedIndex(new_index(), log)

    import_activities(InMemoryImportSource({"a.fit": imp.fit_run(1)}), archive, index, FixedClock())

    assert log.events == ["begin", "store", "add", "commit"]


def test_when_the_archive_cannot_write_nothing_is_indexed() -> None:
    log = OrderLog()
    index = new_index()
    archive = ObservedArchive(InMemoryActivityArchive(), log, fail_with=ArchiveWriteError("disco"))

    summary = import_activities(
        InMemoryImportSource({"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2)}),
        archive,
        ObservedIndex(index, log),
        FixedClock(),
    )

    assert [(r.status, r.issue) for r in summary.results] == [
        (ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)
    ] * 2
    assert not index.list_by_start()
    assert log.events == ["begin", "store", "rollback"] * 2


def test_when_the_record_cannot_be_added_the_transaction_rolls_back() -> None:
    log = OrderLog()
    index = new_index()
    observed = ObservedIndex(index, log, fail_add_with=DuplicateActivityError("corsa"))

    summary = import_activities(
        InMemoryImportSource({"a.fit": imp.fit_run(1)}),
        InMemoryActivityArchive(),
        observed,
        FixedClock(),
    )

    # An activity that appeared in the meantime (other process) is "already present", not an error.
    assert summary.results[0].status is ImportStatus.ALREADY_PRESENT
    assert log.events[-1] == "rollback"
    assert not index.list_by_start()


def test_cancelling_stops_between_files_and_a_rerun_completes_without_duplicates() -> None:
    files = {f"{n}.fit": imp.fit_run(n) for n in range(1, 6)}
    setup = Setup()
    checks = iter([False, False, True])

    partial = setup.run(InMemoryImportSource(files), is_cancelled=lambda: next(checks))

    assert partial.cancelled
    assert len(partial.results) == 2
    assert len(setup.index.list_by_start()) == 2

    complete = setup.run(InMemoryImportSource(files))

    assert not complete.cancelled
    assert complete.count(ImportStatus.ALREADY_PRESENT) == 2
    assert complete.count(ImportStatus.IMPORTED) == 3
    assert len(setup.index.list_by_start()) == 5


def test_cancelled_before_the_first_file_imports_nothing() -> None:
    setup = Setup()

    summary = setup.run(InMemoryImportSource({"a.fit": imp.fit_run(1)}), is_cancelled=lambda: True)

    assert summary.cancelled
    assert not summary.results
    assert not setup.index.list_by_start()


def test_progress_is_reported_before_the_first_file_and_after_each() -> None:
    progress: list[tuple[int, int]] = []
    files = {"a.fit": imp.fit_run(1), "b.fit": b"garbage", "c.fit": imp.fit_run(2)}

    Setup().run(
        InMemoryImportSource(files), report=lambda done, total: progress.append((done, total))
    )

    assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_the_use_case_does_not_close_the_source_it_does_not_own() -> None:
    source = InMemoryImportSource({"a.fit": imp.fit_run(1)})

    Setup().run(source)

    assert not source.closed


def test_empty_source_gives_an_empty_summary() -> None:
    summary = Setup().run(InMemoryImportSource({}))

    assert summary == ImportSummary((), cancelled=False)


def test_result_rejects_incoherent_combinations() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        FileImportResult("a.fit", ImportStatus.FAILED)
    with pytest.raises(ValueError, match="inconsistent"):
        FileImportResult("a.fit", ImportStatus.IMPORTED, ImportIssue.CORRUPT)
    with pytest.raises(ValueError, match="inconsistent"):
        FileImportResult("a.fit", ImportStatus.ALREADY_PRESENT)


def test_imported_at_comes_from_the_injected_clock() -> None:
    setup = Setup()
    moment = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)

    import_activities(
        InMemoryImportSource({"a.fit": imp.fit_run(1)}),
        setup.archive,
        setup.index,
        FixedClock(moment),
    )

    assert setup.index.list_by_start()[0].imported_at == moment


def test_imported_at_is_truncated_to_the_millisecond() -> None:
    setup = Setup()
    moment = datetime(2030, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)

    import_activities(
        InMemoryImportSource({"a.fit": imp.fit_run(1)}),
        setup.archive,
        setup.index,
        FixedClock(moment),
    )

    assert setup.index.list_by_start()[0].imported_at == datetime(
        2030, 1, 2, 3, 4, 5, 678000, tzinfo=UTC
    )


# --- Archive repair (D-F1-03) ------------------------------------------------------


def _flip_first_byte(archive: InMemoryActivityArchive) -> None:
    [path] = archive.files
    data = bytearray(archive.files[path])
    data[0] ^= 0xFF
    archive.files[path] = bytes(data)


@pytest.mark.parametrize("damage", ["alterato", "mancante"])
def test_importing_the_original_again_repairs_a_missing_or_altered_archived_file(
    damage: str,
) -> None:
    setup = Setup()
    files = {"a.fit": imp.fit_run(1)}
    setup.run(InMemoryImportSource(files))
    [record] = setup.index.list_by_start()
    if damage == "mancante":
        setup.archive.files.clear()
    else:
        _flip_first_byte(setup.archive)

    again = setup.run(InMemoryImportSource(files))

    [result] = again.results
    assert result.status is ImportStatus.REPAIRED
    assert result.matches == record.identity
    setup.archive.verify(record.archived)  # the archive is intact again
    assert setup.index.list_by_start() == [record]  # and the index is the same


def test_a_sound_archived_file_is_never_rewritten_by_a_second_import() -> None:
    setup = Setup()
    files = {"a.fit": imp.fit_run(1)}
    setup.run(InMemoryImportSource(files))
    stored = dict(setup.archive.files)

    again = setup.run(InMemoryImportSource(files))

    assert set(statuses(again).values()) == {ImportStatus.ALREADY_PRESENT}
    assert setup.archive.files == stored


def test_a_repaired_result_needs_the_identity_it_matches() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        FileImportResult("a.fit", ImportStatus.REPAIRED)


# --- Unexpected errors on a single file (R1) ---------------------------------------------------


class _Exploding(InMemoryImportSource):
    """Source that raises an unexpected error while reading a file."""

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        if self._names[entry.entry_id] == "b.fit":
            raise RuntimeError("percorso segreto /home/atleta")
        return super().read(entry, max_bytes)


def test_an_unexpected_error_reading_one_file_fails_only_that_file(
    caplog: pytest.LogCaptureFixture,
) -> None:
    files = {"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2), "c.fit": imp.fit_run(3)}
    setup = Setup()

    with caplog.at_level(logging.ERROR):
        summary = setup.run(_Exploding(files))

    assert statuses(summary) == {
        "a.fit": ImportStatus.IMPORTED,
        "b.fit": ImportStatus.FAILED,
        "c.fit": ImportStatus.IMPORTED,
    }
    assert summary.results[1].issue is ImportIssue.UNREADABLE
    [record] = caplog.records  # logged once, with only the exception type
    assert "RuntimeError" in record.getMessage()
    assert "segreto" not in caplog.text


def test_an_unexpected_error_decoding_one_file_fails_only_that_file(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = decode_activity_file

    def exploding(data: bytes, name: str) -> object:
        if name == "b.fit":
            raise OverflowError("date value out of range")
        return real(data, name)

    monkeypatch.setattr(use_cases, "decode_activity_file", exploding)
    files = {"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2), "c.fit": imp.fit_run(3)}

    with caplog.at_level(logging.ERROR):
        summary = Setup().run(InMemoryImportSource(files))

    assert [(r.name, r.status) for r in summary.results] == [
        ("a.fit", ImportStatus.IMPORTED),
        ("b.fit", ImportStatus.FAILED),
        ("c.fit", ImportStatus.IMPORTED),
    ]
    assert summary.results[1].issue is ImportIssue.CORRUPT
    assert [r.getMessage() for r in caplog.records].count(
        "import: unexpected error on a file (OverflowError)"
    ) == 1


def test_an_unexpected_error_storing_one_file_fails_only_that_file() -> None:
    log = OrderLog()
    archive = ObservedArchive(InMemoryActivityArchive(), log, fail_with=RuntimeError("boom"))

    summary = import_activities(
        InMemoryImportSource({"a.fit": imp.fit_run(1), "b.fit": imp.fit_run(2)}),
        archive,
        new_index(),
        FixedClock(),
    )

    assert [(r.status, r.issue) for r in summary.results] == [
        (ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)
    ] * 2
    assert log.events == ["store", "store"]


# --- Cancelling the scan (R6) ----------------------------------------------------------


def test_the_cancellation_check_reaches_the_scan() -> None:
    source = InMemoryImportSource({"a.fit": imp.fit_run(1)})

    def never() -> bool:
        return False

    Setup().run(source, is_cancelled=never)

    assert source.scan_cancellation is never


def test_a_scan_cancelled_by_the_source_gives_an_empty_cancelled_summary() -> None:
    setup = Setup()

    summary = setup.run(InMemoryImportSource({"a.fit": imp.fit_run(1)}, cancel_scan=True))

    assert summary == ImportSummary((), cancelled=True)
    assert not setup.index.list_by_start()
