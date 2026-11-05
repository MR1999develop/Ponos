# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Spike Garmin: verifica dal vivo dell'accesso a Garmin Connect (fase 0).

Si lancia a mano dall'utente nel proprio terminale:

    uv run --locked python experiments/garmin_spike.py            # esegue lo spike
    uv run --locked python experiments/garmin_spike.py --logout   # cancella ogni traccia

Non e' codice di produzione: serve a misurare (login con MFA, dimensione dei
token e portachiavi, tempi dello storico, download FIT, differenza d'orario,
funzioni del calendario) e a scrivere `garmin_spike.md` con soli dati non
personali. Password, token, id e nomi non vengono mai stampati ne' salvati
nel rapporto.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import inspect
import io
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

# --- Costanti (un solo posto) ------------------------------------------------

KEYRING_SERVICE = "PonosCoach-spike"
KEYRING_USER = "tokens"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "spike-data"
REPORT_PATH = BASE_DIR / "garmin_spike.md"
ZIP_NAME = "original.zip"
FIT_NAME = "activity.fit"

PAGE_SIZE = 100
MAX_PAGES = 200  # tetto: 20.000 attivita'
MIN_PAUSE_S = 1.0
JITTER_MAX_S = 0.75
BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 120.0
MAX_ATTEMPTS = 5  # tentativi per richiesta su 429

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


# --- Rapporto e stato --------------------------------------------------------


class SpikeError(Exception):
    """Errore dello spike con un messaggio gia' privo di dati personali."""


@dataclass
class Report:
    """Raccolta di fatti non personali e di errori dei passi."""

    facts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    steps_ok: list[str] = field(default_factory=list)
    steps_failed: list[str] = field(default_factory=list)

    def fact(self, key: str, value: object) -> None:
        self.facts[key] = str(value)


@dataclass
class Candidate:
    """Attivita' dell'elenco con FC, tenuta solo in memoria."""

    activity_id: str
    start_gmt: datetime


@dataclass
class State:
    """Dati condivisi tra i passi (solo in memoria)."""

    report: Report
    garmin: Garmin | None = None
    token_json: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    total_activities: int | None = None
    list_seconds_per_page: float | None = None
    download_seconds: list[float] = field(default_factory=list)
    offsets_s: list[float] = field(default_factory=list)


def describe_error(exc: BaseException) -> str:
    """Descrive un errore senza ripetere messaggi che potrebbero avere dati."""
    if isinstance(exc, SpikeError):
        return str(exc)
    parts = [type(exc).__name__]
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        parts.append(f"causa {type(cause).__name__}")
    return ", ".join(parts)


# --- Richieste con pausa e backoff --------------------------------------------


class Throttle:
    """Pausa tra le richieste, backoff su 429 e conteggi.

    Ogni chiamata passa da qui dopo il login. Il tetto di tentativi e quello
    del backoff sono costanti: nessuna attesa senza limite.
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
                print(f"  429: attendo {wait:.0f} s (tentativo {attempt}/{MAX_ATTEMPTS})")
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


# --- Passi --------------------------------------------------------------------


def step_login(state: State) -> None:
    """Login con email e password, MFA nel terminale; serializza i token."""
    email = input("Email Garmin: ").strip()
    password = getpass.getpass("Password Garmin (non viene mostrata): ")
    mfa_used = False

    def prompt_mfa() -> str:
        nonlocal mfa_used
        mfa_used = True
        return input("Codice MFA: ").strip()

    garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    del email, password
    started = time.monotonic()
    try:
        garmin.login()
    finally:
        state.report.fact("Durata del login (s)", f"{time.monotonic() - started:.1f}")
        state.report.fact("MFA richiesta", "si" if mfa_used else "no")
    state.garmin = garmin
    state.token_json = garmin.client.dumps()
    state.report.fact("Login", "riuscito")


def step_keyring_roundtrip(state: State) -> None:
    """Salva i token nel portachiavi e li rilegge con un secondo client."""
    if state.token_json is None:
        raise SpikeError("token non disponibili (login non riuscito)")
    size_bytes = len(state.token_json.encode("utf-8"))
    report = state.report
    report.fact("Dimensione token serializzati (byte UTF-8)", size_bytes)
    report.fact("Dimensione token serializzati (caratteri)", len(state.token_json))
    report.fact("Limite Credential Manager generico (byte)", WINDOWS_CRED_LIMIT_BYTES)
    report.fact("Backend portachiavi", type(keyring.get_keyring()).__name__)
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, state.token_json)
    except Exception as exc:
        report.fact("Salvataggio nel portachiavi", f"FALLITO ({describe_error(exc)})")
        raise SpikeError(f"salvataggio nel portachiavi fallito: {describe_error(exc)}") from exc
    report.fact("Salvataggio nel portachiavi", "riuscito")

    stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if stored != state.token_json:
        report.fact("Rilettura dal portachiavi", "DIVERSA dai token salvati")
        raise SpikeError("i token riletti dal portachiavi differiscono da quelli salvati")
    report.fact("Rilettura dal portachiavi", "identica")

    second = Garmin()
    second.login(tokenstore=stored)
    del stored
    page = second.get_activities(0, 1)
    report.fact("Secondo client solo da token", "autenticato, chiamata riuscita")
    report.fact("Chiamata del secondo client", f"{type(page).__name__} con {len(page)} elementi")


def step_list_recent(state: State) -> None:
    """Elenca le ultime 5 attivita' stampando solo tipo, data, durata, FC."""
    garmin = require_garmin(state)
    throttle = Throttle(state.report)
    items = as_activity_list(throttle.call(lambda: garmin.get_activities(0, 5)))
    print("Ultime attivita' (tipo, data, durata, FC):")
    for item in items:
        kind = (item.get("activityType") or {}).get("typeKey", "?")
        start = str(item.get("startTimeLocal", "?"))[:10]
        minutes = float(item.get("duration") or 0) / 60
        has_hr = "si" if item.get("averageHR") else "no"
        print(f"  {kind:<20} {start}  {minutes:6.1f} min  FC: {has_hr}")
    remember_candidates(state, items)
    state.report.fact("Ultime attivita' elencate", len(items))


