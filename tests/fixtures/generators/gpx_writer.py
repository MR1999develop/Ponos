# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scrittore di file GPX 1.0 e 1.1 di attività, solo libreria standard, e GPX ostili.

Le coordinate sono inventate, in mare aperto (vedi `tracks.SEA_LAT_DEG`). I file validi hanno
una traccia con uno o più segmenti, FC nelle estensioni Garmin (`gpxtpx:hr`, v1 o v2), quota e
orari opzionali; quelli ostili servono ai test di sicurezza e si generano al volo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from xml.sax.saxutils import escape

from .tracks import SEA_LAT_DEG, SEA_LON_DEG

GPX_NAMESPACE = {
    "1.1": "http://www.topografix.com/GPX/1/1",
    "1.0": "http://www.topografix.com/GPX/1/0",
}
TPX_NAMESPACE = {
    "v1": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "v2": "http://www.garmin.com/xmlschemas/TrackPointExtension/v2",
}

HrExtension = Literal["v1", "v2"]


@dataclass(frozen=True)
class GpxPoint:
    """Un `trkpt`: posizione in gradi e, se presenti, orario (UTC), quota in metri e FC."""

    lat: float
    lon: float
    time: datetime | None = None
    ele: float | None = None
    hr: int | None = None


@dataclass(frozen=True)
class GpxSpec:
    """Descrizione di un GPX: segmenti di punti, tipo di sport, versione e stile della FC."""

    segments: Sequence[Sequence[GpxPoint]]
    sport_type: str | None = None
    version: Literal["1.0", "1.1"] = "1.1"
    hr_extension: HrExtension | None = "v2"
    name: str | None = "Attivita di prova"
    fractional_seconds: bool = False


def format_time(moment: datetime, *, fractional: bool = False) -> str:
    """Orario ISO 8601 UTC con `Z` (con millisecondi se richiesto)."""
    utc = moment.astimezone(UTC)
    if fractional:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _point_xml(point: GpxPoint, spec: GpxSpec) -> str:
    parts = [f'<trkpt lat="{point.lat:.7f}" lon="{point.lon:.7f}">']
    if point.ele is not None:
        parts.append(f"<ele>{point.ele:.1f}</ele>")
    if point.time is not None:
        parts.append(f"<time>{format_time(point.time, fractional=spec.fractional_seconds)}</time>")
    if point.hr is not None and spec.hr_extension is not None:
        parts.append(
            "<extensions><gpxtpx:TrackPointExtension>"
            f"<gpxtpx:hr>{point.hr}</gpxtpx:hr>"
            "</gpxtpx:TrackPointExtension></extensions>"
        )
    parts.append("</trkpt>")
    return "".join(parts)


def build_gpx(spec: GpxSpec) -> bytes:
    """File GPX completo e valido (UTF-8) secondo `spec`."""
    tpx = TPX_NAMESPACE[spec.hr_extension or "v2"]
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<gpx version="{spec.version}" creator="ponos-coach-tests" '
        f'xmlns="{GPX_NAMESPACE[spec.version]}" xmlns:gpxtpx="{tpx}">',
        "<trk>",
    ]
    if spec.name is not None:
        out.append(f"<name>{escape(spec.name)}</name>")
    if spec.sport_type is not None:
        out.append(f"<type>{escape(spec.sport_type)}</type>")
    for segment in spec.segments:
        out.append("<trkseg>")
        out.extend(_point_xml(point, spec) for point in segment)
        out.append("</trkseg>")
    out.append("</trk></gpx>\n")
    return "".join(out).encode("utf-8")


