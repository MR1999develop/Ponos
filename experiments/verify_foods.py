# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Verifies `nutrition/foods.json` against the ANSES-CIQUAL table, and compares it with CREA.

Usage:
    uv run --locked python experiments/verify_foods.py --ciqual-dir DIR [--apply [--force]]
        [--crea-dir DIR | --crea-fetch]

CIQUAL is the source of the values (see `docs/dipendenze.md` for version and licence). `DIR` holds
the XML files of one release of the table, downloaded by hand from the official site
(`alim_YYYY_MM_DD.xml` and `compo_YYYY_MM_DD.xml`); they are untrusted data: size-capped, refused
if they declare a DTD, streamed (never loaded whole) and only the constants and foods of the table
are read. For each food the script reports the difference from CIQUAL in percent for each value,
flags a missing code, a stored original name that is not the one of the code and differences over
10% and over an absolute tolerance (0.5 g, 5 kcal). `--apply` rewrites the values, the original
names and the source version of `foods.json` from CIQUAL, but only if no food has a problem
(unless `--force` says the report was looked at); the new document is validated with the table's
own parser before it is written, and `source.verified_on` is set only when every food was found.

CREA ("Tabelle di composizione degli alimenti", alimentinutrizione.it) forbids copying its data
without a clear indication of the source and has no bulk download, so its values are never put in
the repository: they are only compared. `--crea-fetch` reads the page of each code of
`experiments/crea_mapping.json` (one request per second, size-capped, fixed host) and `--crea-dir`
reads pages saved by hand (`<code>.html`). The comparison is a report, never a failure: the two
tables measure carbohydrates and energy in different ways.

Exit status: 0 all fine, 1 an input or a network error (no file touched), 2 the table has problems.
Parsing and comparison are pure functions; only `main` and the readers touch files or network.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ponos_coach.fsio.durable import write_file_atomically
from ponos_coach.nutrition.food_table import FoodTableError, parse_food_table

FOODS_PATH = Path(__file__).resolve().parents[1] / "src/ponos_coach/nutrition/foods.json"
CREA_MAPPING_PATH = Path(__file__).resolve().parent / "crea_mapping.json"
MAX_ALIM_BYTES = 8 * 1024 * 1024  # the real file is about 1.6 MB
MAX_COMPO_BYTES = 128 * 1024 * 1024  # the real file is about 69 MB
MAX_CREA_PAGE_BYTES = 1024 * 1024
MAX_NAME_CHARS = 200
MAX_VALUE_PER_100G = 1000.0
CREA_URL = "https://www.alimentinutrizione.it/tabelle-nutrizionali/{code}"
CREA_CODE = re.compile(r"[0-9A-Z]{6}")
CREA_TIMEOUT_S = 20.0
CREA_PAUSE_S = 1.0
DIFF_THRESHOLD_PCT = 10.0
# A difference counts only if it also exceeds an absolute tolerance, otherwise tenths of a gram
# of fat would be flagged for being a large share of a tiny value. 0.5 g is the rounding the
# table itself uses for macros; 5 kcal is about 1% of a typical energy value per 100 g.
KCAL_TOLERANCE = 5.0
MACRO_TOLERANCE_G = 0.5

# CIQUAL constant codes: energy (EU Regulation 1169/2011, kcal), available carbohydrates,
# protein (Jones factors) and fat, all per 100 g.
_CIQUAL_CONSTANTS = {"328": "kcal", "31000": "carbs", "25000": "protein", "40000": "fat"}
_FOOD_FIELDS = (
    ("kcal_100g", "kcal"),
    ("carbs_100g", "carbs"),
    ("protein_100g", "protein"),
    ("fat_100g", "fat"),
)
_TOLERANCES = (KCAL_TOLERANCE, MACRO_TOLERANCE_G, MACRO_TOLERANCE_G, MACRO_TOLERANCE_G)
_CREA_LABELS = {
    "Energia (kcal)": "kcal",
    "Carboidrati disponibili (g)": "carbs",
    "Proteine (g)": "protein",
    "Lipidi (g)": "fat",
}
_DTD_MARKERS = (b"<!DOCTYPE", b"<!ENTITY")
_CHUNK = 1024 * 1024
_ALIM_NAME = re.compile(r"alim_(\d{4})_(\d{2})_(\d{2})\.xml")


