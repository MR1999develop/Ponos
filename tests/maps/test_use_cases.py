# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tile proxy and key settings: cache first, one attempt, cool-downs, fallback, T-MAP-10/11."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest

from ponos_coach.maps.domain import CacheLimit, MapSettings, TileAddress, TileAddressError, TileKey
from ponos_coach.maps.ports import (
    TileCacheError,
    TileImage,
    TileKeyStoreCorruptError,
    TileKeyStoreUnavailableError,
    TileProblem,
    TileUnavailable,
)
from ponos_coach.maps.use_cases import COOLDOWN_S, KeyCheck, TileAnswer, TileKeySettings, TileProxy
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, InMemoryTileKeyStore, make_png

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
GOOD = TileKey(FakeTileSource.ACCEPTED_KEY)
BAD = TileKey("chiave-sbagliata-0123456")
HERE = TileAddress(12, 2200, 1500)


class Clocks:
    """A UTC clock and a monotonic clock that only move when told."""

    def __init__(self) -> None:
        self.utc = NOW
        self.monotonic = 1000.0

    def advance(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)
        self.monotonic += seconds


class Rig:
    """A proxy with a fake source, a fake cache and a fake key store."""

    def __init__(self, *, key: TileKey | None = GOOD, script=None) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        self.clocks = Clocks()
        self.keys = InMemoryTileKeyStore(key)
        self.source = FakeTileSource(self.keys, script)
        self.cache = InMemoryTileCache()
        self.proxy = TileProxy(
            self.source, self.cache, lambda: self.clocks.utc, lambda: self.clocks.monotonic
        )
        self.settings = TileKeySettings(self.keys, self.source, self.proxy)


def test_a_tile_is_fetched_once_and_then_served_from_the_cache() -> None:
    rig = Rig()

    first = rig.proxy.tile(HERE, "it")
    second = rig.proxy.tile(HERE, "it")

    assert first == second == TileAnswer(rig.source.png, None)
    assert len(rig.source.calls) == 1
    assert rig.source.calls[0] == (12, 2200, 1500, "it")


def test_the_language_is_part_of_the_cache_key() -> None:
    rig = Rig()

    rig.proxy.tile(HERE, "it")
    rig.proxy.tile(HERE, "en")
    rig.proxy.tile(HERE, "de")  # no labels in German: the neutral style

    assert [call[3] for call in rig.source.calls] == ["it", "en", "__"]


def test_t_map_11_an_expired_tile_is_renewed() -> None:
    rig = Rig()
    rig.proxy.tile(HERE, "en")
    rig.clocks.advance(rig.source.ttl_s + 1)

    rig.proxy.tile(HERE, "en")

    assert len(rig.source.calls) == 2


def test_t_map_11_a_tile_is_still_served_from_the_cache_before_it_expires() -> None:
    rig = Rig()
    rig.proxy.tile(HERE, "en")
    rig.clocks.advance(rig.source.ttl_s - 1)

    rig.proxy.tile(HERE, "en")

    assert len(rig.source.calls) == 1


def test_t_map_11_only_the_tile_asked_for_is_fetched_no_prefetch() -> None:
    rig = Rig()

    rig.proxy.tile(HERE, "en")

    assert rig.source.calls == [(12, 2200, 1500, "en")]


def test_t_map_11_clearing_the_cache_takes_it_to_zero_and_the_next_tile_is_fetched_again() -> None:
    rig = Rig()
    rig.proxy.tile(HERE, "en")
    assert rig.cache.used_bytes() > 0

    rig.proxy.clear_cache()

    assert rig.cache.used_bytes() == 0
    rig.proxy.tile(HERE, "en")
    assert len(rig.source.calls) == 2


def test_t_map_10_without_a_key_the_answer_is_the_problem_and_no_tile() -> None:
    rig = Rig(key=None)

    answer = rig.proxy.tile(HERE, "en")

    assert answer == TileAnswer(None, TileProblem.NO_KEY)


@pytest.mark.parametrize(
    "problem",
    [
        TileProblem.KEY_REJECTED,
        TileProblem.NETWORK_UNAVAILABLE,
        TileProblem.CREDIT_EXHAUSTED,
        TileProblem.INVALID_RESPONSE,
    ],
)
def test_t_map_10_each_problem_is_reported_and_asked_again_only_after_its_cool_down(
    problem: TileProblem,
) -> None:
    rig = Rig(script=[TileUnavailable(problem), TileImage(make_png(), 3600)])

    first = rig.proxy.tile(HERE, "en")
    during = rig.proxy.tile(TileAddress(12, 2201, 1500), "en")
    rig.clocks.advance(COOLDOWN_S[problem] + 1)
    after = rig.proxy.tile(TileAddress(12, 2202, 1500), "en")

    assert first == TileAnswer(None, problem)
    assert during == TileAnswer(None, problem)
    assert len(rig.source.calls) == 2  # the one that failed and the one after the cool-down
    assert after.problem is None
    assert after.png is not None


def test_t_map_10_a_page_that_wants_many_tiles_does_not_hammer_a_refusing_provider() -> None:
    rig = Rig(script=[TileUnavailable(TileProblem.KEY_REJECTED)])

    answers = [rig.proxy.tile(TileAddress(14, x, 5000), "en") for x in range(100)]

    assert len(rig.source.calls) == 1
    assert all(a == TileAnswer(None, TileProblem.KEY_REJECTED) for a in answers)


