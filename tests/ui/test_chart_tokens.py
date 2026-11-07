# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-05 (parte pura): contrasto e ordine delle tavolozze dei grafici nei due temi."""

from __future__ import annotations

import pytest

from ponos_coach.ui.tokens import Theme, oklch_to_hex, tokens_for


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", list(Theme))
def test_category_colors_have_at_least_3_to_1_contrast_on_the_background(theme: Theme) -> None:
    tokens = tokens_for(theme)

    assert len(tokens.categories) == 7
    for color in tokens.categories:
        assert contrast(color, tokens.background) >= 3.0, color


@pytest.mark.parametrize("theme", list(Theme))
def test_zone_colors_have_at_least_3_to_1_contrast_on_the_background(theme: Theme) -> None:
    tokens = tokens_for(theme)

    assert len(tokens.zones) == 5
    for color in tokens.zones:
        assert contrast(color, tokens.background) >= 3.0, color


@pytest.mark.parametrize("theme", list(Theme))
def test_zone_scale_is_sequential_more_intense_is_further_from_the_background(
    theme: Theme,
) -> None:
    tokens = tokens_for(theme)

    distances = [contrast(color, tokens.background) for color in tokens.zones]

    assert distances == sorted(distances)
    assert len(set(distances)) == 5  # cinque passi distinti, non un gradino ripetuto


def test_zone_scale_gets_lighter_on_dark_and_darker_on_light() -> None:
    light = [luminance(c) for c in tokens_for(Theme.LIGHT).zones]
    dark = [luminance(c) for c in tokens_for(Theme.DARK).zones]

    assert light == sorted(light, reverse=True)
    assert dark == sorted(dark)


def test_every_color_is_a_valid_hex_and_categories_are_distinct() -> None:
    for theme in Theme:
        tokens = tokens_for(theme)
        for color in (*tokens.categories, *tokens.zones):
            assert len(color) == 7
            assert color.startswith("#")
            int(color[1:], 16)
        assert len(set(tokens.categories)) == len(tokens.categories)


def test_oklch_extremes_and_gamut_clipping() -> None:
    assert oklch_to_hex(0.0, 0.0, 0.0) == "#000000"
    assert oklch_to_hex(1.0, 0.0, 0.0) == "#FFFFFF"
    # una croce impossibile entra comunque nel gamut sRGB (si riduce la croce, non la luminosità)
    clipped = oklch_to_hex(0.5, 0.5, 140.0)
    assert len(clipped) == 7
    assert 0.0 < luminance(clipped) < 1.0
