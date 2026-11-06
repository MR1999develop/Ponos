# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Archivio dei token di Garmin protetto dal sistema (porta `TokenStore`).

Windows: un file per profilo in `<base>/secrets/<id>.bin`, cifrato con DPAPI (ambito utente) e
un'entropia che lega il contenuto all'app e all'id del profilo. Il Credential Manager generico
accetta al massimo 2560 byte, meno dei token di Garmin. Formato del file: magic `PNSEC`, versione
(1 byte), blob DPAPI. Il testo dei token non tocca mai il disco in chiaro.

Ubuntu: Secret Service tramite `keyring` (servizio "PonosCoach", utente = id del profilo).
Un backend assente, bloccato o insicuro (in chiaro) è `TokenStoreUnavailableError`: mai un
ripiego su un file in chiaro.

Dichiarati leggibili dai processi dell'utente stesso (decisione del 2026-11-06).
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Final

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from ponos_coach.activities.domain import MAX_TOKEN_CHARS, SessionTokens
from ponos_coach.fsio.durable import write_file_atomically
from ponos_coach.platform import windows_api
from ponos_coach.platform.private_folder import make_private_directory
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import (
    TokenStore,
    TokenStoreCorruptError,
    TokenStoreUnavailableError,
)

KEYRING_SERVICE: Final = "PonosCoach"
SECRETS_FOLDER_NAME: Final = "secrets"

_MAGIC: Final = b"PNSEC"
_FORMAT_VERSION: Final = 1
_HEADER: Final = _MAGIC + bytes([_FORMAT_VERSION])
_ENTROPY_PREFIX: Final = b"PonosCoach/tokens/v1/"
# UTF-8 al massimo 4 byte per carattere, più l'overhead di DPAPI (poche centinaia di byte).
_MAX_FILE_BYTES: Final = len(_HEADER) + MAX_TOKEN_CHARS * 4 + 4096
_KEYRING_PREFIX: Final = "v1:"
_SECRET_SERVICE_MODULE: Final = "keyring.backends.SecretService"  # noqa: S105
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo


class DpapiTokenStore:
    """`TokenStore` per Windows: file per profilo cifrati con DPAPI.

    Args:
        base_dir: cartella dati locale dell'app; contiene la sottocartella privata `secrets`.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    @property
    def _secrets_dir(self) -> Path:
        return self._base_dir / SECRETS_FOLDER_NAME

    def _file(self, profile_id: ProfileId) -> Path:
        return self._secrets_dir / f"{profile_id}.bin"

    @staticmethod
    def _entropy(profile_id: ProfileId) -> bytes:
        return _ENTROPY_PREFIX + str(profile_id).encode("ascii")

    def _ensure_secrets_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        try:
            make_private_directory(self._secrets_dir)
        except FileExistsError:
            if not self._secrets_dir.is_dir():
                raise

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """Vedi `TokenStore.save`."""
        try:
            blob = windows_api.protect_data(
                tokens.reveal().encode("utf-8"), self._entropy(profile_id)
            )
            self._ensure_secrets_dir()
            write_file_atomically(self._file(profile_id), _HEADER + blob)
        except OSError as exc:
            raise TokenStoreUnavailableError("token non salvati") from exc

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """Vedi `TokenStore.load`."""
        try:
            with self._file(profile_id).open("rb") as handle:
                content = handle.read(_MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TokenStoreUnavailableError("token non leggibili") from exc
        if len(content) > _MAX_FILE_BYTES or not content.startswith(_HEADER):
            raise TokenStoreCorruptError("file dei token non riconosciuto")
        try:
            plain = windows_api.unprotect_data(content[len(_HEADER) :], self._entropy(profile_id))
            return SessionTokens(plain.decode("utf-8"))
        except (OSError, ValueError) as exc:  # blob alterato, altro utente, testo non valido
            raise TokenStoreCorruptError("token non decifrabili") from exc

    def delete(self, profile_id: ProfileId) -> None:
        """Vedi `TokenStore.delete`."""
        try:
            self._file(profile_id).unlink(missing_ok=True)
        except OSError as exc:
            raise TokenStoreUnavailableError("token non cancellati") from exc


class KeyringTokenStore:
    """`TokenStore` su un backend `keyring` già verificato come sicuro (Secret Service).

    Args:
        backend: il backend; chi costruisce ne garantisce la sicurezza (vedi
            `create_token_store`).
    """

    def __init__(self, backend: KeyringBackend) -> None:
        self._backend = backend

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """Vedi `TokenStore.save`."""
        try:
            self._backend.set_password(
                KEYRING_SERVICE, str(profile_id), _KEYRING_PREFIX + tokens.reveal()
            )
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("token non salvati") from exc

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """Vedi `TokenStore.load`."""
        try:
            stored = self._backend.get_password(KEYRING_SERVICE, str(profile_id))
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("token non leggibili") from exc
        if stored is None:
            return None
        if not stored.startswith(_KEYRING_PREFIX):
            raise TokenStoreCorruptError("contenuto dei token non riconosciuto")
        try:
            return SessionTokens(stored[len(_KEYRING_PREFIX) :])
        except ValueError as exc:
            raise TokenStoreCorruptError("token non validi") from exc

    def delete(self, profile_id: ProfileId) -> None:
        """Vedi `TokenStore.delete`."""
        try:
            with contextlib.suppress(PasswordDeleteError):  # già assente: nessun errore
                self._backend.delete_password(KEYRING_SERVICE, str(profile_id))
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("token non cancellati") from exc


def _secret_service_backend() -> KeyringBackend:
    """Il backend attivo di `keyring`, se è il Secret Service e funziona.

    Raises:
        TokenStoreUnavailableError: backend assente, in chiaro o di altro tipo.
    """
    try:
        backend = keyring.get_keyring()
        usable = type(backend).__module__ == _SECRET_SERVICE_MODULE and backend.priority > 0
    except (KeyringError, RuntimeError, OSError) as exc:
        raise TokenStoreUnavailableError("Secret Service non disponibile") from exc
    if not usable:
        raise TokenStoreUnavailableError("Secret Service non disponibile")
    return backend


def create_token_store(base_dir: Path) -> TokenStore:
    """L'archivio dei token della piattaforma (unico punto di scelta).

    Args:
        base_dir: cartella dati locale dell'app (usata solo su Windows), per esempio
            `platformdirs.user_data_dir("PonosCoach", appauthor=False)`.

    Raises:
        TokenStoreUnavailableError: su Linux, nessun Secret Service utilizzabile.
    """
    if _IS_WINDOWS:
        return DpapiTokenStore(base_dir)
    return KeyringTokenStore(_secret_service_backend())
