# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Visual tokens: spacing, Okabe-Ito palette and light and dark theme colours.

Single source of the interface's and charts' visual values: no magic numbers elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# Spacing on a base of 4 (logical pixels).
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_6 = 24
DIALOG_MIN_WIDTH = 480
STATUS_PROGRESS_WIDTH = 160

SIDEBAR_WIDTH = 190
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 560
STATE_PAGE_MAX_WIDTH = 480  # maximum text width on state pages
TITLE_SCALE = 1.4  # font factor of a state page's title

# Okabe-Ito palette (also suited to those who cannot tell some colours apart).
OKABE_ITO_BLUE = "#0072B2"
OKABE_ITO_VERMILLION = "#D55E00"
OKABE_ITO_GREEN = "#009E73"
OKABE_ITO_SKY = "#56B4E9"
OKABE_ITO_ORANGE = "#E69F00"
OKABE_ITO_YELLOW = "#F0E442"

# Darker Okabe-Ito, for light themes: the originals (yellow and sky blue) do not reach 3:1
# on white. Same hue, just less bright.
OKABE_ITO_YELLOW_DEEP = "#8C8305"
OKABE_ITO_SKY_DEEP = "#2E8BC0"
OKABE_ITO_PURPLE = "#CC79A7"

# Okabe-Ito's black is the theme's text colour; neutral greys are the "Other" category
# and the background of the daily load bars (context, not a category).
BLACK_LIGHT = "#1F2328"
BLACK_DARK = "#E6E6E6"
NEUTRAL_LIGHT = "#6E7781"
NEUTRAL_DARK = "#8B949E"
DAILY_LOAD_LIGHT = "#57606A"  # darker than the neutral: the bars, with their opacity, reach 3:1


# Sequential scale of HR zones: same hue family (blue-violet), only lightness
# grows with intensity. Generated in OkLCh, never in HSL: the steps are perceptually equal.
_ZONE_CHROMA = 0.13
_ZONE_HUE_START = 215.0
_ZONE_HUE_STEP = 20.0
# On a light background more intense = darker; on a dark background more intense = lighter. All
# steps
# stay at 3:1 or more against their theme's background.
_ZONE_LIGHTNESS_LIGHT = (0.64, 0.55, 0.46, 0.37, 0.28)
_ZONE_LIGHTNESS_DARK = (0.58, 0.67, 0.75, 0.83, 0.91)
_SRGB_THRESHOLD = 0.0031308
_GAMUT_STEP = 0.004


def _srgb_channel(linear: float) -> float:
    if linear <= _SRGB_THRESHOLD:
        return 12.92 * linear
    return 1.055 * math.pow(linear, 1 / 2.4) - 0.055


def _linear_rgb(lightness: float, chroma: float, hue_degrees: float) -> tuple[float, float, float]:
    """From OkLCh to linear RGB (OkLab formulas), without clamping to the gamut."""
    a = chroma * math.cos(math.radians(hue_degrees))
    b = chroma * math.sin(math.radians(hue_degrees))
    long = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    medium = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    short = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short,
        -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short,
        -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short,
    )


def oklch_to_hex(lightness: float, chroma: float, hue_degrees: float) -> str:
    """sRGB colour `#RRGGBB` from OkLCh; if out of gamut the chroma is reduced, lightness stays."""
    current = chroma
    channels = _linear_rgb(lightness, current, hue_degrees)
    while current > 0 and not all(0.0 <= value <= 1.0 for value in channels):
        current = max(0.0, current - _GAMUT_STEP)
        channels = _linear_rgb(lightness, current, hue_degrees)
    encoded = (round(255 * _srgb_channel(min(1.0, max(0.0, v)))) for v in channels)
    return "#{:02X}{:02X}{:02X}".format(*encoded)


def _zone_ramp(lightness: tuple[float, ...]) -> tuple[str, ...]:
    return tuple(
        oklch_to_hex(level, _ZONE_CHROMA, _ZONE_HUE_START + step * _ZONE_HUE_STEP)
        for step, level in enumerate(lightness)
    )


