# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tests of `experiments/verify_foods.py`: CIQUAL files, CREA pages and comparison (no network)."""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

_PATH = Path(__file__).resolve().parents[2] / "experiments" / "verify_foods.py"
_spec = importlib.util.spec_from_file_location("verify_foods", _PATH)
assert _spec is not None
assert _spec.loader is not None
vf: Any = importlib.util.module_from_spec(_spec)
sys.modules["verify_foods"] = vf
_spec.loader.exec_module(vf)

SHIPPED = json.loads(vf.FOODS_PATH.read_text(encoding="utf-8"))
TODAY = date(2026, 11, 9)
VERSION = "Ciqual 2025 (2025-11-03)"

# --- Values: the rule for "traces", "< x" and missing ------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", 12.0),
        ("12,5", 12.5),
        ("12.5", 12.5),
        (" 0 ", 0.0),
        ("traces", 0.0),
        ("TRACES", 0.0),
        ("tr", 0.0),  # CREA's symbol
        ("< 0,5", 0.25),  # half of the bound
        ("<0.5", 0.25),
        ("< 20", 10.0),
        ("12\xa0", 12.0),  # CREA cells end with a no-break space
        ("1000", 1000.0),
        ("1000,1", None),  # more than 1000 g per 100 g: absurd
        ("-", None),  # CIQUAL's missing value
        ("", None),
        ("n.d.", None),
        ("-3", None),
        ("1e3", None),
        ("nan", None),
        ("inf", None),
        ("<", None),
        ("< < 1", None),
        ("1,2,3", None),
        ("12 g", None),
    ],
)
def test_a_value_is_a_number_per_100_g_or_missing_never_repaired(
    text: str, expected: float | None
) -> None:
    assert vf.parse_value(text) == expected


@given(st.text(max_size=40))
def test_no_text_makes_a_value_that_is_not_a_finite_number_in_range(text: str) -> None:
    value = vf.parse_value(text)

    assert value is None or (math.isfinite(value) and 0.0 <= value <= vf.MAX_VALUE_PER_100G)


@given(st.decimals(min_value=0, max_value=1000, places=3, allow_nan=False))
def test_a_bound_is_half_of_its_number(number: Any) -> None:
    text = format(number, "f").replace(".", ",")

    assert vf.parse_value(f"< {text}") == pytest.approx(vf.parse_value(text) / 2)


# --- Synthetic CIQUAL files ----------------------------------------------------------------------


def _alim(entries: list[tuple[int, str]]) -> str:
    body = "".join(
        f"<ALIM><alim_code> {code} </alim_code><alim_nom_fr> {name} </alim_nom_fr></ALIM>"
        for code, name in entries
    )
    return f'﻿<?xml version="1.0" encoding="utf-8" ?><TABLE>{body}</TABLE>'


def _compo(rows: list[tuple[int, str, str]]) -> str:
    body = "".join(
        f"<COMPO><alim_code> {code} </alim_code><const_code> {const} </const_code>"
        f"<teneur> {escape(value)} </teneur></COMPO>"
        for code, const, value in rows
    )
    return f'<?xml version="1.0" encoding="utf-8" ?><TABLE>{body}</TABLE>'


def _food_rows(code: int, kcal: str, carbs: str, protein: str, fat: str) -> list[Any]:
    return [
        (code, "328", kcal),
        (code, "31000", carbs),
        (code, "25000", protein),
        (code, "40000", fat),
        (code, "34100", "9"),  # fibre: a constant the script does not read
    ]


def _release(folder: Path, alim: str, compo: str, stamp: str = "2025_11_03") -> Path:
    (folder / f"alim_{stamp}.xml").write_text(alim, encoding="utf-8")
    (folder / f"compo_{stamp}.xml").write_text(compo, encoding="utf-8")
    return folder


@pytest.fixture
def release(tmp_path: Path) -> Path:
    rows = [
        *_food_rows(1, "100", "20,5", "5", "traces"),
        *_food_rows(2, "55", "< 0,5", "1,25", "< 0,3"),
        *_food_rows(4, "-", "1", "1", "1"),  # energy missing
        (5, "328", "10"),  # the other three missing
        *_food_rows(99, "1", "1", "1", "1"),  # not asked for
    ]
    names = [(1, "Flocons d&apos;avoine"), (2, "Pomme"), (4, "Sans énergie"), (5, "Partiel")]
    return _release(tmp_path, _alim([*names, (99, "Autre")]), _compo(rows))


