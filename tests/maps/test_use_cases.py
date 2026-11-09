# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tile proxy: cache first, one attempt, conditional renewal, cool-downs, fallback, T-MAP-10/11."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest

from ponos_coach.maps.domain import TileAddress, TileAddressError
from ponos_coach.maps.ports import (
    NO_VALIDATORS,
    TileCacheError,
    TileImage,
    TileNotModified,
    TileProblem,
    TileResult,
    TileUnavailable,
    TileValidators,
)
from ponos_coach.maps.use_cases import COOLDOWN_S, TileAnswer, TileProxy
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, make_png

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
HERE = TileAddress(12, 2200, 1500)
VALIDATORS = TileValidators('"abc123"', "Tue, 03 Nov 2026 10:00:00 GMT")


class Clocks:
    """A UTC clock and a monotonic clock that only move when told."""

    def __init__(self) -> None:
        self.utc = NOW
        self.monotonic = 1000.0

    def advance(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)
        self.monotonic += seconds


class Rig:
    """A proxy with a fake source and a fake cache."""

    def __init__(self, script: list[TileResult] | None = None) -> None:
        self.clocks = Clocks()
        self.source = FakeTileSource(script)
        self.cache = InMemoryTileCache()
        self.proxy = TileProxy(
            self.source, self.cache, lambda: self.clocks.utc, lambda: self.clocks.monotonic
        )


def test_a_tile_is_fetched_once_and_then_served_from_the_cache() -> None:
    rig = Rig()

    first = rig.proxy.tile(HERE)
    second = rig.proxy.tile(HERE)

    assert first == second == TileAnswer(rig.source.png, None)
    assert rig.source.calls == [(12, 2200, 1500, NO_VALIDATORS)]


def test_t_map_11_an_expired_tile_is_renewed() -> None:
    rig = Rig()
    rig.proxy.tile(HERE)
    rig.clocks.advance(rig.source.ttl_s + 1)

    rig.proxy.tile(HERE)

    assert len(rig.source.calls) == 2


def test_t_map_11_a_tile_is_still_served_from_the_cache_before_it_expires() -> None:
    rig = Rig()
    rig.proxy.tile(HERE)
    rig.clocks.advance(rig.source.ttl_s - 1)

    rig.proxy.tile(HERE)

    assert len(rig.source.calls) == 1


def test_t_map_11_only_the_tile_asked_for_is_fetched_no_prefetch() -> None:
    rig = Rig()

    rig.proxy.tile(HERE)

    assert rig.source.calls == [(12, 2200, 1500, NO_VALIDATORS)]


def test_t_map_11_clearing_the_cache_takes_it_to_zero_and_the_next_tile_is_fetched_again() -> None:
    rig = Rig()
    rig.proxy.tile(HERE)
    assert rig.cache.used_bytes() > 0

    rig.proxy.clear_cache()

    assert rig.cache.used_bytes() == 0
    rig.proxy.tile(HERE)
    assert len(rig.source.calls) == 2


# --- Conditional renewal --------------------------------------------------------------------


def test_an_expired_tile_is_asked_about_with_the_validators_it_was_stored_with() -> None:
    png = make_png()
    rig = Rig([TileImage(png, 60, VALIDATORS), TileNotModified(60)])
    rig.proxy.tile(HERE)
    rig.clocks.advance(61)

    rig.proxy.tile(HERE)

    assert rig.source.calls[1] == (12, 2200, 1500, VALIDATORS)


def test_a_not_modified_answer_serves_the_stored_tile_and_renews_its_expiry() -> None:
    png = make_png()
    rig = Rig([TileImage(png, 60, VALIDATORS), TileNotModified(7 * 86_400)])
    rig.proxy.tile(HERE)
    rig.clocks.advance(61)

    renewed = rig.proxy.tile(HERE)
    rig.clocks.advance(7 * 86_400 - 1)
    later = rig.proxy.tile(HERE)

    assert renewed == TileAnswer(png, None)
    assert later == TileAnswer(png, None)
    assert len(rig.source.calls) == 2  # the renewed copy is fresh for the whole new lifetime


def test_a_new_image_after_a_conditional_request_replaces_the_stored_one() -> None:
    old, new = make_png(), make_png(extra_bytes=500)
    rig = Rig([TileImage(old, 60, VALIDATORS), TileImage(new, 60, TileValidators('"new"', None))])
    rig.proxy.tile(HERE)
    rig.clocks.advance(61)

    answer = rig.proxy.tile(HERE)

    assert answer == TileAnswer(new, None)
    found = rig.cache.get(HERE, rig.clocks.utc)
    assert found is not None
    assert found.png == new
    assert found.validators == TileValidators('"new"', None)


def test_a_not_modified_answer_nobody_asked_for_is_a_bad_answer_not_a_tile() -> None:
    rig = Rig([TileNotModified(60)])  # nothing in the cache to confirm

    answer = rig.proxy.tile(HERE)

    assert answer == TileAnswer(None, TileProblem.INVALID_RESPONSE)


# --- Problems and cool-downs ----------------------------------------------------------------


