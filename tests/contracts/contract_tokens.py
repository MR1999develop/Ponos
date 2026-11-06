# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contratto di `TokenStore`: token di Garmin per profilo.

Si prova con la fixture `harness` (`TokenStoreHarness`): ogni implementazione, fake o reale,
dice come rendere illeggibile il contenuto di un profilo e come simulare un riavvio dell'app.
"""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import TokenStoreCorruptError
from tests.contracts.harness import TokenStoreHarness

PROFILE_A = ProfileId.parse("11111111-1111-4111-8111-111111111111")
PROFILE_B = ProfileId.parse("22222222-2222-4222-8222-222222222222")
TOKENS_A = SessionTokens("TOKEN-SEGNAPOSTO-A-" + "a" * 40)
TOKENS_B = SessionTokens("TOKEN-SEGNAPOSTO-B-" + "b" * 40)
# Più dei 2560 byte che il Credential Manager generico di Windows accetta: i token veri di
# Garmin sono di questa taglia.
BIG_TOKENS = SessionTokens("TOKEN-SEGNAPOSTO-GRANDE-" + "x" * 8000)


class TokenStoreContract:
    """Salvare, caricare e cancellare i token di un profilo."""

    def test_profile_without_tokens_gives_none(self, harness: TokenStoreHarness) -> None:
        assert harness.store.load(PROFILE_A) is None

    def test_saved_tokens_are_loaded_back_unchanged(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)

        assert harness.store.load(PROFILE_A) == TOKENS_A

    def test_tokens_larger_than_a_generic_credential_are_kept(
        self, harness: TokenStoreHarness
    ) -> None:
        harness.store.save(PROFILE_A, BIG_TOKENS)

        assert harness.store.load(PROFILE_A) == BIG_TOKENS

    def test_saving_again_replaces_the_tokens(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.store.save(PROFILE_A, TOKENS_B)

        assert harness.store.load(PROFILE_A) == TOKENS_B

    def test_each_profile_has_its_own_tokens(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)

        assert harness.store.load(PROFILE_B) is None
        harness.store.save(PROFILE_B, TOKENS_B)
        assert harness.store.load(PROFILE_A) == TOKENS_A
        assert harness.store.load(PROFILE_B) == TOKENS_B

    def test_delete_removes_only_that_profile(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.store.save(PROFILE_B, TOKENS_B)

        harness.store.delete(PROFILE_A)

        assert harness.store.load(PROFILE_A) is None
        assert harness.store.load(PROFILE_B) == TOKENS_B

    def test_delete_without_tokens_is_not_an_error(self, harness: TokenStoreHarness) -> None:
        harness.store.delete(PROFILE_A)
        harness.store.delete(PROFILE_A)

    def test_tokens_survive_a_restart(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)

        assert harness.reopen().load(PROFILE_A) == TOKENS_A

    def test_deleted_tokens_stay_deleted_after_a_restart(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.store.delete(PROFILE_A)

        assert harness.reopen().load(PROFILE_A) is None

    def test_unreadable_content_is_reported_not_returned(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.corrupt(PROFILE_A)

        with pytest.raises(TokenStoreCorruptError):
            harness.store.load(PROFILE_A)

    def test_unreadable_content_is_replaced_by_a_new_save(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.corrupt(PROFILE_A)

        harness.store.save(PROFILE_A, TOKENS_B)

        assert harness.store.load(PROFILE_A) == TOKENS_B

    def test_unreadable_content_can_be_deleted(self, harness: TokenStoreHarness) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.corrupt(PROFILE_A)

        harness.store.delete(PROFILE_A)

        assert harness.store.load(PROFILE_A) is None

    def test_corrupt_content_of_one_profile_does_not_affect_another(
        self, harness: TokenStoreHarness
    ) -> None:
        harness.store.save(PROFILE_A, TOKENS_A)
        harness.store.save(PROFILE_B, TOKENS_B)

        harness.corrupt(PROFILE_A)

        assert harness.store.load(PROFILE_B) == TOKENS_B