def step_count_history(state: State) -> None:
    """Conta tutto lo storico a pagine e stima la prima sincronizzazione."""
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
            print(f"  pagina {pages}: {len(page)} attivita' (totale {total})")
            if len(page) < PAGE_SIZE:
                break
        else:
            failure = SpikeError(f"tetto di {MAX_PAGES} pagine raggiunto: storico non completo")
    except Exception as exc:
        failure = exc
    elapsed = time.monotonic() - started
    report.fact("Attivita' contate a pagine", total)
    report.fact("Pagine richieste", pages)
    report.fact("Dimensione pagina", PAGE_SIZE)
    report.fact("Richieste dell'elenco", throttle.requests)
    report.fact("Risposte 429 durante l'elenco", throttle.rate_limited)
    report.fact("Tempo totale dell'elenco (s)", f"{elapsed:.1f}")
    if pages:
        state.list_seconds_per_page = elapsed / pages
    state.total_activities = total
    if failure is not None:
        raise failure
    _report_declared_total(throttle, report, garmin)


def _report_declared_total(throttle: Throttle, report: Report, garmin: Garmin) -> None:
    """Riporta il totale dichiarato da Garmin; un errore qui non ferma lo spike."""
    try:
        declared = throttle.call(garmin.count_activities)
        report.fact("Totale dichiarato da count_activities", declared)
    except Exception as exc:
        report.fact(
            "Totale dichiarato da count_activities", f"non disponibile ({describe_error(exc)})"
        )


def step_download_and_read_fit(state: State, downloads: int) -> None:
    """Scarica uno o piu' ORIGINAL, estrae il FIT e lo legge con fitdecode."""
    garmin = require_garmin(state)
    if not state.candidates:
        raise SpikeError("nessuna attivita' con FC trovata nell'elenco")
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
            raise SpikeError(f"zip scaricato oltre il limite di {MAX_ZIP_BYTES} byte")
        fit_bytes = extract_single_fit(blob)
        if index == 0:
            (DATA_DIR / ZIP_NAME).write_bytes(blob)
            (DATA_DIR / FIT_NAME).write_bytes(fit_bytes)
        summary = read_fit(fit_bytes)
        offset = (summary.session_start - cand.start_gmt).total_seconds()
        state.offsets_s.append(offset)
        if index == 0:
            report.fact("Dimensione zip (byte)", len(blob))
            report.fact("Dimensione FIT estratto (byte)", len(fit_bytes))
            report.fact("Campioni di FC (messaggi record con FC)", summary.hr_samples)
            report.fact("Messaggi record totali", summary.record_count)
            report.fact("Intervallo mediano tra record (s)", summary.median_interval_s)
            report.fact("Campi di file_id presenti", ", ".join(summary.file_id_fields) or "nessuno")
            report.fact(
                "file_id.serial_number presente", yes_no("serial_number" in summary.file_id_fields)
            )
            report.fact(
                "file_id.time_created presente", yes_no("time_created" in summary.file_id_fields)
            )
            report.fact("session.start_time presente", "si")
    report.fact("Download ORIGINAL eseguiti", len(chosen))
    report.fact("Tempo medio di un download (s)", f"{statistics.fmean(state.download_seconds):.2f}")
    report.fact("Risposte 429 durante i download", throttle.rate_limited)
    report.fact(
        "Differenza session.start_time FIT - startTimeGMT elenco (s)",
        describe_offsets(state.offsets_s),
    )