@pytest.mark.parametrize("problem", [TileProblem.REFUSED, TileProblem.NETWORK_UNAVAILABLE])
def test_t_map_10_each_problem_is_reported_and_asked_again_only_after_its_cool_down(
    problem: TileProblem,
) -> None:
    rig = Rig([TileUnavailable(problem), TileImage(make_png(), 3600)])

    first = rig.proxy.tile(HERE)
    during = rig.proxy.tile(TileAddress(12, 2201, 1500))
    rig.clocks.advance(COOLDOWN_S[problem] + 1)
    after = rig.proxy.tile(TileAddress(12, 2202, 1500))

    assert first == TileAnswer(None, problem)
    assert during == TileAnswer(None, problem)
    assert len(rig.source.calls) == 2  # the one that failed and the one after the cool-down
    assert after.problem is None
    assert after.png is not None


def test_a_refusal_has_the_longest_cool_down() -> None:
    assert COOLDOWN_S[TileProblem.REFUSED] == 300.0
    assert COOLDOWN_S[TileProblem.REFUSED] > COOLDOWN_S[TileProblem.NETWORK_UNAVAILABLE]


def test_t_map_10_a_bad_answer_about_one_tile_blocks_only_that_tile() -> None:
    """D-F5B-02: the cool-down of INVALID_RESPONSE (a 404, not a PNG) is for the tile itself."""
    rig = Rig([TileUnavailable(TileProblem.INVALID_RESPONSE), TileImage(make_png(), 3600)])

    first = rig.proxy.tile(HERE)
    again = rig.proxy.tile(HERE)
    neighbour = rig.proxy.tile(TileAddress(12, 2201, 1500))
    rig.clocks.advance(COOLDOWN_S[TileProblem.INVALID_RESPONSE] + 1)
    later = rig.proxy.tile(HERE)

    assert first == TileAnswer(None, TileProblem.INVALID_RESPONSE)
    assert again == TileAnswer(None, TileProblem.INVALID_RESPONSE)  # not asked again
    assert neighbour.problem is None
    assert neighbour.png is not None
    assert later.problem is None  # asked again after its cool-down
    assert len(rig.source.calls) == 3


def test_t_map_10_a_page_that_wants_many_tiles_does_not_hammer_a_refusing_server() -> None:
    rig = Rig([TileUnavailable(TileProblem.REFUSED)])

    answers = [rig.proxy.tile(TileAddress(14, x, 5000)) for x in range(100)]

    assert len(rig.source.calls) == 1
    assert all(a == TileAnswer(None, TileProblem.REFUSED) for a in answers)


def test_t_map_10_an_expired_tile_is_served_with_the_problem_when_offline() -> None:
    rig = Rig([TileImage(make_png(), 60), TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)])
    rig.proxy.tile(HERE)
    rig.clocks.advance(120)

    answer = rig.proxy.tile(HERE)

    assert answer.png is not None  # better an old tile than a grey square
    assert answer.problem is TileProblem.NETWORK_UNAVAILABLE


def test_a_tile_with_no_reuse_time_is_served_but_not_kept() -> None:
    rig = Rig([TileImage(make_png(), 0)])

    rig.proxy.tile(HERE)
    rig.proxy.tile(HERE)

    assert len(rig.source.calls) == 2
    assert rig.cache.used_bytes() == 0


def test_t_sec_15_a_path_that_is_not_a_tile_is_refused_before_cache_and_server() -> None:
    rig = Rig()

    for bad in ("/12/2200", "/99/0/0", "/1/2/3?key=x", "/../../etc/passwd"):
        with pytest.raises(TileAddressError):
            rig.proxy.serve(bad)

    assert rig.source.calls == []


def test_serve_reads_the_path_the_page_writes() -> None:
    rig = Rig()

    answer = rig.proxy.serve("/12/2200/1500.png")

    assert answer.png == rig.source.png
    assert rig.source.calls == [(12, 2200, 1500, NO_VALIDATORS)]


class BrokenCache(InMemoryTileCache):
    """A cache whose every read and write fails."""

    def get(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")

    def put(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")

    def renew(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")


def test_a_cache_that_cannot_be_used_does_not_stop_the_map() -> None:
    rig = Rig()
    rig.proxy = TileProxy(
        rig.source, BrokenCache(), lambda: rig.clocks.utc, lambda: rig.clocks.monotonic
    )

    answer = rig.proxy.tile(HERE)

    assert answer == TileAnswer(rig.source.png, None)


class RenewFailsCache(InMemoryTileCache):
    """A cache that reads and stores but cannot renew."""

    def renew(self, *_args: object) -> NoReturn:
        raise TileCacheError("simulated")


def test_a_cache_that_cannot_renew_still_serves_the_confirmed_tile() -> None:
    png = make_png()
    rig = Rig([TileImage(png, 60, VALIDATORS), TileNotModified(60)])
    rig.cache = RenewFailsCache()
    rig.proxy = TileProxy(
        rig.source, rig.cache, lambda: rig.clocks.utc, lambda: rig.clocks.monotonic
    )
    rig.proxy.tile(HERE)
    rig.clocks.advance(61)

    assert rig.proxy.tile(HERE) == TileAnswer(png, None)
