# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""How the map draws a sport: a category colour and a dash pattern (never the colour alone).

The domain gives the sport; here the interface chooses the look, from the theme tokens. The index
of a sport in `SPORT_ORDER` is what the page receives instead of a name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ponos_coach.activities.domain import Sport
from ponos_coach.ui.tokens import ThemeTokens

SPORT_ORDER: Final[tuple[Sport, ...]] = tuple(Sport)
"""The sports in the order the page knows them (the index travels in the payloads)."""


@dataclass(frozen=True, slots=True)
class SportLook:
    """Slot of the theme's category colours and the dash pattern of the line ("" = solid)."""

    color_slot: int
    dash: str


_LOOK: Final[dict[Sport, SportLook]] = {
    Sport.RUNNING: SportLook(0, ""),
    Sport.TRAIL: SportLook(3, "10 5"),
    Sport.CYCLING: SportLook(2, "2 5"),
    Sport.HIKING: SportLook(1, "12 4 2 4"),
    Sport.WALKING: SportLook(4, "6 6"),
    Sport.STRENGTH: SportLook(5, "1 8"),
    Sport.OTHER: SportLook(6, "3 3 9 3"),
}


def sport_index(sport: Sport) -> int:
    """Position of the sport in `SPORT_ORDER` (what the page receives)."""
    return SPORT_ORDER.index(sport)


def sport_look(sport: Sport) -> SportLook:
    """Colour slot and dash of a sport."""
    return _LOOK[sport]


def sport_color(sport: Sport, tokens: ThemeTokens) -> str:
    """The sport's colour in the theme."""
    return tokens.categories[_LOOK[sport].color_slot]


def page_sports(tokens: ThemeTokens) -> list[dict[str, str]]:
    """Colour and dash of every sport in `SPORT_ORDER`, as the page's configuration."""
    return [{"color": sport_color(s, tokens), "dash": _LOOK[s].dash} for s in SPORT_ORDER]


def page_colors(tokens: ThemeTokens) -> dict[str, str]:
    """Colours of the page's own elements (background behind the tiles, controls, lines)."""
    return {"background": tokens.map_background, "text": tokens.text, "panel": tokens.map_panel}