def test_the_asked_foods_are_read_with_the_rule_for_traces_and_bounds(release: Path) -> None:
    alim, compo, version = vf.ciqual_files(release)

    found, rejected = vf.read_ciqual(alim, compo, [1, 2])

    assert version == VERSION
    assert rejected == {}
    assert found[1] == vf.RefFood("1", "Flocons d'avoine", 100.0, 20.5, 5.0, 0.0)
    assert found[2] == vf.RefFood("2", "Pomme", 55.0, 0.25, 1.25, 0.15)
    assert 99 not in found


def test_missing_values_and_unknown_codes_are_reasons_not_zeros(release: Path) -> None:
    alim, compo, _ = vf.ciqual_files(release)

    found, rejected = vf.read_ciqual(alim, compo, [4, 5, 77])

    assert found == {}
    assert "kcal" in rejected[4]
    assert "carbs" in rejected[5]
    assert rejected[77] == "code not in the CIQUAL table"


def test_a_file_that_declares_a_dtd_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaa">]><TABLE>&a;</TABLE>'
    folder = _release(tmp_path, bomb, _compo([]))
    alim, compo, _ = vf.ciqual_files(folder)

    with pytest.raises(vf.ReferenceFormatError, match="DTD"):
        vf.read_ciqual(alim, compo, [1])


def test_a_dtd_split_across_the_reading_chunks_is_still_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vf, "_CHUNK", 7)
    folder = _release(tmp_path, _alim([]), '<TABLE><!doctype t [<!entity a "b">]></TABLE>')
    alim, compo, _ = vf.ciqual_files(folder)

    with pytest.raises(vf.ReferenceFormatError, match="DTD"):
        vf.read_ciqual(alim, compo, [1])


def test_a_file_over_the_size_cap_is_refused(
    release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alim, compo, _ = vf.ciqual_files(release)
    monkeypatch.setattr(vf, "MAX_COMPO_BYTES", compo.stat().st_size - 1)

    with pytest.raises(vf.ReferenceFormatError, match="larger"):
        vf.read_ciqual(alim, compo, [1])


def test_xml_that_is_not_well_formed_is_a_format_error(tmp_path: Path) -> None:
    folder = _release(tmp_path, _alim([(1, "A")]), "<TABLE><COMPO></TABLE>")
    alim, compo, _ = vf.ciqual_files(folder)

    with pytest.raises(vf.ReferenceFormatError, match="XML"):
        vf.read_ciqual(alim, compo, [1])


def test_a_release_is_one_alim_file_with_its_compo_file_of_the_same_date(tmp_path: Path) -> None:
    with pytest.raises(vf.ReferenceFormatError, match="exactly one"):
        vf.ciqual_files(tmp_path)
    (tmp_path / "alim_2025_11_03.xml").write_text(_alim([]), encoding="utf-8")
    with pytest.raises(vf.ReferenceFormatError, match="missing"):
        vf.ciqual_files(tmp_path)
    (tmp_path / "compo_2025_11_04.xml").write_text(_compo([]), encoding="utf-8")
    with pytest.raises(vf.ReferenceFormatError, match="missing"):
        vf.ciqual_files(tmp_path)
    (tmp_path / "alim_2026_01_02.xml").write_text(_alim([]), encoding="utf-8")
    with pytest.raises(vf.ReferenceFormatError, match="exactly one"):
        vf.ciqual_files(tmp_path)


# --- CREA pages (synthetic HTML in the shape of the real ones, no real values) -------------------


def _crea_page(
    title: str = "Fiocchi d'avena",
    kcal: str = "100&nbsp;",
    carbs: str = "20,5&nbsp;",
    protein: str = "5&nbsp;",
    fat: str = "tr&nbsp;",
) -> str:
    def row(label: str, unit: str, value: str) -> str:
        return (
            f'<tr class="corponutriente"><td width="250">{label}</td><td>{unit}</td>'
            f"<td>{value}</td><td></td><td></td><td>1</td><td>A</td></tr>"
        )

    return (
        f'<h1 class="article-title" itemprop="name">\n  {title}\n <meta itemprop="url"/></h1>'
        "<table><tr><td>Categoria</td><td>Cereali</td></tr>"
        + row("Acqua (g)", "g", "9")
        + row("Energia (kcal)", "kcal", kcal)
        + row("Proteine (g)", "g (N x 6,25)", protein)
        + row("Lipidi (g)", "g", fat)
        + row("Carboidrati disponibili (g)", "g", carbs)
        + "</table>"
    )


def test_a_crea_page_gives_the_title_and_the_four_values() -> None:
    assert vf.parse_crea_page("003030", _crea_page()) == vf.RefFood(
        "003030", "Fiocchi d'avena", 100.0, 20.5, 5.0, 0.0
    )


def test_a_crea_page_with_a_missing_value_or_without_the_table_is_a_reason() -> None:
    assert "kcal" in vf.parse_crea_page("1", _crea_page(kcal="n.d.&nbsp;"))
    assert "kcal" in vf.parse_crea_page("1", "<p>Not found</p>")
    assert vf.parse_crea_page("1", "x" * (vf.MAX_CREA_PAGE_BYTES + 1)) == "page too large"


@pytest.mark.parametrize("page", ["", "<tr><td>", "</td></tr></h1>", "<h1 class=article-title>"])
def test_broken_html_never_raises(page: str) -> None:
    assert isinstance(vf.parse_crea_page("1", page), str)


def test_crea_pages_are_read_from_a_folder_and_a_failure_is_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "003030.html").write_text(_crea_page(), encoding="utf-8")
    (tmp_path / "000530.html").write_bytes(b"\xff\xfe<td>")  # not UTF-8: replaced, then unusable
    (tmp_path / "000550.html").write_text("x" * (vf.MAX_CREA_PAGE_BYTES + 1), encoding="utf-8")

    found, rejected = vf.read_crea(
        ["003030", "000530", "000550", "999999", "../etc", "003030"], tmp_path
    )

    assert list(found) == ["003030"]
    assert set(rejected) == {"000530", "000550", "999999", "../etc"}
    assert rejected["../etc"] == "not read: ReferenceFormatError"
    assert rejected["999999"] == "not read: FileNotFoundError"


