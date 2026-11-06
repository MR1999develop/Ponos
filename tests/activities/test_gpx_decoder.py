# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine GPX: T-GPX-01 (file validi, valori calcolati a mano), T-SEC-02 (GPX ostili)."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import FileFormat, GpxIdentity, Sport, decode_polyline
from ponos_coach.activities.fit_decoder import FitDecodeError, FitDecodeReason
from ponos_coach.activities.gpx_decoder import (
    GpxDecodeError,
    GpxLimits,
    decode_gpx,
    map_gpx_sport,
    positive_ascent_m,
)
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators.tracks import SEA_LAT_DEG, SEA_LON_DEG

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
STEP_M = 111.19508  # 0,001 gradi di latitudine: 0,001 * pi / 180 * 6.371.008,8 m, a mano
LIMITS_TIGHT = GpxLimits(max_depth=6, max_points=3, max_text_chars=10)


def reason_of(data: bytes, limits: GpxLimits | None = None) -> FitDecodeReason:
    with pytest.raises(GpxDecodeError) as caught:
        decode_gpx(data, limits)
    return caught.value.reason


def meridian(count: int, **options: object) -> list[gw.GpxPoint]:
    """Punti ogni 10 s lungo un meridiano: ogni passo è di 0,001 gradi di latitudine."""
    return gw.line_points(START, count, step_s=10, lat_step_deg=0.001, **options)  # type: ignore[arg-type]


def decode(points: list[gw.GpxPoint], **spec: object) -> object:
    return decode_gpx(gw.build_gpx(gw.GpxSpec([points], **spec)))  # type: ignore[arg-type]


# --- T-GPX-01: file validi -------------------------------------------------------------------


def test_t_gpx_01_one_segment_values_computed_by_hand() -> None:
    # 11 punti, 10 passi da 111,19508 m, 10 s ciascuno: 1111,9508 m in 100 s, tutto in movimento.
    data = gw.build_gpx(gw.GpxSpec([meridian(11)], sport_type="running"))

    activity = decode_gpx(data)

    assert activity.file_format is FileFormat.GPX
    assert activity.sport is Sport.RUNNING
    assert activity.start == START
    assert activity.elapsed_s == 100.0
    assert activity.timer_s == 100.0
    assert activity.summary.distance_m == pytest.approx(10 * STEP_M, rel=1e-7)
    assert activity.summary.ascent_m is None  # nessuna quota nel file
    assert activity.heart_rate is None
    assert activity.discarded_hr_samples == 0


def test_identity_is_the_sha256_of_the_bytes() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(5)]))

    assert decode_gpx(data).identity == GpxIdentity(hashlib.sha256(data).hexdigest())
    assert decode_gpx(data + b"\n").identity != decode_gpx(data).identity


def test_start_point_and_simplified_track() -> None:
    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([meridian(11)])))

    start = activity.summary.start_point
    assert start is not None
    assert (start.lat_deg, start.lon_deg) == (SEA_LAT_DEG, SEA_LON_DEG)
    assert activity.summary.track_polyline is not None
    track = decode_polyline(activity.summary.track_polyline)
    assert len(track) == 2  # i punti allineati si semplificano ai due estremi
    assert track[1].lat_deg == pytest.approx(SEA_LAT_DEG + 0.01, abs=1e-5)


def test_moving_time_skips_gaps_over_30_seconds() -> None:
    # Istanti 0, 10, 20, 60, 70: l'intervallo di 40 s non conta (10 + 10 + 10 = 30 s).
    seconds = [0, 10, 20, 60, 70]
    points = [
        gw.GpxPoint(SEA_LAT_DEG + 0.001 * i, SEA_LON_DEG, START + timedelta(seconds=s))
        for i, s in enumerate(seconds)
    ]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([points])))

    assert activity.elapsed_s == 70.0
    assert activity.timer_s == 30.0
    assert activity.summary.distance_m == pytest.approx(4 * STEP_M, rel=1e-7)  # la distanza sì


