# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The only place that really uses `garminconnect` (0.3.x): client and factory, thin.

No logic: each method is a call to the library, whose exceptions it lets through
(`errors` translates them). Choices:

- `retry_attempts=0`: the library would retry 5xx and network errors on its own with waits up to 10
s and the
  caller already has its own policy (`SyncPolicy`); two levels of retries multiply.
- Timeouts: those of the library (15 s per API request and download, 30 s for each step
  of the login). They cannot be configured from here without using internal parts of the library;
  for a
  very large original 15 s may not be enough and the error is transient.
- Log: `requests`, `urllib3` and `curl_cffi` at WARNING; `garminconnect` at CRITICAL, because at
  ERROR level it logs with a traceback the request path (which contains the activity
  id) and the text of the errors.
- Login with credentials passes an empty token store (`"{}"`): without an argument the
  library would look for the tokens in the file given by the `GARMINTOKENS` environment variable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from garminconnect import Garmin

from ponos_coach.garmin.client import GarminClient

_QUIET_LOGGERS: Final = {
    "requests": logging.WARNING,
    "urllib3": logging.WARNING,
    "curl_cffi": logging.WARNING,
    "garminconnect": logging.CRITICAL,
}
_EMPTY_TOKENSTORE: Final = "{}"


def quiet_library_loggers() -> None:
    """Brings the loggers of the library and its transport to the levels of `_QUIET_LOGGERS`."""
    for name, level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


class ConnectClient:
    """`GarminClient` on top of an authenticated `garminconnect.Garmin` instance."""

    def __init__(self, garmin: Garmin) -> None:
        self._garmin = garmin

    def list_activities(self, offset: int, limit: int) -> object:
        """See `GarminClient.list_activities`."""
        return self._garmin.get_activities(offset, limit)

    def count_activities(self) -> object:
        """See `GarminClient.count_activities`."""
        return self._garmin.count_activities()

    def download_original(self, activity_id: int) -> bytes:
        """See `GarminClient.download_original`."""
        content: bytes = self._garmin.download_activity(
            str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )
        return content

    def dump_tokens(self) -> str:
        """See `GarminClient.dump_tokens`."""
        tokens: str = self._garmin.client.dumps()
        return tokens


class ConnectClientFactory:
    """`GarminClientFactory` on `garminconnect`: login with MFA and resumption from tokens."""

    def __init__(self) -> None:
        quiet_library_loggers()

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> GarminClient:
        """See `GarminClientFactory.login`."""
        garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa, retry_attempts=0)
        garmin.login(tokenstore=_EMPTY_TOKENSTORE)
        return ConnectClient(garmin)

    def resume(self, tokens: str) -> GarminClient:
        """See `GarminClientFactory.resume`. `tokens` must be a JSON object."""
        garmin = Garmin(retry_attempts=0)
        garmin.login(tokenstore=tokens)
        return ConnectClient(garmin)
