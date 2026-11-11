# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""PMC: morning ATL, CTL and TSB (T-CALC-04, examples D and E of `modello-carico.md`)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import PmcSeries, compute_pmc, ctl_share_of_recent_peak

DAY0 = date(2026, 3, 2)


def test_t_calc_04_single_impulse_matches_example_d() -> None:
    pmc = compute_pmc({DAY0: 100.0}, DAY0, DAY0 + timedelta(days=2))

    assert pmc.atl[0] == pytest.approx(100 / 7)
    assert pmc.ctl[0] == pytest.approx(100 / 42)
    assert pmc.tsb[1] == pytest.approx(100 / 42 - 100 / 7)  # about -11.905
    assert pmc.atl[1] == pytest.approx(100 / 7 * 6 / 7)  # about 12.245
    assert pmc.ctl[1] == pytest.approx(100 / 42 * 41 / 42)  # about 2.324


def test_t_calc_04_impulse_decays_with_the_two_time_constants() -> None:
    pmc = compute_pmc({DAY0: 100.0}, DAY0, DAY0 + timedelta(days=99))

    n = np.arange(100)
    assert pmc.atl == pytest.approx(100 / 7 * (6 / 7) ** n)
    assert pmc.ctl == pytest.approx(100 / 42 * (41 / 42) ** n)


def test_t_calc_04_constant_load_converges_to_the_load_and_tsb_to_zero() -> None:
    days = 600
    last = DAY0 + timedelta(days=days - 1)
    pmc = compute_pmc({DAY0 + timedelta(days=i): 50.0 for i in range(days)}, DAY0, last)

    assert pmc.atl[-1] == pytest.approx(50.0, rel=1e-9)
    assert pmc.ctl[-1] == pytest.approx(50.0, rel=1e-5)
    assert pmc.tsb[-1] == pytest.approx(0.0, abs=1e-3)


def test_t_calc_04_morning_form_uses_the_previous_day_values() -> None:
    loads = {DAY0: 80.0, DAY0 + timedelta(days=1): 20.0, DAY0 + timedelta(days=3): 120.0}
    pmc = compute_pmc(loads, DAY0, DAY0 + timedelta(days=4))

    assert pmc.tsb[0] == 0.0
    for i in range(1, 5):
        assert pmc.tsb[i] == pytest.approx(pmc.ctl[i - 1] - pmc.atl[i - 1])


def test_days_without_activity_have_zero_load() -> None:
    pmc = compute_pmc({}, DAY0, DAY0 + timedelta(days=9))

    assert not pmc.atl.any()
    assert not pmc.ctl.any()
    assert not pmc.tsb.any()
    assert pmc.days[0] == DAY0
    assert pmc.days[-1] == DAY0 + timedelta(days=9)


def test_loads_outside_the_range_are_ignored() -> None:
    inside = compute_pmc({DAY0: 10.0}, DAY0, DAY0 + timedelta(days=3))
    outside = compute_pmc(
        {DAY0 - timedelta(days=5): 999.0, DAY0: 10.0, DAY0 + timedelta(days=30): 999.0},
        DAY0,
        DAY0 + timedelta(days=3),
    )

    assert np.array_equal(inside.ctl, outside.ctl)
    assert np.array_equal(inside.atl, outside.atl)


def test_single_day_range_is_valid() -> None:
    assert len(compute_pmc({DAY0: 7.0}, DAY0, DAY0)) == 1


def test_last_day_before_first_is_rejected() -> None:
    with pytest.raises(ValueError, match="precedes"):
        compute_pmc({}, DAY0, DAY0 - timedelta(days=1))


@pytest.mark.parametrize("load", [-1.0, float("nan"), float("inf")])
def test_invalid_daily_load_is_rejected(load: float) -> None:
    with pytest.raises(ValueError, match="load"):
        compute_pmc({DAY0: load}, DAY0, DAY0)


def test_result_is_read_only_and_empty_series_is_valid() -> None:
    pmc = compute_pmc({DAY0: 1.0}, DAY0, DAY0)
    with pytest.raises(ValueError, match="read-only"):
        pmc.atl[0] = 5.0

    empty = PmcSeries.empty(DAY0)
    assert len(empty) == 0
    assert empty.days == ()


def test_series_with_different_lengths_is_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        PmcSeries(DAY0, np.zeros(2), np.zeros(3), np.zeros(2))


@given(st.lists(st.floats(0.0, 500.0), min_size=1, max_size=120))
def test_pmc_equals_closed_form_convolution(loads: list[float]) -> None:
    """Comparison with the closed form: X(n) = sum_k L(k)/tau * (1 - 1/tau)^(n-k)."""
    last = DAY0 + timedelta(days=len(loads) - 1)
    pmc = compute_pmc({DAY0 + timedelta(days=i): v for i, v in enumerate(loads)}, DAY0, last)

    for tau, got in ((7.0, pmc.atl), (42.0, pmc.ctl)):
        expected = [
            sum(loads[k] / tau * (1 - 1 / tau) ** (n - k) for k in range(n + 1))
            for n in range(len(loads))
        ]
        assert got == pytest.approx(expected, rel=1e-9, abs=1e-9)


# --- CTL as a share of the recent personal peak --------------------------------------------


def series_of_ctl(values: list[float]) -> PmcSeries:
    ctl = np.array(values, dtype=np.float64)
    return PmcSeries(date(2026, 1, 1), np.zeros_like(ctl), ctl, np.zeros_like(ctl))


def test_the_share_is_of_the_best_ctl_inside_the_window_only() -> None:
    pmc = series_of_ctl([10.0, 40.0, 20.0, 30.0, 15.0])

    shares = ctl_share_of_recent_peak(pmc, window_days=3)

    # day 4 (15) looks back at days 2-4 (20, 30, 15): the 40 of day 1 has left the window
    assert shares.tolist() == pytest.approx([1.0, 1.0, 0.5, 0.75, 0.5])


def test_a_series_that_never_rose_has_a_share_of_zero_not_a_division_error() -> None:
    assert ctl_share_of_recent_peak(series_of_ctl([0.0, 0.0])).tolist() == [0.0, 0.0]


def test_the_window_must_be_at_least_one_day() -> None:
    with pytest.raises(ValueError, match="window"):
        ctl_share_of_recent_peak(series_of_ctl([1.0]), window_days=0)


@given(
    st.lists(st.floats(0.0, 200.0), min_size=1, max_size=60),
    st.integers(1, 20),
)
def test_the_sliding_maximum_equals_the_naive_one(values: list[float], window: int) -> None:
    shares = ctl_share_of_recent_peak(series_of_ctl(values), window_days=window)

    for index, value in enumerate(values):
        peak = max(values[max(0, index - window + 1) : index + 1])
        expected = value / peak if peak > 0 else 0.0
        assert shares[index] == pytest.approx(expected)
        assert 0.0 <= shares[index] <= 1.0
