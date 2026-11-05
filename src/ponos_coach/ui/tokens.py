# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Token visivi: spaziature, tavolozza Okabe-Ito e colori dei temi chiaro e scuro.

Unica fonte dei valori visivi dell'interfaccia e dei grafici: nessun numero magico altrove.
"""

from __future__ import annotations

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

# Grigi neutri per le barre del carico giornaliero (contesto, non una categoria).
NEUTRAL_LIGHT = "#6E7781"
NEUTRAL_DARK = "#8B949E"


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
    daily_load=NEUTRAL_LIGHT,
    daily_load_opacity=0.6,
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
    daily_load_opacity=0.6,
)


def tokens_for(theme: Theme) -> ThemeTokens:
    """Token del tema."""
    return _DARK if theme is Theme.DARK else _LIGHT