# --- Comparison ----------------------------------------------------------------------------------


def _food(code: int = 1, name: str = "Flocons d'avoine") -> dict[str, Any]:
    return {
        "id": "oats",
        "name": "Rolled oats",
        "ciqual_code": code,
        "ciqual_name": name,
        "kcal_100g": 100.0,
        "carbs_100g": 20.0,
        "protein_100g": 5.0,
        "fat_100g": 1.0,
    }


def _ref(code: str = "1", name: str = "Flocons d'avoine", **values: float) -> Any:
    base = {"kcal": 100.0, "carbs": 20.0, "protein": 5.0, "fat": 1.0} | values
    return vf.RefFood(code, name, **base)


def test_diff_pct_cases() -> None:
    assert vf.diff_pct(110, 100) == pytest.approx(10.0)
    assert vf.diff_pct(0, 0) == 0.0
    assert math.isinf(vf.diff_pct(1, 0))


@pytest.mark.parametrize(
    ("current", "reference", "tolerance", "expected"),
    [
        (0.4, 0.2, 0.5, False),  # 100% relative but 0.2 g: tiny value
        (1.0, 0.4, 0.5, True),  # over both
        (0.9, 0.4, 0.5, False),  # on the absolute limit: not over it
        (105, 100, 5.0, False),  # 5% relative
        (130, 100, 5.0, True),
        (104, 90, 5.0, True),  # 15.6% and 14 kcal
        (0.6, 0.0, 0.5, True),  # reference zero: infinite relative difference
        (0.3, 0.0, 0.5, False),
    ],
)
def test_a_difference_counts_only_over_both_the_relative_and_the_absolute_tolerance(
    current: float, reference: float, tolerance: float, expected: bool
) -> None:
    assert vf.is_significant_diff(current, reference, tolerance) is expected


def test_small_fat_differences_are_not_flagged_but_a_wrong_food_is() -> None:
    foods = [_food(1), {**_food(2), "id": "other"}]
    reference = {"1": _ref("1", fat=1.4), "2": _ref("2", kcal=200.0)}
    codes = {"oats": "1", "other": "2"}

    rows = vf.compare_foods(foods, codes, reference, {}, check_names=True)

    assert not rows[0].over_threshold  # fat 1.0 vs 1.4: 28% but 0.4 g
    assert rows[1].over_threshold
    assert not rows[0].has_problem
    assert rows[1].has_problem


def test_compare_flags_difference_missing_code_and_a_stored_name_that_is_not_the_entrys() -> None:
    foods = [_food(1), {**_food(2), "id": "b"}, {**_food(3), "id": "c", "ciqual_name": "Pomme"}]
    reference = {"1": _ref("1", kcal=200.0), "3": _ref("3", "Flocons d'avoine")}
    codes = {"oats": "1", "b": "2", "c": "3"}

    rows = vf.compare_foods(
        foods, codes, reference, {"2": "missing or invalid value: fat"}, check_names=True
    )

    assert rows[0].over_threshold
    assert rows[1].problem == "missing or invalid value: fat"
    assert not rows[2].name_matches
    report = vf.format_report(rows, "ciqual")
    assert "[OVER 10%]" in report
    assert "PROBLEM missing or invalid value: fat" in report
    assert "[STORED NAME DIFFERS]" in report


