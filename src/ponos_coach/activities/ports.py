# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Porte della funzionalità attività: archivio degli originali, indice, giornale, Garmin.

Le implementazioni reali stanno negli adattatori (`storage`); i fake in memoria e i test di
contratto in `tests/`. Nessuna porta registra dati di posizione o di salute.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Protocol

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityPage,
    ActivityRecord,
    ArchivedFile,
    FileFormat,
    GarminActivityId,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RemoteActivity,
    SessionTokens,
    SyncStatus,
)

# --- Archivio dei file originali ---------------------------------------------------------


class ArchiveError(Exception):
    """Radice degli errori dell'archivio dei file originali."""


class ArchiveWriteError(ArchiveError):
    """Scrittura non riuscita (disco pieno, permessi, I/O); nulla resta nell'archivio."""


class ArchiveFileMissingError(ArchiveError):
    """Il file indicato non c'è nell'archivio."""


class ArchiveTooLargeError(ArchiveError):
    """Il contenuto supera il massimo di byte ammesso; non è stato letto né scritto."""


class ArchiveIntegrityError(ArchiveError):
    """Il file c'è ma il suo SHA-256 o la sua dimensione non sono quelli registrati."""


class ActivityArchive(Protocol):
    """Archivio dei file originali (FIT, GPX), fonte di verità delle attività.

    Il nome del file lo genera il programma, mai un dato esterno. Una scrittura è durevole
    (file temporaneo, flush, rinomina) e idempotente: gli stessi byte, formato e anno danno
    lo stesso `ArchivedFile` senza duplicare nulla.
    """

    def store(self, content: bytes, file_format: FileFormat, start: datetime) -> ArchivedFile:
        """Salva i byte originali e ne restituisce percorso relativo, SHA-256 e dimensione.

        `start` (UTC) sceglie la sottocartella dell'anno. Alla fine il file è durevole e il
        chiamante può scrivere il record in indice.

        Raises:
            ArchiveTooLargeError: più di `MAX_ACTIVITY_FILE_BYTES` byte.
            ArchiveWriteError: scrittura non riuscita; l'archivio non cambia.
        """
        ...

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        """Contenuto del file, dopo aver verificato dimensione e SHA-256.

        Raises:
            ArchiveFileMissingError: il file non c'è.
            ArchiveTooLargeError: il file supera `max_bytes` (non viene letto oltre).
            ArchiveIntegrityError: dimensione o SHA-256 diversi da quelli registrati.
        """
        ...

    def verify(self, file: ArchivedFile) -> None:
        """Controlla dimensione e SHA-256 senza tenere il file in memoria.

        Raises:
            ArchiveFileMissingError: il file non c'è.
            ArchiveIntegrityError: dimensione o SHA-256 diversi da quelli registrati.
        """
        ...

    def list_orphan_temporaries(self) -> Sequence[PurePosixPath]:
        """Temporanei rimasti da una scrittura interrotta, relativi all'archivio, ordinati."""
        ...

    def remove_orphan_temporaries(self) -> int:
        """Cancella i temporanei orfani; restituisce quanti ne ha tolti. I file archiviati
        non si toccano mai."""
        ...


# --- Indice delle attività -----------------------------------------------------------------


class ActivityIndexError(Exception):
    """Radice degli errori dell'indice delle attività."""


class DuplicateActivityError(ActivityIndexError):
    """Esiste già un record con la stessa identità o con lo stesso id Garmin."""


class ActivityNotFoundError(ActivityIndexError):
    """L'attività indicata non è nell'indice."""


class TransactionStateError(ActivityIndexError):
    """Transazione annidata: una transazione per volta, per connessione."""


