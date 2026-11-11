# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Behaviors of the `GarminActivitySource` adapter that the contract does not cover."""

from __future__ import annotations

import logging

import pytest

from ponos_coach.activities.domain import MAX_PAGE_SIZE, GarminActivityId
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.garmin import GarminActivitySource
from tests.fakes.garmin import make_remote_history
from tests.fixtures.garmin import load_json
from tests.garmin.scripted import (
    ScriptedClient,
    auth_error,
    entry_for,
    http_error,
    newest_first,
    rate_limit_error,
)


def client_with(count: int) -> ScriptedClient:
    return ScriptedClient([entry_for(a) for a in newest_first(make_remote_history(count))])


class TestListPage:
    def test_the_arguments_reach_the_client_unchanged(self) -> None:
        client = client_with(5)

        GarminActivitySource(client).list_page(2, 3)

        assert client.list_calls == [(2, 3)]

    def test_the_total_is_asked_only_for_the_first_page(self) -> None:
        client = client_with(5)
        source = GarminActivitySource(client)

        first, later = source.list_page(0, 2), source.list_page(2, 2)

        assert (first.declared_total, later.declared_total) == (5, None)
        assert client.count_calls == 1

    @pytest.mark.parametrize("total", ["cinque", -3, 10**12, 2.5, [5]])
    def test_a_strange_total_is_ignored(self, total: object) -> None:
        client = ScriptedClient([entry_for(make_remote_history(1)[0])], total=total)

        assert GarminActivitySource(client).list_page(0, 5).declared_total is None

    def test_a_transient_failure_of_the_count_does_not_fail_the_page(self) -> None:
        client = client_with(2)
        client.count_failure = http_error(503)

        page = GarminActivitySource(client).list_page(0, 5)

        assert (page.raw_count, page.declared_total) == (2, None)

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (auth_error(), AuthenticationRequiredError),
            (rate_limit_error("9"), RateLimitedError),
        ],
    )
    def test_authentication_and_rate_limit_of_the_count_are_not_hidden(
        self, failure: Exception, expected: type[Exception]
    ) -> None:
        client = client_with(2)
        client.count_failure = failure

        with pytest.raises(expected):
            GarminActivitySource(client).list_page(0, 5)

    @pytest.mark.parametrize("listing", ["testo", 7, True, {"altro": 1}, {"activityList": "x"}])
    def test_a_response_that_is_not_a_list_is_definitive(self, listing: object) -> None:
        source = GarminActivitySource(ScriptedClient(listing=listing))

        with pytest.raises(DefinitiveSourceError, match="not interpretable"):
            source.list_page(0, 10)

    def test_a_response_over_the_page_limit_is_definitive(self) -> None:
        entries = [entry_for(make_remote_history(1)[0])] * (MAX_PAGE_SIZE + 1)
        source = GarminActivitySource(ScriptedClient(listing=entries))

        with pytest.raises(DefinitiveSourceError):
            source.list_page(0, 10)

    def test_the_hostile_page_is_read_without_errors_and_counts_what_it_skipped(self) -> None:
        raw = load_json("activity_list_hostile.json")
        assert isinstance(raw, list)
        source = GarminActivitySource(ScriptedClient(listing=raw[:MAX_PAGE_SIZE]))

        page = source.list_page(0, 100)

        assert page.raw_count == len(raw)
        assert page.skipped_count == len(raw) - 2
        assert [item.garmin_id.value for item in page.items] == [2000001, 2000027]

    def test_the_library_error_stays_as_the_cause(self) -> None:
        client = client_with(1)
        original = http_error(503)
        client.list_failures.append(original)

        with pytest.raises(TransientSourceError) as caught:
            GarminActivitySource(client).list_page(0, 5)

        assert caught.value.__cause__ is original

    def test_skipped_entries_are_logged_as_a_count_without_ids_or_names(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        raw = load_json("activity_list_hostile.json")
        source = GarminActivitySource(ScriptedClient(listing=raw))

        with caplog.at_level(logging.DEBUG, logger="ponos_coach.garmin"):
            source.list_page(0, 100)

        records = [r for r in caplog.records if r.name.startswith("ponos_coach.garmin")]
        assert len(records) == 1
        assert records[0].getMessage() == "list entries discarded"
        assert "2000001" not in caplog.text
        assert "script" not in caplog.text


class TestDownload:
    def test_the_numeric_id_reaches_the_client(self) -> None:
        client = ScriptedClient(downloads={123: b"zip"})

        content = GarminActivitySource(client).download_original(GarminActivityId(123), 10)

        assert (content, client.downloaded) == (b"zip", [123])

    @pytest.mark.parametrize("max_bytes", [0, -1])
    def test_a_non_positive_limit_is_refused(self, max_bytes: int) -> None:
        source = GarminActivitySource(ScriptedClient(downloads={1: b"x"}))

        with pytest.raises(ValueError, match="positive"):
            source.download_original(GarminActivityId(1), max_bytes)

    def test_too_large_is_reported_after_reading(self) -> None:
        source = GarminActivitySource(ScriptedClient(downloads={1: b"x" * 11}))

        with pytest.raises(DownloadTooLargeError):
            source.download_original(GarminActivityId(1), 10)

    def test_something_that_is_not_bytes_is_definitive(self) -> None:
        client = ScriptedClient()
        client.set_download(1, "testo")  # type: ignore[arg-type]  # the real client does not do this
        source = GarminActivitySource(client)

        with pytest.raises(DefinitiveSourceError, match="byte"):
            source.download_original(GarminActivityId(1), 10)

    def test_an_empty_original_is_returned_for_the_import_to_judge(self) -> None:
        source = GarminActivitySource(ScriptedClient(downloads={1: b""}))

        assert source.download_original(GarminActivityId(1), 10) == b""
