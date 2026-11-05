# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Porte della funzionalità profili: elenco, cartelle con lock, database del profilo.

Le implementazioni reali stanno negli adattatori (`platform` per registro e cartelle,
`storage` per il database); i fake in memoria e i test di contratto in `tests/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.load.ports import LoadCache, PhysiologyStore
from ponos_coach.profiles.domain import (
    FolderInspection,
    ProfileId,
    ProfileInfo,
    ProfileName,
    RegistryEntry,
)

# --- Elenco dei profili --------------------------------------------------------------------


class ProfileRegistryError(Exception):
    """Radice degli errori dell'elenco dei profili (configurazione illeggibile o corrotta)."""


class ProfileAlreadyRegisteredError(ProfileRegistryError):
    """Un profilo con lo stesso id o la stessa cartella è già nell'elenco."""


class ProfileNotRegisteredError(ProfileRegistryError):
    """Il profilo non è nell'elenco."""


class ProfileRegistry(Protocol):
    """Elenco dei profili noti all'app e dell'ultimo usato (configurazione dell'app)."""

    def list_entries(self) -> Sequence[RegistryEntry]:
        """Voci dalla più recentemente usata; a parità, per id."""
        ...

    def add(self, entry: RegistryEntry) -> None:
        """Aggiunge una voce.

        Raises:
            ProfileAlreadyRegisteredError: id o cartella già presenti; nulla cambia.
        """
        ...

    def remove(self, profile_id: ProfileId) -> None:
        """Toglie la voce dall'elenco. La cartella e i dati del profilo non si toccano.

        Raises:
            ProfileNotRegisteredError: id assente.
        """
        ...

    def mark_used(self, profile_id: ProfileId, when_utc: datetime) -> None:
        """Segna l'ultimo uso (UTC) del profilo.

        Raises:
            ProfileNotRegisteredError: id assente.
        """
        ...


# --- Cartelle e lock -----------------------------------------------------------------------


class ProfileFolderError(Exception):
    """Radice degli errori sulle cartelle dei profili."""


class FolderUnavailableError(ProfileFolderError):
    """La cartella non si può leggere o scrivere (permessi, I/O, percorso non valido)."""


class FolderNotEmptyError(ProfileFolderError):
    """La cartella da creare esiste già e contiene qualcosa."""


class NotAProfileFolderError(ProfileFolderError):
    """La cartella non contiene il database di un profilo: non si cancella."""


class ProfileInUseError(ProfileFolderError):
    """Il profilo è già aperto da un'altra istanza (lock preso)."""


class ProfileLock(Protocol):
    """Lock esclusivo di un profilo, tenuto finché non si rilascia."""

    def release(self) -> None:
        """Rilascia il lock; chiamarlo di nuovo non ha effetti."""
        ...


class ProfileFolders(Protocol):
    """Cartelle dei profili: ispezione, creazione privata, lock esclusivo, cancellazione."""

    def inspect(self, folder: Path) -> FolderInspection:
        """Stato (mancante, vuota, non vuota) e tipo (locale, di rete, sincronizzata, ignoto).

        Raises:
            FolderUnavailableError: la cartella esiste ma non si può leggere.
        """
        ...

    def create_private_folder(self, folder: Path) -> None:
        """Crea la cartella (e i genitori mancanti) leggibile e scrivibile solo dall'utente.

        Se esiste ed è vuota la riusa, restringendone i permessi.

        Raises:
            FolderNotEmptyError: esiste e non è vuota; nulla cambia.
            FolderUnavailableError: creazione o permessi non riusciti.
        """
        ...

    def acquire_lock(self, folder: Path) -> ProfileLock:
        """Prende il lock esclusivo del profilo in `folder`, senza attese.

        Un lock rimasto da un processo morto si riconosce e si supera.

        Raises:
            ProfileInUseError: il lock è tenuto da un'altra istanza (o da chi chiama).
            FolderUnavailableError: la cartella non esiste o non è scrivibile.
        """
        ...

    def delete_folder(self, folder: Path) -> None:
        """Cancella la cartella di un profilo con tutto il contenuto.

        Solo se contiene il database di un profilo e se nessuna istanza la tiene aperta; il
        lock lo prende l'operazione stessa, quindi chi chiama deve aver rilasciato il suo.

        Raises:
            NotAProfileFolderError: manca il database del profilo (anche cartella inesistente).
            ProfileInUseError: il profilo è aperto.
            FolderUnavailableError: cancellazione non riuscita.
        """
        ...


# --- Database del profilo ------------------------------------------------------------------


class ProfileStorageError(Exception):
    """Radice degli errori del database di un profilo."""


class ProfileAlreadyExistsError(ProfileStorageError):
    """La cartella ha già il database di un profilo: `create` non lo sovrascrive mai."""


class ProfileNotFoundError(ProfileStorageError):
    """La cartella non ha il database di un profilo."""


class ProfileFormatTooNewError(ProfileStorageError):
    """Il database è di una versione più nuova di quella che questa app conosce."""


class ProfileCorruptError(ProfileStorageError):
    """Il database non si apre o non supera i controlli di integrità."""


class OpenProfile(Protocol):
    """Un profilo aperto: le sue informazioni e gli store che condividono una connessione.

    Le transazioni di `activities.transaction()` coprono anche `physiology` e `load_cache`.
    Va chiuso con `close()`; dopo la chiusura gli store non si usano più.
    """

    @property
    def info(self) -> ProfileInfo:
        """Informazioni del profilo lette dal database."""
        ...

    @property
    def activities(self) -> ActivityIndex:
        """Indice delle attività."""
        ...

    @property
    def physiology(self) -> PhysiologyStore:
        """Parametri fisiologici con data di validità."""
        ...

    @property
    def load_cache(self) -> LoadCache:
        """Cache dei carichi."""
        ...

    def close(self) -> None:
        """Chiude la connessione (checkpoint compreso); chiamarlo di nuovo non ha effetti."""
        ...


class ProfileStorage(Protocol):
    """Crea o apre il database di un profilo in una cartella."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """Crea il database del profilo con lo schema più recente.

        La cartella esiste già (la crea `ProfileFolders`).

        Raises:
            ProfileAlreadyExistsError: c'è già un database; non si tocca.
            ProfileStorageError: creazione non riuscita; non resta nulla a metà.
            ValueError: `timezone_name` sconosciuto.
        """
        ...

    def open(self, folder: Path) -> OpenProfile:
        """Apre il database, portandolo alla versione più recente se è più vecchio.

        Raises:
            ProfileNotFoundError: manca il database.
            ProfileFormatTooNewError: versione più nuova di quella nota; non si tocca.
            ProfileCorruptError: database illeggibile o non integro.
        """
        ...
