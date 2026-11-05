# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-04 con due processi veri: due istanze dell'app sullo stesso elenco dei profili.

Due sottoprocessi creano profili diversi (e nella stessa cartella madre, mancante) con la
partenza data insieme da un segnale sullo standard input; poi si controlla l'elenco e ogni
cartella. In piu un test deterministico della perdita di una gara sulla stessa cartella.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.platform.profile_registry import JsonProfileRegistry
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile, ProfileAlreadyExistsError
from ponos_coach.profiles.use_cases import CreateProfile, NewProfileRequest, ProfilePorts
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.builders import physiology
from tests.collaudo.processes import Worker, workers
from tests.collaudo.support import TODAY, fixed_now, new_id, real_ports

PER_WORKER = 6


def read_results(worker: Worker, expected: int) -> list[str]:
    lines: list[str] = []
    while len(lines) < expected:
        line = worker.read_line()
        assert line, "il processo e finito troppo presto"
        lines.append(line)
    assert worker.read_line() == "finished"
    return lines


def test_t_dati_04_two_instances_creating_different_profiles_lose_no_entry(tmp_path: Path) -> None:
    config, parent = tmp_path / "config", tmp_path / "profili"
    with workers(
        ("create", config, parent, "A", PER_WORKER), ("create", config, parent, "B", PER_WORKER)
    ) as (first, second):
        first.expect_ready()
        second.expect_ready()
        first.send("via")
        second.send("via")  # partenza quasi insieme
        outcomes = read_results(first, PER_WORKER) + read_results(second, PER_WORKER)

    assert outcomes == ["ok"] * (2 * PER_WORKER)
    entries = JsonProfileRegistry(config).list_entries()
    assert len(entries) == 2 * PER_WORKER
    assert len({e.profile_id for e in entries}) == len(entries)
    assert len({e.folder for e in entries}) == len(entries)
    storage = SqliteProfileStorage()
    for entry in entries:
        opened = storage.open(entry.folder)  # ogni cartella ha il suo database integro
        try:
            assert opened.info.profile_id == entry.profile_id
            assert opened.info.name == entry.cached_name
        finally:
            opened.close()


@pytest.mark.collaudo
@pytest.mark.parametrize("repetition", range(10))
def test_t_dati_04_collaudo_two_instances_racing_for_the_same_folder_leave_one_valid_profile(
    tmp_path: Path, repetition: int
) -> None:
    config, folder = tmp_path / "config", tmp_path / "contesa"
    with workers(("race", config, folder), ("race", config, folder)) as (first, second):
        first.expect_ready()
        second.expect_ready()
        first.send("via")
        second.send("via")
        outcomes = [read_results(first, 1)[0], read_results(second, 1)[0]]

    assert outcomes.count("ok") >= 1
    losers = [o for o in outcomes if o != "ok"]
    assert all(o.startswith("error ") for o in losers)
    entries = JsonProfileRegistry(config).list_entries()
    assert len(entries) == 1
    assert entries[0].folder == folder
    opened = SqliteProfileStorage().open(folder)  # il profilo vincitore e intero
    try:
        assert opened.info.profile_id == entries[0].profile_id
    finally:
        opened.close()


class _RacingStorage(SqliteProfileStorage):
    """Un altro processo mette il suo database nella cartella un attimo prima del nostro."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        winner = SqliteProfileStorage().create(
            folder, new_id(), ProfileName.parse("Vincitore"), timezone_name
        )
        winner.close()
        return super().create(folder, profile_id, name, timezone_name)


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-07: se la creazione perde la gara (un altro processo ha messo un database "
    "nella cartella tra il controllo e la creazione) l'annullamento cancella tutta la cartella, "
    "compreso il profilo del vincitore",
)
def test_d_f1_07_losing_a_creation_race_never_deletes_the_winners_profile(tmp_path: Path) -> None:
    ports = real_ports(tmp_path / "config")
    racing = ProfilePorts(ports.registry, ports.folders, _RacingStorage())
    folder = tmp_path / "contesa"
    request = NewProfileRequest(
        ProfileName.parse("Perdente"), folder, "Europe/Rome", physiology(), TODAY
    )

    with pytest.raises(ProfileAlreadyExistsError):
        CreateProfile(racing, fixed_now, new_id)(request)

    assert (folder / PROFILE_DATABASE_NAME).exists()  # il profilo del vincitore e salvo
    assert list(ports.registry.list_entries()) == []  # e il perdente non e nell'elenco