def step_calendar_readonly(state: State) -> None:
    """Conta gli allenamenti programmati (solo lettura) ed elenca i metodi."""
    garmin = require_garmin(state)
    throttle = Throttle(state.report)
    report = state.report
    today = datetime.now(UTC).date()
    end = today + timedelta(days=CALENDAR_DAYS)
    scheduled: object = throttle.call(
        lambda: garmin.get_scheduled_workouts_by_period(today.isoformat(), end.isoformat())
    )
    count = len(scheduled) if isinstance(scheduled, list) else -1
    report.fact(f"Allenamenti programmati nei prossimi {CALENDAR_DAYS} giorni", count)
    for group, names in workout_methods(garmin).items():
        report.fact(f"Metodi {group}", "; ".join(names) or "nessuno")


# --- Funzioni di supporto --------------------------------------------------------


def require_garmin(state: State) -> Garmin:
    if state.garmin is None:
        raise SpikeError("client non autenticato (login non riuscito)")
    return state.garmin


def yes_no(value: bool) -> str:
    return "si" if value else "no"


def as_activity_list(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SpikeError(f"risposta dell'elenco inattesa: {type(raw).__name__}")
    return [item for item in raw if isinstance(item, dict)]


def parse_gmt(text: object) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def remember_candidates(state: State, items: list[dict[str, Any]]) -> None:
    """Conserva in memoria le attivita' con FC piu' recenti (id e ora GMT)."""
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
    """Estrae l'unico .fit dallo zip con tetti su voci e dimensioni.

    Il nome della voce non viene mai usato come percorso. Si rifiuta tutto
    cio' che non torna: nessuna riparazione.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SpikeError(f"zip con troppe voci ({len(infos)})")
        fits = [i for i in infos if not i.is_dir() and i.filename.lower().endswith(".fit")]
        if len(fits) != 1:
            raise SpikeError(f"attesa una sola voce .fit, trovate {len(fits)}")
        info = fits[0]
        if info.file_size > MAX_FIT_BYTES:
            raise SpikeError("FIT dichiarato oltre il limite")
        with archive.open(info) as handle:
            data = handle.read(MAX_FIT_BYTES + 1)
    if len(data) != info.file_size:
        raise SpikeError("dimensione del FIT diversa da quella dichiarata")
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
    """Legge il FIT con CRC e errori in modalita' RAISE e tetto sui frame."""
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
                raise SpikeError("FIT con troppi frame")
            if isinstance(frame, fitdecode.FitDataMessage):
                _scan_message(scan, frame)
    if scan.session_start is None:
        raise SpikeError("messaggio session con start_time assente")
    gaps = [(b - a).total_seconds() for a, b in pairwise(scan.times)]
    median = f"{statistics.median(gaps):.1f}" if gaps else "n/d"
    return FitSummary(
        scan.session_start, scan.hr_samples, scan.record_count, median, scan.file_id_fields
    )


def describe_offsets(offsets: list[float]) -> str:
    if not offsets:
        return "n/d"
    return (
        f"n={len(offsets)}, min={min(offsets):.1f}, mediana={statistics.median(offsets):.1f}, "
        f"max={max(offsets):.1f}"
    )


def workout_methods(garmin: Garmin) -> dict[str, list[str]]:
    """Nomi e firme dei metodi per allenamenti e calendario (mai chiamati)."""
    groups: dict[str, tuple[str, ...]] = {
        "per creare allenamenti": (
            "upload_workout",
            "upload_running_workout",
            "upload_cycling_workout",
            "upload_swimming_workout",
            "upload_strength_workout",
            "copy_workout",
            "update_workout",
        ),
        "per programmare": ("schedule_workout", "reschedule_workout", "create_calendar_event"),
        "per deprogrammare": ("unschedule_workout", "delete_calendar_event"),
        "per eliminare allenamenti": ("delete_workout",),
        "di lettura": (
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
                found.append(f"`{name}` ASSENTE")
        result[group] = found
    return result


# --- Logout ---------------------------------------------------------------------


def logout() -> int:
    """Cancella la voce del portachiavi e i file in spike-data."""
    status = 0
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        print("Voce del portachiavi cancellata.")
    except keyring.errors.PasswordDeleteError:
        print("Nessuna voce nel portachiavi.")
    except Exception as exc:
        print(f"garmin_spike: portachiavi: {describe_error(exc)}", file=sys.stderr)
        status = 1
    if DATA_DIR.is_dir() and not DATA_DIR.is_symlink():
        for entry in DATA_DIR.iterdir():
            if entry.is_file() and not entry.is_symlink():
                entry.unlink()
                print("File di spike-data cancellato.")
            else:
                print("garmin_spike: voce inattesa in spike-data, non cancellata", file=sys.stderr)
                status = 1
        if status == 0:
            DATA_DIR.rmdir()
    return status


# --- Rapporto -----------------------------------------------------------------------


def estimate_full_sync(state: State) -> str:
    """Stima: elenco misurato + un download per attivita' con pausa media."""
    total = state.total_activities
    if not total or not state.download_seconds or state.list_seconds_per_page is None:
        return "n/d"
    pages = -(-total // PAGE_SIZE)
    list_s = pages * state.list_seconds_per_page
    per_item = statistics.fmean(state.download_seconds) + MIN_PAUSE_S + JITTER_MAX_S / 2
    total_s = list_s + total * per_item
    return (
        f"circa {total_s / 60:.0f} minuti "
        f"({total} download x {per_item:.1f} s + elenco {list_s:.0f} s)"
    )


def write_report(state: State) -> None:
    report = state.report
    lines = [
        "# Esito dello spike Garmin",
        "",
        "Generato da `experiments/garmin_spike.py`. Solo dati non personali.",
        "",
        "## Ambiente",
        "",
        f"- Python: {platform.python_version()}",
        f"- Sistema: {platform.system()} {platform.release()}",
        f"- garminconnect: {importlib.metadata.version('garminconnect')}",
        f"- fitdecode: {importlib.metadata.version('fitdecode')}",
        f"- keyring: {importlib.metadata.version('keyring')}",
        f"- Data: {datetime.now(UTC).date().isoformat()}",
        "",
        "## Misure",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in report.facts.items()]
    lines += [
        f"- Stima della prima sincronizzazione completa: {estimate_full_sync(state)}",
        "",
        "## Passi",
        "",
        f"- Riusciti: {', '.join(report.steps_ok) or 'nessuno'}",
        f"- Falliti: {', '.join(report.steps_failed) or 'nessuno'}",
        "",
        "## Errori incontrati",
        "",
    ]
    lines += [f"- {err}" for err in report.errors] or ["- nessuno"]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- Programma ------------------------------------------------------------------------


def run_step(state: State, name: str, fn: Callable[[], None]) -> None:
    """Esegue un passo; l'errore va nel rapporto e lo spike continua."""
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
        print(f"  ERRORE: {describe_error(exc)}", file=sys.stderr)
    except Exception as exc:  # confine dello spike: nessun altro errore ferma gli altri passi
        state.report.steps_failed.append(name)
        state.report.errors.append(f"{name}: inatteso, {describe_error(exc)}")
        print(f"  ERRORE inatteso: {describe_error(exc)}", file=sys.stderr)
    else:
        state.report.steps_ok.append(name)


def quiet_logging() -> None:
    """Porta a WARNING i logger che potrebbero stampare token."""
    logging.basicConfig(level=logging.WARNING)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="garmin_spike",
        description=(
            "Spike Garmin di Ponos Coach: login, token, storico, FIT, calendario (sola lettura)."
        ),
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="cancella la voce del portachiavi e i file in experiments/spike-data/",
    )
    parser.add_argument(
        "--downloads",
        type=int,
        default=1,
        choices=range(1, MAX_DOWNLOADS + 1),
        metavar=f"1..{MAX_DOWNLOADS}",
        help="quanti file ORIGINAL scaricare per la statistica d'orario (default 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    quiet_logging()
    if args.logout:
        return logout()
    os.environ.pop("GARMINTOKENS", None)  # il login deve partire dalle credenziali
    state = State(report=Report())
    run_step(state, "login", lambda: step_login(state))
    run_step(state, "portachiavi", lambda: step_keyring_roundtrip(state))
    run_step(state, "ultime-5", lambda: step_list_recent(state))
    run_step(state, "storico", lambda: step_count_history(state))
    run_step(state, "download-fit", lambda: step_download_and_read_fit(state, args.downloads))
    run_step(state, "calendario", lambda: step_calendar_readonly(state))
    if "login" in state.report.steps_failed:
        state.report.fact("Login", "FALLITO")
    write_report(state)
    print(f"Rapporto scritto in {REPORT_PATH.name}.")
    return 1 if state.report.steps_failed else 0


if __name__ == "__main__":
    sys.exit(main())