def test_gap_of_exactly_30_seconds_counts() -> None:
    points = gw.line_points(START, 3, step_s=30, lat_step_deg=0.001)

    assert decode_gpx(gw.build_gpx(gw.GpxSpec([points]))).timer_s == 60.0


def test_segments_are_independent_for_time_distance_and_ascent() -> None:
    # Segmento 1: t = 0, 10, 20. Segmento 2: t = 25, 35. Il salto 20 -> 25 (e la sua distanza)
    # non si conta: tempo in movimento 20 + 10 = 30 s, distanza 3 passi.
    first = gw.line_points(START, 3, step_s=10, lat_step_deg=0.001, ele_m=[100.0, 100.0, 100.0])
    second = [
        gw.GpxPoint(SEA_LAT_DEG + 0.5, SEA_LON_DEG, START + timedelta(seconds=25), 200.0),
        gw.GpxPoint(SEA_LAT_DEG + 0.501, SEA_LON_DEG, START + timedelta(seconds=35), 200.0),
    ]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([first, second])))

    assert activity.elapsed_s == 35.0
    assert activity.timer_s == 30.0
    assert activity.summary.distance_m == pytest.approx(3 * STEP_M, rel=1e-5)
    assert activity.summary.ascent_m == 0.0  # il salto di quota tra i segmenti non è dislivello


def test_ascent_with_a_3_m_threshold_by_hand() -> None:
    # Sale 100 -> 106 (il calo a 104 è sotto 3 m, quello a 102 chiude la salita: +6), scende a 99,
    # risale a 105 (+6): 12 m. Il rumore di 1-2 m non conta.
    elevations = [100.0, 101, 102, 103, 106, 104, 102, 99, 100, 105, 103]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([meridian(11, ele_m=elevations)])))

    assert activity.summary.ascent_m == 12.0


@pytest.mark.parametrize(
    ("elevations", "expected"),
    [
        ([100, 101, 100, 101, 100, 102], 0.0),  # rumore sotto la soglia
        ([100, 103], 3.0),  # esattamente la soglia conta
        ([100, 102.9], 0.0),
        ([100, 101, 102, 103, 104], 4.0),  # salita costante: conta intera
        ([110, 108, 105, 100], 0.0),  # solo discesa
        ([100, 98, 105], 7.0),  # sotto la partenza, poi su: dal punto più basso
        ([100, 105, 100, 105], 10.0),
        ([100, 90, 93], 3.0),  # dopo una discesa, risalire esattamente della soglia conta
        ([100, 90, 92.9], 0.0),
        ([100, 106, 103, 106], 9.0),  # un calo di esattamente 3 m chiude la salita (6), poi +3
        ([100], 0.0),
    ],
)
def test_ascent_cases(elevations: list[float], expected: float) -> None:
    assert positive_ascent_m(np.array(elevations, dtype=np.float64)) == expected


def test_points_without_elevation_are_skipped_in_the_ascent() -> None:
    elevations = [100.0, None, None, 110.0, None]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([meridian(5, ele_m=elevations)])))

    assert activity.summary.ascent_m == 10.0


@pytest.mark.parametrize("extension", ["v1", "v2"])
def test_heart_rate_from_garmin_extensions(extension: gw.HrExtension) -> None:
    rates = [120, 130, None, 140, 150]
    data = gw.build_gpx(gw.GpxSpec([meridian(5, hr=rates)], hr_extension=extension))

    activity = decode_gpx(data)

    assert activity.heart_rate is not None
    assert list(activity.heart_rate.seconds) == [0.0, 10.0, 30.0, 40.0]
    assert list(activity.heart_rate.bpm) == [120.0, 130.0, 140.0, 150.0]


def test_heart_rate_in_another_namespace_is_ignored() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(3, hr=[100, 110, 120])]))
    other = data.replace(gw.TPX_NAMESPACE["v2"].encode(), b"http://example.com/other")

    assert decode_gpx(other).heart_rate is None


