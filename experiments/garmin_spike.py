# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin spike: live check of access to Garmin Connect (phase 0).

Launched by hand by the user in their own terminal:

    uv run --locked python experiments/garmin_spike.py            # runs the spike
    uv run --locked python experiments/garmin_spike.py --logout   # deletes every trace

It is not production code: it serves to measure (login with MFA, size of the
tokens and keyring, history timings, FIT download, time difference,
calendar functions) and to write `garmin_spike.md` and `garmin_spike_shapes.json`
(anonymous structures of the responses) with non-personal data
only. Passwords, tokens, ids and names are never printed or saved
in the report.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import inspect
import io
import json
import logging
import os
import platform
import random
import statistics
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any

import fitdecode
import keyring
import keyring.errors
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from spike_shapes import shape_of

# --- Constants (one place) ---------------------------------------------------

KEYRING_SERVICE = "PonosCoach-spike"
KEYRING_USER = "tokens"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "spike-data"
REPORT_PATH = BASE_DIR / "garmin_spike.md"
SHAPES_PATH = BASE_DIR / "garmin_spike_shapes.json"
ZIP_NAME = "original.zip"
FIT_NAME = "activity.fit"

PAGE_SIZE = 100
MAX_PAGES = 200  # cap: 20,000 activities
MIN_PAUSE_S = 1.0
JITTER_MAX_S = 0.75
BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 120.0
MAX_ATTEMPTS = 5  # attempts per request on 429

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_FIT_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 10
MAX_FIT_FRAMES = 5_000_000
MAX_DOWNLOADS = 5
CALENDAR_DAYS = 30
WINDOWS_CRED_LIMIT_BYTES = 2560

QUIET_LOGGERS = (
    "garminconnect",
    "requests",
    "urllib3",
    "curl_cffi",
    "httpx",
    "httpcore",
    "keyring",
    "charset_normalizer",
)


# --- Report and state --------------------------------------------------------


class SpikeError(Exception):
    """Spike error with a message already free of personal data."""


@dataclass
class Report:
    """Collection of non-personal facts and of step errors."""

    facts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    steps_ok: list[str] = field(default_factory=list)
    steps_failed: list[str] = field(default_factory=list)

    def fact(self, key: str, value: object) -> None:
        self.facts[key] = str(value)


@dataclass
class Candidate:
    """Activity of the list with HR, kept in memory only."""

    activity_id: str
    start_gmt: datetime


@dataclass
class State:
    """Data shared between the steps (in memory only)."""

    report: Report
    garmin: Garmin | None = None
    token_json: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    total_activities: int | None = None
    list_seconds_per_page: float | None = None
    download_seconds: list[float] = field(default_factory=list)
    offsets_s: list[float] = field(default_factory=list)
    shapes: dict[str, object] = field(default_factory=dict)


def describe_error(exc: BaseException) -> str:
    """Describes an error without repeating messages that might contain data."""
    if isinstance(exc, SpikeError):
        return str(exc)
    parts = [type(exc).__name__]
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        parts.append(f"cause {type(cause).__name__}")
    return ", ".join(parts)


# --- Requests with pause and backoff -----------------------------------------


class Throttle:
    """Pause between requests, backoff on 429 and counts.

    Every call goes through here after the login. The attempt cap and the backoff cap
    are constants: no wait without a limit.
    """

    def __init__(self, report: Report) -> None:
        self._report = report
        self._last = 0.0
        self.requests = 0
        self.rate_limited = 0

    def call[T](self, fn: Callable[[], T]) -> T:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._pause()
            self.requests += 1
            try:
                return fn()
            except GarminConnectTooManyRequestsError:
                self.rate_limited += 1
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
                wait += random.uniform(0, JITTER_MAX_S)
                print(f"  429: waiting {wait:.0f} s (attempt {attempt}/{MAX_ATTEMPTS})")
                time.sleep(wait)
            finally:
                self._last = time.monotonic()
        raise AssertionError("irraggiungibile")  # pragma: no cover

    def _pause(self) -> None:
        if self._last == 0.0:
            return
        wanted = MIN_PAUSE_S + random.uniform(0, JITTER_MAX_S)
        remaining = wanted - (time.monotonic() - self._last)
        if remaining > 0:
            time.sleep(remaining)


