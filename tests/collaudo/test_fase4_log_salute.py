# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for the threshold test: heart rate, pace and threshold never in logs or error texts.

The whole flow runs on a real profile with recognisable values (a threshold of 167 bpm, limits of
47 and 197, a pace of 270 s/km): evaluate, apply, edit, refused undo, undo, rejections and store
failures. Then the values are searched for in every log record (message, arguments, traceback),
in everything written to the standard streams and in the text of every exception raised, with its
chain of causes and notes. The modules of the feature must not even import `logging`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.assessment.domain import (
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
)
from ponos_coach.assessment.ports import (
    AssessmentStoreError,
    ThresholdTestAlreadyStoredError,
    ThresholdTestNotFoundError,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.measurements import InvalidMeasurementsError, MeasurementUpdate
from ponos_coach.profiles.threshold_tests import ThresholdTestChangedError
from tests.builders import fit_identity
from tests.collaudo.fase4_support import (
    DAY,
    FIRST_DAY,
    START,
    Leg,
    ThresholdProfile,
    make_threshold_profile,
    run_fit,
)
from tests.collaudo.support import format_logs

MIN_BPM, MAX_BPM, THRESHOLD_BPM, PACE = 47, 197, 167, 270
SPEED_MS = 1000 / PACE  # 3.7037 m/s: the pace of the last 20 minutes is 270 s/km
ROOT = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
FEATURE_FILES = (
    *sorted((ROOT / "assessment").rglob("*.py")),
    ROOT / "profiles" / "threshold_tests.py",
    ROOT / "storage" / "sqlite_assessment.py",
)
FORBIDDEN = re.compile(rf"(?<![\w.:-])({MIN_BPM}|{MAX_BPM}|{THRESHOLD_BPM}|{PACE})(?![\w:-])")


@pytest.fixture
def tp(tmp_path: Path) -> Iterator[ThresholdProfile]:
    profile = make_threshold_profile(tmp_path)
    narrow = Physiology(
        HeartRateLimits(MIN_BPM, MAX_BPM), ThresholdHeartRate(THRESHOLD_BPM + 1), Sex.MALE
    )
    profile.profile.session.physiology.put(PhysiologyEntry(FIRST_DAY, narrow))
    yield profile
    profile.close()


def chain_text(error: BaseException) -> str:
    """Message, repr, notes and the same for every cause and context."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [str(current), repr(current), *getattr(current, "__notes__", [])]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def leaked(text: str) -> list[str]:
    return sorted(set(FORBIDDEN.findall(text)))


def run_whole_flow(tp: ThresholdProfile) -> list[BaseException]:
    """Everything a user can do with a test, with the failures; returns the exceptions raised."""
    errors: list[BaseException] = []

    def attempt(action: Callable[[], object]) -> object:
        try:
            return action()
        except Exception as exc:
            errors.append(exc)
            return None

    legs = [Leg(600, 130, 3.0), Leg(1800, THRESHOLD_BPM, SPEED_MS), Leg(300, 120, 2.5)]
    result = tp.evaluate_bytes(run_fit(legs, serial=1), "a.fit")
    assert isinstance(result, ThresholdTestResult)
    assert (result.threshold_bpm, result.pace_s_per_km) == (THRESHOLD_BPM, PACE)
    attempt(lambda: tp.evaluate_bytes(run_fit(legs, serial=2, elapsed_s=1700.0), "b.fit"))
    attempt(lambda: tp.evaluate(fit_identity(99)))  # not in the index
    applied = tp.apply(result)
    attempt(lambda: tp.apply(result))  # already a test
    attempt(lambda: tp.measurements(MeasurementUpdate(date(2030, 1, 1), threshold_bpm=170)))
    attempt(lambda: tp.measurements(MeasurementUpdate(DAY, threshold_bpm=THRESHOLD_BPM + 3)))
    attempt(lambda: tp.remove(applied))  # edited since: refused
    store = tp.history
    attempt(lambda: store.remove(fit_identity(77)))
    attempt(lambda: ThresholdTest(fit_identity(), DAY, START, 299, 5000))
    attempt(lambda: ThresholdTest(fit_identity(), DAY, START, THRESHOLD_BPM, 5000))
    return errors


def test_t_sec_08_the_flow_raises_only_texts_without_health_data(tp: ThresholdProfile) -> None:
    errors = run_whole_flow(tp)

    kinds = {type(error) for error in errors}
    assert {
        ThresholdTestAlreadyStoredError,
        InvalidMeasurementsError,
        ThresholdTestChangedError,
        ThresholdTestNotFoundError,
        ValueError,
    } <= kinds
    for error in errors:
        assert leaked(chain_text(error)) == [], type(error)


def test_t_sec_08_nothing_reaches_the_logs_or_the_streams(
    tp: ThresholdProfile, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(0)  # every level of every logger

    run_whole_flow(tp)

    assert leaked(format_logs(caplog.records)) == []
    for record in caplog.records:
        assert leaked(repr(record.args)) == []
    written = capsys.readouterr()
    assert leaked(written.out + written.err) == []


def test_t_sec_08_a_failing_store_gives_a_message_without_values(tp: ThresholdProfile) -> None:
    store = tp.history
    result = tp.evaluate_bytes(
        run_fit([Leg(600, 130, 3.0), Leg(1800, THRESHOLD_BPM, SPEED_MS), Leg(300, 120, 2.5)])
    )
    assert isinstance(result, ThresholdTestResult)
    test = ThresholdTest.of(result)
    store.add(test)
    tp.profile.session.close()  # the connection is closed: every operation fails

    errors: list[BaseException] = []
    for operation in (
        lambda: store.add(test),
        store.list_tests,
        lambda: store.get(test.identity),
    ):
        with pytest.raises(AssessmentStoreError) as raised:
            operation()
        errors.append(raised.value)

    for error in errors:
        assert leaked(chain_text(error)) == []


def test_t_sec_08_a_rejection_is_a_code_not_a_text_with_the_values() -> None:
    for rejection in ThresholdRejection:
        assert leaked(rejection.value) == []


@pytest.mark.parametrize("path", FEATURE_FILES, ids=lambda p: p.name)
def test_t_sec_08_the_feature_modules_do_not_log_or_print(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    assert not re.search(r"^\s*(import logging|from logging)", source, re.MULTILINE)
    assert not re.search(r"(?<![\w.])print\(", source)
    assert "getLogger" not in source


def test_t_sec_08_the_sentinels_are_found_when_they_are_present() -> None:
    """The detector sees a leak: without this the other tests could pass for the wrong reason."""
    logger = logging.getLogger("collaudo.fase4")
    record = logger.makeRecord("collaudo.fase4", logging.INFO, "f", 1, "threshold %d", (167,), None)

    assert leaked(format_logs([record])) == ["167"]
    assert leaked("pace 270 s/km") == ["270"]
    assert leaked(chain_text(ValueError("max 197"))) == ["197"]
    assert leaked("1670 and 2700 and x1670") == []