class ActivityIndex(Protocol):
    """Indice delle attività di un profilo (derivato dai file, rigenerabile).

    Ogni operazione fuori da una transazione è atomica da sola. Dentro `transaction()` le
    modifiche (anche degli altri store dello stesso profilo) valgono tutte o nessuna.
    """

    def transaction(self) -> AbstractContextManager[None]:
        """Transazione: `with index.transaction(): ...`.

        All'uscita normale si conferma, con un'eccezione si annulla tutto e l'eccezione
        riparte. Non si annida (`TransactionStateError`) e non resta aperta durante rete o
        attese dell'utente.
        """
        ...

    def exists(self, identity: ActivityIdentity) -> bool:
        """Vero se c'è un record con questa identità."""
        ...

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        """Il record con questa identità, o None."""
        ...

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        """Record per cui `is_possible_duplicate(start, elapsed_s, ...)` è vero, per inizio.

        Serve a riconoscere la stessa attività arrivata in un altro formato. Non esclude
        l'identità uguale: chi chiama controlla prima `exists`.
        """
        ...

    def add(self, record: ActivityRecord) -> None:
        """Aggiunge un record. Il file in archivio deve già essere durevole.

        Raises:
            DuplicateActivityError: identità o id Garmin già presenti; nulla cambia.
        """
        ...

    def list_by_start(self) -> Sequence[ActivityRecord]:
        """Tutti i record per inizio crescente (a parità, per chiave di identità)."""
        ...

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> ActivityRecord | None:
        """Il record con questo id Garmin, o None."""
        ...

    def link_garmin_id(self, identity: ActivityIdentity, garmin_id: GarminActivityId) -> None:
        """Collega un id Garmin a un'attività già presente (abbinata a una importata da file).

        Idempotente: ripetere lo stesso collegamento non cambia nulla. L'origine del record
        non cambia (resta quella con cui è entrato).

        Raises:
            ActivityNotFoundError: l'identità non è nell'indice.
            DuplicateActivityError: l'id è già di un altro record, oppure questo record ha già
                un id Garmin diverso; nulla cambia.
        """
        ...


# --- Import di file, cartelle e archivi ----------------------------------------------------


class Clock(Protocol):
    """Orologio iniettato: l'istante corrente, in UTC, per `imported_at`."""

    def now(self) -> datetime:
        """Istante corrente con fuso UTC."""
        ...


class ImportIssue(Enum):
    """Motivo per cui un file non diventa un'attività. Il valore è stabile.

    I primi otto coincidono (per valore) con `ActivityFileReason`; gli altri nascono prima della
    decodifica (la sorgente) o dopo (l'archivio del profilo).
    """

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    EXTENSION_MISMATCH = "extension_mismatch"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"
    UNREADABLE = "unreadable"  # sparito, permessi, errore di I/O
    NOT_REGULAR_FILE = "not_regular_file"  # collegamento, giunzione, reparse point, FIFO...
    RESERVED_NAME = "reserved_name"  # nome riservato o non valido su Windows
    PATH_TOO_LONG = "path_too_long"
    UNSAFE_NAME = "unsafe_name"  # voce di uno ZIP con `..`, percorso assoluto, unità, controlli
    ARCHIVE_INVALID = "archive_invalid"  # ZIP corrotto, incoerente, con dimensione o CRC falsi
    ARCHIVE_LIMIT = "archive_limit"  # troppe voci, troppi byte, rapporto di compressione
    ENCRYPTED = "encrypted"  # voce cifrata
    NESTED_ARCHIVE = "nested_archive"  # ZIP dentro uno ZIP
    STORAGE_FAILED = "storage_failed"  # l'archivio del profilo non ha potuto scrivere


@dataclass(frozen=True, slots=True)
class ImportEntry:
    """Un file di attività candidato all'import, come lo elenca una sorgente.

    `entry_id` è opaco (lo assegna la sorgente), `name` è un nome mostrabile (percorso
    relativo; per la voce di uno ZIP `<zip>!<voce>`) senza caratteri di controllo, `size_bytes`
    la dimensione dichiarata. Nessun dato esterno diventa mai un percorso su disco.
    """

    entry_id: int
    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SkippedEntry:
    """Un file o una voce che la sorgente ha saltato prima di leggerli, con il motivo."""

    name: str
    issue: ImportIssue


@dataclass(frozen=True, slots=True)
class ImportScan:
    """Esito dell'esame di una sorgente: cosa importare, cosa saltato, quante voci esaminate.

    Le due sequenze sono ordinate per nome (ordine stabile).
    """

    entries: tuple[ImportEntry, ...]
    skipped: tuple[SkippedEntry, ...]
    examined: int


class ImportLimit(Enum):
    """Tetto che ferma l'esame di una sorgente (nessun import parziale)."""

    ACTIVITY_FILES = "activity_files"
    EXAMINED_ENTRIES = "examined_entries"
    DEPTH = "depth"


class ImportSourceError(Exception):
    """Radice degli errori di una sorgente di import."""


class ImportLimitExceededError(ImportSourceError):
    """L'esame ha superato un tetto: non si importa nulla, serve una sorgente più piccola."""

    def __init__(self, limit: ImportLimit, maximum: int) -> None:
        super().__init__(f"{limit.value}>{maximum}")
        self.limit = limit
        self.maximum = maximum


