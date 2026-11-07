# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fake Garmin: listing and originals (`ActivitySource`) and account with MFA (`GarminAccount`).

No network and no real data: the activities are generated (one run per day, with synthetic FIT or
GPX of the same start and duration as the listing). Failures are injected by
call number or by activity; the slow response advances a fake clock, without
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
PLACEHOLDER_TOKEN = "TOKEN-SEGNAPOSTO-0123456789abcdef"  # noqa: S105 - placeholder
TEST_EMAIL = "atleta@example.invalid"
TEST_PASSWORD = "password-di-prova"  # noqa: S105 - placeholder
TEST_MFA_CODE = "123456"


class OriginalKind(Enum):
    """What is in the ZIP downloaded for an activity."""

    FIT = "fit"  # as from the Garmin site: `<id>_ACTIVITY.fit`
    GPX = "gpx"  # activity uploaded as GPX
    TCX = "tcx"  # unsupported format
    CORRUPT_FIT = "corrupt_fit"  # valid ZIP, `.fit` with random bytes
    TRUNCATED_ZIP = "truncated_zip"  # truncated ZIP: does not open
    NOT_A_ZIP = "not_a_zip"  # bytes that are not a ZIP
    EMPTY_ZIP = "empty_zip"  # ZIP without entries


def _empty_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    return buffer.getvalue()


def original_zip(number: int, kind: OriginalKind = OriginalKind.FIT) -> bytes:
    """The ZIP of the original of run `number` (the same as in `make_remote_history`)."""
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
    """`count` runs, one per day, number `n` with id `FIRST_ACTIVITY_ID + n` and start
    `run_start(n)`: the larger `n`, the more recent. Increasing order of `n`."""
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
    """The two calls of `ActivitySource`, to inject failures."""

    LIST_PAGE = "list_page"
    DOWNLOAD = "download"


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """A call received: which one, and the position (listing) or the id (download)."""

    call: Call
    offset: int | None = None
    limit: int | None = None
    activity_id: GarminActivityId | None = None


class FakeActivitySource:
    """`ActivitySource` on an in-memory listing, newest first, with injectable failures.

    - `fail_call(Call, n, error)`: the n-th call (from 1) of that kind raises `error`.
    - `fail_download_of(id, error, times=None)`: downloads of that activity fail
      `times` times (always if None).
    - `set_original(id, bytes)` / `set_kind(id, OriginalKind)`: change what gets downloaded.
    - `malformed(ids)`: those entries appear in the listing as discarded (count in `raw_count`).
    - `latency_s` and `on_latency`: each call invokes `on_latency(latency_s)` (to advance
      a fake clock) before answering.
    - `calls`, `call_count(Call)`, `downloads` record what happened.
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

    # --- Test direction ------------------------------------------------------------------

    def add_activity(self, activity: RemoteActivity) -> None:
        """A new activity appears in the listing (for example uploaded late)."""
        self._activities[activity.garmin_id] = activity

    def remove_activity(self, garmin_id: GarminActivityId) -> None:
        """The activity disappears from the listing and from the downloads."""
        self._activities.pop(garmin_id, None)

    def set_original(self, garmin_id: GarminActivityId, content: bytes) -> None:
        """Replace the bytes downloaded for the activity."""
        self._originals[garmin_id] = content

    def set_kind(self, garmin_id: GarminActivityId, kind: OriginalKind) -> None:
        """The activity's ZIP contains this kind of original."""
        self._kinds[garmin_id] = kind

    def malformed(self, ids: Sequence[GarminActivityId]) -> None:
        """These entries arrive in the listing but the adapter discards them."""
        self._malformed = frozenset(ids)

    def fail_call(self, call: Call, number: int, error: Exception) -> None:
        """The `number`-th call (from 1) of that kind raises `error`, once."""
        self._failures[(call, number)] = error

    def fail_download_of(
        self, garmin_id: GarminActivityId, error: Exception, *, times: int | None = None
    ) -> None:
        """Downloads of that activity fail `times` times, or always if `times` is None."""
        self._download_failures[garmin_id] = (error, times)

    def call_count(self, call: Call) -> int:
        """How many calls of that kind arrived."""
        return self._counts[call]

    @property
    def activities(self) -> Mapping[GarminActivityId, RemoteActivity]:
        """The activities currently present on the fake Garmin."""
        return dict(self._activities)

    # --- Port -----------------------------------------------------------------------------

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        """See `ActivitySource.list_page`."""
        if offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("invalid position or limit")
        self._enter(Call.LIST_PAGE, RecordedCall(Call.LIST_PAGE, offset=offset, limit=limit))
        ordered = sorted(self._activities.values(), key=lambda a: (a.start, a.garmin_id.value))
        ordered.reverse()
        window = ordered[offset : offset + limit]
        items = tuple(a for a in window if a.garmin_id not in self._malformed)
        total = len(ordered) if self.declares_total else None
        return ActivityPage(offset, items, len(window), total)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        """See `ActivitySource.download_original`."""
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
            raise DownloadTooLargeError("download too large")
        return content

    # --- Internals -----------------------------------------------------------------------------

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
    """Fake `GarminAccount` with optional MFA and recognizable placeholder tokens.

    Login succeeds with `TEST_EMAIL` and `TEST_PASSWORD` (and `TEST_MFA_CODE` if `mfa_required`) and
    gives a session on the given `ActivitySource`. The issued tokens are valid until
    `revoke_tokens()` is called. `login_calls`, `resume_calls` and `mfa_prompts` count the calls;
    the password is not kept anywhere.
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
        """The next login (and the following ones, until None is passed) raises `error`."""
        self._login_error = error

    def fail_resume_with(self, error: Exception | None) -> None:
        """Like `fail_login_with`, for `resume`."""
        self._resume_error = error

    def revoke_tokens(self) -> None:
        """All issued tokens stop being valid: `resume` gives `AuthenticationRequiredError`."""
        self._valid.clear()

    def rate_limit(self, retry_after_s: float | None = None) -> None:
        """Login and resume give 429 until `fail_login_with(None)` is called."""
        self._login_error = RateLimitedError(retry_after_s)
        self._resume_error = RateLimitedError(retry_after_s)

    def go_offline(self) -> None:
        """Login and resume give `ServiceUnavailableError`."""
        self._login_error = ServiceUnavailableError("non disponibile")
        self._resume_error = ServiceUnavailableError("non disponibile")

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """See `GarminAccount.login`."""
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
                raise InvalidCredentialsError("code rejected")
        self._valid.add(self._token_value)
        return GarminSession(self._source, SessionTokens(self._token_value))

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """See `GarminAccount.resume`."""
        self.resume_calls += 1
        if self._resume_error is not None:
            raise self._resume_error
        if tokens.reveal() not in self._valid:
            raise AuthenticationRequiredError("invalid tokens")
        return GarminSession(self._source, tokens)
