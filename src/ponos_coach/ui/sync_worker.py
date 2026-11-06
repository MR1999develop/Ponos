# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Il lavoro dell'account Garmin, senza Qt: controllo dei token, collegamento, scollegamento e
sincronizzazione. Gira in un thread proprio, avviato da `SyncController`.

La sincronizzazione apre una sua connessione al database del profilo (`ProfileStorage.open`
sulla stessa cartella) e la chiude alla fine: non tocca mai quella del thread dei dati. La
password e il codice MFA passano solo a `GarminAccount`; qui non si conservano né si registrano
(nei log solo il tipo degli errori).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from ponos_coach.activities.domain import DEFAULT_POLICY, RandomSource, SessionTokens, SyncPolicy
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivitySourceError,
    AuthenticationRequiredError,
    GarminAccount,
    GarminSession,
    InvalidCredentialsError,
    MfaCancelledError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.activities.sync import (
    ActivityDecoder,
    OriginalExtractor,
    SyncClock,
    SyncMode,
    SyncPhase,
    SyncPorts,
    SyncProgress,
    synchronize_activities,
)
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import (
    ProfileStorage,
    ProfileStorageError,
    TokenStore,
    TokenStoreCorruptError,
    TokenStoreUnavailableError,
)
from ponos_coach.ui.diagnostics import exception_chain
from ponos_coach.ui.sync_model import (
    AccountStatus,
    LoginEnd,
    LoginFailure,
    LoginResult,
    SyncFinished,
    SyncNotStarted,
    SyncProblem,
    SyncResult,
)

_LOG = logging.getLogger(__name__)

MFA_WAIT_S: Final = 300.0  # quanto si aspetta il codice che l'utente sta ricevendo
_PROGRESS_INTERVAL_S: Final = 0.05  # al massimo 20 aggiornamenti al secondo


class SystemNotifier(Protocol):
    """Notifica di sistema: non blocca mai e non solleva mai (se fallisce non succede nulla)."""

    def notify(self, title: str, message: str) -> None:
        """Mostra la notifica, se il sistema la supporta."""
        ...


@dataclass(frozen=True, slots=True)
class SyncServices:
    """Tutto ciò che la sincronizzazione riceve dal Composition Root.

    `tokens` è `None` se il portachiavi del sistema non si può usare: nessun account si può
    collegare, l'import da file resta disponibile.
    """

    account: GarminAccount
    tokens: TokenStore | None
    extract_original: OriginalExtractor
    decode: ActivityDecoder
    clock: SyncClock
    make_rng: Callable[[], RandomSource]
    notifier: SystemNotifier
    policy: SyncPolicy = DEFAULT_POLICY
    mfa_wait_s: float = MFA_WAIT_S


@dataclass(frozen=True, slots=True)
class Credentials:
    """Email e password digitate; la password non compare mai in `repr`."""

    email: str
    password: str = field(repr=False)


class MfaMailbox:
    """Dove il thread del login aspetta il codice che la UI raccoglie.

    `answer(None)` (Annulla) e `cancel()` sbloccano l'attesa con "annullato"; l'attesa ha un
    tetto (`wait_s`) oltre il quale vale come annullata.
    """

    def __init__(self, wait_s: float) -> None:
        self._wait_s = wait_s
        self._answers: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def answer(self, code: str | None) -> None:
        """Consegna il codice (o `None` se l'utente annulla). Una sola risposta per richiesta."""
        with contextlib.suppress(queue.Full):  # già risposto o annullato: la seconda non conta
            self._answers.put_nowait(code)

    def cancel(self) -> None:
        """Sblocca l'attesa come se l'utente avesse annullato."""
        self.answer(None)

    def wait(self) -> str | None:
        """Il codice, o `None` se annullato o scaduto il tempo."""
        try:
            return self._answers.get(timeout=self._wait_s)
        except queue.Empty:
            return None