class ReferenceFormatError(ValueError):
    """A reference file or page is unusable as a whole (too large, a DTD, not XML)."""


class ApplyRefusedError(Exception):
    """The CIQUAL values must not be written to the table (the reason is in the message)."""


@dataclass(frozen=True, slots=True)
class RefFood:
    """Validated reference entry: values per 100 g."""

    code: str
    name: str
    kcal: float
    carbs: float
    protein: float
    fat: float


@dataclass(frozen=True, slots=True)
class Row:
    """Comparison of one food of the table with a reference."""

    food_id: str
    code: str
    problem: str | None
    name: str | None
    diffs_pct: tuple[float, ...]
    significant: tuple[bool, ...] = ()
    name_matches: bool = True

    @property
    def over_threshold(self) -> bool:
        """True if any value differs by more than both the relative and the absolute tolerance."""
        return any(self.significant)

    @property
    def has_problem(self) -> bool:
        """True if the food is not verified: no usable entry, a stored original name that is not
        the entry's, or a difference over the threshold."""
        return self.problem is not None or self.over_threshold or not self.name_matches


# --- Values -----------------------------------------------------------------------------------


def parse_value(text: str) -> float | None:
    """One value of a reference table as a number per 100 g, None if it is missing.

    The rule, the same for both tables: `traces` (and CREA's `tr`) is 0; `< x` is half of x (the
    middle of the interval the table says the value lies in); a decimal comma is accepted; `-`,
    empty, `n.d.`, negative or absurd values are missing and are never repaired.
    """
    cleaned = text.replace("\xa0", " ").strip()
    if cleaned.lower() in ("traces", "tr"):
        return 0.0
    below = cleaned.startswith("<")
    number = cleaned[1:].strip() if below else cleaned
    if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", number):
        return None
    value = float(number.replace(",", "."))
    if not 0.0 <= value <= MAX_VALUE_PER_100G:
        return None
    return value / 2 if below else value


def _build_ref(code: str, name: str, raw: Mapping[str, str]) -> RefFood | str:
    """Validated reference entry, or the reason why it is unusable."""
    values: dict[str, float] = {}
    for field in ("kcal", "carbs", "protein", "fat"):
        value = parse_value(raw.get(field, ""))
        if value is None:
            return f"missing or invalid value: {field}"
        values[field] = value
    return RefFood(code, name[:MAX_NAME_CHARS], **values)


# --- CIQUAL files -----------------------------------------------------------------------------


def _refuse_unsafe_file(path: Path, limit: int) -> None:
    """Refuses a file over `limit` bytes or one that declares a DTD (entities can be abused)."""
    size = path.stat().st_size
    if size > limit:
        raise ReferenceFormatError(f"{path.name} is larger than {limit} bytes")
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            window = (tail + chunk).upper()
            if any(marker in window for marker in _DTD_MARKERS):
                raise ReferenceFormatError(f"{path.name} declares a DTD")
            tail = chunk[-16:]


def _elements(path: Path, tag: str) -> Iterator[ET.Element]:
    """The `tag` elements of the file one by one; each is cleared after use (bounded memory)."""
    iterator = ET.iterparse(path, events=("start", "end"))  # noqa: S314
    root: ET.Element | None = None
    for event, element in iterator:
        if root is None:
            root = element
        if event == "end" and element.tag == tag:
            yield element
            root.clear()


