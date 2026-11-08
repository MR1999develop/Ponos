# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tile key store protected by the system (`TileKeyStore` port).

Same archive as the Garmin tokens, a separate entry: Windows keeps one DPAPI-encrypted file
`<base>/secrets/tracestrack-key.bin` (user scope, with its own entropy, so a token file cannot be
read as the key or the reverse); Ubuntu keeps one Secret Service item (service "PonosCoach", user
"tracestrack-key"). The key is app-wide, not per profile, and never goes into a profile folder or
its backups. An absent, locked or insecure (plaintext) backend is `TileKeyStoreUnavailableError`:
never a fallback to a plaintext file. The key text never touches the disk in the clear.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Final

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from ponos_coach.fsio.durable import write_file_atomically
from ponos_coach.maps.domain import TileKey
from ponos_coach.maps.ports import (
    TileKeyStore,
    TileKeyStoreCorruptError,
    TileKeyStoreUnavailableError,
)
from ponos_coach.platform import windows_api
from ponos_coach.platform.token_store import (
    KEYRING_SERVICE,
    SECRETS_FOLDER_NAME,
    ensure_secrets_dir,
    secret_service_backend,
)
from ponos_coach.profiles.ports import TokenStoreUnavailableError

KEY_FILE_NAME: Final = "tracestrack-key.bin"
KEYRING_USER: Final = "tracestrack-key"

_MAGIC: Final = b"PNKEY"
_FORMAT_VERSION: Final = 1
_HEADER: Final = _MAGIC + bytes([_FORMAT_VERSION])
_ENTROPY: Final = b"PonosCoach/tile-key/v1"
_MAX_FILE_BYTES: Final = len(_HEADER) + 128 * 4 + 4096  # key text plus the DPAPI overhead
_KEYRING_PREFIX: Final = "v1:"
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, not Literal: mypy does not drop a branch


class DpapiTileKeyStore:
    """`TileKeyStore` for Windows: a file encrypted with DPAPI.

    Args:
        base_dir: local data folder of the app; contains the private subfolder `secrets`.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _file(self) -> Path:
        return self._base_dir / SECRETS_FOLDER_NAME / KEY_FILE_NAME

    def save(self, key: TileKey) -> None:
        """See `TileKeyStore.save`."""
        try:
            blob = windows_api.protect_data(key.reveal().encode("ascii"), _ENTROPY)
            ensure_secrets_dir(self._base_dir)
            write_file_atomically(self._file(), _HEADER + blob)
        except OSError as exc:
            raise TileKeyStoreUnavailableError("tile key not saved") from exc

    def load(self) -> TileKey | None:
        """See `TileKeyStore.load`."""
        try:
            with self._file().open("rb") as handle:
                content = handle.read(_MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TileKeyStoreUnavailableError("tile key not readable") from exc
        if len(content) > _MAX_FILE_BYTES or not content.startswith(_HEADER):
            raise TileKeyStoreCorruptError("tile key file not recognized")
        try:
            plain = windows_api.unprotect_data(content[len(_HEADER) :], _ENTROPY)
            return TileKey(plain.decode("ascii"))
        except (OSError, ValueError):  # altered blob, other user, invalid text
            raise TileKeyStoreCorruptError("tile key cannot be decrypted") from None

    def delete(self) -> None:
        """See `TileKeyStore.delete`."""
        try:
            self._file().unlink(missing_ok=True)
        except OSError as exc:
            raise TileKeyStoreUnavailableError("tile key not deleted") from exc


class KeyringTileKeyStore:
    """`TileKeyStore` on a `keyring` backend already verified as secure (Secret Service).

    Args:
        backend: the backend; whoever builds it guarantees its security.
    """

    def __init__(self, backend: KeyringBackend) -> None:
        self._backend = backend

    def save(self, key: TileKey) -> None:
        """See `TileKeyStore.save`."""
        try:
            self._backend.set_password(
                KEYRING_SERVICE, KEYRING_USER, _KEYRING_PREFIX + key.reveal()
            )
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TileKeyStoreUnavailableError("tile key not saved") from exc

    def load(self) -> TileKey | None:
        """See `TileKeyStore.load`."""
        try:
            stored = self._backend.get_password(KEYRING_SERVICE, KEYRING_USER)
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TileKeyStoreUnavailableError("tile key not readable") from exc
        if stored is None:
            return None
        if not stored.startswith(_KEYRING_PREFIX):
            raise TileKeyStoreCorruptError("tile key content not recognized")
        try:
            return TileKey(stored[len(_KEYRING_PREFIX) :])
        except ValueError:
            raise TileKeyStoreCorruptError("invalid tile key") from None

    def delete(self) -> None:
        """See `TileKeyStore.delete`."""
        try:
            with contextlib.suppress(PasswordDeleteError):  # already absent: no error
                self._backend.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except (KeyringError, RuntimeError, OSError) as exc:
            raise TileKeyStoreUnavailableError("tile key not deleted") from exc


def create_tile_key_store(base_dir: Path) -> TileKeyStore:
    """The platform's tile key store (single point of choice).

    Args:
        base_dir: local data folder of the app (used only on Windows), the same as for the
            token store.

    Raises:
        TileKeyStoreUnavailableError: on Linux, no usable Secret Service.
    """
    if _IS_WINDOWS:
        return DpapiTileKeyStore(base_dir)
    try:
        return KeyringTileKeyStore(secret_service_backend())
    except TokenStoreUnavailableError as exc:
        raise TileKeyStoreUnavailableError("Secret Service not available") from exc
