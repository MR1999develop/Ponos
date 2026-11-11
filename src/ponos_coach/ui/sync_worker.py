# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Garmin account work, without Qt: token check, connection, disconnection and
synchronisation. Runs on its own thread, started by `SyncController`.

Synchronisation opens its own connection to the profile database (`ProfileStorage.open`
on the same folder) and closes it at the end: it never touches the data thread's one. The
password and the MFA code go only to `GarminAccount`; here they are neither kept nor logged
(in the logs only the type of the errors).
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
from ponos_coach.ui.sync_preferences import MemorySyncPreferences, SyncPreferences

_LOG = logging.getLogger(__name__)

MFA_WAIT_S: Final = 300.0  # how long to wait for the code the user is receiving
_PROGRESS_INTERVAL_S: Final = 0.05  # at most 20 updates per second


class SystemNotifier(Protocol):
    """System notification: never blocks and never raises (if it fails nothing happens)."""

    def notify(self, title: str, message: str) -> None:
        """Show the notification, if the system supports it."""
        ...


@dataclass(frozen=True, slots=True)
class SyncServices:
    """Everything the synchronisation receives from the Composition Root.

    `tokens` is `None` if the system keychain cannot be used: no account can be
    connected, file import stays available.
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
    preferences: SyncPreferences = field(default_factory=MemorySyncPreferences)


@dataclass(frozen=True, slots=True)
class Credentials:
    """Email and password typed; the password never appears in `repr`."""

    email: str
    password: str = field(repr=False)


class MfaMailbox:
    """Where the login thread waits for the code that the UI collects.

    `answer(None)` (Cancel) and `cancel()` unblock the wait with "cancelled"; the wait has a
    cap (`wait_s`) beyond which `wait` returns `None` and `timed_out` becomes true: the timeout is
    told apart from a cancellation.
    """

    def __init__(self, wait_s: float) -> None:
        self._wait_s = wait_s
        self._timed_out = False
        self._answers: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def answer(self, code: str | None) -> None:
        """Deliver the code (or `None` if the user cancels). One answer per request."""
        with contextlib.suppress(
            queue.Full
        ):  # already answered or cancelled: the second does not count
            self._answers.put_nowait(code)

    def cancel(self) -> None:
        """Unblock the wait as if the user had cancelled."""
        self.answer(None)

    @property
    def timed_out(self) -> bool:
        """True if the wait ended because time ran out (nobody answered)."""
        return self._timed_out

    def wait(self) -> str | None:
        """The code, or `None` if cancelled or timed out."""
        try:
            return self._answers.get(timeout=self._wait_s)
        except queue.Empty:
            self._timed_out = True
            return None


class SyncWorker:
    """The account's four operations; each returns a value, none raises."""

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
        """What the profile's saved tokens say."""
        store = self._services.tokens
        if store is None:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        try:
            tokens = store.load(profile_id)
        except TokenStoreCorruptError:
            return AccountStatus.NEEDS_RECONNECT
        except TokenStoreUnavailableError:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        except Exception as error:  # thread boundary: an unexpected failure does not bring it down
            _log_unexpected("token check", error)
            return AccountStatus.KEYCHAIN_UNAVAILABLE
        return AccountStatus.NOT_LINKED if tokens is None else AccountStatus.LINKED

    def log_out(self, profile_id: ProfileId) -> bool:
        """Delete the profile's tokens. False if the keychain did not do it."""
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
        *,
        mfa_timed_out: Callable[[], bool] = lambda: False,
    ) -> LoginResult:
        """Log in to Garmin and save the profile's tokens. If `cancelled` fires during the login
        the session is discarded and nothing is saved. If the MFA code did not arrive in time
        (`mfa_timed_out`) the outcome is `LoginFailure.MFA_TIMED_OUT`, not a cancellation."""
        store = self._services.tokens
        if store is None:
            return LoginFailure.KEYCHAIN_UNAVAILABLE
        try:
            session = self._services.account.login(
                credentials.email, credentials.password, ask_mfa_code
            )
        except Exception as error:  # thread boundary; expected failures are outcomes
            expired = mfa_timed_out() and not cancelled.is_set()
            return _login_failure_for(error, mfa_timed_out=expired)
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
        """One synchronisation round of the profile, with its own connection and archive."""
        try:
            session = self._resume(profile_id)
            return self._run(session, folder, mode, automatic, cancelled, report)
        except SessionUnavailableError as skipped:
            return SyncNotStarted(skipped.problem, mode, automatic)
        except Exception as error:  # thread boundary: the error goes to the UI as "unexpected"
            _log_unexpected("synchronisation", error)
            return SyncNotStarted(SyncProblem.UNEXPECTED, mode, automatic)

    def open_session(self, profile_id: ProfileId) -> GarminSession:
        """Resume the profile's Garmin session from its saved tokens (renewed tokens are saved).

        Raises:
            SessionUnavailableError: no session could be had; `problem` says why.
        """
        return self._resume(profile_id)

    def _resume(self, profile_id: ProfileId) -> GarminSession:
        store = self._services.tokens
        if store is None:
            raise SessionUnavailableError(SyncProblem.KEYCHAIN_UNAVAILABLE)
        try:
            tokens = store.load(profile_id)
        except TokenStoreCorruptError as error:
            raise SessionUnavailableError(SyncProblem.AUTH_REQUIRED) from error
        except TokenStoreUnavailableError as error:
            raise SessionUnavailableError(SyncProblem.KEYCHAIN_UNAVAILABLE) from error
        if tokens is None:
            raise SessionUnavailableError(SyncProblem.NOT_LINKED)
        session = self._resume_session(tokens)
        self._save_renewed(store, profile_id, tokens, session)
        return session

    def _resume_session(self, tokens: SessionTokens) -> GarminSession:
        try:
            return self._services.account.resume(tokens)
        except AuthenticationRequiredError as error:
            raise SessionUnavailableError(SyncProblem.AUTH_REQUIRED) from error
        except RateLimitedError as error:
            raise SessionUnavailableError(SyncProblem.RATE_LIMITED) from error
        except TransientSourceError as error:
            raise SessionUnavailableError(SyncProblem.SERVICE_UNAVAILABLE) from error
        except ActivitySourceError as error:
            _log_unexpected("resume", error)
            raise SessionUnavailableError(SyncProblem.UNEXPECTED) from error

    @staticmethod
    def _save_renewed(
        store: TokenStore, profile_id: ProfileId, old: SessionTokens, session: GarminSession
    ) -> None:
        """If the library renewed the tokens, they are saved; a failure here does not stop the
        round."""
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
            # Second connection to a profile already opened and verified by the data thread.
            opened = self._storage.open(folder, check_integrity=False)
        except ProfileStorageError as error:
            raise SessionUnavailableError(SyncProblem.PROFILE_UNAVAILABLE) from error
        services = self._services
        try:
            ports = SyncPorts(
                source=session.source,
                index=opened.activities,
                deleted=opened.deleted_activities,
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


class SessionUnavailableError(Exception):
    """The round cannot start (or continue): `problem` says why."""

    def __init__(self, problem: SyncProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def _login_failure_for(error: Exception, *, mfa_timed_out: bool) -> LoginResult:
    """The outcome of a failed login, according to how the user reacts. An MFA code that never
    arrived (`mfa_timed_out`) is not a cancellation: the user must know."""
    if isinstance(error, MfaCancelledError):
        return LoginFailure.MFA_TIMED_OUT if mfa_timed_out else LoginEnd.CANCELLED
    if isinstance(error, InvalidCredentialsError):
        return LoginFailure.INVALID_CREDENTIALS
    if isinstance(error, RateLimitedError):
        return LoginFailure.RATE_LIMITED
    if isinstance(error, TransientSourceError):
        return LoginFailure.SERVICE_UNAVAILABLE
    _log_unexpected("login", error)
    return LoginFailure.OTHER


def _throttled(report: Callable[[SyncProgress], None]) -> Callable[[SyncProgress], None]:
    """At most 20 updates per second; the phase change and the last step always go through."""
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
    except Exception as error:  # closing does not change the round's outcome
        _log_unexpected("closing the profile connection", error)


def _log_unexpected(step: str, error: BaseException) -> None:
    """A single record per unexpected error, with the type only: the text may contain data."""
    _LOG.error("Garmin: unexpected error in %s: %s", step, exception_chain(error))