# --- Steps --------------------------------------------------------------------


def step_login(state: State) -> None:
    """Login with email and password, MFA in the terminal; serialises the tokens."""
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not shown): ")
    mfa_used = False

    def prompt_mfa() -> str:
        nonlocal mfa_used
        mfa_used = True
        return input("MFA code: ").strip()

    garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    del email, password
    started = time.monotonic()
    try:
        garmin.login()
    finally:
        state.report.fact("Login duration (s)", f"{time.monotonic() - started:.1f}")
        state.report.fact("MFA required", "yes" if mfa_used else "no")
    state.garmin = garmin
    state.token_json = garmin.client.dumps()
    record_token_shape(state)
    state.report.fact("Login", "succeeded")


def step_keyring_roundtrip(state: State) -> None:
    """Saves the tokens in the keyring and reads them back with a second client."""
    if state.token_json is None:
        raise SpikeError("tokens not available (login failed)")
    size_bytes = len(state.token_json.encode("utf-8"))
    report = state.report
    report.fact("Serialised token size (UTF-8 bytes)", size_bytes)
    report.fact("Serialised token size (characters)", len(state.token_json))
    utf16_bytes = len(state.token_json.encode("utf-16-le"))
    report.fact("Serialised token size (UTF-16 bytes)", utf16_bytes)
    report.fact(
        "Tokens over the Credential Manager limit (UTF-16)",
        yes_no(utf16_bytes > WINDOWS_CRED_LIMIT_BYTES),
    )
    report.fact("Generic Credential Manager limit (bytes)", WINDOWS_CRED_LIMIT_BYTES)
    report.fact("Keyring backend", type(keyring.get_keyring()).__name__)
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, state.token_json)
    except Exception as exc:
        report.fact("Saving to the keyring", f"FAILED ({describe_error(exc)})")
        raise SpikeError(f"saving to the keyring failed: {describe_error(exc)}") from exc
    report.fact("Saving to the keyring", "succeeded")

    stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if stored != state.token_json:
        report.fact("Reading back from the keyring", "DIFFERENT from the saved tokens")
        raise SpikeError("the tokens read back from the keyring differ from the saved ones")
    report.fact("Reading back from the keyring", "identica")

    second = Garmin()
    second.login(tokenstore=stored)
    del stored
    page = second.get_activities(0, 1)
    report.fact("Second client from tokens only", "authenticated, call succeeded")
    report.fact("Call of the second client", f"{type(page).__name__} with {len(page)} items")


def step_list_recent(state: State) -> None:
    """Lists the last 5 activities printing only type, date, duration, HR."""
    garmin = require_garmin(state)
    throttle = Throttle(state.report)
    items = as_activity_list(throttle.call(lambda: garmin.get_activities(0, 5)))
    print("Latest activities (type, date, duration, HR):")
    for item in items:
        kind = (item.get("activityType") or {}).get("typeKey", "?")
        start = str(item.get("startTimeLocal", "?"))[:10]
        minutes = float(item.get("duration") or 0) / 60
        has_hr = "yes" if item.get("averageHR") else "no"
        print(f"  {kind:<20} {start}  {minutes:6.1f} min  HR: {has_hr}")
    remember_candidates(state, items)
    if items:
        state.shapes["activity_list_item"] = shape_of(items[0])
    state.report.fact("Latest activities listed", len(items))


