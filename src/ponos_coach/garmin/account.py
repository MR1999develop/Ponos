# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GarminAccount` su Garmin Connect: login con MFA e ripresa dai token salvati.

La password e il codice MFA passano solo al client e non si conservano né si registrano. I
token (`SessionTokens`) si rivelano (`reveal()`) solo nel punto in cui si passano al client per
riprendere la sessione. Il login non ritenta: ogni errore è tradotto nel tipo della porta
(`errors`) e la politica dei ritentativi è di chi chiama.

Limiti: la richiesta del codice MFA (`prompt_mfa_code`) è una callback dell'interfaccia e non
ha un timeout qui, che attenderebbe una persona; il numero di richieste per login è limitato.
Se i token si rinnovano durante la sessione, il rinnovo non viene salvato: alla ripresa
successiva la libreria li rinnova da sé.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    GarminSession,
    InvalidCredentialsError,
    MfaCancelledError,
)
from ponos_coach.garmin.client import GarminClient, GarminClientFactory
from ponos_coach.garmin.errors import translate_login_error, translate_resume_error
from ponos_coach.garmin.source import GarminActivitySource

DEFAULT_MAX_MFA_PROMPTS: Final = 3
_MAX_EMAIL_CHARS: Final = 320
_MAX_PASSWORD_CHARS: Final = 1024
_MAX_MFA_CODE_CHARS: Final = 32


class _MfaAbortError(Exception):
    """Interrompe il login dal punto in cui la libreria chiede il codice MFA."""


class _MfaPrompter:
    """Il codice MFA per la libreria: al più `max_prompts` richieste, annullabile.

    Ricorda perché ha interrotto il login (`cancelled`, `exhausted`), così l'errore della
    libreria che ne segue non si scambia per credenziali sbagliate o per rete assente.
    """

    def __init__(self, ask: Callable[[], str | None], max_prompts: int) -> None:
        self._ask = ask
        self._remaining = max_prompts
        self.cancelled = False
        self.exhausted = False

    def __call__(self) -> str:
        if self._remaining <= 0:
            self.exhausted = True
            raise _MfaAbortError
        self._remaining -= 1
        code = self._ask()
        if code is None:
            self.cancelled = True
            raise _MfaAbortError
        code = code.strip()
        if not 0 < len(code) <= _MAX_MFA_CODE_CHARS:
            self.exhausted = True  # un codice vuoto o enorme non si manda a Garmin
            raise _MfaAbortError
        return code


def _is_json_object(text: str) -> bool:
    """Vero se il testo è un oggetto JSON (i token sono un oggetto, mai un percorso)."""
    try:
        return isinstance(json.loads(text), dict)
    except (ValueError, RecursionError):
        return False


class GarminConnectAccount:
    """`GarminAccount` su un `GarminClientFactory` (in produzione `ConnectClientFactory`).

    Può sollevare, oltre agli errori della porta, `DefinitiveSourceError` se il client non dà
    token utilizzabili dopo un accesso riuscito.
    """

    def __init__(
        self, factory: GarminClientFactory, *, max_mfa_prompts: int = DEFAULT_MAX_MFA_PROMPTS
    ) -> None:
        if max_mfa_prompts < 1:
            raise ValueError("max_mfa_prompts deve essere almeno 1")
        self._factory = factory
        self._max_mfa_prompts = max_mfa_prompts

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """Vedi `GarminAccount.login`. Email o password vuote o enormi sono credenziali
        rifiutate senza contattare Garmin."""
        if not 0 < len(email) <= _MAX_EMAIL_CHARS or not 0 < len(password) <= _MAX_PASSWORD_CHARS:
            raise InvalidCredentialsError("credenziali non valide")
        prompter = _MfaPrompter(prompt_mfa_code, self._max_mfa_prompts)
        try:
            client = self._factory.login(email, password, prompter)
        except Exception as exc:
            if prompter.cancelled:
                raise MfaCancelledError("MFA annullato") from exc
            if prompter.exhausted:
                raise InvalidCredentialsError("codice MFA rifiutato") from exc
            raise translate_login_error(exc) from exc
        return self._session(client)

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """Vedi `GarminAccount.resume`. Token che non sono un oggetto JSON non arrivano alla
        libreria (potrebbe leggerli come percorso di un file)."""
        raw = tokens.reveal()
        if not _is_json_object(raw):
            raise AuthenticationRequiredError("token non riconosciuti")
        try:
            client = self._factory.resume(raw)
        except Exception as exc:
            raise translate_resume_error(exc) from exc
        return self._session(client)

    @staticmethod
    def _session(client: GarminClient) -> GarminSession:
        try:
            tokens = SessionTokens(client.dump_tokens())
        except Exception as exc:
            raise DefinitiveSourceError("token della sessione non utilizzabili") from exc
        return GarminSession(GarminActivitySource(client), tokens)