def line_points(
    start: datetime,
    count: int,
    *,
    step_s: int = 1,
    lat_step_deg: float = 0.0,
    lon_step_deg: float = 0.0,
    ele_m: Sequence[float | None] | None = None,
    hr: Sequence[int | None] | None = None,
    first_lat: float = SEA_LAT_DEG,
    first_lon: float = SEA_LON_DEG,
) -> list[GpxPoint]:
    """`count` punti a passo costante di tempo su una retta (nel mare inventato)."""
    return [
        GpxPoint(
            first_lat + i * lat_step_deg,
            first_lon + i * lon_step_deg,
            start + timedelta(seconds=i * step_s),
            None if ele_m is None else ele_m[i],
            None if hr is None else hr[i],
        )
        for i in range(count)
    ]


# --- GPX ostili ------------------------------------------------------------------------------

_HEAD = f'<?xml version="1.0"?>\n{{doctype}}<gpx version="1.1" xmlns="{GPX_NAMESPACE["1.1"]}">'


def _with_doctype(doctype: str, body: str) -> bytes:
    return (_HEAD.format(doctype=doctype) + body + "</gpx>").encode("utf-8")


def gpx_with_empty_doctype() -> bytes:
    """DOCTYPE senza entità: già un DTD, quindi rifiutato."""
    return _with_doctype("<!DOCTYPE gpx>\n", "<metadata><name>x</name></metadata>")


def billion_laughs(levels: int = 9, fan_out: int = 10) -> bytes:
    """Espansione esponenziale di entità (`fan_out ** levels` ripetizioni), mai da espandere."""
    entities = ['<!ENTITY e0 "lol">']
    for level in range(1, levels):
        reference = f"&e{level - 1};" * fan_out
        entities.append(f'<!ENTITY e{level} "{reference}">')
    doctype = "<!DOCTYPE gpx [" + "".join(entities) + "]>\n"
    return _with_doctype(doctype, f"<metadata><name>&e{levels - 1};</name></metadata>")


def quadratic_blowup(size: int = 50_000, uses: int = 50_000) -> bytes:
    """Una entità lunga richiamata moltissime volte (`size * uses` caratteri)."""
    doctype = f'<!DOCTYPE gpx [<!ENTITY a "{"A" * size}">]>\n'
    return _with_doctype(doctype, "<metadata><name>" + "&a;" * uses + "</name></metadata>")


def external_entity(target: str) -> bytes:
    """Entità esterna (`SYSTEM`) verso `target`: un file o un indirizzo, mai da aprire."""
    doctype = f'<!DOCTYPE gpx [<!ENTITY secret SYSTEM "{target}">]>\n'
    return _with_doctype(doctype, "<metadata><name>&secret;</name></metadata>")


def external_dtd(target: str) -> bytes:
    """DTD esterno (`SYSTEM`) verso `target`, mai da scaricare."""
    return _with_doctype(f'<!DOCTYPE gpx SYSTEM "{target}">\n', "")


def parameter_entity(target: str) -> bytes:
    """Entità parametrica che include un file esterno nel DTD interno."""
    doctype = f'<!DOCTYPE gpx [<!ENTITY % remote SYSTEM "{target}">%remote;]>\n'
    return _with_doctype(doctype, "")


def deeply_nested(depth: int) -> bytes:
    """`depth` elementi `<a>` annidati dentro `gpx`."""
    return _with_doctype("", "<a>" * depth + "</a>" * depth)


def points_without_data(count: int) -> bytes:
    """`count` punti minimi (solo coordinate): per il tetto dei punti, senza orari."""
    body = "<trk><trkseg>" + '<trkpt lat="0" lon="0"/>' * count + "</trkseg></trk>"
    return _with_doctype("", body)


def huge_text(chars: int) -> bytes:
    """Un nome di `chars` caratteri dentro `metadata`."""
    return _with_doctype("", "<metadata><name>" + "A" * chars + "</name></metadata>")


def huge_attribute(chars: int) -> bytes:
    """Un punto la cui latitudine è un attributo di `chars` caratteri."""
    value = "1" * chars
    return _with_doctype("", f'<trk><trkseg><trkpt lat="{value}" lon="0"/></trkseg></trk>')