def step_count_history(state: State) -> None:
    """Counts the whole history by pages and estimates the first synchronisation."""
    garmin = require_garmin(state)
    throttle = Throttle(state.report)
    report = state.report
    started = time.monotonic()
    total = 0
    pages = 0
    failure: BaseException | None = None
    try:
        for pages in range(1, MAX_PAGES + 1):
            start = (pages - 1) * PAGE_SIZE
            page = as_activity_list(throttle.call(partial(garmin.get_activities, start, PAGE_SIZE)))
            if pages == 1:
                remember_candidates(state, page)
            total += len(page)
            print(f"  page {pages}: {len(page)} activities (total {total})")
            if len(page) < PAGE_SIZE:
                break
        else:
            failure = SpikeError(f"cap of {MAX_PAGES} pages reached: history not complete")
    except Exception as exc:
        failure = exc
    elapsed = time.monotonic() - started
    report.fact("Activities counted by pages", total)
    report.fact("Pages requested", pages)
    report.fact("Page size", PAGE_SIZE)
    report.fact("List requests", throttle.requests)
    report.fact("429 responses during the list", throttle.rate_limited)
    report.fact("Total list time (s)", f"{elapsed:.1f}")
    if pages:
        state.list_seconds_per_page = elapsed / pages
    state.total_activities = total
    if failure is not None:
        raise failure
    _report_declared_total(throttle, state, garmin)


def _report_declared_total(throttle: Throttle, state: State, garmin: Garmin) -> None:
    """Reports the total declared by Garmin; an error here does not stop the spike."""
    report = state.report
    try:
        declared = throttle.call(garmin.count_activities)
        state.shapes["count_activities_response"] = shape_of(declared)
        report.fact("Total declared by count_activities", declared)
    except Exception as exc:
        report.fact("Total declared by count_activities", f"not available ({describe_error(exc)})")


def step_download_and_read_fit(state: State, downloads: int) -> None:
    """Downloads one or more ORIGINAL, extracts the FIT and reads it with fitdecode."""
    garmin = require_garmin(state)
    if not state.candidates:
        raise SpikeError("no activity with HR found in the list")
    throttle = Throttle(state.report)
    report = state.report
    DATA_DIR.mkdir(exist_ok=True)
    chosen = state.candidates[:downloads]
    for index, cand in enumerate(chosen):
        started = time.monotonic()
        blob = throttle.call(
            partial(
                garmin.download_activity,
                cand.activity_id,
                dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
            )
        )
        state.download_seconds.append(time.monotonic() - started)
        if len(blob) > MAX_ZIP_BYTES:
            raise SpikeError(f"downloaded zip over the limit of {MAX_ZIP_BYTES} bytes")
        fit_bytes = extract_single_fit(blob)
        if index == 0:
            (DATA_DIR / ZIP_NAME).write_bytes(blob)
            (DATA_DIR / FIT_NAME).write_bytes(fit_bytes)
        summary = read_fit(fit_bytes)
        offset = (summary.session_start - cand.start_gmt).total_seconds()
        state.offsets_s.append(offset)
        if index == 0:
            report.fact("Zip size (bytes)", len(blob))
            report.fact("Extracted FIT size (bytes)", len(fit_bytes))
            report.fact("HR samples (record messages with HR)", summary.hr_samples)
            report.fact("Total record messages", summary.record_count)
            report.fact("Median interval between records (s)", summary.median_interval_s)
            report.fact("file_id fields present", ", ".join(summary.file_id_fields) or "none")
            report.fact(
                "file_id.serial_number present", yes_no("serial_number" in summary.file_id_fields)
            )
            report.fact(
                "file_id.time_created present", yes_no("time_created" in summary.file_id_fields)
            )
            report.fact("session.start_time present", "yes")
    report.fact("ORIGINAL downloads performed", len(chosen))
    report.fact("Average download time (s)", f"{statistics.fmean(state.download_seconds):.2f}")
    report.fact("429 responses during the downloads", throttle.rate_limited)
    report.fact(
        "Difference session.start_time FIT - startTimeGMT list (s)",
        describe_offsets(state.offsets_s),
    )


