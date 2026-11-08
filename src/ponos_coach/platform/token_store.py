# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin token store protected by the system (`TokenStore` port).

Windows: one file per profile in `<base>/secrets/<id>.bin`, encrypted with DPAPI (user scope) and
an entropy that binds the content to the app and to the profile id. The generic Credential Manager
accepts at most 2560 bytes, less than Garmin tokens. File format: magic `PNSEC`, version
(1 byte), DPAPI blob. The token text never touches the disk in the clear.

Ubuntu: Secret Service via `keyring` (service "PonosCoach", user = profile id).
An absent, locked or insecure (plaintext) backend is `TokenStoreUnavailableError`: never a
fallback to a plaintext file.

Declared readable by the user's own processes (decision of 2026-11-06).
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Final

import keyring
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
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
# UTF-8 at most 4 bytes per character, plus the DPAPI overhead (a few hundred bytes).
_MAX_FILE_BYTES: Final = len(_HEADER) + MAX_TOKEN_CHARS * 4 + 4096
_KEYRING_PREFIX: Final = "v1:"
_SECRET_SERVICE_MODULE: Final = "keyring.backends.SecretService"  # noqa: S105
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, not Literal: mypy does not drop a branch


def ensure_secrets_dir(base_dir: Path) -> Path:
    """The private `secrets` folder under `base_dir`, created if missing.

    Raises:
        FileExistsError: `secrets` exists and is not a folder.
        FolderUnavailableError: private permissions not applicable.
        OSError: creation failed.
    """
    secrets_dir = base_dir / SECRETS_FOLDER_NAME
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        make_private_directory(secrets_dir)
    except FileExistsError:
        if not secrets_dir.is_dir():
            raise
    return secrets_dir


class DpapiTokenStore:
    """`TokenStore` for Windows: per-profile files encrypted with DPAPI.

    Args:
        base_dir: local data folder of the app; contains the private subfolder `secrets`.
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
        ensure_secrets_dir(self._base_dir)

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """See `TokenStore.save`."""
        try:
            blob = windows_api.protect_data(
                tokens.reveal().encode("utf-8"), self._entropy(profile_id)
            )
            self._ensure_secrets_dir()
            write_file_atomically(self._file(profile_id), _HEADER + blob)
        except OSError as exc:
            raise TokenStoreUnavailableError("tokens not saved") from exc

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """See `TokenStore.load`."""
        try:
            with self._file(profile_id).open("rb") as handle:
                content = handle.read(_MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TokenStoreUnavailableError("tokens not readable") from exc
        if len(content) > _MAX_FILE_BYTES or not content.startswith(_HEADER):
            raise TokenStoreCorruptError("token file not recognized")
        try:
            plain = windows_api.unprotect_data(content[len(_HEADER) :], self._entropy(profile_id))
            return SessionTokens(plain.decode("utf-8"))
        except (OSError, ValueError) as exc:  # altered blob, other user, invalid text
            raise TokenStoreCorruptError("tokens cannot be decrypted") from exc

    def delete(self, profile_id: ProfileId) -> None:
        """See `TokenStore.delete`."""
        try:
            self._file(profile_id).unlink(missing_ok=True)
        except OSError as exc:
            raise TokenStoreUnavailableError("tokens not deleted") from exc


class KeyringTokenStore:
    """`TokenStore` on a `keyring` backend already verified as secure (Secret Service).

    Args:
        backend: the backend; whoever builds it guarantees its security (see
            `create_token_store`).
    """

    def __init__(self, backend: KeyringBackend) -> None:
        self._backend = backend

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """See `TokenStore.save`."""
        try:
            self._backend.set_password(
                KEYRING_SERVICE, str(profile_id), _KEYRING_PREFIX + tokens.reveal()
            )
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("tokens not saved") from exc

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """See `TokenStore.load`."""
        try:
            stored = self._backend.get_password(KEYRING_SERVICE, str(profile_id))
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("tokens not readable") from exc
        if stored is None:
            return None
        if not stored.startswith(_KEYRING_PREFIX):
            raise TokenStoreCorruptError("token content not recognized")
        try:
            return SessionTokens(stored[len(_KEYRING_PREFIX) :])
        except ValueError as exc:
            raise TokenStoreCorruptError("invalid tokens") from exc

    def delete(self, profile_id: ProfileId) -> None:
        """See `TokenStore.delete`."""
        try:
            with contextlib.suppress(PasswordDeleteError):  # already absent: no error
                self._backend.delete_password(KEYRING_SERVICE, str(profile_id))
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TokenStoreUnavailableError("tokens not deleted") from exc


def _is_secret_service(backend: KeyringBackend) -> bool:
    return type(backend).__module__ == _SECRET_SERVICE_MODULE and backend.priority > 0


def secret_service_backend() -> KeyringBackend:
    """The active `keyring` backend, if it is the Secret Service and works.

    If `keyring` chose the `ChainerBackend` (several backends usable together), the Secret
    Service one is looked for among them and used directly: never another one in the chain.

    Raises:
        TokenStoreUnavailableError: backend absent, plaintext or of another kind.
    """
    try:
        active = keyring.get_keyring()
        candidates = list(active.backends) if isinstance(active, ChainerBackend) else [active]
        found = next((item for item in candidates if _is_secret_service(item)), None)
    except (KeyringError, RuntimeError, OSError) as exc:
        raise TokenStoreUnavailableError("Secret Service not available") from exc
    if found is None:
        raise TokenStoreUnavailableError("Secret Service not available")
    return found


def create_token_store(base_dir: Path) -> TokenStore:
    """The platform's token store (single point of choice).

    Args:
        base_dir: local data folder of the app (used only on Windows), for example
            `platformdirs.user_data_dir("PonosCoach", appauthor=False)`.

    Raises:
        TokenStoreUnavailableError: on Linux, no usable Secret Service.
    """
    if _IS_WINDOWS:
        return DpapiTokenStore(base_dir)
    return KeyringTokenStore(secret_service_backend())
