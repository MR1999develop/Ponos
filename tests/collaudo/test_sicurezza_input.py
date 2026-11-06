# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-02, T-SEC-04, T-SEC-05, T-SEC-09 sulla pila reale (file veri, archivio e SQLite veri).

Il corpus ostile (FIT, GPX, ZIP) entra dalla cartella come lo riceverebbe l'app. Oracoli oltre
l'esito: nessun record, nessun file nell'archivio, nulla scritto fuori dalla cartella del profilo,
nessuna connessione di rete tentata, memoria al picco sotto un tetto.
"""

from __future__ import annotations

import ast
import json
import os
import random
import socket
import stat
import struct
import subprocess
import sys
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ponos_coach.activities.domain import FileFormat
from ponos_coach.activities.ports import ArchiveWriteError, ImportIssue
from ponos_coach.activities.use_cases import ImportStatus
from ponos_coach.platform import windows_api
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR, ProfileName
from ponos_coach.storage import file_archive
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.import_scan import FolderImportSource
from tests.collaudo.support import (
    RealProfile,
    create_profile,
    outcomes,
    real_ports,
    stored_files,
    temporaries,
)
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators import import_files as imp
from tests.fixtures.generators import zip_writer as zw
from tests.platform.windows_dacl import dacl_sddl, normalize_sid, trustees

HOSTILE_FIT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "fit-ostili"
MIB = 1024 * 1024
PEAK_MEMORY_LIMIT = 96 * MIB  # tetto al picco di memoria dell'intero corpus ostile
FAILURES = {ImportStatus.FAILED, ImportStatus.IGNORED}


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


def all_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


@contextmanager
def peak_memory() -> Iterator[list[int]]:
    """Picco di memoria di Python nel blocco, in byte (la lista ha un solo elemento)."""
    peak = [0]
    tracemalloc.start()
    try:
        yield peak
    finally:
        peak[0] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()


@contextmanager
def loopback_listener() -> Iterator[tuple[int, list[bool]]]:
    """Un server sul solo 127.0.0.1: dice se qualcuno ha provato a connettersi."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.setblocking(False)
    contacted = [False]
    try:
        yield server.getsockname()[1], contacted
    finally:
        try:
            server.accept()
            contacted[0] = True
        except BlockingIOError:
            pass
        server.close()


# --- T-SEC-02: FIT -------------------------------------------------------------------------------


def hostile_fit_files() -> dict[str, bytes]:
    random_bytes = random.Random(20260705).randbytes(300_000)
    files = {f"fit/{p.name}": p.read_bytes() for p in sorted(HOSTILE_FIT_DIR.glob("*.fit"))}
    files |= {
        "fit/dichiara_4gib.fit": fw.huge_declared_file(),
        "fit/senza_definizione.fit": fw.undefined_local_message(),
        "fit/png.fit": b"\x89PNG\r\n\x1a\n" + bytes(2000),
        "fit/eseguibile.fit": b"MZ\x90\x00" + bytes(4096),
        "fit/zip_travestito.fit": zw.garmin_download(imp.fit_run(1)),
        "fit/sqlite.fit": b"SQLite format 3\x00" + bytes(4096),
        "fit/json.fit": b'{"activity": "no"}',
        "fit/casuale.fit": random_bytes,
        "fit/tcx.fit": b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="urn:x"/>',
        "fit/gpx_travestito.fit": imp.gpx_run(1),
    }
    return files


