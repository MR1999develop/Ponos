# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-IMP-01, T-IMP-03, T-IMP-04, T-GPX-01, T-GPX-02 sulla pila reale.

Cartelle, ZIP e file veri sul disco, sorgenti `FolderImportSource` e `FilesImportSource`,
archivio su file system e indice SQLite: il comportamento che l'utente vede, file per file.
"""

from __future__ import annotations

import hashlib
import io
import warnings
import zipfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.domain import FileFormat, Sport
from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import ImportIssue, ImportLimit, ImportLimitExceededError
from ponos_coach.activities.use_cases import ImportStatus
from ponos_coach.load.domain import LoadSource
from tests.collaudo.support import RealProfile, create_profile, outcomes, stored_files
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators import import_files as imp
from tests.fixtures.generators.zip_writer import build_zip, garmin_download

IMPORTED = (ImportStatus.IMPORTED, None)
ALREADY = (ImportStatus.ALREADY_PRESENT, None)


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    yield opened
    opened.close()


def write(root: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# --- T-IMP-01 ------------------------------------------------------------------------------------


def mixed_tree(root: Path) -> dict[str, tuple[ImportStatus, ImportIssue | None]]:
    """Scrive l'albero di prova e dice l'esito atteso per ogni file elencato."""
    fit1 = imp.fit_run(1)
    resaved = imp.fit_run(1, seconds=1200)  # stesso file_id, byte diversi
    no_times = gw.build_gpx(gw.GpxSpec([[gw.GpxPoint(1.0, 1.0), gw.GpxPoint(1.0, 1.1)]]))
    tcx = b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="urn:x"/>'
    write(
        root,
        {
            "a_corsa.fit": fit1,
            "b_camminata.gpx": imp.gpx_run(2),
            "c_maiuscolo.FIT": imp.fit_run(3),
            "copie/a_copia.fit": fit1,
            "copie/profonda/risalvata.fit": resaved,
            "rotti/crc.fit": fit1[:-1] + b"\x00",
            "rotti/troncato.fit": fit1[: len(fit1) // 2],
            "rotti/vuoto.fit": b"",
            "rotti/testo.fit": b"non sono un FIT",
            "rotti/fit_chiamato.gpx": imp.fit_run(4),
            "rotti/gpx_chiamato.fit": imp.gpx_run(5),
            "rotti/senza_orari.gpx": no_times,
            "rotti/garmin.tcx.fit": tcx,
            "ignorati/note.txt": b"niente da importare",
            "ignorati/leggimi.md": b"# leggimi",
            "zip/scaricato.zip": garmin_download(imp.fit_run(6), activity_id=100),
        },
    )
    return {
        "a_corsa.fit": IMPORTED,
        "b_camminata.gpx": IMPORTED,
        "c_maiuscolo.FIT": IMPORTED,
        "copie/a_copia.fit": ALREADY,
        "copie/profonda/risalvata.fit": ALREADY,
        "rotti/crc.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/troncato.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/vuoto.fit": (ImportStatus.FAILED, ImportIssue.EMPTY),
        "rotti/testo.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/fit_chiamato.gpx": (ImportStatus.FAILED, ImportIssue.EXTENSION_MISMATCH),
        "rotti/gpx_chiamato.fit": (ImportStatus.FAILED, ImportIssue.EXTENSION_MISMATCH),
        "rotti/senza_orari.gpx": (ImportStatus.IGNORED, ImportIssue.NOT_AN_ACTIVITY),
        "rotti/garmin.tcx.fit": (ImportStatus.FAILED, ImportIssue.UNSUPPORTED_FORMAT),
        "zip/scaricato.zip!100_ACTIVITY.fit": IMPORTED,
    }


def test_t_imp_01_a_real_tree_gives_the_exact_summary_file_by_file(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    expected = mixed_tree(source)
    before = snapshot(source)

    summary = profile.import_folder(source)

    assert outcomes(summary) == expected
    names = [r.name for r in summary.results]
    assert names == sorted(names)  # nessun file skipped dalla sorgente: ordine di nome
    assert len(names) == len(set(names))
    assert summary.count(ImportStatus.IMPORTED) == 4
    assert summary.count(ImportStatus.ALREADY_PRESENT) == 2
    assert summary.count(ImportStatus.IGNORED) == 1
    assert summary.count(ImportStatus.FAILED) == 7
    assert not summary.cancelled
    assert snapshot(source) == before  # la cartella d'origine non si modifica mai
    assert len(profile.records()) == 4
    assert len(stored_files(profile.folder)) == 4


def test_t_imp_01_the_archive_holds_the_original_bytes_under_generated_names(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    mixed_tree(source)

    profile.import_folder(source)

    originals = {
        hashlib.sha256(source.joinpath(name).read_bytes()).hexdigest()
        for name in ("a_corsa.fit", "b_camminata.gpx", "c_maiuscolo.FIT")
    } | {hashlib.sha256(imp.fit_run(6)).hexdigest()}  # il FIT estratto dallo ZIP, non lo ZIP
    in_archive = {p.stem for p in stored_files(profile.folder)}
    assert in_archive == originals
    assert {p.suffix for p in stored_files(profile.folder)} == {".fit", ".gpx"}
    for record in profile.records():
        assert record.archived.sha256_hex in originals
        assert str(record.archived.relative_path).endswith(f".{record.file_format.value}")


# --- T-IMP-03 ------------------------------------------------------------------------------------


def test_t_imp_03_importing_the_same_tree_again_changes_nothing(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    expected = mixed_tree(source)
    profile.import_folder(source)
    records, archive = list(profile.records()), tree_bytes(profile.folder / "archivio")

    again = profile.import_folder(source)

    assert {s for s, _ in outcomes(again).values()} == {
        ImportStatus.ALREADY_PRESENT,
        ImportStatus.FAILED,
        ImportStatus.IGNORED,
    }
    for name, outcome in expected.items():  # i rifiuti restano gli stessi, il resto e gia presente
        if outcome == IMPORTED:
            assert outcomes(again)[name] == ALREADY
        else:
            assert outcomes(again)[name] == outcome
    assert list(profile.records()) == records  # compreso imported_at
    assert tree_bytes(profile.folder / "archivio") == archive


def test_t_imp_03_touched_renamed_and_copied_files_are_still_already_present(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    write(source, {"x.fit": imp.fit_run(1), "y.gpx": imp.gpx_run(2)})
    profile.import_folder(source)
    records = list(profile.records())

    (source / "x.fit").touch()
    (source / "x.fit").rename(source / "rinominato.fit")
    (source / "altro").mkdir()
    (source / "y.gpx").replace(source / "altro" / "nome.gpx")

    again = profile.import_folder(source)

    assert {s for s, _ in outcomes(again).values()} == {ImportStatus.ALREADY_PRESENT}
    assert list(profile.records()) == records


def test_t_imp_03_a_file_chosen_twice_in_one_import_is_imported_once(
    tmp_path: Path, profile: RealProfile
) -> None:
    write(tmp_path, {"uno.fit": imp.fit_run(1)})

    summary = profile.import_files([tmp_path / "uno.fit", tmp_path / "uno.fit"])

    assert [r.status for r in summary.results] == [
        ImportStatus.IMPORTED,
        ImportStatus.ALREADY_PRESENT,
    ]
    assert len(profile.records()) == 1


def test_t_imp_03_the_first_arrival_is_kept_when_a_resaved_copy_comes_later(
    tmp_path: Path, profile: RealProfile
) -> None:
    first, resaved = imp.fit_run(1), imp.fit_run(1, seconds=1200)
    write(tmp_path, {"a.fit": first, "b.fit": resaved})
    assert first != resaved

    summary = profile.import_files([tmp_path / "a.fit", tmp_path / "b.fit"])

    assert [r.status for r in summary.results] == [
        ImportStatus.IMPORTED,
        ImportStatus.ALREADY_PRESENT,
    ]
    ((stored),) = stored_files(profile.folder)
    assert stored.read_bytes() == first


# --- T-IMP-04 ------------------------------------------------------------------------------------


def test_t_imp_04_a_garmin_download_stores_the_extracted_fit_not_the_zip(
    tmp_path: Path, profile: RealProfile
) -> None:
    fit = imp.fit_run(1)
    write(tmp_path, {"scaricato.zip": garmin_download(fit, activity_id=777)})

    summary = profile.import_files([tmp_path / "scaricato.zip"])

    assert outcomes(summary) == {"scaricato.zip!777_ACTIVITY.fit": IMPORTED}
    ((stored),) = stored_files(profile.folder)
    assert stored.read_bytes() == fit
    assert not zipfile.is_zipfile(stored)


def test_t_imp_04_many_fit_and_gpx_activities_and_other_entries_are_ignored(
    tmp_path: Path, profile: RealProfile
) -> None:
    entries: list[tuple[str, bytes]] = []
    for number in range(8):
        entries.append((f"fit/corsa-{number}.fit", imp.fit_run(number)))
        entries.append((f"gpx/giro-{number}.gpx", imp.gpx_run(number + 100)))
    entries += [
        ("leggimi.txt", b"niente"),
        ("foto.png", b"\x89PNG"),
        ("dati.json", b"{}"),
        ("cartella/", b""),
        ("__MACOSX/._corsa-0.fit.txt", b"x"),
    ]
    write(tmp_path, {"tutto.zip": build_zip(entries)})

    summary = profile.import_files([tmp_path / "tutto.zip"])

    assert len(summary.results) == 16  # le voci di altri tipi non compaiono
    assert {r.status for r in summary.results} == {ImportStatus.IMPORTED}
    assert {r.name.split("!")[1].split("/")[0] for r in summary.results} == {"fit", "gpx"}
    assert len(profile.records()) == 16


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_t_imp_04_stored_and_deflated_entries_both_import(
    tmp_path: Path, profile: RealProfile, method: int
) -> None:
    write(tmp_path, {"z.zip": build_zip([("a.fit", imp.fit_run(1))], method=method)})

    summary = profile.import_files([tmp_path / "z.zip"])

    assert outcomes(summary) == {"z.zip!a.fit": IMPORTED}


def zip_of_runs(count: int) -> bytes:
    return build_zip([(f"e{i}.fit", imp.fit_run(i)) for i in range(count)])


def test_t_imp_04_entries_of_a_zip_count_in_the_caps_at_the_limit_and_one_over(
    tmp_path: Path,
) -> None:
    write(tmp_path, {"cinque.zip": zip_of_runs(5), "sei.zip": zip_of_runs(6)})
    limits = ImportLimits(max_activity_files=5)

    at_limit = create_profile(tmp_path / "p1")
    try:
        assert (
            at_limit.import_files([tmp_path / "cinque.zip"], limits).count(ImportStatus.IMPORTED)
            == 5
        )
    finally:
        at_limit.close()

    over = create_profile(tmp_path / "p2")
    try:
        with pytest.raises(ImportLimitExceededError) as raised:
            over.import_files([tmp_path / "sei.zip"], limits)
        assert raised.value.limit is ImportLimit.ACTIVITY_FILES
        assert over.records() == []  # niente import parziali a sorpresa
        assert stored_files(over.folder) == []
    finally:
        over.close()


def test_t_imp_04_loose_files_and_zip_entries_share_one_cap(tmp_path: Path) -> None:
    write(
        tmp_path / "src",
        {
            "a.fit": imp.fit_run(1),
            "b.fit": imp.fit_run(2),
            "c.zip": build_zip([(f"e{i}.fit", imp.fit_run(10 + i)) for i in range(3)]),
        },
    )
    opened = create_profile(tmp_path / "p")
    try:
        with pytest.raises(ImportLimitExceededError):
            opened.import_folder(tmp_path / "src", ImportLimits(max_activity_files=4))
        assert (
            opened.import_folder(tmp_path / "src", ImportLimits(max_activity_files=5)).count(
                ImportStatus.IMPORTED
            )
            == 5
        )
    finally:
        opened.close()


def duplicated_zip(order: str) -> bytes:
    fit, gpx = imp.same_run_as_fit_and_gpx(7)
    names = {"fit_first": ("a1.fit", "a3.fit", "b.gpx"), "gpx_first": ("z1.fit", "z3.fit", "a.gpx")}
    first_fit, second_fit, gpx_name = names[order]
    return build_zip([(first_fit, fit), (second_fit, fit), (gpx_name, gpx)])


DUP = (ImportStatus.DUPLICATE_OF_OTHER_FORMAT, None)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        # Si importa in ordine di nome: vince la prima arrivata, l'altro formato e un doppione.
        (
            "fit_first",
            {"d.zip!a1.fit": IMPORTED, "d.zip!a3.fit": ALREADY, "d.zip!b.gpx": DUP},
        ),
        (
            "gpx_first",
            {"d.zip!a.gpx": IMPORTED, "d.zip!z1.fit": DUP, "d.zip!z3.fit": DUP},
        ),
    ],
)
def test_a_zip_with_the_same_fit_twice_and_a_gpx_of_the_same_activity_keeps_one(
    tmp_path: Path,
    profile: RealProfile,
    order: str,
    expected: dict[str, tuple[ImportStatus, ImportIssue | None]],
) -> None:
    write(tmp_path, {"d.zip": duplicated_zip(order)})

    summary = profile.import_files([tmp_path / "d.zip"])

    assert outcomes(summary) == expected
    assert len(profile.records()) == 1
    assert len(stored_files(profile.folder)) == 1


def test_a_zip_with_the_same_fit_twice_gives_one_import_and_one_already_present(
    tmp_path: Path, profile: RealProfile
) -> None:
    fit = imp.fit_run(1)
    write(tmp_path, {"due.zip": build_zip([("uno.fit", fit), ("due.fit", fit)])})

    summary = profile.import_files([tmp_path / "due.zip"])

    assert outcomes(summary) == {"due.zip!due.fit": IMPORTED, "due.zip!uno.fit": ALREADY}
    assert len(stored_files(profile.folder)) == 1


def test_a_zip_with_two_entries_of_the_same_name_is_refused_whole_and_the_rest_imports(
    tmp_path: Path, profile: RealProfile
) -> None:
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)  # zipfile avvisa dei nomi doppi
        archive.writestr("stesso.fit", imp.fit_run(1))
        archive.writestr("stesso.fit", imp.fit_run(2))
    write(tmp_path, {"doppio-nome.zip": buffer.getvalue(), "sano.fit": imp.fit_run(3)})

    summary = profile.import_files([tmp_path / "doppio-nome.zip", tmp_path / "sano.fit"])

    assert outcomes(summary) == {
        "doppio-nome.zip": (ImportStatus.FAILED, ImportIssue.ARCHIVE_INVALID),
        "sano.fit": IMPORTED,
    }
    assert len(profile.records()) == 1


# --- T-GPX-01 e T-GPX-02 -------------------------------------------------------------------------


def gpx_file(tmp_path: Path, name: str, spec: gw.GpxSpec) -> Path:
    path = tmp_path / name
    path.write_bytes(gw.build_gpx(spec))
    return path


def test_t_gpx_01_gpx_activities_reach_the_index_and_the_load_with_the_right_source(
    tmp_path: Path, profile: RealProfile
) -> None:
    start = imp.run_start(1)
    with_hr = gw.line_points(
        start, 181, step_s=10, lat_step_deg=0.0001, ele_m=[10.0] * 181, hr=[150] * 181
    )
    without_hr = gw.line_points(
        start + timedelta(days=1), 181, step_s=10, lat_step_deg=0.0001, ele_m=[10.0] * 181
    )
    two_segments = [
        gw.line_points(start + timedelta(days=2), 91, step_s=10, lat_step_deg=0.0001),
        gw.line_points(start + timedelta(days=2, minutes=20), 91, step_s=10, lat_step_deg=0.0001),
    ]
    files = [
        gpx_file(tmp_path, "con_fc.gpx", gw.GpxSpec([with_hr], sport_type="running")),
        gpx_file(tmp_path, "senza_fc.gpx", gw.GpxSpec([without_hr], sport_type="kayaking")),
        gpx_file(tmp_path, "due_segmenti.gpx", gw.GpxSpec(two_segments, sport_type="cycling")),
    ]

    summary = profile.import_files(files)

    assert {r.status for r in summary.results} == {ImportStatus.IMPORTED}
    by_day = {r.start.date(): r for r in profile.records()}
    assert [r.file_format for r in by_day.values()] == [FileFormat.GPX] * 3
    assert by_day[start.date()].sport is Sport.RUNNING
    assert by_day[(start + timedelta(days=1)).date()].sport is Sport.OTHER
    assert by_day[(start + timedelta(days=2)).date()].sport is Sport.CYCLING
    overview = profile.overview()
    sources = {a.local_day: a.load.source for a in overview.activities}
    assert sources[start.date()] is LoadSource.HEART_RATE
    assert sources[(start + timedelta(days=1)).date()] is LoadSource.ESTIMATE
    two = by_day[(start + timedelta(days=2)).date()]
    assert two.timer_s == pytest.approx(2 * 900.0)  # la pausa tra i segmenti non conta


def test_t_gpx_01_a_gpx_without_times_is_ignored_and_a_gpx_with_a_doctype_is_refused(
    tmp_path: Path, profile: RealProfile
) -> None:
    no_times = gw.build_gpx(gw.GpxSpec([[gw.GpxPoint(1.0, 1.0), gw.GpxPoint(1.0, 1.1)]]))
    write(tmp_path, {"senza.gpx": no_times, "dtd.gpx": gw.gpx_with_empty_doctype()})

    summary = profile.import_files([tmp_path / "senza.gpx", tmp_path / "dtd.gpx"])

    assert outcomes(summary) == {
        "senza.gpx": (ImportStatus.IGNORED, ImportIssue.NOT_AN_ACTIVITY),
        "dtd.gpx": (ImportStatus.FAILED, ImportIssue.CORRUPT),
    }
    assert profile.records() == []


@pytest.mark.parametrize("first", ["fit", "gpx"])
@pytest.mark.parametrize(
    ("shift_s", "duplicate"),
    [(0, True), (59, True), (60, True), (-60, True), (61, False), (-61, False)],
)
def test_t_gpx_02_arrival_order_and_the_60_second_boundary(
    tmp_path: Path, profile: RealProfile, first: str, shift_s: int, duplicate: bool
) -> None:
    fit = imp.fit_run(1)
    start = imp.run_start(1) + timedelta(seconds=shift_s)
    gpx = gw.build_gpx(gw.GpxSpec([gw.line_points(start, 181, step_s=10, lat_step_deg=0.0001)]))
    write(tmp_path, {"a.fit": fit, "b.gpx": gpx})
    ordered = [tmp_path / "a.fit", tmp_path / "b.gpx"]
    if first == "gpx":
        ordered.reverse()

    for path in ordered:  # due import separati: l'ordine di arrivo e quello scelto
        last = profile.import_files([path])

    ((_, outcome),) = ((r.name, r.status) for r in last.results)
    assert (outcome is ImportStatus.DUPLICATE_OF_OTHER_FORMAT) is duplicate
    assert len(profile.records()) == (1 if duplicate else 2)
    kept = profile.records()[0].file_format
    if duplicate:
        assert kept is (FileFormat.FIT if first == "fit" else FileFormat.GPX)  # prima arrivata


@pytest.mark.parametrize(
    ("gpx_seconds", "duplicate"), [(1710, True), (1890, True), (1700, False), (1900, False)]
)
def test_t_gpx_02_duration_within_5_percent_of_the_longer_is_a_duplicate(
    tmp_path: Path, profile: RealProfile, gpx_seconds: int, duplicate: bool
) -> None:
    write(tmp_path, {"a.fit": imp.fit_run(1)})
    path = tmp_path / "b.gpx"
    path.write_bytes(imp.gpx_run(1, seconds=gpx_seconds))
    profile.import_files([tmp_path / "a.fit"])

    summary = profile.import_files([path])

    ((result),) = summary.results
    assert (result.status is ImportStatus.DUPLICATE_OF_OTHER_FORMAT) is duplicate


def test_t_gpx_02_a_duplicate_is_not_stored_and_does_not_leave_a_file(
    tmp_path: Path, profile: RealProfile
) -> None:
    fit, gpx = imp.same_run_as_fit_and_gpx(2)
    write(tmp_path, {"a.fit": fit, "b.gpx": gpx})

    profile.import_files([tmp_path / "a.fit", tmp_path / "b.gpx"])

    assert {p.suffix for p in stored_files(profile.folder)} == {".fit"}
