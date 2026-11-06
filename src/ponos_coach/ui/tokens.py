# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Token visivi: spaziature, tavolozza Okabe-Ito e colori dei temi chiaro e scuro.

Unica fonte dei valori visivi dell'interfaccia e dei grafici: nessun numero magico altrove.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# Spaziature a base 4 (pixel logici).
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
STATE_PAGE_MAX_WIDTH = 480  # larghezza massima del testo nelle pagine di stato
TITLE_SCALE = 1.4  # fattore del carattere del titolo di una pagina di stato

# Tavolozza Okabe-Ito (adatta anche a chi non distingue alcuni colori).
OKABE_ITO_BLUE = "#0072B2"
OKABE_ITO_VERMILLION = "#D55E00"
OKABE_ITO_GREEN = "#009E73"
OKABE_ITO_SKY = "#56B4E9"
OKABE_ITO_ORANGE = "#E69F00"
OKABE_ITO_YELLOW = "#F0E442"

# Okabe-Ito più scuri, per i temi chiari: gli originali (giallo e azzurro) non arrivano a 3:1
# sul bianco. Stessa tonalità, solo meno luminosi.
OKABE_ITO_YELLOW_DEEP = "#8C8305"
OKABE_ITO_SKY_DEEP = "#2E8BC0"
OKABE_ITO_PURPLE = "#CC79A7"

# Il nero di Okabe-Ito è il colore del testo del tema; i grigi neutri sono la categoria "Altro"
# e il fondo delle barre del carico giornaliero (contesto, non una categoria).
BLACK_LIGHT = "#1F2328"
BLACK_DARK = "#E6E6E6"
NEUTRAL_LIGHT = "#6E7781"
NEUTRAL_DARK = "#8B949E"
DAILY_LOAD_LIGHT = "#57606A"  # più scuro del neutro: le barre, con la loro opacità, arrivano a 3:1


# Scala sequenziale delle zone di FC: stessa famiglia di tinte (blu-viola), solo la luminosità
# cresce con l'intensità. Generata in OkLCh, mai in HSL: i passi sono percettivamente uguali.
_ZONE_CHROMA = 0.13
_ZONE_HUE_START = 215.0
_ZONE_HUE_STEP = 20.0
# Su fondo chiaro più intenso = più scuro; su fondo scuro più intenso = più chiaro. Tutti i passi
# restano a 3:1 o più dal fondo del proprio tema.
_ZONE_LIGHTNESS_LIGHT = (0.64, 0.55, 0.46, 0.37, 0.28)
_ZONE_LIGHTNESS_DARK = (0.58, 0.67, 0.75, 0.83, 0.91)
_SRGB_THRESHOLD = 0.0031308
_GAMUT_STEP = 0.004


def _srgb_channel(linear: float) -> float:
    if linear <= _SRGB_THRESHOLD:
        return 12.92 * linear
    return 1.055 * math.pow(linear, 1 / 2.4) - 0.055


def _linear_rgb(lightness: float, chroma: float, hue_degrees: float) -> tuple[float, float, float]:
    """Da OkLCh a RGB lineare (formule di OkLab), senza limitare al gamut."""
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
    """Colore sRGB `#RRGGBB` da OkLCh; se fuori gamut si riduce la croma, la luminosità resta."""
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
    """Schema di colori dell'interfaccia."""

    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Colori di un tema; le serie sono gli stessi in entrambi i temi (Okabe-Ito)."""

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
    categories: tuple[str, ...]  # sette colori di categoria, uno per posto (mai rosso né arancio)
    zones: tuple[str, ...]  # da Z1 a Z5, dal meno al più intenso
    band_opacity: float  # trasparenza delle bande di zona dietro una curva


_LIGHT = ThemeTokens(
    background="#FFFFFF",
    text="#1F2328",
    text_muted="#57606A",
    error="#B42318",  # rosso solo per gli errori, sempre accanto a parole
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
    band_opacity=0.14,  # abbastanza tenue da lasciare i nomi delle zone a 4,5:1
)

_DARK = ThemeTokens(
    background="#1E1F22",
    text="#E6E6E6",
    text_muted="#A9ADB3",
    error="#F97066",
    grid="#3A3D41",
    axis_line="#7D8590",
    ctl=OKABE_ITO_SKY,  # il blu scuro non regge su fondo scuro: stessa famiglia, piu chiaro
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
    band_opacity=0.16,  # nomi delle zone a 4,5:1 e croci degli scarti a 3:1
)


def tokens_for(theme: Theme) -> ThemeTokens:
    """Token del tema."""
    return _DARK if theme is Theme.DARK else _LIGHT
