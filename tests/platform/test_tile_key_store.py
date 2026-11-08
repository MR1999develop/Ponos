# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Real `TileKeyStore`s: contract, separation from the Garmin tokens, damaged content (T-SEC-15)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend

from ponos_coach.maps.ports import (
    TileKeyStore,
    TileKeyStoreCorruptError,
    TileKeyStoreUnavailableError,
)
from ponos_coach.platform import tile_key_store as module
from ponos_coach.platform.tile_key_store import (
    KEY_FILE_NAME,
    KEYRING_USER,
    DpapiTileKeyStore,
    KeyringTileKeyStore,
    create_tile_key_store,
)
from ponos_coach.platform.token_store import (
    KEYRING_SERVICE,
    SECRETS_FOLDER_NAME,
    DpapiTokenStore,
)
from tests.contracts.contract_tiles import KEY_A, TileKeyStoreContract
from tests.contracts.contract_tokens import PROFILE_A, TOKENS_A
from tests.fakes.tiles import InMemoryTileKeyStore
from tests.platform.test_token_store import _MemoryBackend
from tests.platform.windows_dacl import dacl_sddl, is_protected

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")


class TestInMemoryTileKeyStore(TileKeyStoreContract):
    @pytest.fixture
    def key_store(self) -> TileKeyStore:
        return InMemoryTileKeyStore()


class TestKeyringTileKeyStore(TileKeyStoreContract):
    @pytest.fixture
    def key_store(self) -> TileKeyStore:
        return KeyringTileKeyStore(_MemoryBackend())

    def test_the_key_has_its_own_entry_next_to_the_profile_tokens(self) -> None:
        backend = _MemoryBackend()
        KeyringTileKeyStore(backend).save(KEY_A)

        assert set(backend.items) == {(KEYRING_SERVICE, KEYRING_USER)}
        assert backend.items[(KEYRING_SERVICE, KEYRING_USER)].endswith(KEY_A.reveal())

    def test_content_that_is_not_a_key_is_corrupt(self) -> None:
        backend = _MemoryBackend()
        backend.items[(KEYRING_SERVICE, KEYRING_USER)] = "contenuto-estraneo"

        with pytest.raises(TileKeyStoreCorruptError):
            KeyringTileKeyStore(backend).load()

    def test_a_stored_text_with_the_right_prefix_but_not_a_key_is_corrupt(self) -> None:
        backend = _MemoryBackend()
        backend.items[(KEYRING_SERVICE, KEYRING_USER)] = "v1:con spazio e ?"

        with pytest.raises(TileKeyStoreCorruptError):
            KeyringTileKeyStore(backend).load()

    def test_a_failing_backend_is_unavailable(self) -> None:
        class Broken(KeyringBackend):
            priority = 1

            def get_password(self, _service: str, _username: str) -> str | None:
                raise OSError("simulated")

            def set_password(self, _service: str, _username: str, _password: str) -> None:
                raise OSError("simulated")

            def delete_password(self, _service: str, _username: str) -> None:
                raise OSError("simulated")

        store = KeyringTileKeyStore(Broken())  # type: ignore[no-untyped-call]
        with pytest.raises(TileKeyStoreUnavailableError):
            store.load()
        with pytest.raises(TileKeyStoreUnavailableError):
            store.save(KEY_A)
        with pytest.raises(TileKeyStoreUnavailableError):
            store.delete()


@windows_only
class TestDpapiTileKeyStore(TileKeyStoreContract):
    @pytest.fixture
    def key_store(self, tmp_path: Path) -> TileKeyStore:
        return DpapiTileKeyStore(tmp_path / "data")

    def test_no_file_under_base_contains_the_key_text(self, tmp_path: Path) -> None:
        DpapiTileKeyStore(tmp_path).save(KEY_A)

        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert [p.name for p in files] == [KEY_FILE_NAME]
        assert all(KEY_A.reveal().encode() not in p.read_bytes() for p in files)

    def test_the_secrets_folder_is_private(self, tmp_path: Path) -> None:
        DpapiTileKeyStore(tmp_path).save(KEY_A)

        assert is_protected(dacl_sddl(tmp_path / SECRETS_FOLDER_NAME))

    def test_the_key_and_the_garmin_tokens_live_side_by_side_and_do_not_mix(
        self, tmp_path: Path
    ) -> None:
        keys, tokens = DpapiTileKeyStore(tmp_path), DpapiTokenStore(tmp_path)
        keys.save(KEY_A)
        tokens.save(PROFILE_A, TOKENS_A)

        assert keys.load() == KEY_A
        assert tokens.load(PROFILE_A) == TOKENS_A
        keys.delete()
        assert tokens.load(PROFILE_A) == TOKENS_A  # deleting the key leaves the tokens alone
        # the token file, copied over the key file, is not read as a key (own entropy)
        tokens_file = tmp_path / SECRETS_FOLDER_NAME / f"{PROFILE_A}.bin"
        (tmp_path / SECRETS_FOLDER_NAME / KEY_FILE_NAME).write_bytes(tokens_file.read_bytes())
        with pytest.raises(TileKeyStoreCorruptError):
            keys.load()

    @pytest.mark.parametrize(
        "content",
        [b"", b"PNKEY", b"XXXXX\x01" + b"a" * 64, b"PNKEY\x02" + b"a" * 64, b"PNKEY\x01"],
        ids=["empty", "magic-only", "wrong-magic", "wrong-version", "no-blob"],
    )
    def test_a_bad_file_is_corrupt(self, tmp_path: Path, content: bytes) -> None:
        store = DpapiTileKeyStore(tmp_path)
        store.save(KEY_A)
        (tmp_path / SECRETS_FOLDER_NAME / KEY_FILE_NAME).write_bytes(content)

        with pytest.raises(TileKeyStoreCorruptError):
            store.load()

    def test_an_oversized_file_is_corrupt_without_reading_it_all(self, tmp_path: Path) -> None:
        store = DpapiTileKeyStore(tmp_path)
        store.save(KEY_A)
        (tmp_path / SECRETS_FOLDER_NAME / KEY_FILE_NAME).write_bytes(
            b"PNKEY\x01" + b"a" * 1_000_000
        )

        with pytest.raises(TileKeyStoreCorruptError):
            store.load()

    def test_an_unwritable_base_is_unavailable(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_bytes(b"x")

        with pytest.raises(TileKeyStoreUnavailableError):
            DpapiTileKeyStore(blocker / "sub").save(KEY_A)


def test_the_platform_choice_is_the_secret_service_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_service = type(
        "Backend", (_MemoryBackend,), {"__module__": "keyring.backends.SecretService"}
    )()
    monkeypatch.setattr("keyring.get_keyring", lambda: secret_service)
    monkeypatch.setattr(module, "_IS_WINDOWS", False)

    store = create_tile_key_store(Path())

    assert isinstance(store, KeyringTileKeyStore)


def test_a_plain_backend_is_unavailable_never_a_plaintext_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("keyring.get_keyring", _MemoryBackend)
    monkeypatch.setattr(module, "_IS_WINDOWS", False)

    with pytest.raises(TileKeyStoreUnavailableError):
        create_tile_key_store(Path())