def read_ciqual(
    alim: Path, compo: Path, codes: Collection[int]
) -> tuple[dict[int, RefFood], dict[int, str]]:
    """The wanted CIQUAL foods (valid ones by `alim_code`, reasons for the others).

    Raises:
        ReferenceFormatError: a file too large, with a DTD or not well-formed XML.
    """
    wanted = {str(code) for code in codes}
    _refuse_unsafe_file(alim, MAX_ALIM_BYTES)
    _refuse_unsafe_file(compo, MAX_COMPO_BYTES)
    try:
        names = {
            (e.findtext("alim_code") or "").strip(): (e.findtext("alim_nom_fr") or "").strip()
            for e in _elements(alim, "ALIM")
        }
        raw: dict[str, dict[str, str]] = {}
        for entry in _elements(compo, "COMPO"):
            code = (entry.findtext("alim_code") or "").strip()
            constant = _CIQUAL_CONSTANTS.get((entry.findtext("const_code") or "").strip())
            if code in wanted and constant is not None:
                raw.setdefault(code, {})[constant] = entry.findtext("teneur") or ""
    except ET.ParseError as error:
        raise ReferenceFormatError("a CIQUAL file is not well-formed XML") from error
    found: dict[int, RefFood] = {}
    rejected: dict[int, str] = {}
    for code in sorted(wanted, key=int):
        built = _build_ref(code, names[code], raw.get(code, {})) if code in names else None
        if isinstance(built, RefFood):
            found[int(code)] = built
        else:
            rejected[int(code)] = built or "code not in the CIQUAL table"
    return found, rejected


def ciqual_files(folder: Path) -> tuple[Path, Path, str]:
    """(alim file, compo file, version text) of the one release in `folder`.

    Raises:
        ReferenceFormatError: no release, or the two files do not have the same date.
    """
    found = sorted(p for p in folder.glob("alim_*.xml") if _ALIM_NAME.fullmatch(p.name))
    if len(found) != 1:
        raise ReferenceFormatError("the folder must hold exactly one alim_YYYY_MM_DD.xml")
    match = _ALIM_NAME.fullmatch(found[0].name)
    assert match is not None  # noqa: S101 (filtered above)
    year, month, day = match.groups()
    compo = folder / f"compo_{year}_{month}_{day}.xml"
    if not compo.is_file():
        raise ReferenceFormatError(f"{compo.name} is missing next to {found[0].name}")
    return found[0], compo, f"Ciqual {year} ({year}-{month}-{day})"


# --- CREA pages -------------------------------------------------------------------------------


