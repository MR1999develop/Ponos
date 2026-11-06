# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin finto: elenco e originali (`ActivitySource`) e account con MFA (`GarminAccount`).

Nessuna rete e nessun dato reale: le attività sono generate (una corsa al giorno, con FIT o GPX
sintetici dello stesso inizio e della stessa durata dell'elenco). I guasti si iniettano per
numero di chiamata o per attività; la risposta lenta fa avanzare un orologio finto, senza
`sleep`.
"""

from __future__ import annotations

import io
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import (
    MAX_PAGE_SIZE,
    ActivityPage,
    GarminActivityId,
    RemoteActivity,
    SessionTokens,
)
from ponos_coach.activities.ports import (
    ActivitySource,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    GarminSession,
    InvalidCredentialsError,
    MfaCancelledError,
    RateLimitedError,
    ServiceUnavailableError,
)
from tests.fixtures.generators.import_files import RUN_SECONDS, fit_run, gpx_run, run_start
from tests.fixtures.generators.zip_writer import build_zip, garmin_download

FIRST_ACTIVITY_ID = 1_000_000
_TCX = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
    b"</TrainingCenterDatabase>"
)
PLACEHOLDER_TOKEN = "TOKEN-SEGNAPOSTO-0123456789abcdef"  # noqa: S105 - segnaposto
TEST_EMAIL = "atleta@example.invalid"
TEST_PASSWORD = "password-di-prova"  # noqa: S105 - segnaposto
TEST_MFA_CODE = "123456"


class OriginalKind(Enum):
    """Che cosa c'è nello ZIP scaricato per un'attività."""

    FIT = "fit"  # come dal sito Garmin: `<id>_ACTIVITY.fit`
    GPX = "gpx"  # attività caricata come GPX
    TCX = "tcx"  # formato non supportato
    CORRUPT_FIT = "corrupt_fit"  # ZIP valido, `.fit` con byte a caso
    TRUNCATED_ZIP = "truncated_zip"  # ZIP troncato: non si apre
    NOT_A_ZIP = "not_a_zip"  # byte che non sono uno ZIP
    EMPTY_ZIP = "empty_zip"  # ZIP senza voci


def _empty_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    return buffer.getvalue()


def original_zip(number: int, kind: OriginalKind = OriginalKind.FIT) -> bytes:
    """Lo ZIP dell'originale della corsa `number` (la stessa di `make_remote_history`)."""
    activity_id = FIRST_ACTIVITY_ID + number
    name = f"{activity_id}_ACTIVITY"
    builders: dict[OriginalKind, Callable[[], bytes]] = {
        OriginalKind.FIT: lambda: garmin_download(fit_run(number), activity_id),
        OriginalKind.GPX: lambda: build_zip([(f"{name}.gpx", gpx_run(number))]),
        OriginalKind.TCX: lambda: build_zip([(f"{name}.tcx", _TCX)]),
        OriginalKind.CORRUPT_FIT: lambda: build_zip([(f"{name}.fit", bytes(range(256)) * 4)]),
        OriginalKind.TRUNCATED_ZIP: lambda: garmin_download(fit_run(number), activity_id)[:-40],
        OriginalKind.NOT_A_ZIP: lambda: b"questo non e uno zip" * 10,
        OriginalKind.EMPTY_ZIP: _empty_zip,
    }
    return builders[kind]()


def make_remote_history(
    count: int, *, sport_key: str = "running", has_heart_rate: bool | None = True
) -> list[RemoteActivity]:
    """`count` corse, una al giorno, la numero `n` con id `FIRST_ACTIVITY_ID + n` e inizio
    `run_start(n)`: più `n` è grande, più è recente. Ordine crescente di `n`."""
    return [
        RemoteActivity(
            GarminActivityId(FIRST_ACTIVITY_ID + number),
            run_start(number),
            float(RUN_SECONDS),
            sport_key,
            has_heart_rate,
        )
        for number in range(count)
    ]


def _number_of(activity: RemoteActivity) -> int:
    return activity.garmin_id.value - FIRST_ACTIVITY_ID


class Call(Enum):
    """Le due chiamate di `ActivitySource`, per iniettare guasti."""

    LIST_PAGE = "list_page"
    DOWNLOAD = "download"


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """Una chiamata ricevuta: quale, e la posizione (elenco) o l'id (download)."""

    call: Call
    offset: int | None = None
    limit: int | None = None
    activity_id: GarminActivityId | None = None


class FakeActivitySource:
    """`ActivitySource` su un elenco in memoria, newest first, con guasti iniettabili.

    - `fail_call(Call, n, error)`: la n-esima chiamata (da 1) di quel tipo solleva `error`.
    - `fail_download_of(id, error, times=None)`: i download di quell'attività falliscono
      `times` volte (sempre se None).
    - `set_original(id, bytes)` / `set_kind(id, OriginalKind)`: cambia ciò che si scarica.
    - `malformed(ids)`: quelle voci compaiono nell'elenco come scartate (contano in `raw_count`).
    - `latency_s` e `on_latency`: ogni chiamata invoca `on_latency(latency_s)` (per far avanzare
      un orologio finto) prima di rispondere.
    - `calls`, `call_count(Call)`, `downloads` registrano ciò che è successo.
    """

    def __init__(
        self,
        activities: Sequence[RemoteActivity],
        *,
        declares_total: bool = True,
        latency_s: float = 0.0,
        on_latency: Callable[[float], None] | None = None,
    ) -> None:
        self._activities: dict[GarminActivityId, RemoteActivity] = {
            a.garmin_id: a for a in activities
        }
        self._originals: dict[GarminActivityId, bytes] = {}
        self._kinds: dict[GarminActivityId, OriginalKind] = {}
        self._malformed: frozenset[GarminActivityId] = frozenset()
        self._failures: dict[tuple[Call, int], Exception] = {}
        self._download_failures: dict[GarminActivityId, tuple[Exception, int | None]] = {}
        self._counts: Counter[Call] = Counter()
        self.declares_total = declares_total
        self.latency_s = latency_s
        self.on_latency = on_latency
        self.calls: list[RecordedCall] = []
        self.downloads: list[GarminActivityId] = []

    # --- Regia del test ------------------------------------------------------------------

    def add_activity(self, activity: RemoteActivity) -> None:
        """Un'attività nuova compare nell'elenco (per esempio caricata in ritardo)."""
        self._activities[activity.garmin_id] = activity

    def remove_activity(self, garmin_id: GarminActivityId) -> None:
        """L'attività sparisce dall'elenco e dai download."""
        self._activities.pop(garmin_id, None)

    def set_original(self, garmin_id: GarminActivityId, content: bytes) -> None:
        """Sostituisce i byte scaricati per l'attività."""
        self._originals[garmin_id] = content

    def set_kind(self, garmin_id: GarminActivityId, kind: OriginalKind) -> None:
        """Lo ZIP dell'attività contiene questo tipo di originale."""
        self._kinds[garmin_id] = kind

    def malformed(self, ids: Sequence[GarminActivityId]) -> None:
        """Queste voci arrivano nell'elenco ma l'adattatore le scarta."""
        self._malformed = frozenset(ids)

    def fail_call(self, call: Call, number: int, error: Exception) -> None:
        """La `number`-esima chiamata (da 1) di quel tipo solleva `error`, una volta."""
        self._failures[(call, number)] = error

    def fail_download_of(
        self, garmin_id: GarminActivityId, error: Exception, *, times: int | None = None
    ) -> None:
        """I download di quell'attività falliscono `times` volte, o sempre se `times` è None."""
        self._download_failures[garmin_id] = (error, times)

    def call_count(self, call: Call) -> int:
        """Quante chiamate di quel tipo sono arrivate."""
        return self._counts[call]

    @property
    def activities(self) -> Mapping[GarminActivityId, RemoteActivity]:
        """Le attività oggi presenti sul finto Garmin."""
        return dict(self._activities)

    # --- Porta -----------------------------------------------------------------------------

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        """Vedi `ActivitySource.list_page`."""
        if offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("posizione o limite non validi")
        self._enter(Call.LIST_PAGE, RecordedCall(Call.LIST_PAGE, offset=offset, limit=limit))
        ordered = sorted(self._activities.values(), key=lambda a: (a.start, a.garmin_id.value))
        ordered.reverse()
        window = ordered[offset : offset + limit]
        items = tuple(a for a in window if a.garmin_id not in self._malformed)
        total = len(ordered) if self.declares_total else None
        return ActivityPage(offset, items, len(window), total)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        """Vedi `ActivitySource.download_original`."""
        self._enter(Call.DOWNLOAD, RecordedCall(Call.DOWNLOAD, activity_id=activity_id))
        self.downloads.append(activity_id)
        self._raise_download_failure(activity_id)
        activity = self._activities.get(activity_id)
        if activity is None:
            raise DefinitiveSourceError("originale assente")
        content = self._originals.get(activity_id)
        if content is None:
            content = original_zip(
                _number_of(activity), self._kinds.get(activity_id, OriginalKind.FIT)
            )
        if len(content) > max_bytes:
            raise DownloadTooLargeError("download troppo grande")
        return content

    # --- Interni -----------------------------------------------------------------------------

    def _enter(self, call: Call, recorded: RecordedCall) -> None:
        self._counts[call] += 1
        self.calls.append(recorded)
        if self.latency_s and self.on_latency is not None:
            self.on_latency(self.latency_s)
        error = self._failures.pop((call, self._counts[call]), None)
        if error is not None:
            raise error

    def _raise_download_failure(self, activity_id: GarminActivityId) -> None:
        entry = self._download_failures.get(activity_id)
        if entry is None:
            return
        error, times = entry
        if times is not None:
            if times <= 1:
                del self._download_failures[activity_id]
            else:
                self._download_failures[activity_id] = (error, times - 1)
        raise error


class FakeGarminAccount:
    """`GarminAccount` finto con MFA opzionale e token segnaposto riconoscibili.

    Il login riesce con `TEST_EMAIL` e `TEST_PASSWORD` (e `TEST_MFA_CODE` se `mfa_required`) e
    dà una sessione sulla `ActivitySource` data. I token emessi sono validi finché non si chiama
    `revoke_tokens()`. `login_calls`, `resume_calls` e `mfa_prompts` contano le chiamate; la
    password non si conserva da nessuna parte.
    """

    def __init__(
        self,
        source: ActivitySource,
        *,
        mfa_required: bool = True,
        token_value: str = PLACEHOLDER_TOKEN,
    ) -> None:
        self._source = source
        self._mfa_required = mfa_required
        self._token_value = token_value
        self._valid: set[str] = set()
        self._login_error: Exception | None = None
        self._resume_error: Exception | None = None
        self.login_calls = 0
        self.resume_calls = 0
        self.mfa_prompts = 0

    def fail_login_with(self, error: Exception | None) -> None:
        """Il prossimo login (e i successivi, finché non si passa None) solleva `error`."""
        self._login_error = error

    def fail_resume_with(self, error: Exception | None) -> None:
        """Come `fail_login_with`, per `resume`."""
        self._resume_error = error

    def revoke_tokens(self) -> None:
        """Tutti i token emessi smettono di valere: `resume` dà `AuthenticationRequiredError`."""
        self._valid.clear()

    def rate_limit(self, retry_after_s: float | None = None) -> None:
        """Il login e la ripresa danno 429 finché non si chiama `fail_login_with(None)`."""
        self._login_error = RateLimitedError(retry_after_s)
        self._resume_error = RateLimitedError(retry_after_s)

    def go_offline(self) -> None:
        """Il login e la ripresa danno `ServiceUnavailableError`."""
        self._login_error = ServiceUnavailableError("non disponibile")
        self._resume_error = ServiceUnavailableError("non disponibile")

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """Vedi `GarminAccount.login`."""
        self.login_calls += 1
        if self._login_error is not None:
            raise self._login_error
        if email != TEST_EMAIL or password != TEST_PASSWORD:
            raise InvalidCredentialsError("credenziali rifiutate")
        if self._mfa_required:
            self.mfa_prompts += 1
            code = prompt_mfa_code()
            if code is None:
                raise MfaCancelledError("MFA annullato")
            if code != TEST_MFA_CODE:
                raise InvalidCredentialsError("codice rifiutato")
        self._valid.add(self._token_value)
        return GarminSession(self._source, SessionTokens(self._token_value))

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """Vedi `GarminAccount.resume`."""
        self.resume_calls += 1
        if self._resume_error is not None:
            raise self._resume_error
        if tokens.reveal() not in self._valid:
            raise AuthenticationRequiredError("token non validi")
        return GarminSession(self._source, tokens)