def test_t_map_10_an_expired_tile_is_served_with_the_problem_when_offline() -> None:
    rig = Rig(script=[TileImage(make_png(), 60), TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)])
    rig.proxy.tile(HERE, "en")
    rig.clocks.advance(120)

    answer = rig.proxy.tile(HERE, "en")

    assert answer.png is not None  # better an old tile than a grey square
    assert answer.problem is TileProblem.NETWORK_UNAVAILABLE


def test_changing_the_key_lifts_the_cool_down() -> None:
    rig = Rig(key=BAD)
    assert rig.proxy.tile(HERE, "en").problem is TileProblem.KEY_REJECTED

    rig.keys.key = GOOD
    rig.proxy.key_changed()

    assert rig.proxy.tile(TileAddress(12, 2201, 1500), "en").problem is None


def test_a_tile_with_no_reuse_time_is_served_but_not_kept() -> None:
    rig = Rig(script=[TileImage(make_png(), 0)])

    rig.proxy.tile(HERE, "en")
    rig.proxy.tile(HERE, "en")

    assert len(rig.source.calls) == 2
    assert rig.cache.used_bytes() == 0


def test_t_sec_15_a_path_that_is_not_a_tile_is_refused_before_cache_and_provider() -> None:
    rig = Rig()

    for bad in ("/12/2200", "/99/0/0", "/1/2/3?key=x", "/../../etc/passwd"):
        with pytest.raises(TileAddressError):
            rig.proxy.serve(bad, "en")

    assert rig.source.calls == []


def test_serve_reads_the_path_the_page_writes() -> None:
    rig = Rig()

    answer = rig.proxy.serve("/12/2200/1500.png", "it")

    assert answer.png == rig.source.png
    assert rig.source.calls == [(12, 2200, 1500, "it")]


class BrokenCache(InMemoryTileCache):
    """A cache whose every read and write fails."""

    def get(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")

    def put(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")


def test_a_cache_that_cannot_be_used_does_not_stop_the_map() -> None:
    rig = Rig()
    rig.proxy = TileProxy(
        rig.source, BrokenCache(), lambda: rig.clocks.utc, lambda: rig.clocks.monotonic
    )

    answer = rig.proxy.tile(HERE, "en")

    assert answer == TileAnswer(rig.source.png, None)


# --- Key settings ---------------------------------------------------------------------------


def test_a_good_key_is_saved_and_reported_accepted() -> None:
    rig = Rig(key=None)

    outcome = rig.settings.set_key(f"  {FakeTileSource.ACCEPTED_KEY}\n")

    assert outcome is KeyCheck.ACCEPTED
    assert rig.keys.key == GOOD
    assert rig.settings.settings(CacheLimit(10**9)) == MapSettings(True, CacheLimit(10**9))


def test_a_rejected_key_is_not_kept_and_the_previous_one_comes_back() -> None:
    rig = Rig(key=GOOD)

    outcome = rig.settings.set_key(BAD.reveal())

    assert outcome is KeyCheck.REJECTED
    assert rig.keys.key == GOOD


def test_a_rejected_key_with_no_previous_one_leaves_none() -> None:
    rig = Rig(key=None)

    assert rig.settings.set_key(BAD.reveal()) is KeyCheck.REJECTED
    assert rig.keys.key is None
    assert rig.settings.settings(CacheLimit(10**9)).has_key is False


@pytest.mark.parametrize("text", ["", "corta", "con spazi dentro", "a&b=cdefghijk"])
def test_text_that_is_not_a_key_is_refused_and_nothing_is_saved_or_asked(text: str) -> None:
    rig = Rig(key=GOOD)

    outcome = rig.settings.set_key(text)

    assert outcome is KeyCheck.INVALID_FORMAT
    assert rig.keys.key == GOOD
    assert rig.source.calls == []


@pytest.mark.parametrize(
    "problem",
    [TileProblem.NETWORK_UNAVAILABLE, TileProblem.CREDIT_EXHAUSTED, TileProblem.INVALID_RESPONSE],
)
def test_when_the_provider_cannot_confirm_the_key_it_is_kept_and_the_user_is_told(
    problem: TileProblem,
) -> None:
    rig = Rig(key=None, script=[TileUnavailable(problem)])

    outcome = rig.settings.set_key(GOOD.reveal())

    assert outcome is KeyCheck.SAVED_UNVERIFIED
    assert rig.keys.key == GOOD


def test_removing_the_key_goes_back_to_the_neutral_background() -> None:
    rig = Rig(key=GOOD)
    rig.proxy.tile(HERE, "en")

    rig.settings.remove_key()

    assert rig.keys.key is None
    assert rig.settings.settings(CacheLimit(10**9)).has_key is False
    assert rig.proxy.tile(TileAddress(12, 2201, 1500), "en").problem is TileProblem.NO_KEY


def test_an_unreadable_saved_key_counts_as_no_key_in_the_settings() -> None:
    rig = Rig(key=GOOD)
    rig.keys.corrupt = True

    assert rig.settings.settings(CacheLimit(10**9)).has_key is False
    with pytest.raises(TileKeyStoreCorruptError):
        rig.keys.load()


def test_an_unavailable_key_store_is_reported_not_hidden() -> None:
    rig = Rig(key=GOOD)
    rig.keys.unavailable = True

    with pytest.raises(TileKeyStoreUnavailableError):
        rig.settings.set_key(GOOD.reveal())
    with pytest.raises(TileKeyStoreUnavailableError):
        rig.settings.settings(CacheLimit(10**9))