def test_repeated_timestamps_discard_the_later_heart_rate_samples() -> None:
    points = [
        gw.GpxPoint(SEA_LAT_DEG, SEA_LON_DEG, START, hr=100),
        gw.GpxPoint(SEA_LAT_DEG, SEA_LON_DEG, START, hr=101),  # stesso istante: scartato
        gw.GpxPoint(SEA_LAT_DEG, SEA_LON_DEG, START + timedelta(seconds=10), hr=110),
    ]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([points])))

    assert activity.heart_rate is not None
    assert list(activity.heart_rate.bpm) == [100.0, 110.0]
    assert activity.discarded_hr_samples == 1


def test_gpx_1_0_is_read_like_1_1() -> None:
    elevations = [100.0, 110.0, 110.0]
    data = gw.build_gpx(gw.GpxSpec([meridian(3, ele_m=elevations)], version="1.0"))

    activity = decode_gpx(data)

    assert activity.timer_s == 20.0
    assert activity.summary.ascent_m == 10.0


def test_time_with_fraction_and_with_offset() -> None:
    fraction = gw.build_gpx(gw.GpxSpec([meridian(3)], fractional_seconds=True))
    offset = gw.build_gpx(gw.GpxSpec([meridian(3)])).replace(b"07:00:00Z", b"09:00:00+02:00")

    assert decode_gpx(fraction).start == START
    assert decode_gpx(offset).start == START


@pytest.mark.parametrize(
    ("text", "sport"),
    [
        ("running", Sport.RUNNING),
        ("Run", Sport.RUNNING),
        ("trail_running", Sport.TRAIL),
        ("Trail Running", Sport.TRAIL),
        ("cycling", Sport.CYCLING),
        ("biking", Sport.CYCLING),
        ("hiking", Sport.HIKING),
        ("walking", Sport.WALKING),
        ("strength_training", Sport.STRENGTH),
        ("kitesurf", Sport.OTHER),
        ("", Sport.OTHER),
        (None, Sport.OTHER),
    ],
)
def test_sport_from_the_type_field(text: str | None, sport: Sport) -> None:
    assert map_gpx_sport(text) is sport
    data = gw.build_gpx(gw.GpxSpec([meridian(3)], sport_type=text))
    assert decode_gpx(data).sport is sport


def test_decoding_is_deterministic() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(50, ele_m=[100.0 + i % 7 for i in range(50)])]))

    assert decode_gpx(data) == decode_gpx(data)


# --- Non attività e file incoerenti ------------------------------------------------------------


def test_gpx_without_times_is_not_an_activity() -> None:
    points = [gw.GpxPoint(SEA_LAT_DEG + 0.001 * i, SEA_LON_DEG) for i in range(5)]

    assert reason_of(gw.build_gpx(gw.GpxSpec([points]))) is FitDecodeReason.NOT_AN_ACTIVITY


def test_gpx_with_only_waypoints_and_routes_is_not_an_activity() -> None:
    body = (
        f'<gpx version="1.1" xmlns="{gw.GPX_NAMESPACE["1.1"]}">'
        '<wpt lat="1" lon="1"><time>2026-03-02T07:00:00Z</time></wpt>'
        '<rte><rtept lat="1" lon="1"><time>2026-03-02T07:00:00Z</time></rtept></rte></gpx>'
    )

    assert reason_of(body.encode()) is FitDecodeReason.NOT_AN_ACTIVITY


def test_single_point_and_zero_duration_are_not_activities() -> None:
    assert reason_of(gw.build_gpx(gw.GpxSpec([meridian(1)]))) is FitDecodeReason.NOT_AN_ACTIVITY
    same_time = [gw.GpxPoint(SEA_LAT_DEG, SEA_LON_DEG, START)] * 2
    assert reason_of(gw.build_gpx(gw.GpxSpec([same_time]))) is FitDecodeReason.NOT_AN_ACTIVITY