def test_names_are_not_compared_with_crea_and_foods_without_a_code_are_skipped() -> None:
    foods = [_food(1, name="anything"), {**_food(2), "id": "no_crea"}]

    rows = vf.compare_foods(
        foods, {"oats": "003030"}, {"003030": _ref("003030")}, {}, check_names=False
    )

    assert [r.food_id for r in rows] == ["oats"]
    assert not rows[0].has_problem


def test_a_code_nobody_returned_is_a_problem_with_a_default_reason() -> None:
    rows = vf.compare_foods([_food(1)], {"oats": "1"}, {}, {}, check_names=True)

    assert rows[0].problem == "code not found"


# --- --apply: refused without entries, revalidated, verified only when everything is -------------


def _refs_like_the_table(document: dict[str, Any]) -> dict[int, Any]:
    """CIQUAL entries identical to the table's own values: nothing differs."""
    return {
        food["ciqual_code"]: vf.RefFood(
            str(food["ciqual_code"]),
            food["ciqual_name"],
            food["kcal_100g"],
            food["carbs_100g"],
            food["protein_100g"],
            food["fat_100g"],
        )
        for food in document["foods"]
    }


def _rows(document: dict[str, Any], refs: dict[int, Any]) -> list[Any]:
    codes = {f["id"]: str(f["ciqual_code"]) for f in document["foods"]}
    rows: list[Any] = vf.compare_foods(
        document["foods"],
        codes,
        {str(k): v for k, v in refs.items()},
        {},
        check_names=True,
    )
    return rows


def test_apply_rewrites_values_names_and_version_and_keeps_the_rest() -> None:
    document: dict[str, Any] = {
        "source": {"verified_on": None, "name": "x", "version": ""},
        "foods": [_food(1, ""), {**_food(2, ""), "id": "other"}],
    }
    refs = {1: _ref("1", "Flocons", kcal=379.44, carbs=67.744, protein=13.26, fat=6.5)}

    updated = vf.apply_ciqual_values(document, refs, VERSION, TODAY)

    first = updated["foods"][0]
    assert (first["kcal_100g"], first["carbs_100g"], first["protein_100g"]) == (379.4, 67.74, 13.26)
    assert first["ciqual_name"] == "Flocons"
    # the second food has no CIQUAL entry: unchanged, and the table as a whole is not verified
    assert updated["foods"][1] == document["foods"][1]
    assert updated["source"] == {"verified_on": None, "name": "x", "version": VERSION}
    assert document["source"]["version"] == ""  # input not mutated
    assert list(first) == list(document["foods"][0])  # key order kept


def test_the_table_is_verified_only_when_every_food_has_its_entry() -> None:
    refs = _refs_like_the_table(SHIPPED)

    full = vf.apply_ciqual_values(SHIPPED, refs, VERSION, TODAY)
    del refs[SHIPPED["foods"][0]["ciqual_code"]]
    partial = vf.apply_ciqual_values(SHIPPED, refs, VERSION, TODAY)

    assert full["source"]["verified_on"] == "2026-11-09"
    assert partial["source"]["verified_on"] is None


def test_rendering_the_shipped_values_back_gives_the_shipped_file() -> None:
    refs = _refs_like_the_table(SHIPPED)
    rows = _rows(SHIPPED, refs)

    text = vf.render_applied_document(SHIPPED, refs, rows, SHIPPED["source"]["version"], TODAY)

    assert not any(row.has_problem for row in rows)
    assert text == vf.FOODS_PATH.read_text(encoding="utf-8")


def test_apply_is_refused_when_a_food_has_no_entry_unless_forced() -> None:
    refs = _refs_like_the_table(SHIPPED)
    del refs[SHIPPED["foods"][0]["ciqual_code"]]
    rows = _rows(SHIPPED, refs)

    with pytest.raises(vf.ApplyRefusedError, match="1 foods"):
        vf.render_applied_document(SHIPPED, refs, rows, VERSION, TODAY)
    forced = vf.render_applied_document(SHIPPED, refs, rows, VERSION, TODAY, force=True)

    assert json.loads(forced)["source"]["verified_on"] is None


def test_apply_is_refused_when_the_updated_table_would_not_parse() -> None:
    refs = _refs_like_the_table(SHIPPED)
    first = SHIPPED["foods"][0]["ciqual_code"]
    refs[first] = vf.RefFood(str(first), "x", 900.0, 1.0, 1.0, 1.0)  # energy against the macros

    with pytest.raises(vf.ApplyRefusedError, match="not valid"):
        vf.render_applied_document(SHIPPED, refs, _rows(SHIPPED, refs), VERSION, TODAY)