def step_calendar_readonly(state: State) -> None:
    """Counts the scheduled workouts (read only) and lists the methods."""
    garmin = require_garmin(state)
    throttle = Throttle(state.report)
    report = state.report
    today = datetime.now(UTC).date()
    end = today + timedelta(days=CALENDAR_DAYS)
    scheduled: object = throttle.call(
        lambda: garmin.get_scheduled_workouts_by_period(today.isoformat(), end.isoformat())
    )
    state.shapes["scheduled_workouts_response"] = shape_of(scheduled)
    count = len(scheduled) if isinstance(scheduled, list) else -1
    report.fact(f"Scheduled workouts in the next {CALENDAR_DAYS} days", count)
    for group, names in workout_methods(garmin).items():
        report.fact(f"Methods {group}", "; ".join(names) or "none")


# --- Helper functions --------------------------------------------------------


def require_garmin(state: State) -> Garmin:
    if state.garmin is None:
        raise SpikeError("client not authenticated (login failed)")
    return state.garmin


def record_token_shape(state: State) -> None:
    """Saves only the anonymous structure of the tokens JSON (never the values)."""
    try:
        parsed: object = json.loads(state.token_json or "")
    except ValueError:
        state.shapes["token_json"] = {"type": "non-json"}
        return
    state.shapes["token_json"] = shape_of(parsed)