def test_t_sec_02_hostile_fit_corpus_is_refused_end_to_end_without_side_effects(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "ostili"
    files = hostile_fit_files()
    write(source, files)
    before = all_files(tmp_path)

    with peak_memory() as peak:
        summary = profile.import_folder(source)

    assert set(outcomes(summary)) == set(files)
    assert {status for status, _ in outcomes(summary).values()} <= FAILURES
    assert profile.records() == []
    assert stored_files(profile.folder) == []
    assert temporaries(profile.folder) == []
    assert all_files(tmp_path) == before  # nulla di nuovo, da nessuna parte
    assert peak[0] < PEAK_MEMORY_LIMIT


def test_t_sec_02_a_valid_fit_among_the_hostile_ones_is_still_imported(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "misto"
    write(source, {**hostile_fit_files(), "fit/valido.fit": imp.fit_run(1)})

    summary = profile.import_folder(source)

    assert outcomes(summary)["fit/valido.fit"] == (ImportStatus.IMPORTED, None)
    assert [r.name for r in summary.results if r.status is ImportStatus.IMPORTED] == [
        "fit/valido.fit"
    ]
    assert len(profile.records()) == 1


def test_t_sec_02_file_just_over_50_mib_is_skipped_without_being_read(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "grande"
    source.mkdir()
    with (source / "enorme.fit").open("wb") as handle:
        handle.truncate(50 * MIB + 1)  # file sparso: nessun byte scritto davvero

    with peak_memory() as peak:
        summary = profile.import_folder(source)

    assert outcomes(summary) == {"enorme.fit": (ImportStatus.FAILED, ImportIssue.TOO_LARGE)}
    assert peak[0] < 4 * MIB  # non si e allocato nulla di proporzionale al file


def test_t_sec_02_file_growing_after_the_scan_is_stopped_at_the_cap_not_read_whole(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "cresce"
    write(source, {"a.fit": imp.fit_run(1)})
    reader = FolderImportSource(source)
    (entry,) = reader.scan().entries
    with (source / "a.fit").open("wb") as handle:
        handle.truncate(50 * MIB + 1)

    with peak_memory() as peak, pytest.raises(Exception, match="too_large"):
        reader.read(entry, 50 * MIB)
    reader.close()

    assert peak[0] < 4 * MIB


@pytest.mark.collaudo
def test_t_sec_02_collaudo_a_fit_with_more_records_than_the_cap_is_refused(
    tmp_path: Path, profile: RealProfile
) -> None:
    definition = struct.pack("<BBBHB", 0x40, 0, 0, 20, 1) + struct.pack("<BBB", 253, 4, 0x86)
    body = definition + (b"\x00" + struct.pack("<I", 1_100_000_000)) * 1_200_000
    source = tmp_path / "senza-fine"
    write(source, {"infinito.fit": fw.wrap_body(body)})

    with peak_memory() as peak:
        summary = profile.import_folder(source)

    ((status, issue),) = outcomes(summary).values()
    assert status is ImportStatus.FAILED
    assert issue in {ImportIssue.TOO_MANY_RECORDS, ImportIssue.CORRUPT, ImportIssue.NOT_AN_ACTIVITY}
    assert profile.records() == []
    assert peak[0] < 512 * MIB


# --- T-SEC-02: GPX -------------------------------------------------------------------------------


def test_t_sec_02_hostile_gpx_corpus_is_refused_without_network_or_side_effects(
    tmp_path: Path, profile: RealProfile
) -> None:
    sentinel = tmp_path / "segreto.txt"
    sentinel.write_text("contenuto che non deve uscire", encoding="utf-8")
    with loopback_listener() as (port, contacted):
        url = f"http://127.0.0.1:{port}/x"
        files = {
            "gpx/laughs.gpx": gw.billion_laughs(),
            "gpx/quadratico.gpx": gw.quadratic_blowup(),
            "gpx/entita_http.gpx": gw.external_entity(url),
            "gpx/entita_file.gpx": gw.external_entity(sentinel.as_uri()),
            "gpx/dtd_http.gpx": gw.external_dtd(url + ".dtd"),
            "gpx/parametrica.gpx": gw.parameter_entity(url + ".ent"),
            "gpx/doctype_vuoto.gpx": gw.gpx_with_empty_doctype(),
            "gpx/annidato.gpx": gw.deeply_nested(20_000),
            "gpx/testo_enorme.gpx": gw.huge_text(8 * MIB),
            "gpx/attributo_enorme.gpx": gw.huge_attribute(1 * MIB),
            "gpx/png.gpx": b"\x89PNG\r\n\x1a\n" + bytes(2000),
            "gpx/fit_travestito.gpx": imp.fit_run(1),
            "gpx/casuale.gpx": random.Random(7).randbytes(100_000),
            "gpx/xml_non_gpx.gpx": b'<?xml version="1.0"?><svg xmlns="urn:x"/>',
            "gpx/senza_chiusura.gpx": b'<?xml version="1.0"?><gpx><trk><trkseg>' * 50,
        }
        source = tmp_path / "ostili"
        write(source, files)
        before = all_files(tmp_path)

        with peak_memory() as peak:
            summary = profile.import_folder(source)

    assert not contacted[0], "il lettore GPX ha aperto una connessione"
    assert set(outcomes(summary)) == set(files)
    assert {status for status, _ in outcomes(summary).values()} <= FAILURES
    assert profile.records() == []
    assert stored_files(profile.folder) == []
    assert all_files(tmp_path) == before
    assert peak[0] < PEAK_MEMORY_LIMIT


@pytest.mark.collaudo
def test_t_sec_02_collaudo_gpx_with_millions_of_points_is_refused_by_the_cap(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "milioni"
    write(source, {"punti.gpx": gw.points_without_data(1_200_000)})

    with peak_memory() as peak:
        summary = profile.import_folder(source)

    ((status, issue),) = outcomes(summary).values()
    assert status in FAILURES
    assert issue is not None
    assert profile.records() == []
    assert peak[0] < 1024 * MIB


# --- T-SEC-02: ZIP -------------------------------------------------------------------------------

UNSAFE_ENTRY_NAMES = [
    "../evil.fit",
    "..\\evil.fit",
    "/abs/evil.fit",
    "\\abs\\evil.fit",
    "C:\\evil.fit",
    "C:evil.fit",
    "a/../../evil.fit",
    "a\\..\\..\\evil.fit",
    "con.fit",
    "dir/aux.fit",
    "NUL.gpx",
    "ok/\x07campanello.fit",
    "traversal/./../../x.fit",
    "a:b.fit",
    "a|b.fit",
]


def test_t_sec_02_zip_entries_with_hostile_names_write_nothing_outside_the_archive(
    tmp_path: Path, profile: RealProfile
) -> None:
    fit = imp.fit_run(1)
    # Su Windows `zipfile` legge `\` come `/`: i nomi uguali dopo questo si contano una volta.
    distinct = {
        name.replace("\\", "/") if sys.platform == "win32" else name: name
        for name in UNSAFE_ENTRY_NAMES
    }
    entries = [(name, fit) for name in distinct.values()]
    entries.append(("buona/corsa.fit", imp.fit_run(2)))
    write(tmp_path / "src", {"nomi.zip": zw.build_zip(entries)})
    before = all_files(tmp_path)

    summary = profile.import_folder(tmp_path / "src")

    unsafe = [r for r in summary.results if r.issue is ImportIssue.UNSAFE_NAME]
    assert len(unsafe) == len(distinct)
    assert [r.name for r in summary.results if r.status is ImportStatus.IMPORTED] == [
        "nomi.zip!buona/corsa.fit"
    ]
    new_files = all_files(tmp_path) - before
    assert {Path(name).parts[:2] for name in new_files} <= {
        ("profilo", PROFILE_ARCHIVE_DIR),
        ("profilo", "ponos.sqlite3-wal"),
        ("profilo", "ponos.sqlite3-shm"),
    }
    assert len(profile.records()) == 1


def hostile_zips() -> dict[str, bytes]:
    fit = imp.fit_run(1)
    return {
        "bomba.zip": zw.zero_bomb(45 * MIB),
        "dichiara_falso.zip": zw.patch_entry(
            zw.build_zip([("a.fit", b"x" * 100)]), 0, file_size=4_000_000_000
        ),
        "crc.zip": zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, crc=0x12345678),
        "cifrato.zip": zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, flags=0x1),
        "annidato.zip": zw.build_zip([("dentro.zip", zw.garmin_download(fit))]),
        "troncato.zip": zw.garmin_download(fit)[:-30],
        "rumore.zip": random.Random(3).randbytes(50_000),
        "taglia_piu_piccola.zip": zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, file_size=10),
    }


def test_t_sec_02_hostile_zip_corpus_is_refused_with_a_reason_and_no_side_effects(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "ostili"
    write(source, hostile_zips())
    before = all_files(tmp_path)

    with peak_memory() as peak:
        summary = profile.import_folder(source)

    assert {status for status, _ in outcomes(summary).values()} == {ImportStatus.FAILED}
    assert all(issue is not None for _, issue in outcomes(summary).values())
    assert profile.records() == []
    assert stored_files(profile.folder) == []
    assert all_files(tmp_path) == before
    assert peak[0] < PEAK_MEMORY_LIMIT


def test_t_sec_02_a_zip_inside_the_folder_does_not_hide_the_loose_files_next_to_it(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "misto"
    write(source, {**hostile_zips(), "valido.fit": imp.fit_run(5)})

    summary = profile.import_folder(source)

    assert [r.name for r in summary.results if r.status is ImportStatus.IMPORTED] == ["valido.fit"]


# --- T-SEC-04 ------------------------------------------------------------------------------------


def make_junction(link: Path, target: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603 - comando di sistema, argomenti fissi
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        else:
            link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError, subprocess.CalledProcessError):
        pytest.skip("il sistema non permette di creare giunzioni o collegamenti")


def test_t_sec_04_a_junction_to_the_outside_and_a_loop_are_reported_and_not_followed(
    tmp_path: Path, profile: RealProfile
) -> None:
    outside = tmp_path / "fuori"
    write(outside, {"segreta.fit": imp.fit_run(9)})
    source = tmp_path / "sorgente"
    write(source, {"dentro.fit": imp.fit_run(1)})
    make_junction(source / "scorciatoia", outside)
    make_junction(source / "anello", source)

    summary = profile.import_folder(source)

    results = outcomes(summary)
    assert results["dentro.fit"] == (ImportStatus.IMPORTED, None)
    assert results["scorciatoia"] == (ImportStatus.FAILED, ImportIssue.NOT_REGULAR_FILE)
    assert results["anello"] == (ImportStatus.FAILED, ImportIssue.NOT_REGULAR_FILE)
    assert len(results) == 3
    assert len(profile.records()) == 1
    assert (outside / "segreta.fit").exists()  # e non e stata toccata


def test_t_sec_04_a_folder_that_is_called_like_a_file_is_walked_as_a_folder(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    write(source, {"trappola.fit/dentro.fit": imp.fit_run(1), "trappola.gpx/x.txt": b"x"})

    summary = profile.import_folder(source)

    assert outcomes(summary) == {"trappola.fit/dentro.fit": (ImportStatus.IMPORTED, None)}


def test_t_sec_04_importing_the_profile_folder_itself_changes_nothing(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "sorgente"
    write(source, {"a.fit": imp.fit_run(1), "b.gpx": imp.gpx_run(2)})
    profile.import_folder(source)
    snapshot = {p: p.read_bytes() for p in sorted(stored_files(profile.folder))}

    summary = profile.import_folder(profile.folder)  # errore dell'utente: la cartella dei dati

    assert {r.status for r in summary.results} == {ImportStatus.ALREADY_PRESENT}
    assert {p: p.read_bytes() for p in sorted(stored_files(profile.folder))} == snapshot
    assert len(profile.records()) == 2


def test_t_sec_04_archive_paths_never_contain_anything_from_the_file_names(
    tmp_path: Path, profile: RealProfile
) -> None:
    names = [
        "o'brien.fit",
        "a b (1).fit",
        "100%.fit",
        "\u202egpj.fit",  # inversione bidi
        "caff\u00e8 \U0001f3c3.fit",
        "..fit",
        "x'); DROP TABLE activities;--.fit",
    ]
    source = tmp_path / "nomi"
    write(source, {name: imp.fit_run(number) for number, name in enumerate(names)})

    summary = profile.import_folder(source)

    assert {r.status for r in summary.results} == {ImportStatus.IMPORTED}
    for record in profile.records():
        assert str(record.archived.relative_path).count("/") == 1
        year, _, name = str(record.archived.relative_path).partition("/")
        assert year.isdigit()
        assert name == f"{record.archived.sha256_hex}.fit"
    assert {p.name for p in stored_files(profile.folder)}.isdisjoint(set(names))


# --- T-SEC-05 ------------------------------------------------------------------------------------

SQL_NAMES = [
    "Robert'); DROP TABLE activities;--",
    "' OR '1'='1",
    '"; SELECT * FROM meta; --',
    "'||(SELECT 1)||'",
    "x' UNION SELECT key, value FROM meta --",
    "100% _ \\ \\' \\\\",
    "\U0001f3c3 Atleta \u05d0\u05d1",
    "a\u0301 (NFD)",
    "NULL",
    "';--",
]


@pytest.mark.parametrize("name", SQL_NAMES)
def test_t_sec_05_profile_names_with_quotes_and_sql_are_stored_and_read_back_identical(
    tmp_path: Path, name: str
) -> None:
    expected = ProfileName.parse(name).value  # il nome canonico (NFC) e quello salvato
    profile = create_profile(tmp_path, name, folder_name="o'brien profilo")
    profile_id = profile.session.info.profile_id
    (tmp_path / "a.fit").write_bytes(imp.fit_run(1))
    profile.import_files([tmp_path / "a.fit"])
    profile.close()

    ports = real_ports(tmp_path / "config")
    (entry,) = ports.registry.list_entries()
    assert entry.cached_name.value == expected
    assert (
        json.loads((tmp_path / "config" / "profiles.json").read_text("utf-8"))["profiles"][0][
            "name"
        ]
        == expected
    )
    reopened = ports.storage.open(entry.folder)
    try:
        assert reopened.info.name.value == expected
        assert reopened.info.profile_id == profile_id
        assert len(reopened.activities.list_by_start()) == 1  # le tabelle ci sono ancora
    finally:
        reopened.close()


def test_t_sec_05_no_sql_in_the_application_is_built_from_text(tmp_path: Path) -> None:
    """Controllo statico: ogni argomento di `execute*` e una costante, mai testo composto."""
    root = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
    constants: dict[str, set[str]] = {}
    suspicious: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        constants[path.name] = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "executescript"}
                and node.args
            ):
                continue
            if not _is_constant_sql(node.args[0], constants[path.name]):
                suspicious.append(f"{path.name}: {ast.unparse(node.args[0])[:80]}")
    # Le sole composizioni ammesse: PRAGMA con costanti intere del modulo e le istruzioni
    # delle migrazioni, che sono costanti di `sqlite_schema`. Qualsiasi altra va rivista.
    assert suspicious == [
        "sqlite_database.py: f'PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}'",
        "sqlite_database.py: f'PRAGMA user_version = {int(SCHEMA_VERSION)}'",
        "sqlite_database.py: statement",
    ]


def _is_constant_sql(node: ast.expr, module_constants: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Name):
        return node.id in module_constants
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_constant_sql(node.left, module_constants) and _is_constant_sql(
            node.right, module_constants
        )
    return False


# --- T-SEC-09 ------------------------------------------------------------------------------------


def populated_profile_paths(tmp_path: Path, profile: RealProfile) -> tuple[list[Path], list[Path]]:
    """(cartella, lock e database con -wal/-shm), (archivio e tutto cio che contiene)."""
    source = tmp_path / "sorgente"
    write(
        source,
        {
            "a.fit": imp.fit_run(1),
            "b.gpx": imp.gpx_run(2),
            "z.zip": zw.garmin_download(imp.fit_run(3)),
        },
    )
    profile.import_folder(source)  # il database e aperto: ci sono anche -wal e -shm
    archive = profile.folder / PROFILE_ARCHIVE_DIR
    in_archive = [archive, *sorted(archive.rglob("*"))]
    others = [p for p in profile.folder.rglob("*") if p not in in_archive]
    assert {p.name for p in others} == {
        "ponos.lock",
        "ponos.sqlite3",
        "ponos.sqlite3-shm",
        "ponos.sqlite3-wal",
    }
    assert len(in_archive) == 1 + 1 + 3  # radice, anno, tre file
    return [profile.folder, *others], in_archive


def assert_private(paths: list[Path]) -> None:
    if sys.platform == "win32":
        allowed = {normalize_sid(windows_api.current_user_sid()), normalize_sid("SY")}
        for path in paths:
            assert set(trustees(dacl_sddl(path))) <= allowed, (path.name, dacl_sddl(path))
    else:
        for path in paths:
            assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path.name


def test_t_sec_09_folder_lock_and_database_files_are_private_to_the_user(
    tmp_path: Path, profile: RealProfile
) -> None:
    database_side, _ = populated_profile_paths(tmp_path, profile)

    assert_private(database_side)


def test_d_f1_06_the_archive_folder_and_the_original_files_are_private_to_the_user(
    tmp_path: Path, profile: RealProfile
) -> None:
    _, archive_side = populated_profile_paths(tmp_path, profile)

    assert_private(archive_side)


def test_t_sec_09_the_registry_and_its_lock_are_written_by_the_app_only_inside_config(
    tmp_path: Path, profile: RealProfile
) -> None:
    config = tmp_path / "config"

    assert sorted(p.name for p in config.iterdir()) == ["profiles.json", "profiles.json.lock"]
    if sys.platform != "win32":
        for path in config.iterdir():
            assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_t_sec_09_a_temporary_name_that_already_exists_is_never_opened_or_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archivio"
    year = root / "2026"
    year.mkdir(parents=True)
    planted = year / (".tmp-" + "ab" * 16)
    planted.write_bytes(b"gia qui")
    monkeypatch.setattr(file_archive, "secrets", SimpleNamespace(token_hex=lambda _n: "ab" * 16))
    start = imp.run_start(1)

    with pytest.raises(ArchiveWriteError):
        FileArchive(root).store(imp.fit_run(1), FileFormat.FIT, start)

    assert planted.read_bytes() == b"gia qui"
    assert [p.name for p in year.iterdir()] == [planted.name]


def test_t_sec_09_a_few_name_collisions_are_retried_with_a_new_random_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archivio"
    year = root / "2026"
    year.mkdir(parents=True)
    names = iter(["01" * 16, "02" * 16, "03" * 16, "04" * 16])
    for taken in ("01" * 16, "02" * 16, "03" * 16):
        (year / f".tmp-{taken}").write_bytes(b"occupato")
    monkeypatch.setattr(file_archive, "secrets", SimpleNamespace(token_hex=lambda _n: next(names)))

    archived = FileArchive(root).store(imp.fit_run(1), FileFormat.FIT, imp.run_start(1))

    assert (root / archived.relative_path).read_bytes() == imp.fit_run(1)
    assert {p.read_bytes() for p in year.iterdir() if p.name.startswith(".tmp-")} == {b"occupato"}


def test_t_sec_02_a_file_of_exactly_50_mib_is_read_not_skipped_and_then_refused_as_corrupt(
    tmp_path: Path, profile: RealProfile
) -> None:
    source = tmp_path / "al-limite"
    source.mkdir()
    with (source / "limite.fit").open("wb") as handle:
        handle.truncate(50 * MIB)  # esattamente il tetto: non e "oltre 50 MB"

    summary = profile.import_folder(source)

    assert outcomes(summary) == {"limite.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT)}
    assert profile.records() == []
