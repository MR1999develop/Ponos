# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The thin class that really uses `garminconnect`, tried without network.

No login and no call to Garmin: only what the library decides locally is tried
(tokens with nothing usable) and the log configuration.
"""

from __future__ import annotations

import logging

import pytest
from garminconnect import GarminConnectAuthenticationError

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import AuthenticationRequiredError
from ponos_coach.garmin import ConnectClientFactory, GarminConnectAccount

EMPTY_TOKENS = '{"di_token": null, "di_refresh_token": null, "di_client_id": null}'


def test_tokens_with_nothing_usable_are_refused_by_the_library_without_network() -> None:
    with pytest.raises(GarminConnectAuthenticationError):
        ConnectClientFactory().resume(EMPTY_TOKENS)


def test_the_account_turns_that_refusal_into_authentication_required() -> None:
    account = GarminConnectAccount(ConnectClientFactory())

    with pytest.raises(AuthenticationRequiredError) as caught:
        account.resume(SessionTokens(EMPTY_TOKENS))

    assert isinstance(caught.value.__cause__, GarminConnectAuthenticationError)


def test_the_loggers_of_the_library_and_its_transport_are_quieted() -> None:
    for name in ("requests", "urllib3", "curl_cffi", "garminconnect"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    ConnectClientFactory()

    levels = {
        name: logging.getLogger(name).level
        for name in ("requests", "urllib3", "curl_cffi", "garminconnect")
    }
    assert levels == {
        "requests": logging.WARNING,
        "urllib3": logging.WARNING,
        "curl_cffi": logging.WARNING,
        "garminconnect": logging.CRITICAL,
    }
