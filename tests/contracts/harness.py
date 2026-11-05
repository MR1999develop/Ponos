# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Impalcature che ogni implementazione offre ai test di contratto.

Le porte non permettono di creare un guasto (un file alterato, un temporaneo orfano, un file
in una cartella): ogni implementazione, fake o reale, lo fa con il suo mezzo. Così lo stesso
contratto gira su tutte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ponos_coach.activities.domain import ArchivedFile
from ponos_coach.activities.ports import ActivityArchive
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.profiles.ports import ProfileFolders, ProfileRegistry, ProfileStorage


class ArchiveHarness(Protocol):
    """Un archivio vuoto e i modi per guastarlo."""

    @property
    def archive(self) -> ActivityArchive:
        """L'archivio sotto prova."""
        ...

    def plant_orphan_temporary(self) -> None:
        """Lascia nell'archivio un temporaneo di una scrittura interrotta."""
        ...

    def corrupt(self, file: ArchivedFile) -> None:
        """Altera i byte del file archiviato (stessa dimensione, contenuto diverso)."""
        ...

    def delete(self, file: ArchivedFile) -> None:
        """Toglie il file dall'archivio."""
        ...


class RegistryHarness(Protocol):
    """Un elenco di profili vuoto."""

    @property
    def registry(self) -> ProfileRegistry:
        """L'elenco sotto prova."""
        ...

    def reopen(self) -> ProfileRegistry:
        """Un nuovo oggetto sullo stesso supporto (come dopo un riavvio dell'app)."""
        ...


class FoldersHarness(Protocol):
    """Una cartella radice esistente e vuota in cui provare, e i modi per riempirla."""

    @property
    def folders(self) -> ProfileFolders:
        """Le cartelle sotto prova."""
        ...

    @property
    def root(self) -> Path:
        """Cartella radice esistente e vuota (assoluta)."""
        ...

    @property
    def root_kind(self) -> FolderKind:
        """Tipo che `inspect(root)` deve riportare."""
        ...

    def put_file(self, folder: Path, name: str) -> None:
        """Mette un file qualunque nella cartella esistente."""
        ...

    def make_profile_folder(self, folder: Path) -> None:
        """Mette nella cartella esistente il database di un profilo (come lo lascia `create`)."""
        ...


class StorageHarness(Protocol):
    """Il database dei profili e cartelle in cui crearli."""

    @property
    def storage(self) -> ProfileStorage:
        """Il database sotto prova."""
        ...

    def new_folder(self) -> Path:
        """Una cartella nuova, esistente e vuota, assoluta e distinta a ogni chiamata."""
        ...
