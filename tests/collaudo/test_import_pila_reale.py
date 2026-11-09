# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-IMP-01, T-IMP-03, T-IMP-04 sulla pila reale: si importano solo file FIT.

Cartelle e file veri sul disco, sorgenti `FolderImportSource` e `FilesImportSource`,
archivio su file system e indice SQLite: il comportamento che l'utente vede, file per file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import ImportIssue, ImportLimit, ImportLimitExceededError
from ponos_coach.activities.use_cases import ImportStatus
from tests.collaudo.support import RealProfile, create_profile, outcomes, stored_files
from tests.fixtures.generators import import_files as imp
from tests.fixtures.generators.zip_writer import garmin_download

IMPORTED = (ImportStatus.IMPORTED, None)
ALREADY = (ImportStatus.ALREADY_PRESENT, None)
GPX = b'<?xml version="1.0"?><gpx version="1.1"><trk/></gpx>'


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
    """Scrive l'albero di prova e dice l'esito atteso per ogni file elencato (gli altri formati
    non compaiono: una cartella li ignora)."""
    fit1 = imp.fit_run(1)
    resaved = imp.fit_run(1, seconds=1200)  # stesso file_id, byte diversi
    tcx = b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="urn:x"/>'
    write(
        root,
        {
            "a_corsa.fit": fit1,
            "b_seconda.fit": imp.fit_run(2),
            "c_maiuscolo.FIT": imp.fit_run(3),
            "d_sesta.fit": imp.fit_run(6),
            "copie/a_copia.fit": fit1,
            "copie/profonda/risalvata.fit": resaved,
            "rotti/crc.fit": fit1[:-1] + b"\x00",
            "rotti/troncato.fit": fit1[: len(fit1) // 2],
            "rotti/vuoto.fit": b"",
            "rotti/testo.fit": b"non sono un FIT",
            "rotti/gpx_chiamato.fit": GPX,
            "rotti/garmin.tcx.fit": tcx,
            "ignorati/note.txt": b"niente da importare",
            "ignorati/leggimi.md": b"# leggimi",
            "ignorati/camminata.gpx": GPX,
            "ignorati/scaricato.zip": garmin_download(imp.fit_run(7), activity_id=100),
        },
    )
    return {
        "a_corsa.fit": IMPORTED,
        "b_seconda.fit": IMPORTED,
        "c_maiuscolo.FIT": IMPORTED,
        "d_sesta.fit": IMPORTED,
        "copie/a_copia.fit": ALREADY,
        "copie/profonda/risalvata.fit": ALREADY,
        "rotti/crc.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/troncato.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/vuoto.fit": (ImportStatus.FAILED, ImportIssue.EMPTY),
        "rotti/testo.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/gpx_chiamato.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "rotti/garmin.tcx.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
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
    assert names == sorted(names)  # no file skipped by the source: name order
    assert len(names) == len(set(names))
    assert summary.count(ImportStatus.IMPORTED) == 4
    assert summary.count(ImportStatus.ALREADY_PRESENT) == 2
    assert summary.count(ImportStatus.IGNORED) == 0
    assert summary.count(ImportStatus.FAILED) == 6
    assert not summary.cancelled
    assert snapshot(source) == before  # the source folder is never modified
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
        for name in ("a_corsa.fit", "b_seconda.fit", "c_maiuscolo.FIT", "d_sesta.fit")
    }
    in_archive = {p.stem for p in stored_files(profile.folder)}
    assert in_archive == originals
    assert {p.suffix for p in stored_files(profile.folder)} == {".fit"}
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
    }
    for (
        name,
        outcome,
    ) in expected.items():  # the refusals stay the same, the rest is already present
        if outcome == IMPORTED:
            assert outcomes(again)[name] == ALREADY
        else:
            assert outcomes(again)[name] == outcome
    assert list(profile.records()) == records  # including imported_at
    assert tree_bytes(profile.folder / "archivio") == archive


def test_t_imp_03_touched_renamed_and_copied_files_are_still_already_present(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    write(source, {"x.fit": imp.fit_run(1), "y.fit": imp.fit_run(2)})
    profile.import_folder(source)
    records = list(profile.records())

    (source / "x.fit").touch()
    (source / "x.fit").rename(source / "rinominato.fit")
    (source / "altro").mkdir()
    (source / "y.fit").replace(source / "altro" / "nome.fit")

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


# --- T-IMP-04: solo FIT --------------------------------------------------------------------------


def test_t_imp_04_a_folder_import_leaves_gpx_and_zip_files_alone_and_stores_no_other_format(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    write(
        source,
        {
            "corsa.fit": imp.fit_run(1),
            "camminata.gpx": GPX,
            "scaricato.zip": garmin_download(imp.fit_run(2), activity_id=777),
        },
    )
    before = snapshot(source)

    summary = profile.import_folder(source)

    assert outcomes(summary) == {"corsa.fit": IMPORTED}  # gli altri non compaiono nemmeno
    assert {p.suffix for p in stored_files(profile.folder)} == {".fit"}
    assert len(profile.records()) == 1
    assert snapshot(source) == before


def test_t_imp_04_chosen_gpx_and_zip_files_are_refused_as_unsupported_and_the_rest_imports(
    tmp_path: Path, profile: RealProfile
) -> None:
    write(
        tmp_path,
        {
            "camminata.gpx": GPX,
            "scaricato.zip": garmin_download(imp.fit_run(2), activity_id=777),
            "corsa.fit": imp.fit_run(1),
        },
    )

    summary = profile.import_files(
        [tmp_path / "camminata.gpx", tmp_path / "scaricato.zip", tmp_path / "corsa.fit"]
    )

    unsupported = (ImportStatus.FAILED, ImportIssue.UNSUPPORTED_FORMAT)
    assert outcomes(summary) == {
        "camminata.gpx": unsupported,
        "scaricato.zip": unsupported,
        "corsa.fit": IMPORTED,
    }
    assert len(profile.records()) == 1
    assert len(stored_files(profile.folder)) == 1


def test_t_imp_04_a_hostile_xml_named_gpx_is_never_opened_by_the_import(
    tmp_path: Path, profile: RealProfile
) -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><gpx>&b;&b;&b;</gpx>'
    )
    write(tmp_path, {"bomba.gpx": bomb})

    summary = profile.import_files([tmp_path / "bomba.gpx"])

    assert outcomes(summary) == {"bomba.gpx": (ImportStatus.FAILED, ImportIssue.UNSUPPORTED_FORMAT)}
    assert profile.records() == []


def test_t_imp_04_loose_files_share_the_activity_file_cap(tmp_path: Path) -> None:
    write(tmp_path / "src", {f"{n}.fit": imp.fit_run(n) for n in range(5)})
    opened = create_profile(tmp_path / "p")
    try:
        with pytest.raises(ImportLimitExceededError) as raised:
            opened.import_folder(tmp_path / "src", ImportLimits(max_activity_files=4))
        assert raised.value.limit is ImportLimit.ACTIVITY_FILES
        assert opened.records() == []  # niente import parziali a sorpresa
        assert stored_files(opened.folder) == []
        assert (
            opened.import_folder(tmp_path / "src", ImportLimits(max_activity_files=5)).count(
                ImportStatus.IMPORTED
            )
            == 5
        )
    finally:
        opened.close()