class SyncWorker:
    """Le quattro operazioni dell'account; ognuna restituisce un valore, non solleva."""

    def __init__(
        self,
        services: SyncServices,
        storage: ProfileStorage,
        archive_for: Callable[[Path], ActivityArchive],
    ) -> None:
        self._services = services
        self._storage = storage
        self._archive_for = archive_for

    # --- Token ---------------------------------------------------------------------------

    def check(self, profile_id: ProfileId) -> AccountStatus:
        """Che cosa dicono i token salvati del profilo."""
        store = self._services.tokens
        if store is None:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        try:
            tokens = store.load(profile_id)
        except TokenStoreCorruptError:
            return AccountStatus.NEEDS_RECONNECT
        except TokenStoreUnavailableError:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        except Exception as error:  # confine del thread: un guasto imprevisto non lo abbatte
            _log_unexpected("token check", error)
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        return AccountStatus.NOT_LINKED if tokens is None else AccountStatus.LINKED

    def log_out(self, profile_id: ProfileId) -> bool:
        """Cancella i token del profilo. Falso se il portachiavi non l'ha fatto."""
        store = self._services.tokens
        if store is None:
            return False
        try:
            store.delete(profile_id)
        except TokenStoreUnavailableError:
            return False
        except Exception as error:
            _log_unexpected("token delete", error)
            return False
        return True

    # --- Collegamento ----------------------------------------------------------------------

    def log_in(
        self,
        profile_id: ProfileId,
        credentials: Credentials,
        ask_mfa_code: Callable[[], str | None],
        cancelled: threading.Event,
    ) -> LoginResult:
        """Accede a Garmin e salva i token del profilo. Se `cancelled` scatta durante l'accesso
        la sessione si butta e non si salva nulla."""
        store = self._services.tokens
        if store is None:
            return LoginFailure.KEYCHAIN_UNAVAILABLE
        try:
            session = self._services.account.login(
                credentials.email, credentials.password, ask_mfa_code
            )
        except Exception as error:  # confine del thread; i fallimenti attesi sono esiti
            return _login_failure_for(error)
        if cancelled.is_set():
            return LoginEnd.CANCELLED
        try:
            store.save(profile_id, session.tokens)
        except TokenStoreUnavailableError:
            return LoginFailure.KEYCHAIN_UNAVAILABLE
        except Exception as error:
            _log_unexpected("token save", error)
            return LoginFailure.OTHER
        return LoginEnd.CONNECTED

    # --- Sincronizzazione ------------------------------------------------------------------

    def synchronize(
        self,
        profile_id: ProfileId,
        folder: Path,
        mode: SyncMode,
        *,
        automatic: bool,
        cancelled: threading.Event,
        report: Callable[[SyncProgress], None],
    ) -> SyncResult:
        """Un giro di sincronizzazione del profilo, con la connessione e l'archivio suoi."""
        try:
            session = self._resume(profile_id)
            return self._run(session, folder, mode, automatic, cancelled, report)
        except _SkippedError as skipped:
            return SyncNotStarted(skipped.problem, mode, automatic)
        except Exception as error:  # confine del thread: l'errore va alla UI come "imprevisto"
            _log_unexpected("synchronisation", error)
            return SyncNotStarted(SyncProblem.UNEXPECTED, mode, automatic)

    def _resume(self, profile_id: ProfileId) -> GarminSession:
        store = self._services.tokens
        if store is None:
            raise _SkippedError(SyncProblem.KEYCHAIN_UNAVAILABLE)
        try:
            tokens = store.load(profile_id)
        except TokenStoreCorruptError as error:
            raise _SkippedError(SyncProblem.AUTH_REQUIRED) from error
        except TokenStoreUnavailableError as error:
            raise _SkippedError(SyncProblem.KEYCHAIN_UNAVAILABLE) from error
        if tokens is None:
            raise _SkippedError(SyncProblem.NOT_LINKED)
        session = self._resume_session(tokens)
        self._save_renewed(store, profile_id, tokens, session)
        return session

    def _resume_session(self, tokens: SessionTokens) -> GarminSession:
        try:
            return self._services.account.resume(tokens)
        except AuthenticationRequiredError as error:
            raise _SkippedError(SyncProblem.AUTH_REQUIRED) from error
        except RateLimitedError as error:
            raise _SkippedError(SyncProblem.RATE_LIMITED) from error
        except TransientSourceError as error:
            raise _SkippedError(SyncProblem.SERVICE_UNAVAILABLE) from error
        except ActivitySourceError as error:
            _log_unexpected("resume", error)
            raise _SkippedError(SyncProblem.UNEXPECTED) from error

    @staticmethod
    def _save_renewed(
        store: TokenStore, profile_id: ProfileId, old: SessionTokens, session: GarminSession
    ) -> None:
        """Se la libreria ha rinnovato i token, si salvano; un guasto qui non ferma il giro."""
        if session.tokens == old:
            return
        try:
            store.save(profile_id, session.tokens)
        except Exception as error:
            _log_unexpected("renewed token save", error)

    def _run(
        self,
        session: GarminSession,
        folder: Path,
        mode: SyncMode,
        automatic: bool,
        cancelled: threading.Event,
        report: Callable[[SyncProgress], None],
    ) -> SyncResult:
        try:
            opened = self._storage.open(folder)
        except ProfileStorageError as error:
            raise _SkippedError(SyncProblem.PROFILE_UNAVAILABLE) from error
        services = self._services
        try:
            ports = SyncPorts(
                source=session.source,
                index=opened.activities,
                journal=opened.sync_journal,
                archive=self._archive_for(folder),
                extract_original=services.extract_original,
                decode=services.decode,
                clock=services.clock,
                wait=cancelled.wait,
                rng=services.make_rng(),
                policy=services.policy,
            )
            outcome = synchronize_activities(
                ports, mode, report=_throttled(report), is_cancelled=cancelled.is_set
            )
        finally:
            _close_quietly(opened.close)
        return SyncFinished(outcome, mode, automatic)


