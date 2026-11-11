# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of the map port `TileCache`.

The test class gets its implementation from a fixture (`cache`), so the same
contract runs on the in-memory fake and on the real adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ponos_coach.maps.domain import BACKGROUNDS, CYCLOSM, OPENSTREETMAP, OPENTOPOMAP, TileAddress
from ponos_coach.maps.ports import TileCache, TileImage, TileValidators
from tests.fakes.tiles import make_png

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def png_of(size: int) -> bytes:
    """A valid PNG of about `size` bytes."""
    return make_png(extra_bytes=max(0, size - len(make_png())))


class TileCacheContract:
    """Store, find, renew, evict and clear tiles."""

    def test_an_empty_cache_has_nothing(self, cache: TileCache) -> None:
        assert cache.get(TileAddress(5, 1, 1), NOW) is None
        assert cache.used_bytes() == 0

    def test_a_stored_tile_is_found_with_its_expiry(self, cache: TileCache) -> None:
        image = TileImage(png_of(3000), 3600)
        cache.put(TileAddress(5, 1, 1), image, NOW)

        found = cache.get(TileAddress(5, 1, 1), NOW)

        assert found is not None
        assert found.png == image.png
        assert found.expires_at == NOW + timedelta(hours=1)
        assert found.is_fresh(NOW)
        assert not found.is_fresh(NOW + timedelta(hours=1))
        assert cache.used_bytes() > 0

    def test_a_tile_is_told_apart_by_every_coordinate(self, cache: TileCache) -> None:
        cache.put(TileAddress(5, 1, 1), TileImage(png_of(3000), 60), NOW)

        assert cache.get(TileAddress(6, 1, 1), NOW) is None
        assert cache.get(TileAddress(5, 2, 1), NOW) is None
        assert cache.get(TileAddress(5, 1, 2), NOW) is None

    def test_a_tile_of_one_background_never_serves_for_another(self, cache: TileCache) -> None:
        at = (5, 1, 1)
        cache.put(TileAddress(*at, OPENSTREETMAP), TileImage(png_of(3000), 60), NOW)

        assert cache.get(TileAddress(*at, OPENTOPOMAP), NOW) is None
        assert cache.get(TileAddress(*at, CYCLOSM), NOW) is None
        assert cache.get(TileAddress(*at, OPENSTREETMAP), NOW) is not None

    def test_the_same_place_of_every_background_is_stored_apart(self, cache: TileCache) -> None:
        for number, background in enumerate(BACKGROUNDS):
            png = png_of(3000 + number)
            cache.put(TileAddress(5, 1, 1, background), TileImage(png, 60), NOW)

        for number, background in enumerate(BACKGROUNDS):
            found = cache.get(TileAddress(5, 1, 1, background), NOW)
            assert found is not None and found.png == png_of(3000 + number)

    def test_the_highest_zoom_of_any_background_can_be_stored(self, cache: TileCache) -> None:
        address = TileAddress(20, 1_048_575, 3, CYCLOSM)
        cache.put(address, TileImage(png_of(3000), 60), NOW)

        assert cache.get(address, NOW) is not None

    def test_the_validators_of_a_stored_tile_come_back_with_it(self, cache: TileCache) -> None:
        validators = TileValidators('"abc123"', "Tue, 03 Nov 2026 10:00:00 GMT")
        cache.put(TileAddress(5, 1, 1), TileImage(png_of(3000), 60, validators), NOW)

        found = cache.get(TileAddress(5, 1, 1), NOW)

        assert found is not None
        assert found.validators == validators

    def test_a_tile_without_validators_has_none(self, cache: TileCache) -> None:
        cache.put(TileAddress(5, 1, 1), TileImage(png_of(3000), 60), NOW)

        found = cache.get(TileAddress(5, 1, 1), NOW)

        assert found is not None
        assert found.validators == TileValidators(None, None)

    def test_renewing_moves_the_expiry_and_keeps_tile_and_validators(
        self, cache: TileCache
    ) -> None:
        validators = TileValidators('"abc123"', None)
        image = TileImage(png_of(3000), 60, validators)
        cache.put(TileAddress(5, 1, 1), image, NOW)
        later = NOW + timedelta(hours=2)  # the tile expired an hour and a half ago

        cache.renew(TileAddress(5, 1, 1), 7 * 86_400, later)

        found = cache.get(TileAddress(5, 1, 1), later)
        assert found is not None
        assert found.png == image.png
        assert found.validators == validators
        assert found.expires_at == later + timedelta(days=7)
        assert found.is_fresh(later)

    def test_renewing_a_tile_that_is_not_stored_does_nothing(self, cache: TileCache) -> None:
        cache.renew(TileAddress(5, 1, 1), 3600, NOW)

        assert cache.get(TileAddress(5, 1, 1), NOW) is None
        assert cache.used_bytes() == 0

    def test_storing_again_replaces_the_tile_and_does_not_double_the_size(
        self, cache: TileCache
    ) -> None:
        address = TileAddress(5, 1, 1)
        cache.put(address, TileImage(png_of(3000), 60), NOW)
        used = cache.used_bytes()
        newer = TileImage(png_of(3500), 120)

        cache.put(address, newer, NOW)

        found = cache.get(address, NOW)
        assert found is not None
        assert found.png == newer.png
        assert cache.used_bytes() <= used + 4096  # at most one more page, never two copies

    def test_a_tile_with_no_reuse_time_is_not_stored(self, cache: TileCache) -> None:
        cache.put(TileAddress(5, 1, 1), TileImage(png_of(3000), 0), NOW)

        assert cache.get(TileAddress(5, 1, 1), NOW) is None
        assert cache.used_bytes() == 0

    def test_t_map_11_clear_takes_the_cache_to_zero(self, cache: TileCache) -> None:
        for x in range(5):
            cache.put(TileAddress(5, x, 1), TileImage(png_of(3000), 60), NOW)

        cache.clear()

        assert cache.used_bytes() == 0
        assert cache.get(TileAddress(5, 0, 1), NOW) is None
        cache.put(TileAddress(5, 0, 1), TileImage(png_of(3000), 60), NOW)  # still usable
        assert cache.get(TileAddress(5, 0, 1), NOW) is not None

    @pytest.mark.parametrize("count", [1, 10, 100])
    def test_t_map_11_the_cap_is_never_exceeded_whatever_is_stored(
        self, cache: TileCache, count: int
    ) -> None:
        cache.set_limit(60_000)

        for number in range(count * 3):
            cache.put(TileAddress(10, number, 1), TileImage(png_of(5000 + number), 60), NOW)
            assert cache.used_bytes() <= 60_000

    def test_t_map_11_the_least_recently_used_tile_goes_first(self, cache: TileCache) -> None:
        cache.set_limit(40_000)
        first, second, third = (TileAddress(10, n, 1) for n in range(3))
        for address in (first, second, third):
            cache.put(address, TileImage(png_of(9000), 60), NOW)
        assert cache.get(first, NOW) is not None  # the oldest is used again: now second is

        for n in range(3, 6):
            cache.put(TileAddress(10, n, 1), TileImage(png_of(9000), 60), NOW)

        assert cache.get(second, NOW) is None
        assert cache.get(TileAddress(10, 5, 1), NOW) is not None

    def test_t_map_11_lowering_the_cap_evicts_at_once(self, cache: TileCache) -> None:
        cache.set_limit(200_000)
        for n in range(10):
            cache.put(TileAddress(10, n, 1), TileImage(png_of(9000), 60), NOW)
        before = cache.used_bytes()

        cache.set_limit(30_000)

        assert before > 30_000
        assert 0 < cache.used_bytes() <= 30_000
        assert cache.get(TileAddress(10, 9, 1), NOW) is not None  # the newest stays

    def test_a_tile_larger_than_the_whole_cap_is_not_stored(self, cache: TileCache) -> None:
        cache.set_limit(10_000)

        cache.put(TileAddress(10, 1, 1), TileImage(png_of(50_000), 60), NOW)

        assert cache.used_bytes() == 0
        assert cache.get(TileAddress(10, 1, 1), NOW) is None
