# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`TokenStore` reali: contratto, T-SEC-07 (parziale) e file danneggiati."""

from __future__ import annotations

import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from keyring.errors import PasswordDeleteError

from ponos_coach.platform import token_store as token_store_module
from ponos_coach.platform.token_store import (
    KEYRING_SERVICE,
    SECRETS_FOLDER_NAME,
    DpapiTokenStore,
    KeyringTokenStore,
    create_token_store,
)
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import (
    TokenStore,
    TokenStoreCorruptError,
    TokenStoreUnavailableError,
)
from tests.contracts.contract_tokens import PROFILE_A, PROFILE_B, TOKENS_A, TokenStoreContract
from tests.contracts.harness import TokenStoreHarness
from tests.platform.windows_dacl import dacl_sddl, is_protected

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI di Windows")


class _MemoryBackend(KeyringBackend):
    """Backend keyring in memoria, solo per i test."""

    priority = 1

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise PasswordDeleteError("assente")
        del self.items[(service, username)]


class _ChainOf(ChainerBackend):
    """Un `ChainerBackend` con i backend dati, senza cercarli nel sistema."""

    def __init__(self, backends: list[KeyringBackend]) -> None:
        self._given = backends

    @property
    def backends(self) -> list[KeyringBackend]:
        return self._given


def _secret_file(base: Path, profile_id: ProfileId = PROFILE_A) -> Path:
    return base / SECRETS_FOLDER_NAME / f"{profile_id}.bin"


class _DpapiHarness:
    def __init__(self, base: Path) -> None:
        self.base = base
        self._store = DpapiTokenStore(base)

    @property
    def store(self) -> TokenStore:
        return self._store

    def reopen(self) -> TokenStore:
        return DpapiTokenStore(self.base)

    def corrupt(self, profile_id: ProfileId) -> None:
        path = _secret_file(self.base, profile_id)
        data = bytearray(path.read_bytes())
        data[-5] ^= 0xFF
        path.write_bytes(bytes(data))


class _KeyringHarness:
    def __init__(self) -> None:
        self.backend = _MemoryBackend()
        self._store = KeyringTokenStore(self.backend)

    @property
    def store(self) -> TokenStore:
        return self._store

    def reopen(self) -> TokenStore:
        return KeyringTokenStore(self.backend)

    def corrupt(self, profile_id: ProfileId) -> None:
        self.backend.items[(KEYRING_SERVICE, str(profile_id))] = "contenuto-estraneo"


@windows_only
class TestDpapiTokenStore(TokenStoreContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> TokenStoreHarness:
        return _DpapiHarness(tmp_path / "data")


class TestKeyringTokenStore(TokenStoreContract):
    @pytest.fixture
    def harness(self) -> TokenStoreHarness:
        return _KeyringHarness()


@windows_only
class TestDpapiFiles:
    def test_no_file_under_base_contains_the_token_text(self, tmp_path: Path) -> None:
        store = DpapiTokenStore(tmp_path)

        store.save(PROFILE_A, TOKENS_A)

        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert files
        needle = TOKENS_A.reveal().encode()
        assert all(needle not in p.read_bytes() for p in files)

    def test_delete_removes_the_file(self, tmp_path: Path) -> None:
        store = DpapiTokenStore(tmp_path)
        store.save(PROFILE_A, TOKENS_A)

        store.delete(PROFILE_A)

        assert not _secret_file(tmp_path).exists()
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    @pytest.mark.parametrize(
        "content",
        [b"", b"PNSEC", b"XXXXX\x01" + b"a" * 64, b"PNSEC\x02" + b"a" * 64, b"PNSEC\x01"],
        ids=["empty", "magic-only", "wrong-magic", "wrong-version", "no-blob"],
    )
    def test_bad_file_is_corrupt(self, tmp_path: Path, content: bytes) -> None:
        store = DpapiTokenStore(tmp_path)
        store.save(PROFILE_A, TOKENS_A)
        _secret_file(tmp_path).write_bytes(content)

        with pytest.raises(TokenStoreCorruptError):
            store.load(PROFILE_A)

    def test_oversized_file_is_corrupt(self, tmp_path: Path) -> None:
        store = DpapiTokenStore(tmp_path)
        store.save(PROFILE_A, TOKENS_A)
        _secret_file(tmp_path).write_bytes(b"PNSEC\x01" + b"a" * 1_000_000)

        with pytest.raises(TokenStoreCorruptError):
            store.load(PROFILE_A)

    def test_file_copied_to_another_profile_is_corrupt(self, tmp_path: Path) -> None:
        store = DpapiTokenStore(tmp_path)
        store.save(PROFILE_A, TOKENS_A)
        _secret_file(tmp_path, PROFILE_B).write_bytes(_secret_file(tmp_path).read_bytes())

        with pytest.raises(TokenStoreCorruptError):
            store.load(PROFILE_B)

    def test_secrets_folder_has_a_private_dacl(self, tmp_path: Path) -> None:
        DpapiTokenStore(tmp_path).save(PROFILE_A, TOKENS_A)

        assert is_protected(dacl_sddl(tmp_path / SECRETS_FOLDER_NAME))

    def test_unwritable_base_is_unavailable(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_bytes(b"x")

        with pytest.raises(TokenStoreUnavailableError):
            DpapiTokenStore(blocker / "sub").save(PROFILE_A, TOKENS_A)


class TestPlatformChoice:
    def test_plain_backend_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyring, "get_keyring", _MemoryBackend)
        monkeypatch.setattr(token_store_module, "_IS_WINDOWS", False)

        with pytest.raises(TokenStoreUnavailableError):
            create_token_store(Path())

    def test_a_chainer_backend_is_searched_for_the_secret_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret_service = type(
            "Backend", (_MemoryBackend,), {"__module__": "keyring.backends.SecretService"}
        )()
        chain = _ChainOf([_MemoryBackend(), secret_service])
        monkeypatch.setattr(keyring, "get_keyring", lambda: chain)
        monkeypatch.setattr(token_store_module, "_IS_WINDOWS", False)

        store = create_token_store(Path())

        store.save(PROFILE_A, TOKENS_A)
        assert store.load(PROFILE_A) == TOKENS_A
        assert secret_service.items  # ha scritto il Secret Service, non l'altro backend

    def test_a_chainer_backend_without_the_secret_service_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(keyring, "get_keyring", lambda: _ChainOf([_MemoryBackend()]))
        monkeypatch.setattr(token_store_module, "_IS_WINDOWS", False)

        with pytest.raises(TokenStoreUnavailableError):
            create_token_store(Path())

    def test_windows_gets_dpapi(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(token_store_module, "_IS_WINDOWS", True)

        assert isinstance(create_token_store(tmp_path), DpapiTokenStore)

    def test_backend_failure_is_unavailable(self) -> None:
        class _Failing(_MemoryBackend):
            def get_password(self, service: str, username: str) -> str | None:  # noqa: ARG002
                raise RuntimeError("bloccato")

        with pytest.raises(TokenStoreUnavailableError):
            KeyringTokenStore(_Failing()).load(PROFILE_A)