class Theme(Enum):
    """Colour scheme of the interface."""

    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Colours of a theme; the series are the same in both themes (Okabe-Ito)."""

    background: str
    text: str
    text_muted: str
    error: str
    grid: str
    axis_line: str
    ctl: str
    atl: str
    tsb: str
    daily_load: str
    daily_load_opacity: float
    categories: tuple[str, ...]  # seven category colours, one per slot (never red or orange)
    zones: tuple[str, ...]  # Z1 to Z5, from least to most intense
    band_opacity: float  # transparency of the zone bands behind a curve


_LIGHT = ThemeTokens(
    background="#FFFFFF",
    text="#1F2328",
    text_muted="#57606A",
    error="#B42318",  # red only for errors, always next to words
    grid="#D8DEE4",
    axis_line="#8C959F",
    ctl=OKABE_ITO_BLUE,
    atl=OKABE_ITO_VERMILLION,
    tsb=OKABE_ITO_GREEN,
    daily_load=DAILY_LOAD_LIGHT,
    daily_load_opacity=0.74,
    categories=(
        OKABE_ITO_BLUE,
        OKABE_ITO_YELLOW_DEEP,
        OKABE_ITO_GREEN,
        OKABE_ITO_PURPLE,
        OKABE_ITO_SKY_DEEP,
        BLACK_LIGHT,
        NEUTRAL_LIGHT,
    ),
    zones=_zone_ramp(_ZONE_LIGHTNESS_LIGHT),
    band_opacity=0.14,  # faint enough to leave the zone names at 4.5:1
)

_DARK = ThemeTokens(
    background="#1E1F22",
    text="#E6E6E6",
    text_muted="#A9ADB3",
    error="#F97066",
    grid="#3A3D41",
    axis_line="#7D8590",
    ctl=OKABE_ITO_SKY,  # dark blue does not hold on a dark background: same family, lighter
    atl=OKABE_ITO_ORANGE,
    tsb=OKABE_ITO_GREEN,
    daily_load=NEUTRAL_DARK,
    daily_load_opacity=0.72,
    categories=(
        OKABE_ITO_BLUE,
        OKABE_ITO_YELLOW,
        OKABE_ITO_GREEN,
        OKABE_ITO_PURPLE,
        OKABE_ITO_SKY,
        BLACK_DARK,
        NEUTRAL_DARK,
    ),
    zones=_zone_ramp(_ZONE_LIGHTNESS_DARK),
    band_opacity=0.16,  # zone names at 4.5:1 and discarded-sample crosses at 3:1
)


def tokens_for(theme: Theme) -> ThemeTokens:
    """Theme tokens."""
    return _DARK if theme is Theme.DARK else _LIGHT


# Faint backgrounds that tell the nutrition goal periods apart in the calendar (never red or
# orange; the legend and the cell text say the goal in words, the colour only helps). One per goal
# kind in legend order: weight loss, performance, maintenance. Generated in OkLCh; the text of the
# theme stays at 4.5:1 or more on each.
_GOAL_TINT_CHROMA = 0.045
_GOAL_TINT_HUES = (250.0, 150.0, 310.0)
_GOAL_TINT_LIGHTNESS_LIGHT = 0.92
_GOAL_TINT_LIGHTNESS_DARK = 0.32


def goal_tints_for(theme: Theme) -> tuple[str, str, str]:
    """Calendar background tints for weight loss, performance and maintenance."""
    lightness = _GOAL_TINT_LIGHTNESS_DARK if theme is Theme.DARK else _GOAL_TINT_LIGHTNESS_LIGHT
    first, second, third = (
        oklch_to_hex(lightness, _GOAL_TINT_CHROMA, hue) for hue in _GOAL_TINT_HUES
    )
    return (first, second, third)


# Amber of a warning (the second level of gravity, after the error red): deep enough to reach 3:1
# against its theme's background, used for a border or an icon, never for the only sign.
_WARNING_LIGHT = "#9A6700"
_WARNING_DARK = "#D4A72C"


def warning_color_for(theme: Theme) -> str:
    """The amber that marks a warning in a theme."""
    return _WARNING_DARK if theme is Theme.DARK else _WARNING_LIGHT