def write_shapes(state: State) -> None:
    """Writes `garmin_spike_shapes.json`: structures only, no values."""
    names = (
        "activity_list_item",
        "count_activities_response",
        "scheduled_workouts_response",
        "token_json",
    )
    document = {
        "note": "Anonymous structure of the responses: key names, types, lengths, "
        "orders of magnitude. No values.",
        "shapes": {name: state.shapes.get(name) for name in names},
    }
    SHAPES_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def as_activity_list(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SpikeError(f"unexpected list response: {type(raw).__name__}")
    return [item for item in raw if isinstance(item, dict)]


def parse_gmt(text: object) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def remember_candidates(state: State, items: list[dict[str, Any]]) -> None:
    """Keeps in memory the most recent activities with HR (id and GMT time)."""
    known = {c.activity_id for c in state.candidates}
    for item in items:
        start = parse_gmt(item.get("startTimeGMT"))
        activity_id = item.get("activityId")
        if not item.get("averageHR") or start is None or activity_id is None:
            continue
        if str(activity_id) in known or len(state.candidates) >= MAX_DOWNLOADS:
            continue
        state.candidates.append(Candidate(str(activity_id), start))
        known.add(str(activity_id))


def extract_single_fit(blob: bytes) -> bytes:
    """Extracts the only .fit from the zip with caps on entries and sizes.

    The entry name is never used as a path. Everything that does not add up
    is refused: no repair.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SpikeError(f"zip with too many entries ({len(infos)})")
        fits = [i for i in infos if not i.is_dir() and i.filename.lower().endswith(".fit")]
        if len(fits) != 1:
            raise SpikeError(f"expected a single .fit entry, found {len(fits)}")
        info = fits[0]
        if info.file_size > MAX_FIT_BYTES:
            raise SpikeError("FIT declared over the limit")
        with archive.open(info) as handle:
            data = handle.read(MAX_FIT_BYTES + 1)
    if len(data) != info.file_size:
        raise SpikeError("FIT size different from the declared one")
    return data


@dataclass(frozen=True)
class FitSummary:
    session_start: datetime
    hr_samples: int
    record_count: int
    median_interval_s: str
    file_id_fields: tuple[str, ...]


@dataclass
class _FitScan:
    file_id_fields: tuple[str, ...] = ()
    session_start: datetime | None = None
    hr_samples: int = 0
    record_count: int = 0
    times: list[datetime] = field(default_factory=list)


def _take_session_start(scan: _FitScan, frame: fitdecode.FitDataMessage) -> None:
    value = frame.get_value("start_time", fallback=None)
    if isinstance(value, datetime):
        scan.session_start = value if value.tzinfo else value.replace(tzinfo=UTC)


def _take_record(scan: _FitScan, frame: fitdecode.FitDataMessage) -> None:
    scan.record_count += 1
    if frame.get_value("heart_rate", fallback=None) is not None:
        scan.hr_samples += 1
    stamp = frame.get_value("timestamp", fallback=None)
    if isinstance(stamp, datetime) and len(scan.times) < 10_000:
        scan.times.append(stamp)


def _scan_message(scan: _FitScan, frame: fitdecode.FitDataMessage) -> None:
    if frame.name == "file_id" and not scan.file_id_fields:
        scan.file_id_fields = tuple(f.name for f in frame.fields)
    elif frame.name == "session" and scan.session_start is None:
        _take_session_start(scan, frame)
    elif frame.name == "record":
        _take_record(scan, frame)


def read_fit(data: bytes) -> FitSummary:
    """Reads the FIT with CRC and errors in RAISE mode and a cap on frames."""
    scan = _FitScan()
    frames = 0
    with fitdecode.FitReader(
        io.BytesIO(data),
        check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE,
    ) as reader:
        for frame in reader:
            frames += 1
            if frames > MAX_FIT_FRAMES:
                raise SpikeError("FIT with too many frames")
            if isinstance(frame, fitdecode.FitDataMessage):
                _scan_message(scan, frame)
    if scan.session_start is None:
        raise SpikeError("session message with start_time missing")
    gaps = [(b - a).total_seconds() for a, b in pairwise(scan.times)]
    median = f"{statistics.median(gaps):.1f}" if gaps else "n/d"
    return FitSummary(
        scan.session_start, scan.hr_samples, scan.record_count, median, scan.file_id_fields
    )


def describe_offsets(offsets: list[float]) -> str:
    if not offsets:
        return "n/d"
    return (
        f"n={len(offsets)}, min={min(offsets):.1f}, median={statistics.median(offsets):.1f}, "
        f"max={max(offsets):.1f}"
    )


def workout_methods(garmin: Garmin) -> dict[str, list[str]]:
    """Names and signatures of the methods for workouts and calendar (never called)."""
    groups: dict[str, tuple[str, ...]] = {
        "to create workouts": (
            "upload_workout",
            "upload_running_workout",
            "upload_cycling_workout",
            "upload_swimming_workout",
            "upload_strength_workout",
            "copy_workout",
            "update_workout",
        ),
        "to schedule": ("schedule_workout", "reschedule_workout", "create_calendar_event"),
        "to unschedule": ("unschedule_workout", "delete_calendar_event"),
        "to delete workouts": ("delete_workout",),
        "read-only": (
            "get_workouts",
            "get_scheduled_workouts",
            "get_scheduled_workouts_by_period",
            "get_calendar_events",
        ),
    }
    result: dict[str, list[str]] = {}
    for group, names in groups.items():
        found: list[str] = []
        for name in names:
            method = getattr(garmin, name, None)
            if callable(method):
                found.append(f"`{name}{inspect.signature(method)}`")
            else:
                found.append(f"`{name}` MISSING")
        result[group] = found
    return result


# --- Logout ---------------------------------------------------------------------


def logout() -> int:
    """Deletes the keyring entry and the files in spike-data."""
    status = 0
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        print("Keyring entry deleted.")
    except keyring.errors.PasswordDeleteError:
        print("No entry in the keyring.")
    except Exception as exc:
        print(f"garmin_spike: keyring: {describe_error(exc)}", file=sys.stderr)
        status = 1
    if DATA_DIR.is_dir() and not DATA_DIR.is_symlink():
        for entry in DATA_DIR.iterdir():
            if entry.is_file() and not entry.is_symlink():
                entry.unlink()
                print("File in spike-data deleted.")
            else:
                print("garmin_spike: unexpected entry in spike-data, not deleted", file=sys.stderr)
                status = 1
        if status == 0:
            DATA_DIR.rmdir()
    return status


# --- Report -----------------------------------------------------------------------


def estimate_full_sync(state: State) -> str:
    """Estimate: measured list + one download per activity with the average pause."""
    total = state.total_activities
    if not total or not state.download_seconds or state.list_seconds_per_page is None:
        return "n/d"
    pages = -(-total // PAGE_SIZE)
    list_s = pages * state.list_seconds_per_page
    per_item = statistics.fmean(state.download_seconds) + MIN_PAUSE_S + JITTER_MAX_S / 2
    total_s = list_s + total * per_item
    return (
        f"about {total_s / 60:.0f} minutes "
        f"({total} downloads x {per_item:.1f} s + list {list_s:.0f} s)"
    )


def write_report(state: State) -> None:
    report = state.report
    lines = [
        "# Garmin spike outcome",
        "",
        "Generated by `experiments/garmin_spike.py`. Non-personal data only.",
        "",
        "## Environment",
        "",
        f"- Python: {platform.python_version()}",
        f"- System: {platform.system()} {platform.release()}",
        f"- garminconnect: {importlib.metadata.version('garminconnect')}",
        f"- fitdecode: {importlib.metadata.version('fitdecode')}",
        f"- keyring: {importlib.metadata.version('keyring')}",
        f"- Date: {datetime.now(UTC).date().isoformat()}",
        "",
        "## Measurements",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in report.facts.items()]
    lines += [
        f"- Estimate of the first complete synchronisation: {estimate_full_sync(state)}",
        "",
        "## Steps",
        "",
        f"- Succeeded: {', '.join(report.steps_ok) or 'none'}",
        f"- Failed: {', '.join(report.steps_failed) or 'none'}",
        "",
        "## Errors encountered",
        "",
    ]
    lines += [f"- {err}" for err in report.errors] or ["- none"]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- Program ------------------------------------------------------------------------


def run_step(state: State, name: str, fn: Callable[[], None]) -> None:
    """Runs a step; the error goes into the report and the spike continues."""
    print(f"[{name}]")
    try:
        fn()
    except (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
        SpikeError,
        zipfile.BadZipFile,
        fitdecode.FitError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        state.report.steps_failed.append(name)
        state.report.errors.append(f"{name}: {describe_error(exc)}")
        print(f"  ERROR: {describe_error(exc)}", file=sys.stderr)
    except Exception as exc:  # spike boundary: no other error stops the other steps
        state.report.steps_failed.append(name)
        state.report.errors.append(f"{name}: unexpected, {describe_error(exc)}")
        print(f"  unexpected ERROR: {describe_error(exc)}", file=sys.stderr)
    else:
        state.report.steps_ok.append(name)


def quiet_logging() -> None:
    """Raises to WARNING the loggers that might print tokens."""
    logging.basicConfig(level=logging.WARNING)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="garmin_spike",
        description=(
            "Ponos Coach Garmin spike: login, tokens, history, FIT, calendar (read only)."
        ),
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="deletes the keyring entry and the files in experiments/spike-data/",
    )
    parser.add_argument(
        "--downloads",
        type=int,
        default=1,
        choices=range(1, MAX_DOWNLOADS + 1),
        metavar=f"1..{MAX_DOWNLOADS}",
        help="how many ORIGINAL files to download for the time statistic (default 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    quiet_logging()
    if args.logout:
        return logout()
    os.environ.pop("GARMINTOKENS", None)  # the login must start from the credentials
    state = State(report=Report())
    run_step(state, "login", lambda: step_login(state))
    run_step(state, "keyring", lambda: step_keyring_roundtrip(state))
    run_step(state, "last-5", lambda: step_list_recent(state))
    run_step(state, "history", lambda: step_count_history(state))
    run_step(state, "download-fit", lambda: step_download_and_read_fit(state, args.downloads))
    run_step(state, "calendar", lambda: step_calendar_readonly(state))
    if "login" in state.report.steps_failed:
        state.report.fact("Login", "FAILED")
    write_report(state)
    write_shapes(state)
    print(f"Report written to {REPORT_PATH.name}, structures in {SHAPES_PATH.name}.")
    return 1 if state.report.steps_failed else 0


if __name__ == "__main__":
    sys.exit(main())
