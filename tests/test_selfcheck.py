# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`--self-check`: the checks the smoke test of the packaged app relies on (T-SEC-12)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import _read_food_table, parse_command_line
from ponos_coach.selfcheck import (
    CheckResult,
    SelfCheckError,
    format_report,
    run_checks,
    run_self_check,
)


def _fails() -> None:
    raise SelfCheckError("the thing is missing")


def _passes() -> None:
    return


class TestRunChecks:
    def test_a_failure_does_not_stop_the_other_checks(self) -> None:
        results = run_checks([("first", _fails), ("second", _passes)])

        assert results == [
            CheckResult("first", "SelfCheckError: the thing is missing"),
            CheckResult("second", None),
        ]

    def test_any_exception_is_a_failed_check_not_a_crash(self) -> None:
        def broken() -> None:
            raise KeyError("x")

        assert run_checks([("broken", broken)])[0].problem is not None

    def test_report_has_one_line_per_check(self) -> None:
        report = format_report([CheckResult("a", None), CheckResult("b", "cause")])

        assert report == "ok a\nFAIL b: cause\n"


class TestCommandLine:
    def test_option_is_a_path_and_defaults_to_none(self) -> None:
        assert parse_command_line([]).self_check_report is None
        assert parse_command_line(["--self-check", "r.txt"]).self_check_report == Path("r.txt")

    def test_option_needs_the_report_name(self) -> None:
        with pytest.raises(SystemExit) as caught:
            parse_command_line(["--self-check"])
        assert caught.value.code == 2


class TestRunSelfCheck:
    def test_a_complete_source_tree_passes_every_check(self, qtbot: QtBot, tmp_path: Path) -> None:
        del qtbot  # makes sure there is a QApplication
        report = tmp_path / "report.txt"

        code = run_self_check(report, _read_food_table)

        lines = report.read_text(encoding="utf-8").splitlines()
        assert code == 0, lines
        assert all(line.startswith("ok ") for line in lines)
        assert len(lines) == 6

    def test_an_existing_report_is_never_overwritten(self, qtbot: QtBot, tmp_path: Path) -> None:
        del qtbot
        report = tmp_path / "report.txt"
        report.write_text("mine", encoding="utf-8")

        with pytest.raises(FileExistsError):
            run_self_check(report, _read_food_table)

        assert report.read_text(encoding="utf-8") == "mine"