def test_points_always_further_than_30_seconds_have_no_moving_time() -> None:
    points = gw.line_points(START, 4, step_s=31, lat_step_deg=0.001)

    assert reason_of(gw.build_gpx(gw.GpxSpec([points]))) is FitDecodeReason.NOT_AN_ACTIVITY


def test_only_some_points_with_a_time_is_corrupt() -> None:
    points = meridian(4)
    points[2] = gw.GpxPoint(points[2].lat, points[2].lon)

    assert reason_of(gw.build_gpx(gw.GpxSpec([points]))) is FitDecodeReason.CORRUPT


def test_time_going_backwards_is_corrupt_not_repaired() -> None:
    points = meridian(4)
    points[2], points[3] = points[3], points[2]

    assert reason_of(gw.build_gpx(gw.GpxSpec([points]))) is FitDecodeReason.CORRUPT


def test_duration_over_the_limit_is_too_long() -> None:
    points = gw.line_points(START, 2, step_s=8 * 86_400)

    assert reason_of(gw.build_gpx(gw.GpxSpec([points]))) is FitDecodeReason.TOO_LONG


VALID_WITH_EXTRAS = gw.build_gpx(
    gw.GpxSpec([meridian(3, ele_m=[1.0, 2.0, 3.0], hr=[100, 101, 102])])
)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"2026-03-02T07:00:00Z", b"2026-03-02T07:00:00"),  # senza fuso: ambiguo
        (b"2026-03-02T07:00:00Z", b"2026-13-02T07:00:00Z"),  # mese 13
        (b"2026-03-02T07:00:00Z", b"yesterday"),
        (b"<ele>1.0</ele>", b"<ele>abc</ele>"),
        (b"<ele>1.0</ele>", b"<ele></ele>"),
        (b"<ele>1.0</ele>", b"<ele>1_0</ele>"),  # float() accetterebbe il trattino basso
        (b"<ele>1.0</ele>", b"<ele>1.0</ele><ele>2.0</ele>"),  # ripetuto
        (b"<gpxtpx:hr>100</gpxtpx:hr>", b"<gpxtpx:hr>fast</gpxtpx:hr>"),
        (b"<gpxtpx:hr>100</gpxtpx:hr>", b"<gpxtpx:hr>-5</gpxtpx:hr>"),
        (b'lat="-29.9990000"', b'lat="91.0"'),
        (b'lat="-29.9990000"', b'lat="nan"'),
        (b'lon="-20.0000000"', b'lon="181"'),
        (b'lon="-20.0000000"', b'lon="1e3"'),
        (b'lat="-29.9990000"', b'lat1="-30"'),  # attributo mancante
        (gw.GPX_NAMESPACE["1.1"].encode(), b"http://example.com/gpx"),
        (b"<gpx ", b"<kml "),
    ],
)
def test_malformed_values_are_corrupt_and_never_repaired(old: bytes, new: bytes) -> None:
    assert decode_gpx(VALID_WITH_EXTRAS).timer_s == 20.0  # il file di partenza è valido
    assert old in VALID_WITH_EXTRAS

    assert reason_of(VALID_WITH_EXTRAS.replace(old, new)) is FitDecodeReason.CORRUPT


def test_unknown_declared_encoding_is_corrupt_not_a_crash() -> None:
    # Trovato dal fuzzing: `encoding="ATF-8"` faceva uscire un LookupError dal parser.
    data = gw.build_gpx(gw.GpxSpec([meridian(3)])).replace(b"UTF-8", b"ATF-8")

    assert reason_of(data) is FitDecodeReason.CORRUPT


