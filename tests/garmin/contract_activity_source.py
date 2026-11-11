# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of `ActivitySource`: the same behavior for the fake and for the Garmin adapter.

Each implementation offers a `SourceHarness` (the port plus the ways to break it: a different
original, a malformed entry, a failure on the next call). This way the same suite runs on the
fake in `tests/fakes/garmin.py` and on the adapter with the fake client. What the two may
do differently (the declared total, at which pages) is outside the contract.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Protocol

import pytest

from ponos_coach.activities.domain import (
    MAX_PAGE_SIZE,
    GarminActivityId,
    RemoteActivity,
)
from ponos_coach.activities.ports import (
    ActivitySource,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    RateLimitedError,
    TransientSourceError,
)
from tests.fakes.garmin import FIRST_ACTIVITY_ID, make_remote_history

RETRY_AFTER_S = 30.0


class Failure(Enum):
    """Failures a call can have, in the categories where the reaction changes."""

    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    AUTHENTICATION = "authentication"
    DEFINITIVE = "definitive"


EXPECTED_ERROR: dict[Failure, type[Exception]] = {
    Failure.RATE_LIMITED: RateLimitedError,
    Failure.TRANSIENT: TransientSourceError,
    Failure.AUTHENTICATION: AuthenticationRequiredError,
    Failure.DEFINITIVE: DefinitiveSourceError,
}


class SourceHarness(Protocol):
    """An `ActivitySource` on the given activities and the ways to break it."""

    @property
    def source(self) -> ActivitySource:
        """The port under test."""
        ...

    def set_original(self, activity_id: GarminActivityId, content: bytes) -> None:
        """What gets downloaded for that activity."""
        ...

    def mark_malformed(self, ids: Sequence[GarminActivityId]) -> None:
        """These entries arrive in the listing but are malformed: the adapter discards them."""
        ...

    def fail_next_list(self, failure: Failure) -> None:
        """The next `list_page` fails in that way."""
        ...

    def fail_next_download(self, failure: Failure) -> None:
        """The next `download_original` fails in that way."""
        ...


HarnessFactory = Callable[[Sequence[RemoteActivity]], SourceHarness]


def history_ids(count: int) -> list[GarminActivityId]:
    """The ids of `make_remote_history(count)`, most recent first."""
    return [GarminActivityId(FIRST_ACTIVITY_ID + n) for n in reversed(range(count))]


class ActivitySourceContract:
    """Partitions: pages (first, middle, last, past the end, empty), malformed entries,
    originals (normal, too large, missing), failures by category, invalid arguments."""

    @pytest.fixture
    def make_harness(self) -> HarnessFactory:
        raise NotImplementedError

    def test_pages_are_newest_first_and_cover_the_history_without_overlap(
        self, make_harness: HarnessFactory
    ) -> None:
        history = make_remote_history(7)
        source = make_harness(history).source

        pages = [source.list_page(offset, 3) for offset in (0, 3, 6)]

        seen = [item.garmin_id for page in pages for item in page.items]
        assert seen == history_ids(7)
        assert [page.raw_count for page in pages] == [3, 3, 1]
        assert [page.offset for page in pages] == [0, 3, 6]
        assert pages[0].items[0] == history[-1]

    @pytest.mark.parametrize("has_heart_rate", [True, False, None])
    @pytest.mark.parametrize("sport_key", ["running", "trail_running", "lap_swimming"])
    def test_entries_come_back_as_the_same_domain_values(
        self, make_harness: HarnessFactory, sport_key: str, has_heart_rate: bool | None
    ) -> None:
        history = make_remote_history(2, sport_key=sport_key, has_heart_rate=has_heart_rate)
        source = make_harness(history).source

        assert set(source.list_page(0, 10).items) == set(history)

    def test_offset_past_the_end_is_an_empty_page(self, make_harness: HarnessFactory) -> None:
        source = make_harness(make_remote_history(3)).source

        page = source.list_page(3, 10)

        assert (page.items, page.raw_count, page.offset) == ((), 0, 3)

    def test_empty_history_is_an_empty_page(self, make_harness: HarnessFactory) -> None:
        page = make_harness([]).source.list_page(0, 10)

        assert (page.items, page.raw_count) == ((), 0)

    def test_page_of_the_maximum_size_is_accepted(self, make_harness: HarnessFactory) -> None:
        source = make_harness(make_remote_history(3)).source

        assert source.list_page(0, MAX_PAGE_SIZE).raw_count == 3

    def test_declared_total_is_absent_or_the_real_total(self, make_harness: HarnessFactory) -> None:
        page = make_harness(make_remote_history(4)).source.list_page(0, 2)

        assert page.declared_total in (None, 4)

    def test_malformed_entries_are_skipped_and_counted(self, make_harness: HarnessFactory) -> None:
        history = make_remote_history(5)
        harness = make_harness(history)
        bad = [history[1].garmin_id, history[3].garmin_id]
        harness.mark_malformed(bad)

        page = harness.source.list_page(0, 10)

        assert page.raw_count == 5
        assert page.skipped_count == 2
        assert {item.garmin_id for item in page.items}.isdisjoint(bad)
        assert len(page.items) == 3

    @pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, MAX_PAGE_SIZE + 1)])
    def test_invalid_arguments_are_refused(
        self, make_harness: HarnessFactory, offset: int, limit: int
    ) -> None:
        source = make_harness(make_remote_history(2)).source

        with pytest.raises(ValueError, match="invalid"):
            source.list_page(offset, limit)

    def test_original_is_returned_as_given(self, make_harness: HarnessFactory) -> None:
        history = make_remote_history(2)
        harness = make_harness(history)
        harness.set_original(history[0].garmin_id, b"PK-originale")

        assert harness.source.download_original(history[0].garmin_id, 1000) == b"PK-originale"

    def test_original_exactly_at_the_limit_is_accepted_and_one_more_byte_is_not(
        self, make_harness: HarnessFactory
    ) -> None:
        history = make_remote_history(1)
        harness = make_harness(history)
        activity_id = history[0].garmin_id
        harness.set_original(activity_id, b"x" * 100)

        assert len(harness.source.download_original(activity_id, 100)) == 100
        with pytest.raises(DownloadTooLargeError):
            harness.source.download_original(activity_id, 99)

    def test_original_of_an_unknown_activity_is_definitive(
        self, make_harness: HarnessFactory
    ) -> None:
        source = make_harness(make_remote_history(1)).source

        with pytest.raises(DefinitiveSourceError):
            source.download_original(GarminActivityId(FIRST_ACTIVITY_ID + 999), 1000)

    @pytest.mark.parametrize("failure", list(Failure))
    def test_a_failed_list_call_maps_to_the_error_of_its_category_and_the_next_works(
        self, make_harness: HarnessFactory, failure: Failure
    ) -> None:
        harness = make_harness(make_remote_history(2))
        harness.fail_next_list(failure)

        with pytest.raises(EXPECTED_ERROR[failure]) as caught:
            harness.source.list_page(0, 10)
        assert harness.source.list_page(0, 10).raw_count == 2
        if failure is Failure.RATE_LIMITED:
            assert isinstance(caught.value, RateLimitedError)
            assert caught.value.retry_after_s == RETRY_AFTER_S

    @pytest.mark.parametrize("failure", list(Failure))
    def test_a_failed_download_maps_to_the_error_of_its_category(
        self, make_harness: HarnessFactory, failure: Failure
    ) -> None:
        history = make_remote_history(1)
        harness = make_harness(history)
        harness.fail_next_download(failure)

        with pytest.raises(EXPECTED_ERROR[failure]):
            harness.source.download_original(history[0].garmin_id, 1000)
