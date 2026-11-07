# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Verifies `nutrition/foods.json` against USDA FoodData Central (FDC).

Usage: `uv run --locked python experiments/verify_foods.py [--api-key KEY] [--apply [--force]]`

Prints, for each food, the difference from FDC in percent for each value; flags
missing ids, suspicious descriptions and differences over 10%. With `--apply`
rewrites `foods.json` with the FDC values, but only if no food has a problem
(missing id, entry discarded, difference over 10%, suspicious description):
otherwise it refuses and touches nothing, unless `--force` says that the
changes were looked at. The new document is always validated with the table's
own parser before it is written, and `source.verified_on` is set only when
every food was verified. Without network the script exits with 1 and touches
no file. Parsing and comparison are pure functions (no I/O); the HTTP response
is untrusted data and is validated.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ponos_coach.fsio.durable import write_file_atomically
from ponos_coach.nutrition.food_table import FoodTableError, parse_food_table

FOODS_PATH = Path(__file__).resolve().parents[1] / "src/ponos_coach/nutrition/foods.json"
API_URL = "https://api.nal.usda.gov/fdc/v1/foods"
BATCH_SIZE = 20
PAUSE_S = 2.0
TIMEOUT_S = 20.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_ENTRIES = 100
MAX_NUTRIENTS = 300
MAX_DESCRIPTION_CHARS = 200
MAX_VALUE_PER_100G = 1000.0
DIFF_THRESHOLD_PCT = 10.0

# FDC nutrient numbers (SR Legacy / abridged format).
_NUTRIENT_FIELDS = {"208": "kcal", "205": "carbs", "203": "protein", "204": "fat"}
_FOOD_FIELDS = (
    ("kcal_100g", "kcal"),
    ("carbs_100g", "carbs"),
    ("protein_100g", "protein"),
    ("fat_100g", "fat"),
)


class FdcFormatError(ValueError):
    """The response is not a list of foods (unusable as a whole)."""


class ApplyRefusedError(Exception):
    """The FDC values must not be written to the table (the reason is in the message)."""


@dataclass(frozen=True, slots=True)
class FdcFood:
    """Validated FDC entry: values per 100 g."""

    fdc_id: int
    description: str
    data_type: str
    kcal: float
    carbs: float
    protein: float
    fat: float


@dataclass(frozen=True, slots=True)
class Row:
    """Comparison of one food of the table with FDC."""

    food_id: str
    fdc_id: int
    problem: str | None
    description: str | None
    diffs_pct: tuple[float, ...]
    suspicious_description: bool

    @property
    def over_threshold(self) -> bool:
        """True if any value differs by more than the threshold."""
        return any(d > DIFF_THRESHOLD_PCT for d in self.diffs_pct)

    @property
    def has_problem(self) -> bool:
        """True if the food is not verified: no usable FDC entry, a difference over the threshold
        or a description that does not look like the food."""
        return self.problem is not None or self.over_threshold or self.suspicious_description


def _number(value: object) -> float | None:
    """A finite non-negative number within bounds, else None (bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= MAX_VALUE_PER_100G:
        return None
    return number


def _nutrient_entry(item: object) -> tuple[str, float] | None:
    """(field name, amount) of a valid nutrient entry of interest, else None."""
    if not isinstance(item, dict):
        return None
    number = item.get("nutrientNumber", item.get("number"))
    unit = item.get("unitName")
    if isinstance(number, int) and not isinstance(number, bool):  # FDC gives it as either
        number = str(number)
    if not isinstance(number, str) or number not in _NUTRIENT_FIELDS:
        return None
    if number == "208" and (not isinstance(unit, str) or unit.upper() != "KCAL"):
        return None
    amount = _number(item.get("value", item.get("amount")))
    return None if amount is None else (_NUTRIENT_FIELDS[number], amount)


def _parse_entry(entry: object) -> FdcFood | str:
    """Validated food, or the reason why the entry is unusable."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    fdc_id = entry.get("fdcId")
    description = entry.get("description")
    nutrients = entry.get("foodNutrients")
    if isinstance(fdc_id, bool) or not isinstance(fdc_id, int):
        return "missing or invalid fdcId"
    if not isinstance(description, str) or not isinstance(nutrients, list):
        return "missing description or nutrients"
    found: dict[str, float] = {}
    for item in nutrients[:MAX_NUTRIENTS]:
        parsed = _nutrient_entry(item)
        if parsed is not None:
            found.setdefault(parsed[0], parsed[1])
    missing = sorted({"kcal", "carbs", "protein", "fat"} - found.keys())
    if missing:
        return "missing or invalid nutrients: " + ", ".join(missing)
    data_type = entry.get("dataType")
    return FdcFood(
        fdc_id=fdc_id,
        description=description[:MAX_DESCRIPTION_CHARS],
        data_type=data_type[:40] if isinstance(data_type, str) else "unknown",
        kcal=found["kcal"],
        carbs=found["carbs"],
        protein=found["protein"],
        fat=found["fat"],
    )