class ImportScanCancelledError(ImportSourceError):
    """L'esame è stato annullato da chi lo ha richiesto: nulla è stato importato."""


class ImportSourceUnreadableError(ImportSourceError):
    """La cartella o l'elenco di file della sorgente non si può leggere."""


class ImportEntryError(ImportSourceError):
    """Un file non si è potuto leggere (sparito, troppo grande, ZIP incoerente...)."""

    def __init__(self, issue: ImportIssue) -> None:
        super().__init__(issue.value)
        self.issue = issue


class ImportSource(Protocol):
    """Sorgente dell'import: un elenco di file scelti o una cartella, ZIP compresi.

    `scan()` esamina senza importare (solo nomi e dimensioni, anche le directory degli ZIP) e
    si chiama una volta; `read()` restituisce i byte di un file già elencato, un ZIP già
    estratto in memoria e verificato. La sorgente possiede le sue risorse: chi la crea la
    chiude con `close()`. Non modifica mai i file d'origine.
    """

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:
        """Esamina la sorgente.

        `is_cancelled`, se dato, si interroga di continuo durante l'esame (anche dentro una
        cartella enorme o uno ZIP con molte voci); appena è vero l'esame si ferma.

        Raises:
            ImportScanCancelledError: `is_cancelled` è diventato vero.
            ImportLimitExceededError: oltre 50.000 file di attività, 500.000 voci esaminate o
                32 livelli di sottocartelle.
            ImportSourceUnreadableError: la cartella non si legge.
        """
        ...

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        """Byte del file, non oltre `max_bytes`.

        Raises:
            ImportEntryError: il file non si legge o è troppo grande (`issue` dice perché).
        """
        ...

    def close(self) -> None:
        """Rilascia le risorse; si può chiamare più volte."""
        ...


# --- Giornale della sincronizzazione -------------------------------------------------------


class SyncJournalError(Exception):
    """Radice degli errori del giornale della sincronizzazione."""


class UnknownJournalEntryError(SyncJournalError):
    """La voce non è nel giornale."""


class SyncJournal(Protocol):
    """Giornale della sincronizzazione di un profilo: cursori e stato di ogni attività remota.

    Sta sulla stessa connessione e nella stessa transazione dell'indice
    (`ActivityIndex.transaction()`): lo stato di una voce e l'attività che ne risulta si
    confermano insieme. Fuori da una transazione ogni operazione è atomica da sola.
    """

    def load_cursor(self, kind: ListingKind) -> ListingCursor | None:
        """Il cursore salvato per quell'elenco, o None se non è mai partito."""
        ...

    def save_cursor(self, kind: ListingKind, cursor: ListingCursor) -> None:
        """Salva (sostituisce) il cursore dell'elenco."""
        ...

    def known_ids(self, ids: Collection[GarminActivityId]) -> frozenset[GarminActivityId]:
        """Tra gli `ids` dati, quelli che hanno una voce nel giornale, in qualunque stato."""
        ...

    def add_pending(self, items: Sequence[RemoteActivity], now: datetime) -> int:
        """Aggiunge una voce `PENDING` per ogni attività che il giornale non conosce ancora.

        Idempotente per id Garmin: le voci esistenti non cambiano (né stato né riepilogo).
        Restituisce quante voci nuove ha aggiunto. `now` è UTC al millisecondo.
        """
        ...

    def next_to_process(self, limit: int) -> Sequence[JournalEntry]:
        """Al più `limit` voci da lavorare (`PENDING` e `RETRYABLE`).

        Ordine stabile: inizio dell'attività dal più recente, a parità id Garmin decrescente.

        Raises:
            ValueError: `limit` minore di 1.
        """
        ...

    def get(self, garmin_id: GarminActivityId) -> JournalEntry | None:
        """La voce con questo id, o None."""
        ...

    def update(self, entry: JournalEntry) -> None:
        """Porta la voce con lo stesso id al nuovo stato (stato, tentativi, causa, istante).

        Il riepilogo remoto della voce non cambia.

        Raises:
            UnknownJournalEntryError: l'id non è nel giornale.
            InvalidTransitionError: lo stato attuale non ammette il nuovo (`can_transition`);
                nulla cambia.
        """
        ...

    def counts(self) -> Mapping[SyncStatus, int]:
        """Quante voci ci sono per stato; ogni stato è presente, anche con 0."""
        ...

    def last_completed_at(self) -> datetime | None:
        """Quando è finita l'ultima sincronizzazione (UTC), o None se non ne è finita nessuna."""
        ...

    def record_completed(self, when: datetime) -> None:
        """Segna la fine di una sincronizzazione (UTC, al millisecondo)."""
        ...