def test_truncated_and_not_xml_are_corrupt() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(5)]))

    assert reason_of(data[: len(data) // 2]) is FitDecodeReason.CORRUPT
    assert reason_of(b"\x00\x01\x02 not xml") is FitDecodeReason.CORRUPT
    assert reason_of(data[:-8]) is FitDecodeReason.CORRUPT  # manca la chiusura


def test_empty_and_oversize() -> None:
    assert reason_of(b"") is FitDecodeReason.EMPTY
    assert reason_of(b"x" * 11, GpxLimits(max_bytes=10)) is FitDecodeReason.TOO_LARGE
    data = gw.build_gpx(gw.GpxSpec([meridian(3)]))
    assert decode_gpx(data, GpxLimits(max_bytes=len(data))).timer_s == 20.0  # al limite


# --- T-SEC-02: GPX ostili -----------------------------------------------------------------------

SOON = 3.0  # secondi: un rifiuto che espande entità o legge file ci mette molto di più


def rejected_quickly(data: bytes, expected: FitDecodeReason = FitDecodeReason.CORRUPT) -> None:
    started = time.monotonic()
    assert reason_of(data) is expected
    assert time.monotonic() - started < SOON


def test_t_sec_02_gpx_with_any_doctype_is_rejected() -> None:
    rejected_quickly(gw.gpx_with_empty_doctype())


def test_t_sec_02_exponential_entity_expansion_is_rejected_without_expanding() -> None:
    data = gw.billion_laughs()  # 10^8 ripetizioni se espanso
    assert len(data) < 2000
    rejected_quickly(data)


def test_t_sec_02_quadratic_blowup_is_rejected() -> None:
    rejected_quickly(gw.quadratic_blowup())


def test_t_sec_02_external_entities_and_dtds_are_never_opened(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SEGRETO", encoding="utf-8")
    uri = secret.as_uri()

    for data in (
        gw.external_entity(uri),
        gw.external_entity("http://127.0.0.1:9/never"),
        gw.external_dtd(uri),
        gw.parameter_entity(uri),
    ):
        rejected_quickly(data)


def test_t_sec_02_deep_nesting_is_rejected_at_the_depth_limit() -> None:
    rejected_quickly(gw.deeply_nested(100_000))
    # gpx + 5 elementi = 6 livelli: al limite si legge (nessun punto: non è un'attività),
    # un livello in più si rifiuta.
    assert reason_of(gw.deeply_nested(5), LIMITS_TIGHT) is FitDecodeReason.NOT_AN_ACTIVITY
    assert reason_of(gw.deeply_nested(6), LIMITS_TIGHT) is FitDecodeReason.CORRUPT


def test_t_sec_02_point_limit_at_and_over_it() -> None:
    assert reason_of(gw.points_without_data(3), LIMITS_TIGHT) is FitDecodeReason.NOT_AN_ACTIVITY
    assert reason_of(gw.points_without_data(4), LIMITS_TIGHT) is FitDecodeReason.TOO_MANY_RECORDS


def test_t_sec_02_millions_of_points_are_rejected_at_the_default_limit() -> None:
    data = gw.points_without_data(1_000_001)  # circa 24 MB, generati al volo
    assert len(data) < GpxLimits().max_bytes

    assert reason_of(data) is FitDecodeReason.TOO_MANY_RECORDS


def test_t_sec_02_text_length_at_and_over_the_limit() -> None:
    assert reason_of(gw.huge_text(10), LIMITS_TIGHT) is FitDecodeReason.NOT_AN_ACTIVITY
    assert reason_of(gw.huge_text(11), LIMITS_TIGHT) is FitDecodeReason.CORRUPT
    rejected_quickly(gw.huge_text(5_000_000))


def test_t_sec_02_huge_attribute_is_rejected() -> None:
    rejected_quickly(gw.huge_attribute(1_000_000))


def test_t_sec_02_gpx_over_the_size_limit_is_refused_before_reading() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(3)]))
    padded = data + b" " * (GpxLimits().max_bytes - len(data) + 1)

    assert reason_of(padded) is FitDecodeReason.TOO_LARGE