def parse_fdc_response(payload: object) -> tuple[dict[int, FdcFood], dict[int, str]]:
    """Turns a decoded FDC response into (valid foods by id, reasons by id).

    Raises FdcFormatError if the payload is not a list or is too long.
    Invalid entries are never repaired: they are reported (by id if known).
    """
    if not isinstance(payload, list) or len(payload) > MAX_ENTRIES:
        raise FdcFormatError("the response is not a list of foods of acceptable size")
    foods: dict[int, FdcFood] = {}
    rejected: dict[int, str] = {}
    for entry in payload:
        parsed = _parse_entry(entry)
        if isinstance(parsed, FdcFood):
            foods[parsed.fdc_id] = parsed
        elif isinstance(entry, dict) and isinstance(entry.get("fdcId"), int):
            rejected[entry["fdcId"]] = parsed
    return foods, rejected


def decode_json_bytes(raw: bytes) -> object:
    """Decodes a response body; raises FdcFormatError if too big or not JSON."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FdcFormatError("the response exceeds the size limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise FdcFormatError("the response is not valid JSON") from error


def _stems(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]{4,}", text.lower())}


def is_suspicious_description(name: str, description: str) -> bool:
    """True if no word (4+ letters, plural ignored) of the name is in the description."""
    wanted = _stems(name)
    return bool(wanted) and not wanted & _stems(description)


def diff_pct(current: float, reference: float) -> float:
    """Absolute difference of `current` from `reference` in percent of the reference."""
    if reference > 0:
        return abs(current - reference) / reference * 100.0
    return 0.0 if current == 0 else math.inf


def compare_foods(
    foods: Sequence[Mapping[str, Any]],
    fdc: Mapping[int, FdcFood],
    rejected: Mapping[int, str],
) -> list[Row]:
    """One row per food of the table, in order."""
    rows: list[Row] = []
    for food in foods:
        fdc_id = int(food["fdc_id"])
        ref = fdc.get(fdc_id)
        if ref is None:
            problem = rejected.get(fdc_id, "id not returned by FDC")
            rows.append(Row(food["id"], fdc_id, problem, None, (), False))
            continue
        diffs = tuple(diff_pct(float(food[k]), getattr(ref, a)) for k, a in _FOOD_FIELDS)
        suspicious = is_suspicious_description(food["name"], ref.description)
        rows.append(Row(food["id"], fdc_id, None, ref.description, diffs, suspicious))
    return rows


def format_report(rows: Iterable[Row]) -> str:
    """Human-readable comparison; one line per food plus the flags."""
    lines = []
    for row in rows:
        if row.problem is not None:
            lines.append(f"{row.food_id} (fdc {row.fdc_id}): PROBLEM {row.problem}")
            continue
        parts = " ".join(
            f"{label}={d:.1f}%" for (_, label), d in zip(_FOOD_FIELDS, row.diffs_pct, strict=True)
        )
        flags = (" [OVER 10%]" if row.over_threshold else "") + (
            " [SUSPICIOUS DESCRIPTION]" if row.suspicious_description else ""
        )
        lines.append(f"{row.food_id} (fdc {row.fdc_id}) {row.description!r}: {parts}{flags}")
    return "\n".join(lines)


def apply_fdc_values(
    document: Mapping[str, Any], fdc: Mapping[int, FdcFood], today: date
) -> dict[str, Any]:
    """New document with FDC values (kcal integer, macros 1 decimal) and dataset.

    Foods without a valid FDC entry are left unchanged. Order and other fields are kept.
    `source.verified_on` is `today` only if every food has a valid FDC entry whose description
    looks like the food; otherwise it is None (the table is not verified as a whole).
    """
    result: dict[str, Any] = json.loads(json.dumps(document))
    verified = True
    for food in result["foods"]:
        ref = fdc.get(int(food["fdc_id"]))
        if ref is None or is_suspicious_description(food["name"], ref.description):
            verified = False
        if ref is None:
            continue
        food["kcal_100g"] = round(ref.kcal)
        food["carbs_100g"] = round(ref.carbs, 1)
        food["protein_100g"] = round(ref.protein, 1)
        food["fat_100g"] = round(ref.fat, 1)
        food["fdc_dataset"] = ref.data_type
    result["source"]["verified_on"] = today.isoformat() if verified else None
    return result


def render_applied_document(
    document: Mapping[str, Any],
    fdc: Mapping[int, FdcFood],
    rows: Sequence[Row],
    today: date,
    *,
    force: bool = False,
) -> str:
    """Text of the table to write with the FDC values applied.

    Raises:
        ApplyRefusedError: some food has a problem and `force` is false, or the new document does
            not pass the parser of the table (nothing is to be written).
    """
    problems = [row.food_id for row in rows if row.has_problem]
    if problems and not force:
        raise ApplyRefusedError(
            f"{len(problems)} foods have problems ({', '.join(problems[:5])}...): "
            "look at the report, then use --force or fix the table"
        )
    text = json.dumps(apply_fdc_values(document, fdc, today), indent=1, ensure_ascii=False) + "\n"
    try:
        parse_food_table(text.encode("utf-8"))
    except FoodTableError as error:
        raise ApplyRefusedError("the updated table is not valid for the app") from error
    return text


def batches(ids: Sequence[int], size: int = BATCH_SIZE) -> list[list[int]]:
    """Consecutive batches of at most `size` unique ids, order kept."""
    unique = list(dict.fromkeys(ids))
    return [unique[i : i + size] for i in range(0, len(unique), size)]


def _fetch(ids: Sequence[int], api_key: str) -> bytes:
    query = urllib.parse.urlencode(
        {"fdcIds": ",".join(map(str, ids)), "format": "abridged", "api_key": api_key}
    )
    request = urllib.request.Request(  # noqa: S310
        f"{API_URL}?{query}", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        return bytes(response.read(MAX_RESPONSE_BYTES + 1))


def fetch_all(ids: Sequence[int], api_key: str) -> tuple[dict[int, FdcFood], dict[int, str]]:
    """Fetches all batches, with a pause between requests (network)."""
    foods: dict[int, FdcFood] = {}
    rejected: dict[int, str] = {}
    for number, batch in enumerate(batches(ids)):
        if number:
            time.sleep(PAUSE_S)
        ok, bad = parse_fdc_response(decode_json_bytes(_fetch(batch, api_key)))
        foods.update(ok)
        rejected.update(bad)
    return foods, rejected


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: 0 ok, 1 network or response error (no file touched)."""
    parser = argparse.ArgumentParser(description="Verify foods.json against USDA FDC.")
    parser.add_argument("--api-key", default="DEMO_KEY")
    parser.add_argument("--apply", action="store_true", help="rewrite foods.json with FDC values")
    parser.add_argument(
        "--force", action="store_true", help="with --apply: write even if some food has problems"
    )
    args = parser.parse_args(argv)
    document = json.loads(FOODS_PATH.read_text(encoding="utf-8"))
    try:
        fdc, rejected = fetch_all([int(f["fdc_id"]) for f in document["foods"]], args.api_key)
    except (OSError, TimeoutError) as error:  # URLError is an OSError
        print(f"verify_foods: network: {type(error).__name__}", file=sys.stderr)
        return 1
    except FdcFormatError as error:
        print(f"verify_foods: response: {error}", file=sys.stderr)
        return 1
    rows = compare_foods(document["foods"], fdc, rejected)
    print(format_report(rows))
    if args.apply:
        today = datetime.now(UTC).date()
        try:
            text = render_applied_document(document, fdc, rows, today, force=args.force)
        except ApplyRefusedError as error:
            print(f"verify_foods: not applied: {error}", file=sys.stderr)
            return 1
        write_file_atomically(FOODS_PATH, text.encode("utf-8"))
        print(f"foods.json updated ({len(fdc)} foods)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
