# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pause and retry policy toward Garmin, and remaining-time estimate.

All the values live in `SyncPolicy` (project default, PLAN.md risks and 4.4 point 4); the
functions are pure: the jitter comes from an injected random source, never from the `random` module.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from ponos_coach.activities.domain.limits import MAX_ACTIVITY_FILE_BYTES
from ponos_coach.activities.domain.remote import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

MAX_DOWNLOAD_BYTES: Final = MAX_ACTIVITY_FILE_BYTES  # cap on the downloaded ZIP (default)
ETA_WINDOW: Final = 20  # recent durations in the moving average of the estimate


class RandomSource(Protocol):
    """Injected random source (`random.Random` satisfies it)."""

    def uniform(self, low: float, high: float) -> float:
        """A number in `[low, high]`."""
        ...


def _positive_finite(*values: float) -> bool:
    return all(math.isfinite(v) and v > 0 for v in values)


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    """Pauses, backoff and caps on attempts (project default).

    - pause between two requests: `min_pause_s` plus a jitter in `[0, max_jitter_s]`;
    - after a transient error or a 429: `backoff_base_s * 2^(n-1)` (n = errors in a row) plus
      the jitter, at most `backoff_cap_s`;
    - at most `max_attempts_per_request` attempts for the same request;
    - a journal entry becomes "definitively failed" after `max_syncs_per_activity`
      failed synchronizations;
    - a journal entry whose download has no useful answer (5xx, no response) while Garmin answers
      other requests becomes "not provided by Garmin" after `max_unanswered_rounds` such rounds
      (three: one bad round is a hiccup, three in a row is the entry);
    - the list is requested in pages of `page_size` entries.
    """

    min_pause_s: float = 1.0
    max_jitter_s: float = 0.75
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 120.0
    max_attempts_per_request: int = 5
    max_syncs_per_activity: int = 3
    page_size: int = DEFAULT_PAGE_SIZE
    max_unanswered_rounds: int = 3

    def __post_init__(self) -> None:
        if not _positive_finite(self.min_pause_s, self.backoff_base_s, self.backoff_cap_s):
            raise ValueError("pauses and backoff must be finite and positive")
        if not math.isfinite(self.max_jitter_s) or self.max_jitter_s < 0:
            raise ValueError("the jitter must be finite and non-negative")
        if self.backoff_cap_s < self.backoff_base_s:
            raise ValueError("the backoff cap cannot be below the base")
        if self.max_attempts_per_request < 1 or self.max_syncs_per_activity < 1:
            raise ValueError("at least one attempt is required")
        if self.max_unanswered_rounds < 1:
            raise ValueError("at least one unanswered round is required")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ValueError("page size out of range")


DEFAULT_POLICY: Final = SyncPolicy()


def pause_between_requests_s(policy: SyncPolicy, rng: RandomSource) -> float:
    """Seconds to wait before the next request: minimum pause plus jitter."""
    return policy.min_pause_s + _jitter(policy, rng)


def retry_wait_s(
    policy: SyncPolicy,
    failures: int,
    rng: RandomSource,
    suggested_s: float | None = None,
) -> float | None:
    """Wait before retrying a request that failed `failures` times in a row, or None.

    None says to stop: the attempts have reached `max_attempts_per_request`, or
    Garmin asks for a wait (`suggested_s`, e.g. `Retry-After`) beyond the backoff
    cap, and waiting that long would be worse than stopping with a clear error. Otherwise
    the wait is `backoff_base_s * 2^(failures-1)`, at least `suggested_s`, plus the jitter, never
    beyond
    `backoff_cap_s`.

    Raises:
        ValueError: `failures` less than 1 or `suggested_s` negative or not finite.
    """
    if failures < 1:
        raise ValueError("at least one failure is required")
    if suggested_s is not None and (not math.isfinite(suggested_s) or suggested_s < 0):
        raise ValueError("invalid suggested wait")
    if failures >= policy.max_attempts_per_request:
        return None
    if suggested_s is not None and suggested_s > policy.backoff_cap_s:
        return None
    exponent = min(failures - 1, 62)  # beyond the cap nothing changes, but 2**n stays small
    base = min(policy.backoff_base_s * 2.0**exponent, policy.backoff_cap_s)
    wait = max(base, suggested_s or 0.0) + _jitter(policy, rng)
    return min(wait, policy.backoff_cap_s)


def _jitter(policy: SyncPolicy, rng: RandomSource) -> float:
    if policy.max_jitter_s == 0:
        return 0.0
    return min(max(rng.uniform(0.0, policy.max_jitter_s), 0.0), policy.max_jitter_s)


def estimate_remaining_s(
    recent_durations_s: Sequence[float], remaining_items: int, window: int = ETA_WINDOW
) -> float | None:
    """Estimated seconds for the remaining entries: moving average of the last `window` durations.

    Each duration is the time of a whole entry (pauses included). None if there is no
    duration yet and entries remain; 0 if nothing remains.

    Raises:
        ValueError: `remaining_items` negative, `window` less than 1 or durations not finite or
            negative.
    """
    if remaining_items < 0 or window < 1:
        raise ValueError("remaining entries or window invalid")
    if remaining_items == 0:
        return 0.0
    if not recent_durations_s:
        return None
    recent = np.asarray(recent_durations_s[-window:], dtype=np.float64)
    if not bool(np.all(np.isfinite(recent))) or bool(np.any(recent < 0)):
        raise ValueError("invalid durations")
    return float(np.mean(recent)) * remaining_items