# --- The shipped mapping and the entry point ---------------------------------------------


def test_the_crea_mapping_covers_the_table_with_codes_and_never_with_values() -> None:
    mapping = json.loads(vf.CREA_MAPPING_PATH.read_text(encoding="utf-8"))
    ids = {food["id"] for food in SHIPPED["foods"]}

    assert set(mapping["foods"]) == ids
    for entry in mapping["foods"].values():
        assert entry["crea_code"] is None or re.fullmatch(r"[0-9A-Z]{6}", entry["crea_code"])
        assert set(entry) <= {"crea_code", "crea_title", "note"}
    codes = vf._load_crea_codes()
    assert all(vf.CREA_CODE.fullmatch(code) for code in codes.values())
    assert len(set(codes.values())) == len(codes)  # no two foods share a CREA entry


def test_every_ciqual_note_of_an_approximation_explains_what_is_missing() -> None:
    for food in SHIPPED["foods"]:
        note = food.get("ciqual_note", "")
        if note.startswith("APPROXIMATE"):
            assert "CIQUAL has" in note, food["id"]


@pytest.fixture
def shipped_release(tmp_path: Path) -> Path:
    """A CIQUAL release holding exactly the shipped values (all four constants per food)."""
    entries = [(f["ciqual_code"], f["ciqual_name"]) for f in SHIPPED["foods"]]
    rows: list[Any] = []
    for food in SHIPPED["foods"]:
        rows += _food_rows(
            food["ciqual_code"],
            str(food["kcal_100g"]),
            str(food["carbs_100g"]),
            str(food["protein_100g"]),
            str(food["fat_100g"]),
        )
    return _release(tmp_path, _alim(entries), _compo(rows))


def test_the_entry_point_reports_a_table_that_agrees_with_its_source(
    shipped_release: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = vf.main(["--ciqual-dir", str(shipped_release)])

    out = capsys.readouterr().out
    assert status == 0
    assert VERSION in out
    assert "OVER" not in out
    assert "PROBLEM" not in out


def test_the_entry_point_exits_2_on_a_table_that_differs_and_1_without_a_release(
    shipped_release: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    edited = json.loads(json.dumps(SHIPPED))
    edited["foods"][0]["kcal_100g"] = 300.0
    table = tmp_path / "foods.json"
    table.write_text(json.dumps(edited), encoding="utf-8")
    monkeypatch.setattr(vf, "FOODS_PATH", table)

    assert vf.main(["--ciqual-dir", str(shipped_release)]) == 2
    assert vf.main(["--ciqual-dir", str(tmp_path / "nothing")]) == 1
    assert "ciqual:" in capsys.readouterr().err


def test_the_entry_point_applies_only_a_valid_release_and_touches_nothing_otherwise(
    shipped_release: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    edited = json.loads(json.dumps(SHIPPED))
    edited["foods"][0]["kcal_100g"] = 300.0
    edited["source"]["verified_on"] = None
    table = tmp_path / "foods.json"
    table.write_text(json.dumps(edited), encoding="utf-8")
    monkeypatch.setattr(vf, "FOODS_PATH", table)

    assert vf.main(["--ciqual-dir", str(shipped_release), "--apply"]) == 0

    applied = json.loads(table.read_text(encoding="utf-8"))
    assert applied["foods"] == SHIPPED["foods"]  # the edited energy is back to the source's
    assert date.fromisoformat(applied["source"]["verified_on"]) >= TODAY
    assert applied["source"]["version"] == VERSION
    assert "updated" in capsys.readouterr().out

    before = table.read_bytes()
    broken = tmp_path / "broken"
    broken.mkdir()
    _release(broken, _alim([]), _compo([]))  # every code missing
    assert vf.main(["--ciqual-dir", str(broken), "--apply"]) == 1
    assert table.read_bytes() == before


def test_the_crea_comparison_is_a_report_and_never_changes_the_exit_status(
    shipped_release: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    codes = vf._load_crea_codes()
    # a page for the oats that agrees and one that does not (the other codes have no page)
    (pages / f"{codes['oats_rolled_dry']}.html").write_text(
        _crea_page(kcal="1&nbsp;", carbs="1&nbsp;", protein="1&nbsp;", fat="1&nbsp;"),
        encoding="utf-8",
    )

    status = vf.main(["--ciqual-dir", str(shipped_release), "--crea-dir", str(pages)])

    out = capsys.readouterr().out
    assert status == 0
    assert "CREA comparison" in out
    assert "[OVER 10%]" in out
    assert "not read: FileNotFoundError" in out
