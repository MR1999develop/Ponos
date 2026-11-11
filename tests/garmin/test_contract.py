# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The `ActivitySource` contract on the fake and on the Garmin adapter with the fake client."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ponos_coach.activities.domain import GarminActivityId, RemoteActivity
from ponos_coach.activities.ports import (
    ActivitySource,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.garmin import GarminActivitySource
from tests.fakes.garmin import Call, FakeActivitySource
from tests.garmin.contract_activity_source import (
    RETRY_AFTER_S,
    ActivitySourceContract,
    Failure,
    HarnessFactory,
    SourceHarness,
)
from tests.garmin.scripted import (
    ScriptedClient,
    auth_error,
    entry_for,
    http_error,
    newest_first,
    not_found_error,
    rate_limit_error,
)


class _FakeHarness:
    def __init__(self, activities: Sequence[RemoteActivity]) -> None:
        self._fake = FakeActivitySource(activities)

    @property
    def source(self) -> ActivitySource:
        return self._fake

    def set_original(self, activity_id: GarminActivityId, content: bytes) -> None:
        self._fake.set_original(activity_id, content)

    def mark_malformed(self, ids: Sequence[GarminActivityId]) -> None:
        self._fake.malformed(ids)

    def fail_next_list(self, failure: Failure) -> None:
        number = self._fake.call_count(Call.LIST_PAGE) + 1
        self._fake.fail_call(Call.LIST_PAGE, number, _port_error(failure))

    def fail_next_download(self, failure: Failure) -> None:
        number = self._fake.call_count(Call.DOWNLOAD) + 1
        self._fake.fail_call(Call.DOWNLOAD, number, _port_error(failure))


def _port_error(failure: Failure) -> Exception:
    return {
        Failure.RATE_LIMITED: RateLimitedError(RETRY_AFTER_S),
        Failure.TRANSIENT: TransientSourceError("failure"),
        Failure.AUTHENTICATION: AuthenticationRequiredError("token"),
        Failure.DEFINITIVE: DefinitiveSourceError("rifiutato"),
    }[failure]


def _library_error(failure: Failure) -> Exception:
    return {
        Failure.RATE_LIMITED: rate_limit_error(str(int(RETRY_AFTER_S))),
        Failure.TRANSIENT: http_error(503),
        Failure.AUTHENTICATION: auth_error(),
        Failure.DEFINITIVE: not_found_error(),
    }[failure]


class _AdapterHarness:
    def __init__(self, activities: Sequence[RemoteActivity]) -> None:
        self._ordered = newest_first(activities)
        self._client = ScriptedClient([entry_for(a) for a in self._ordered])
        self._source = GarminActivitySource(self._client)

    @property
    def source(self) -> ActivitySource:
        return self._source

    def set_original(self, activity_id: GarminActivityId, content: bytes) -> None:
        self._client.set_download(activity_id.value, content)

    def mark_malformed(self, ids: Sequence[GarminActivityId]) -> None:
        for index, activity in enumerate(self._ordered):
            if activity.garmin_id in ids:
                broken = entry_for(activity)
                broken["startTimeGMT"] = "ieri mattina"
                self._client.replace_entry(index, broken)

    def fail_next_list(self, failure: Failure) -> None:
        self._client.list_failures.append(_library_error(failure))

    def fail_next_download(self, failure: Failure) -> None:
        self._client.download_failures.append(_library_error(failure))


def _harness_of(kind: type) -> HarnessFactory:
    def create(activities: Sequence[RemoteActivity]) -> SourceHarness:
        harness: SourceHarness = kind(activities)
        return harness

    return create


class TestFakeActivitySourceContract(ActivitySourceContract):
    @pytest.fixture
    def make_harness(self) -> HarnessFactory:
        return _harness_of(_FakeHarness)


class TestGarminActivitySourceContract(ActivitySourceContract):
    @pytest.fixture
    def make_harness(self) -> HarnessFactory:
        return _harness_of(_AdapterHarness)