# --- Invarianti con hypothesis -------------------------------------------------------------------


def naive_haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(min(1.0, h)))


coordinates = st.tuples(
    st.floats(-89.9, 89.9).map(lambda v: float(f"{v:.7f}")),
    st.floats(-179.9, 179.9).map(lambda v: float(f"{v:.7f}")),
)


@given(st.lists(coordinates, min_size=2, max_size=40))
def test_distance_matches_a_naive_loop(track: list[tuple[float, float]]) -> None:
    points = [
        gw.GpxPoint(lat, lon, START + timedelta(seconds=i)) for i, (lat, lon) in enumerate(track)
    ]

    activity = decode_gpx(gw.build_gpx(gw.GpxSpec([points])))

    expected = sum(naive_haversine_m(a, b) for a, b in itertools.pairwise(track))
    assert activity.summary.distance_m == pytest.approx(expected, rel=1e-9, abs=1e-6)


def naive_ascent(elevations: list[float], threshold: float = 3.0) -> float:
    """Stessa regola, scritta punto per punto senza alcuna riduzione preliminare."""
    low = high = elevations[0]
    rising, total = True, 0.0
    for value in elevations[1:]:
        if rising:
            if value > high:
                high = value
            elif high - value >= threshold:
                total += high - low if high - low >= threshold else 0.0
                rising, low = False, value
            elif value < low:
                low = high = value
        elif value < low:
            low = value
        elif value - low >= threshold:
            rising, high = True, value
    if rising and high - low >= threshold:
        total += high - low
    return total


elevation_lists = st.lists(st.integers(-50, 50).map(float), min_size=1, max_size=60)


@given(elevation_lists)
def test_ascent_matches_the_point_by_point_rule(elevations: list[float]) -> None:
    assert positive_ascent_m(np.array(elevations)) == naive_ascent(elevations)


@given(elevation_lists, st.integers(-1000, 1000).map(float))
def test_ascent_is_bounded_and_independent_of_the_origin(
    elevations: list[float], shift: float
) -> None:
    value = positive_ascent_m(np.array(elevations))
    rises = sum(max(b - a, 0.0) for a, b in itertools.pairwise(elevations))

    assert 0.0 <= value <= rises
    assert positive_ascent_m(np.array(elevations) + shift) == value


@given(st.data())
def test_mutated_files_are_decoded_or_rejected_never_crash(data: st.DataObject) -> None:
    base = gw.build_gpx(
        gw.GpxSpec([meridian(6, ele_m=[1.0, 5, 9, 4, 2, 8], hr=[100, 101, 102, 103, 104, 105])])
    )
    position = data.draw(st.integers(0, len(base)))
    mutation = data.draw(st.sampled_from(("flip", "cut", "insert", "repeat")))
    noise = data.draw(st.binary(min_size=1, max_size=8))
    mutated = {
        "flip": base[:position] + noise[:1] + base[position + 1 :],
        "cut": base[:position],
        "insert": base[:position] + noise + base[position:],
        "repeat": base[:position] + base[position : position + 40] * 3 + base[position:],
    }[mutation]

    with contextlib.suppress(GpxDecodeError):  # rifiutare è lecito; ogni altra eccezione no
        decode_gpx(mutated)


def test_microsecond_times_import_with_the_start_truncated_to_the_millisecond() -> None:
    data = gw.build_gpx(gw.GpxSpec([meridian(3)]))
    data = data.replace(b"T07:00:00Z", b"T07:00:00.123456Z")

    activity = decode_gpx(data)

    assert activity.start == datetime(2026, 3, 2, 7, 0, 0, 123000, tzinfo=UTC)


def test_the_gpx_error_is_its_own_and_not_a_fit_error() -> None:
    with pytest.raises(GpxDecodeError) as caught:
        decode_gpx(b"")

    assert not isinstance(caught.value, FitDecodeError)
    assert caught.value.reason is FitDecodeReason.EMPTY
