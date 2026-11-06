# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Token di sessione di Garmin: una stringa opaca che non si stampa e non si serializza mai."""

from __future__ import annotations

import hmac
from typing import Final, NoReturn

MAX_TOKEN_CHARS: Final = 65536  # default del progetto: i token di Garmin sono di qualche KB


class SessionTokens:
    """Token di sessione come stringa opaca (per l'app non ha struttura).

    `repr`, `str` e le f-string danno sempre `SessionTokens(<hidden>)`; il contenuto esce solo
    da `reveal()`, chiamato dal portachiavi e da chi riprende la sessione. Non si copia con
    `pickle` né `copy`, non è confrontabile come dato (il confronto è a tempo costante) e non
    è hashabile.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Raises:
        ValueError: vuoto, più lungo di `MAX_TOKEN_CHARS` o non una stringa.
        """
        candidate: object = value  # a runtime può arrivare qualunque cosa
        if not isinstance(candidate, str) or not 0 < len(candidate) <= MAX_TOKEN_CHARS:
            raise ValueError("token non valido")
        self._value = candidate

    def reveal(self) -> str:
        """Il contenuto, da non registrare né mostrare."""
        return self._value

    def __repr__(self) -> str:
        return "SessionTokens(<hidden>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SessionTokens):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    __hash__ = None  # type: ignore[assignment]  # non hashabile: un segreto non è una chiave

    def __reduce__(self) -> NoReturn:
        raise TypeError("SessionTokens non si serializza")