class _CreaPage(HTMLParser):
    """Collects the title and the cells of every table row of a CREA page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag == "td" and self.rows:
            self._cell = []
        elif tag == "h1" and ("class", "article-title") in attrs:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "h1":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif self._in_title:
            self.title += data


def parse_crea_page(code: str, page: str) -> RefFood | str:
    """Validated CREA entry from the HTML of its page, or the reason why it is unusable."""
    if len(page) > MAX_CREA_PAGE_BYTES:
        return "page too large"
    parser = _CreaPage()
    parser.feed(page)
    raw: dict[str, str] = {}
    for cells in parser.rows:
        if len(cells) >= 3 and cells[0] in _CREA_LABELS:
            raw.setdefault(_CREA_LABELS[cells[0]], cells[2])
    return _build_ref(code, parser.title.strip(' "|\n\t'), raw)


def _read_crea_page(code: str, folder: Path | None) -> str:
    """The HTML of a CREA page: from `folder` when given, else fetched (network)."""
    if not CREA_CODE.fullmatch(code):
        raise ReferenceFormatError("not a CREA code")
    if folder is not None:
        path = folder / f"{code}.html"
        if path.stat().st_size > MAX_CREA_PAGE_BYTES:
            raise ReferenceFormatError("page too large")
        return path.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(  # noqa: S310 (fixed https host, code validated above)
        CREA_URL.format(code=code), headers={"Accept": "text/html"}
    )
    with urllib.request.urlopen(request, timeout=CREA_TIMEOUT_S) as response:  # noqa: S310
        body = bytes(response.read(MAX_CREA_PAGE_BYTES + 1))
    return body.decode("utf-8", errors="replace")


def read_crea(
    codes: Iterable[str], folder: Path | None
) -> tuple[dict[str, RefFood], dict[str, str]]:
    """CREA entries by code (valid ones, reasons for the others); no error is fatal."""
    found: dict[str, RefFood] = {}
    rejected: dict[str, str] = {}
    for number, code in enumerate(dict.fromkeys(codes)):
        if number and folder is None:
            time.sleep(CREA_PAUSE_S)
        try:
            parsed = parse_crea_page(code, _read_crea_page(code, folder))
        except (OSError, TimeoutError, ReferenceFormatError) as error:
            rejected[code] = f"not read: {type(error).__name__}"
            continue
        if isinstance(parsed, RefFood):
            found[code] = parsed
        else:
            rejected[code] = parsed
    return found, rejected


# --- Comparison -------------------------------------------------------------------------------


def diff_pct(current: float, reference: float) -> float:
    """Absolute difference of `current` from `reference` in percent of the reference."""
    if reference > 0:
        return abs(current - reference) / reference * 100.0
    return 0.0 if current == 0 else math.inf


def is_significant_diff(current: float, reference: float, tolerance: float) -> bool:
    """True if `current` differs from `reference` by more than the relative threshold (10%) and
    by more than the absolute `tolerance` (same unit as the values)."""
    return (
        diff_pct(current, reference) > DIFF_THRESHOLD_PCT and abs(current - reference) > tolerance
    )


def compare_foods(
    foods: Sequence[Mapping[str, Any]],
    codes: Mapping[str, str],
    reference: Mapping[str, RefFood],
    rejected: Mapping[str, str],
    *,
    check_names: bool,
) -> list[Row]:
    """One row per food of the table that has a code in `codes` (food id to reference code).

    With `check_names` the stored `ciqual_name` must be the entry's name.
    """
    rows: list[Row] = []
    for food in foods:
        code = codes.get(food["id"])
        if code is None:
            continue
        ref = reference.get(code)
        if ref is None:
            rows.append(Row(food["id"], code, rejected.get(code, "code not found"), None, ()))
            continue
        pairs = [(float(food[k]), getattr(ref, a)) for k, a in _FOOD_FIELDS]
        diffs = tuple(diff_pct(cur, val) for cur, val in pairs)
        significant = tuple(
            is_significant_diff(cur, val, tol)
            for (cur, val), tol in zip(pairs, _TOLERANCES, strict=True)
        )
        same_name = not check_names or food.get("ciqual_name") == ref.name
        rows.append(Row(food["id"], code, None, ref.name, diffs, significant, same_name))
    return rows


def format_report(rows: Iterable[Row], label: str) -> str:
    """Human-readable comparison; one line per food plus the flags."""
    lines = []
    for row in rows:
        if row.problem is not None:
            lines.append(f"{row.food_id} ({label} {row.code}): PROBLEM {row.problem}")
            continue
        parts = " ".join(
            f"{name}={d:.1f}%" for (_, name), d in zip(_FOOD_FIELDS, row.diffs_pct, strict=True)
        )
        flags = (" [OVER 10%]" if row.over_threshold else "") + (
            "" if row.name_matches else " [STORED NAME DIFFERS]"
        )
        lines.append(f"{row.food_id} ({label} {row.code}) {row.name!r}: {parts}{flags}")
    return "\n".join(lines)


# --- --apply ----------------------------------------------------------------------------------


def apply_ciqual_values(
    document: Mapping[str, Any], ciqual: Mapping[int, RefFood], version: str, today: date
) -> dict[str, Any]:
    """New document with the CIQUAL values (kcal one decimal, macros two), names and version.

    Foods without a valid CIQUAL entry are left unchanged. Order and other fields are kept.
    `source.verified_on` is `today` only if every food has a valid CIQUAL entry; otherwise it is
    None (the table is not verified as a whole).
    """
    result: dict[str, Any] = json.loads(json.dumps(document))
    verified = True
    for food in result["foods"]:
        ref = ciqual.get(int(food["ciqual_code"]))
        if ref is None:
            verified = False
            continue
        food["ciqual_name"] = ref.name
        food["kcal_100g"] = round(ref.kcal, 1)
        food["carbs_100g"] = round(ref.carbs, 2)
        food["protein_100g"] = round(ref.protein, 2)
        food["fat_100g"] = round(ref.fat, 2)
    result["source"]["version"] = version
    result["source"]["verified_on"] = today.isoformat() if verified else None
    return result


def render_applied_document(
    document: Mapping[str, Any],
    ciqual: Mapping[int, RefFood],
    rows: Sequence[Row],
    version: str,
    today: date,
    *,
    force: bool = False,
) -> str:
    """Text of the table to write with the CIQUAL values applied.

    Raises:
        ApplyRefusedError: some food has no usable CIQUAL entry and `force` is false, or the new
            document does not pass the parser of the table (nothing is to be written).
    """
    problems = [row.food_id for row in rows if row.problem is not None]
    if problems and not force:
        raise ApplyRefusedError(
            f"{len(problems)} foods have problems ({', '.join(problems[:5])}...): "
            "look at the report, then use --force or fix the table"
        )
    text = (
        json.dumps(
            apply_ciqual_values(document, ciqual, version, today), indent=1, ensure_ascii=False
        )
        + "\n"
    )
    try:
        parse_food_table(text.encode("utf-8"))
    except FoodTableError as error:
        raise ApplyRefusedError("the updated table is not valid for the app") from error
    return text


# --- Entry point ------------------------------------------------------------------------------


def _load_crea_codes() -> dict[str, str]:
    """CREA code of each food id from `crea_mapping.json` (foods without an entry are skipped)."""
    mapping = json.loads(CREA_MAPPING_PATH.read_text(encoding="utf-8"))
    return {
        food_id: str(entry["crea_code"])
        for food_id, entry in mapping["foods"].items()
        if entry.get("crea_code")
    }


def _report_crea(document: Mapping[str, Any], folder: Path | None) -> None:
    """Prints the CREA comparison of the values the table has now (never a failure)."""
    codes = _load_crea_codes()
    found, rejected = read_crea(codes.values(), folder)
    rows = compare_foods(document["foods"], codes, found, rejected, check_names=False)
    print("\nCREA comparison (report only, CREA values are never stored):")
    print(format_report(rows, "crea"))
    print(f"{sum(r.over_threshold for r in rows)} of {len(rows)} compared foods differ over 10%")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify foods.json against ANSES-CIQUAL.")
    parser.add_argument("--ciqual-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="rewrite foods.json from CIQUAL")
    parser.add_argument(
        "--force", action="store_true", help="with --apply: write even if some food has problems"
    )
    crea = parser.add_mutually_exclusive_group()
    crea.add_argument("--crea-dir", type=Path, help="folder of CREA pages saved as <code>.html")
    crea.add_argument("--crea-fetch", action="store_true", help="read the CREA pages (network)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: 0 ok, 1 input or network error (no file touched), 2 problems in the table."""
    args = _parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # the names are French, the console may not be
    document = json.loads(FOODS_PATH.read_text(encoding="utf-8"))
    codes = {f["id"]: str(f["ciqual_code"]) for f in document["foods"]}
    try:
        alim, compo, version = ciqual_files(args.ciqual_dir)
        found, rejected = read_ciqual(alim, compo, [int(c) for c in codes.values()])
    except (OSError, ReferenceFormatError) as error:
        print(f"verify_foods: ciqual: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    rows = compare_foods(
        document["foods"],
        codes,
        {str(k): v for k, v in found.items()},
        {str(k): v for k, v in rejected.items()},
        check_names=True,
    )
    print(f"{version}\n{format_report(rows, 'ciqual')}")
    if document["source"].get("version") != version:
        print(f"source.version is {document['source'].get('version')!r}, files are {version!r}")
    if args.apply:
        today = datetime.now(UTC).date()
        try:
            text = render_applied_document(document, found, rows, version, today, force=args.force)
        except ApplyRefusedError as error:
            print(f"verify_foods: not applied: {error}", file=sys.stderr)
            return 1
        write_file_atomically(FOODS_PATH, text.encode("utf-8"))
        print(f"foods.json updated ({len(found)} foods)")
        document = json.loads(text)
    if args.crea_dir is not None or args.crea_fetch:
        _report_crea(document, args.crea_dir)
    return 2 if any(row.has_problem for row in rows) and not args.apply else 0


if __name__ == "__main__":
    sys.exit(main())
