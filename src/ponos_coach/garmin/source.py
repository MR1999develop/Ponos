# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ActivitySource` on Garmin Connect: paged list and download of the original.

Thin adapter on top of the "client" (`client.GarminClient`): it calls, translates errors into the
port's
types, interprets the response at the boundary (`listing`) and applies the caps. Timeouts are
those of the client (15 s per request); pauses and retries belong to the caller, who has the
policy (`SyncPolicy`): the client does not retry on its own.

Known limit: `garminconnect` reads the whole response body into memory before
returning it, so the byte cap of the download applies after the read and does not
interrupt it (the 15 s timeout and the cap on the size declared by the downstream ZIP
limit the damage). Same for the size of the list JSON.
"""

from __future__ import annotations

import logging

from ponos_coach.activities.domain import MAX_PAGE_SIZE, ActivityPage, GarminActivityId
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    RateLimitedError,
)
from ponos_coach.garmin.client import GarminClient
from ponos_coach.garmin.errors import translate_source_error
from ponos_coach.garmin.listing import UnexpectedListingError, parse_page, parse_total

_LOGGER = logging.getLogger(__name__)


class GarminActivitySource:
    """Lists and downloads the activities of the user of an already authenticated session.

    Not thread-safe: one session per synchronization thread.
    """

    def __init__(self, client: GarminClient) -> None:
        self._client = client

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        """See `ActivitySource.list_page`.

        The declared total is requested only for the first page (`offset == 0`): another
        request per page would double the load; if the total request fails
        for something other than authentication or rate limit, the total stays unknown.

        Documented choice (R12): the total request is part of the same `list_page` call
        as the first page, so it falls under the same discipline of the caller:
        a single pause before the call, and if Garmin limits the rate or asks for login
        the error comes out of the call and the caller applies backoff or stops. It costs one
        extra request per list (not per page), without a second pause between the two; the
        total is only for progress and does not decide when the list ends.

        Raises:
            ValueError: negative `offset` or `limit` outside 1..`MAX_PAGE_SIZE`.
        """
        if offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("invalid position or limit")
        try:
            raw = self._client.list_activities(offset, limit)
        except Exception as exc:
            raise translate_source_error(exc) from exc
        try:
            items, raw_count = parse_page(raw)
        except UnexpectedListingError as exc:
            raise DefinitiveSourceError("list response not interpretable") from exc
        if raw_count > len(items):
            _LOGGER.warning("list entries discarded", extra={"skipped": raw_count - len(items)})
        total = self._declared_total() if offset == 0 else None
        return ActivityPage(offset, items, raw_count, total)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        """See `ActivitySource.download_original`.

        Raises:
            ValueError: non-positive `max_bytes`.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        try:
            content = self._client.download_original(activity_id.value)
        except Exception as exc:
            raise translate_source_error(exc) from exc
        if not isinstance(content, bytes):
            raise DefinitiveSourceError("the download is not a block of bytes")
        if len(content) > max_bytes:
            raise DownloadTooLargeError("download too large")
        return content

    def _declared_total(self) -> int | None:
        try:
            return parse_total(self._client.count_activities())
        except Exception as exc:
            translated = translate_source_error(exc)
            if isinstance(translated, AuthenticationRequiredError | RateLimitedError):
                raise translated from exc
            return None
