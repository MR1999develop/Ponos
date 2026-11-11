# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Time below LT1, between LT1 and LT2 and above LT2: boundaries (on and off points) and the
comparison with a naive implementation."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import DomainSeconds, seconds_per_domain


def per_domain(bpm: list[float], lt1: int = 140, lt2: int = 170) -> tuple[float, float, float]:
    ones = np.ones(len(bpm))
    return seconds_per_domain(np.array(bpm), ones, lt1, lt2).as_tuple()


@pytest.mark.parametrize(
    ("bpm", "expected"),
    [
        (139, (1.0, 0.0, 0.0)),  # off point below LT1
        (140, (0.0, 1.0, 0.0)),  # on point: LT1 belongs to "between"
        (141, (0.0, 1.0, 0.0)),
        (169, (0.0, 1.0, 0.0)),  # off point below LT2
        (170, (0.0, 0.0, 1.0)),  # on point: LT2 belongs to "above"
        (171, (0.0, 0.0, 1.0)),
    ],
)
def test_a_sample_on_a_threshold_belongs_to_the_higher_domain(
    bpm: float, expected: tuple[float, float, float]
) -> None:
    assert per_domain([bpm]) == expected


def test_seconds_are_summed_per_domain() -> None:
    bpm = np.array([100.0, 139.0, 140.0, 169.0, 170.0, 190.0])
    seconds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    found = seconds_per_domain(bpm, seconds, 140, 170)

    assert found == DomainSeconds(3.0, 7.0, 11.0)
    assert found.total == 21.0


def test_no_samples_give_zero_everywhere() -> None:
    assert seconds_per_domain(np.zeros(0), np.zeros(0), 140, 170) == DomainSeconds(0.0, 0.0, 0.0)


def test_lt1_equal_to_lt2_leaves_the_middle_domain_empty() -> None:
    assert per_domain([139, 140, 141], lt1=140, lt2=140) == (1.0, 0.0, 2.0)


def test_lt1_above_lt2_is_rejected() -> None:
    with pytest.raises(ValueError, match="LT1"):
        seconds_per_domain(np.zeros(1), np.ones(1), 171, 170)


def test_negative_seconds_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        DomainSeconds(-1.0, 0.0, 0.0)


@given(
    st.lists(st.tuples(st.integers(30, 250), st.floats(0.0, 20.0)), max_size=60),
    st.integers(40, 249),
    st.integers(1, 100),
)
def test_vectorized_domains_equal_the_naive_loop(
    samples: list[tuple[int, float]], lt1: int, gap: int
) -> None:
    lt2 = min(lt1 + gap, 250)
    below = between = above = 0.0
    for bpm, seconds in samples:
        if bpm < lt1:
            below += seconds
        elif bpm < lt2:
            between += seconds
        else:
            above += seconds

    found = seconds_per_domain(
        np.array([float(b) for b, _ in samples]), np.array([s for _, s in samples]), lt1, lt2
    )

    assert found.as_tuple() == pytest.approx((below, between, above))
