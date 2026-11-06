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

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import ActivityIndex, SyncJournal
from ponos_coach.load.ports import LoadCache, PhysiologyStore
from ponos_coach.profiles.domain import (
    FolderInspection,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
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

    def upsert(self, entry: RegistryEntry) -> None:
        """Porta l'elenco allo stato "questo profilo sta in questa cartella", in una sola scrittura.

        Sostituisce la voce con lo stesso id (qualunque fosse la sua cartella), toglie ogni altra
        voce con la stessa cartella e, se non c'era nessuna voce con quell'id, aggiunge la nuova.
        Tutto o niente: se la scrittura fallisce l'elenco resta com'era.
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


class FolderHasOtherContentError(FolderUnavailableError):
    """La cartella del profilo contiene file o cartelle che l'app non ha creato: non si cancella.

    È una `FolderUnavailableError`: chi non distingue il caso lo tratta come una cancellazione
    non riuscita; chi lo distingue può dire all'utente di toglierli a mano.
    """


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
        """Stato (mancante, vuota, non vuota), tipo (locale, di rete, sincronizzata, ignoto) e
        se contiene il database di un profilo.

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

    def remove_if_empty(self, folder: Path) -> bool:
        """Toglie la cartella se è vuota a parte il file di lock dell'app; non segue i link.

        Serve ad annullare una creazione fallita. Non tocca nulla (e dà `False`) se la cartella
        manca, è un link, contiene altro oltre al file di lock o è bloccata da un'altra istanza.

        Raises:
            FolderUnavailableError: cartella non leggibile o rimozione non riuscita.
        """
        ...

    def delete_folder(self, folder: Path) -> None:
        """Cancella la cartella di un profilo con tutto il contenuto.

        Solo se contiene il database di un profilo e se nessuna istanza la tiene aperta; il
        lock lo prende l'operazione stessa, quindi chi chiama deve aver rilasciato il suo.
        Si cancellano soltanto i nomi che l'app mette in una cartella di profilo (database con i
        file `-wal`, `-shm` e `-journal`, `archivio/`, `backup/`, lock): se c'è altro non si
        tocca nulla.

        Raises:
            NotAProfileFolderError: manca il database del profilo (anche cartella inesistente).
            ProfileInUseError: il profilo è aperto.
            FolderHasOtherContentError: nella cartella c'è altro; nulla è stato cancellato.
            FolderUnavailableError: cancellazione non riuscita.
        """
        ...


# --- Token di Garmin -----------------------------------------------------------------------


class TokenStoreError(Exception):
    """Radice degli errori dell'archivio dei token. Mai token nei messaggi."""


class TokenStoreUnavailableError(TokenStoreError):
    """Il portachiavi non si può usare (bloccato, assente, permessi, I/O)."""


class TokenStoreCorruptError(TokenStoreError):
    """C'è un contenuto per il profilo ma non è leggibile (alterato, di un altro utente o di una
    versione sconosciuta): chi chiama lo tratta come "nessun token" e chiede il login."""


class TokenStore(Protocol):
    """Token di Garmin per profilo, nel portachiavi del sistema (mai nella cartella del profilo).

    Ogni profilo ha i suoi token, legati al suo id: quelli di un profilo non si leggono con l'id
    di un altro. I token si proteggono con il sistema (DPAPI su Windows, Secret Service su
    Ubuntu) e non compaiono in log né messaggi.
    """

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """Salva i token del profilo, sostituendo quelli che c'erano (tutto o niente).

        Raises:
            TokenStoreUnavailableError: salvataggio non riuscito; i token di prima restano.
        """
        ...

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """I token del profilo, o None se non ce ne sono.

        Raises:
            TokenStoreCorruptError: il contenuto c'è ma non si legge.
            TokenStoreUnavailableError: il portachiavi non risponde.
        """
        ...

    def delete(self, profile_id: ProfileId) -> None:
        """Cancella i token del profilo (logout). Idempotente: nessun errore se non ci sono.

        Raises:
            TokenStoreUnavailableError: cancellazione non riuscita.
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

    Le transazioni di `activities.transaction()` coprono anche `sync_journal`, `physiology`
    e `load_cache`.
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
    def sync_journal(self) -> SyncJournal:
        """Giornale della sincronizzazione (stessa connessione e transazione dell'indice)."""
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

    def peek(self, folder: Path) -> ProfilePeek:
        """Legge informazioni e numero di attività senza aprire il profilo: sola lettura.

        Non migra, non scrive e non prende il lock: un profilo aperto altrove si può guardare.
        Un database di una versione precedente si legge com'è (`info.format_version` la dice).

        Raises:
            ProfileNotFoundError: manca il database.
            ProfileFormatTooNewError: versione più nuova di quella nota.
            ProfileCorruptError: database illeggibile o non di un profilo.
            ProfileStorageError: altro guasto (I/O, lock).
        """
        ...

    def open(self, folder: Path, *, check_integrity: bool = True) -> OpenProfile:
        """Apre il database, portandolo alla versione più recente se è più vecchio.

        `check_integrity=False` salta i controlli di integrità completi (leggono tutto il
        file): solo per una seconda connessione allo stesso profilo già aperto e verificato,
        come quella del thread di sincronizzazione. L'apertura normale li fa sempre; un
        database da migrare si controlla comunque.

        Prima di migrare mette una copia coerente del database in `backup/`, con le versioni di
        partenza e di arrivo nel nome.

        Raises:
            ProfileNotFoundError: manca il database.
            ProfileFormatTooNewError: versione più nuova di quella nota; non si tocca.
            ProfileCorruptError: database illeggibile, non integro o non di un profilo.
            ProfileStorageError: altro guasto; se la migrazione fallisce il database resta
                com'era e la copia di sicurezza è al suo posto.
        """
        ...