# --- Garmin: elenco e originali, account ---------------------------------------------------


class ActivitySourceError(Exception):
    """Radice degli errori che arrivano da Garmin (elenco, download, login).

    Le sottoclassi sono distinte solo dove cambia la reazione di chi chiama. Nessun messaggio
    contiene token, password, risposte di Garmin o dati di salute.
    """


class AuthenticationRequiredError(ActivitySourceError):
    """I token mancano, sono scaduti o revocati: serve un nuovo login."""


class RateLimitedError(ActivitySourceError):
    """Garmin limita la frequenza (HTTP 429): rallentare, poi ritentare con il backoff.

    `retry_after_s` è l'attesa suggerita da Garmin (`Retry-After`), se la dice.
    """

    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__("frequenza limitata")
        self.retry_after_s = retry_after_s


class TransientSourceError(ActivitySourceError):
    """Errore che può passare: rete, timeout, 5xx, risposta troncata. Si ritenta col backoff."""


class ServiceUnavailableError(TransientSourceError):
    """Garmin non risponde o non è raggiungibile (anche al login)."""


class DefinitiveSourceError(ActivitySourceError):
    """Ritentare darebbe lo stesso esito: originale assente (404, 410), rifiutato, risposta
    non interpretabile."""


class DownloadTooLargeError(DefinitiveSourceError):
    """Il download supera il massimo di byte: è stato interrotto, nulla è stato tenuto."""


class ActivitySource(Protocol):
    """Una sessione autenticata verso Garmin che elenca e scarica le attività di un utente.

    Ogni chiamata ha un suo timeout (stabilito dall'adattatore): nessuna attesa senza limite. Le
    pause e i ritentativi non sono qui, sono del chiamante (`SyncPolicy`). Gli input esterni
    (JSON, byte) sono già trasformati in tipi di dominio o in errori.
    """

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        """Una pagina dell'elenco, dalle attività più recenti.

        `limit` è fra 1 e `MAX_PAGE_SIZE`. Le voci malformate si scartano e si contano in
        `raw_count`; `declared_total` è il totale se Garmin lo dice.

        Raises:
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            DefinitiveSourceError: vedi le classi.
        """
        ...

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        """I byte dell'originale così come li dà Garmin: uno ZIP con un FIT, un GPX o altro.

        Non interpreta il contenuto (lo fa l'import). Si ferma a `max_bytes` senza tenere il
        resto in memoria.

        Raises:
            DownloadTooLargeError: più di `max_bytes` byte.
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            DefinitiveSourceError: vedi le classi.
        """
        ...


class GarminAccountError(ActivitySourceError):
    """Radice degli errori del login."""


class InvalidCredentialsError(GarminAccountError):
    """Email o password rifiutate da Garmin."""


class MfaCancelledError(GarminAccountError):
    """L'utente ha annullato la richiesta del codice MFA: nulla è stato salvato."""


@dataclass(frozen=True, slots=True)
class GarminSession:
    """Una sessione autenticata e i token con cui riprenderla.

    `tokens` sono quelli da salvare nel portachiavi (possono essere stati rinnovati rispetto a
    quelli con cui si è ripresa la sessione).
    """

    source: ActivitySource
    tokens: SessionTokens


class GarminAccount(Protocol):
    """Login a Garmin e ripresa di una sessione da token salvati.

    La password va solo a questa porta e non si salva né si registra. Ogni chiamata ha un
    timeout.
    """

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """Accede con email e password; se serve l'MFA chiama `prompt_mfa_code`.

        `prompt_mfa_code` restituisce il codice digitato, o None se l'utente annulla. Va
        chiamata al più poche volte (limite dell'adattatore).

        Raises:
            InvalidCredentialsError: credenziali rifiutate (anche codice MFA sbagliato).
            MfaCancelledError: `prompt_mfa_code` ha restituito None.
            RateLimitedError: Garmin limita la frequenza.
            ServiceUnavailableError: Garmin non risponde.
        """
        ...

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """Riprende la sessione dai token salvati, senza password.

        Raises:
            AuthenticationRequiredError: token scaduti, revocati o non riconosciuti.
            RateLimitedError, ServiceUnavailableError: come per `login`.
        """
        ...
