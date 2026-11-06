# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dagli errori di `garminconnect` a quelli delle porte, senza perdere la catena delle cause.

La libreria avvolge quasi tutto in poche eccezioni (`GarminConnectConnectionError` per ogni
fallimento di rete o HTTP, `GarminConnectAuthenticationError` anche quando il vero guasto è la
rete): qui si guarda la catena `__cause__`/`__context__` per il codice HTTP e per i guasti di
rete, e si decide come reagisce chi chiama.

- 429 (e 427 del login) -> limite di frequenza, con `Retry-After` se Garmin lo dice;
- 401 o autenticazione rifiutata -> autenticazione richiesta (al login: credenziali rifiutate);
- rete, timeout, risposta non JSON, 5xx e 408 -> transitorio;
- 404, altri 4xx e tutto il resto -> definitivo.

Nessun messaggio contiene testi della libreria o di Garmin (potrebbero avere percorsi con id,
nomi o frammenti di risposta): la causa resta nella catena per chi la vuole ispezionare, e chi
la registra deve scrivere il tipo, non il testo.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Final

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from ponos_coach.activities.ports import (
    ActivitySourceError,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    InvalidCredentialsError,
    RateLimitedError,
    ServiceUnavailableError,
    TransientSourceError,
)

MAX_RETRY_AFTER_S: Final = 3600.0  # oltre un'ora l'attesa suggerita non si prende sul serio
_MAX_CHAIN: Final = 8  # profondità massima della catena delle cause che si esamina
_RATE_LIMIT_STATUSES: Final = frozenset({427, 429})  # 427: blocco del login di Garmin
_TRANSIENT_STATUSES: Final = frozenset({408, 425})
_RETRY_AFTER: Final = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,6})?")  # solo cifre ASCII
_NETWORK_MODULES: Final = ("requests", "urllib3", "curl_cffi", "http", "ssl", "socket")


class _Outcome(Enum):
    """Come deve reagire chi chiama."""

    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    TRANSIENT = "transient"
    DEFINITIVE = "definitive"


def _chain(exc: BaseException) -> list[BaseException]:
    """`exc` e le sue cause, in ordine, senza cicli e al più `_MAX_CHAIN` elementi."""
    found: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(found) < _MAX_CHAIN:
        seen.add(id(current))
        found.append(current)
        current = current.__cause__ or current.__context__
    return found


def _response_of(exc: BaseException) -> object:
    return getattr(exc, "response", None)


def _status_of(exc: BaseException) -> int | None:
    """Codice HTTP dichiarato dall'eccezione o dalla sua risposta, se è un intero."""
    for holder in (exc, _response_of(exc)):
        status = getattr(holder, "status_code", None)
        if type(status) is int:
            return status
    return None


def _first_status(chain: list[BaseException]) -> int | None:
    return next((s for s in map(_status_of, chain) if s is not None), None)


def _is_network_failure(exc: BaseException) -> bool:
    """Guasto di trasporto: errore di I/O (anche di `requests` e `curl_cffi`, che lo sono)
    senza una risposta HTTP. Un `HTTPError` con la sua risposta non è un guasto di rete."""
    module = type(exc).__module__.partition(".")[0]
    is_io = isinstance(exc, OSError) or module in _NETWORK_MODULES
    return is_io and _status_of(exc) is None


def _retry_after_s(chain: list[BaseException]) -> float | None:
    """`Retry-After` in secondi (cifre ASCII, intere o decimali), se Garmin lo dice in modo
    sensato; altrimenti None. Non solleva mai: l'intestazione è un input non fidato."""
    for exc in chain:
        headers = getattr(_response_of(exc), "headers", None)
        getter = getattr(headers, "get", None)
        raw = getter("Retry-After") if callable(getter) else None
        if not isinstance(raw, str) or _RETRY_AFTER.fullmatch(raw.strip()) is None:
            continue  # non ASCII, con segno o altro: nessuna attesa suggerita
        value = float(raw)
        if math.isfinite(value) and value > 0:
            return min(value, MAX_RETRY_AFTER_S)
    return None


def _classify(exc: BaseException) -> tuple[_Outcome, float | None]:
    """Esito e, per il limite di frequenza, l'attesa suggerita."""
    chain = _chain(exc)
    status = _first_status(chain)
    if status in _RATE_LIMIT_STATUSES or any(
        isinstance(item, GarminConnectTooManyRequestsError) for item in chain
    ):
        return _Outcome.RATE_LIMITED, _retry_after_s(chain)
    if isinstance(exc, GarminConnectNotFoundError) or status == 404:
        return _Outcome.DEFINITIVE, None
    if status == 401:
        return _Outcome.AUTHENTICATION, None
    if status is not None:
        transient = status >= 500 or status in _TRANSIENT_STATUSES
        return (_Outcome.TRANSIENT if transient else _Outcome.DEFINITIVE), None
    if any(_is_network_failure(item) for item in chain):
        return _Outcome.TRANSIENT, None  # anche un'autenticazione "fallita" per la rete
    is_auth = isinstance(exc, GarminConnectAuthenticationError)
    return (_Outcome.AUTHENTICATION if is_auth else _Outcome.DEFINITIVE), None


def translate_source_error(exc: Exception) -> ActivitySourceError:
    """L'errore di una chiamata di elenco o di download, nel tipo della porta `ActivitySource`.

    Chi solleva lo fa con `raise translate_source_error(exc) from exc`.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return AuthenticationRequiredError("autenticazione richiesta")
    if outcome is _Outcome.TRANSIENT:
        return TransientSourceError("errore transitorio di Garmin")
    return DefinitiveSourceError("richiesta rifiutata o risposta non interpretabile")


def translate_login_error(exc: Exception) -> ActivitySourceError:
    """L'errore del login con credenziali, nel tipo della porta `GarminAccount.login`.

    Ogni guasto che non è credenziali rifiutate o limite di frequenza vale "Garmin non
    risponde" (anche un blocco 403 del sito): ritentare più tardi è l'unica reazione utile.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return InvalidCredentialsError("credenziali rifiutate")
    return ServiceUnavailableError("Garmin non risponde")


def translate_resume_error(exc: Exception) -> ActivitySourceError:
    """L'errore della ripresa dai token, nel tipo della porta `GarminAccount.resume`.

    Un'autenticazione rifiutata vale "token scaduti o revocati"; la libreria dà lo stesso errore
    quando la rete manca durante la verifica dei token, e qui si riconosce dalla catena.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return AuthenticationRequiredError("token scaduti o revocati")
    return ServiceUnavailableError("Garmin non risponde")