class _SkippedError(Exception):
    """Il giro non può partire (o proseguire): `problem` dice perché."""

    def __init__(self, problem: SyncProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def _login_failure_for(error: Exception) -> LoginResult:
    """L'esito di un accesso fallito, secondo come reagisce l'utente."""
    if isinstance(error, MfaCancelledError):
        return LoginEnd.CANCELLED
    if isinstance(error, InvalidCredentialsError):
        return LoginFailure.INVALID_CREDENTIALS
    if isinstance(error, RateLimitedError):
        return LoginFailure.RATE_LIMITED
    if isinstance(error, TransientSourceError):
        return LoginFailure.SERVICE_UNAVAILABLE
    _log_unexpected("login", error)
    return LoginFailure.OTHER


def _throttled(report: Callable[[SyncProgress], None]) -> Callable[[SyncProgress], None]:
    """Al massimo 20 aggiornamenti al secondo; il cambio di fase e l'ultimo passo passano sempre."""
    last_at = 0.0
    last_phase: SyncPhase | None = None

    def forward(progress: SyncProgress) -> None:
        nonlocal last_at, last_phase
        now = time.monotonic()
        finished = progress.total is not None and progress.done >= progress.total
        if progress.phase is not last_phase or finished or now - last_at >= _PROGRESS_INTERVAL_S:
            last_at = now
            last_phase = progress.phase
            report(progress)

    return forward


def _close_quietly(close: Callable[[], None]) -> None:
    try:
        close()
    except Exception as error:  # la chiusura non cambia l'esito del giro
        _log_unexpected("closing the profile connection", error)


def _log_unexpected(step: str, error: BaseException) -> None:
    """Un solo record per errore imprevisto, col solo tipo: il testo può contenere dati."""
    _LOG.error("Garmin: unexpected error in %s: %s", step, exception_chain(error))
