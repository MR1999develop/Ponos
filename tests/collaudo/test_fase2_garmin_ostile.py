# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-03, T-SYNC-03 e Retry-After ostili: risposte di Garmin cattive all'adattatore vero.

L'adattatore (`GarminActivitySource`) e il suo client finto (`ScriptedClient`): le risposte sono
JSON non fidato; tutto ciò che non è una voce valida si scarta o ferma il giro con un errore
della porta, mai un'eccezione qualsiasi. Nessuna rete.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ponos_coach.activities.domain import MAX_PAGE_SIZE, GarminActivityId, SyncStatus
from ponos_coach.activities.ports import (
    ActivitySourceError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    RateLimitedError,
)
from ponos_coach.activities.sync import StopReason
from ponos_coach.garmin.source import GarminActivitySource
from ponos_coach.ui.sync_model import SyncFinished
from tests.collaudo.fase2_support import adapter_rig, build_stack, scripted_client
from tests.fakes.garmin import FIRST_ACTIVITY_ID, make_remote_history
from tests.garmin.scripted import ScriptedClient, entry_for, rate_limit_error

GOOD = entry_for(make_remote_history(1)[0])
HTML = "<script>alert(1)</script><img src=x onerror=alert(2)>"


def nested(depth: int) -> object:
    """Una lista annidata `depth` volte, costruita a mano (`json.loads` rifiuterebbe)."""
    value: object = []
    for _ in range(depth):
        value = [value]
    return value


def nested_dict(depth: int) -> object:
    value: object = {}
    for _ in range(depth):
        value = {"a": value}
    return value


class RawListing(ScriptedClient):
    """Un client che risponde sempre `raw` all'elenco, anche `None`."""

    def __init__(self, raw: object) -> None:
        super().__init__()
        self._raw = raw

    def list_activities(self, offset: int, limit: int) -> object:  # noqa: ARG002
        return self._raw


def page_of(raw: object) -> tuple[int, int]:
    """(attività valide, voci ricevute) di una pagina data come risposta dell'elenco."""
    source = GarminActivitySource(RawListing(raw))
    page = source.list_page(0, 100)
    return len(page.items), page.raw_count


# --- T-SEC-03: l'elenco ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "12/../34",
        "..\\..\\Windows\\win.ini",
        "1000001; DROP TABLE x",
        "0x10",
        "1e3",
        "\u0661\u0662\u0663",
        " 123",
        "123\n",
        True,
        1.5,
        -1,
        0,
        2**70,
        None,
        [1],
        {"id": 1},
        "",
        "9" * 5000,
    ],
)
def test_t_sec_03_ids_that_are_not_plain_numbers_discard_the_entry(bad_id: object) -> None:
    entry = {**GOOD, "activityId": bad_id}

    assert page_of([GOOD, entry]) == (1, 2)


@pytest.mark.parametrize(
    "name_field",
    ["activityName", "description", "locationName", "ownerFullName", "eventType"],
)
def test_t_sec_03_html_and_script_in_free_text_never_reach_the_domain(name_field: str) -> None:
    source = GarminActivitySource(RawListing([{**GOOD, name_field: HTML * 50}]))

    page = source.list_page(0, 100)

    assert len(page.items) == 1
    assert "script" not in repr(page).lower()
    assert "onerror" not in repr(page).lower()


def test_t_sec_03_a_name_with_html_downloads_and_syncs_as_plain_data(tmp_path: Path) -> None:
    entry = {**GOOD, "activityName": HTML, "description": HTML}
    client = scripted_client(1)
    client.replace_entry(0, {**entry_for(make_remote_history(1)[0]), **entry})
    stack = build_stack(tmp_path, client)
    try:
        result = stack.sync()

        assert isinstance(result, SyncFinished) and result.outcome.completed
        assert result.outcome.downloaded == 1
        text = " ".join(path.name for path in stack.folder.rglob("*"))
        assert "script" not in text.lower()
    finally:
        stack.close()


@pytest.mark.parametrize(
    "start",
    [
        "0000-01-01 00:00:00",
        "0001-01-01 00:00:00",
        "1969-12-31 23:59:59",
        "1980-01-01 00:00:00",
        "2999-12-31 23:59:59",
        "9999-12-31 23:59:59",
        "2026-02-30 10:00:00",
        "2026-13-01 10:00:00",
        "2026-03-02 25:00:00",
        "2026-03-02 07:00:00+02:00",
        "2026-03-02 07:00:00 CET",
        "2026-03-02T07:00:00" + "9" * 5000,
        "\u0662\u0660\u0662\u0666-03-02 07:00:00",
        1.0e12,
        10**18,
        None,
        [],
    ],
)
def test_t_sec_03_times_outside_the_window_or_malformed_discard_the_entry(start: object) -> None:
    assert page_of([GOOD, {**GOOD, "startTimeGMT": start}]) == (1, 2)


@pytest.mark.parametrize(
    "duration",
    [0, -1, -0.0, 1e308, 1e300, float("inf"), float("nan"), 10**30, "1800", True, None, [1800]],
)
def test_t_sec_03_durations_that_are_not_sane_discard_the_entry(duration: object) -> None:
    entry = {**GOOD, "duration": duration}
    entry.pop("elapsedDuration", None)

    assert page_of([GOOD, entry]) == (1, 2)


@pytest.mark.parametrize(
    "activity_type",
    [None, "running", [], {}, {"typeKey": ""}, {"typeKey": 1}, {"typeKey": "x" * 10_000},
     {"typeKey": HTML}, {"typeKey": "../../x"}, {"typeKey": "Running"}, {"typeKey": "a b"}],
)  # fmt: skip
def test_t_sec_03_sport_keys_that_are_not_plain_discard_the_entry(activity_type: object) -> None:
    entry = {**GOOD, "activityType": activity_type}

    assert page_of([GOOD, entry]) == (1, 2)


@pytest.mark.parametrize("junk", [None, 1, "x", True, [], [GOOD], 1.5, b"bytes"])
def test_t_sec_03_entries_that_are_not_objects_are_discarded(junk: object) -> None:
    assert page_of([GOOD, junk, GOOD]) == (1, 3)


def test_t_sec_03_a_page_over_the_size_cap_is_refused_whole() -> None:
    source = GarminActivitySource(RawListing([GOOD] * (MAX_PAGE_SIZE + 1)))

    with pytest.raises(DefinitiveSourceError):
        source.list_page(0, 100)


@pytest.mark.parametrize(
    "raw",
    [None, 5, "a" * 100_000, b"x", True, {}, {"activityList": None}, {"activityList": "x"},
     {"activityList": {"a": 1}}, {"other": [GOOD]}],
)  # fmt: skip
def test_t_sec_03_a_response_that_is_not_a_list_is_a_definitive_error(raw: object) -> None:
    source = GarminActivitySource(RawListing(raw))

    with pytest.raises(DefinitiveSourceError):
        source.list_page(0, 100)


def test_t_sec_03_a_deeply_nested_response_is_never_walked() -> None:
    deep_list, deep_dict = nested(100_000), nested_dict(100_000)

    for raw in (deep_list, {"activityList": deep_list}, {"activityList": [deep_dict]}):
        source = GarminActivitySource(RawListing(raw))
        try:
            page = source.list_page(0, 100)
        except DefinitiveSourceError:
            continue
        assert page.items == ()


def test_t_sec_03_deep_nesting_inside_a_good_entry_is_ignored_and_the_entry_kept() -> None:
    entry = {**GOOD, "metadata": nested(100_000), "splits": nested_dict(100_000)}

    assert page_of([entry]) == (1, 1)


def test_t_sec_03_a_huge_but_legal_text_in_an_entry_does_not_matter() -> None:
    entry = {**GOOD, "activityName": "a" * 5_000_000}

    assert page_of([entry]) == (1, 1)


def test_t_sec_03_the_same_id_twice_in_a_page_is_one_activity_both_counted() -> None:
    twin = {**GOOD, "duration": 99.0}

    assert page_of([GOOD, twin, GOOD]) == (1, 3)


@pytest.mark.parametrize(
    "total",
    [float("nan"), float("inf"), -1, 10**12, "5", True, [5], {"n": 5}, 5.5, 2**70],
)
def test_t_sec_03_a_hostile_declared_total_is_ignored(total: object) -> None:
    source = GarminActivitySource(ScriptedClient([GOOD], total=total))

    assert source.list_page(0, 100).declared_total is None


def test_t_sec_03_an_error_while_asking_the_total_leaves_it_unknown() -> None:
    client = ScriptedClient([GOOD])
    client.count_failure = TypeError("Rate limit exceeded? no: " + HTML)

    assert GarminActivitySource(client).list_page(0, 100).declared_total is None


@pytest.mark.parametrize(
    "content", ["testo", bytearray(b"zip"), memoryview(b"zip"), None, 5, [b""]]
)
def test_t_sec_03_a_download_that_is_not_bytes_is_a_definitive_error(content: object) -> None:
    client = ScriptedClient(downloads={FIRST_ACTIVITY_ID: content})  # type: ignore[dict-item]
    source = GarminActivitySource(client)

    with pytest.raises(DefinitiveSourceError):
        source.download_original(GarminActivityId(FIRST_ACTIVITY_ID), 1000)


def test_t_sec_03_a_download_over_the_cap_is_refused_and_the_cap_is_checked() -> None:
    client = ScriptedClient(downloads={FIRST_ACTIVITY_ID: b"x" * 1001})
    source = GarminActivitySource(client)

    with pytest.raises(DownloadTooLargeError):
        source.download_original(GarminActivityId(FIRST_ACTIVITY_ID), 1000)
    with pytest.raises(ValueError, match="max_bytes"):
        source.download_original(GarminActivityId(FIRST_ACTIVITY_ID), 0)


def test_t_sec_03_the_client_receives_only_a_plain_integer_id() -> None:
    client = scripted_client(1)
    GarminActivitySource(client).download_original(GarminActivityId(FIRST_ACTIVITY_ID), 10**7)

    assert client.downloaded == [FIRST_ACTIVITY_ID]
    assert type(client.downloaded[0]) is int


def test_t_sec_03_a_hostile_listing_through_the_whole_stack_downloads_only_the_valid_ones(
    tmp_path: Path,
) -> None:
    good = make_remote_history(3)
    client = scripted_client(history=good)
    hostile = [None, 5, {"activityId": "../x"}, {**entry_for(good[0]), "activityId": "../../a"}]
    client._entries.extend(hostile)
    stack = build_stack(tmp_path, client)
    try:
        result = stack.sync()

        assert isinstance(result, SyncFinished) and result.outcome.completed
        assert result.outcome.downloaded == 3
        assert sorted(client.downloaded) == [a.garmin_id.value for a in good]
    finally:
        stack.close()


# --- T-SYNC-03: Retry-After ostili, con l'adattatore vero ---------------------------------------


@pytest.mark.parametrize(
    "retry_after",
    ["-1", "-99999999999", "0", "1e9", "NaN", "inf", "", "   ", "abc", "1.2.3", "+5", None,
     "99999999999", "9" * 500, "\u0663", "5\n", "5 6"],
)  # fmt: skip
def test_t_sync_03_any_retry_after_gives_a_bounded_sync_with_a_clear_outcome(
    retry_after: str | None,
) -> None:
    client = scripted_client(2)
    for _ in range(50):
        client.list_failures.append(rate_limit_error(retry_after))
    rig = adapter_rig(client)

    outcome = rig.run()

    assert outcome.stop is StopReason.RATE_LIMITED
    assert len(client.list_calls) <= rig.policy.max_attempts_per_request
    # l'attesa totale (orologio finto) non supera il tetto del backoff per ogni tentativo
    assert rig.wait.total_s <= rig.policy.max_attempts_per_request * rig.policy.backoff_cap_s
    assert all(0 <= wait <= rig.policy.backoff_cap_s for wait in rig.wait.waits)


@pytest.mark.xfail(
    strict=True,
    reason="D-F2-01: Retry-After con una cifra che float() non legge (es. '²') solleva ValueError "
    "da translate_source_error invece di un errore della porta",
)
def test_d_f2_01_retry_after_with_a_superscript_digit_is_an_error_of_the_port() -> None:
    client = ScriptedClient([GOOD])
    client.list_failures.append(rate_limit_error("\u00b2"))
    source = GarminActivitySource(client)

    with pytest.raises(ActivitySourceError):
        source.list_page(0, 100)


def test_d_f2_01_the_same_header_through_the_stack_ends_as_an_unexpected_problem_not_a_crash(
    tmp_path: Path,
) -> None:
    client = scripted_client(1)
    client.list_failures.append(rate_limit_error("\u00b2"))
    stack = build_stack(tmp_path, client)
    try:
        result = stack.sync()  # il worker ha il confine del thread: non solleva

        assert not isinstance(result, SyncFinished) or not result.outcome.completed
    finally:
        stack.close()


def test_t_sync_03_a_429_with_a_huge_wait_stops_without_sleeping_for_it(tmp_path: Path) -> None:
    client = scripted_client(2)
    client.list_failures.append(rate_limit_error("99999999999"))
    stack = build_stack(tmp_path, client)
    try:
        result = stack.sync()

        assert isinstance(result, SyncFinished)
        assert result.outcome.stop is StopReason.RATE_LIMITED
        assert len(client.list_calls) == 1
    finally:
        stack.close()


@pytest.mark.xfail(
    strict=True,
    reason="D-F2-02: una sorgente che dà RateLimitedError con attesa negativa o NaN fa sollevare "
    "ValueError da synchronize_activities (retry_wait_s) invece di un esito; la porta non valida "
    "retry_after_s (l'adattatore Garmin non lo produce, ma la porta e' il contratto)",
)
@pytest.mark.parametrize("wait", [-5.0, float("nan")])
def test_d_f2_02_a_source_with_a_hostile_retry_after_value_gives_an_outcome_not_an_exception(
    wait: float,
) -> None:
    from tests.fakes.garmin import Call  # noqa: PLC0415
    from tests.fixtures.generators.sync_harness import in_memory_rig  # noqa: PLC0415

    rig = in_memory_rig(2)
    rig.fake.fail_call(Call.LIST_PAGE, 1, RateLimitedError(wait))

    outcome = rig.run()

    assert not outcome.completed


# --- ZIP di Garmin ------------------------------------------------------------------------------


def _zip_with(entries: list[tuple[str, bytes]]) -> bytes:
    from tests.fixtures.generators.zip_writer import build_zip  # noqa: PLC0415

    return build_zip(entries)


def _sync_one_download(
    tmp_path: Path, content: bytes
) -> tuple[SyncFinished, dict[SyncStatus, int]]:
    activity = make_remote_history(1)
    client = scripted_client(history=activity)
    client.set_download(activity[0].garmin_id.value, content)
    stack = build_stack(tmp_path, client)
    try:
        result = stack.sync()
        assert isinstance(result, SyncFinished)
        return result, dict(stack.journal_counts())
    finally:
        stack.close()


def test_t_sync_zip_with_no_entries_fails_the_entry_only(tmp_path: Path) -> None:
    result, counts = _sync_one_download(tmp_path, _zip_with([]))

    assert result.outcome.completed and result.outcome.downloaded == 0
    assert counts[SyncStatus.DOWNLOADED] == 0 and counts[SyncStatus.PENDING] == 0


def test_t_sync_zip_with_only_other_entries_fails_the_entry_only(tmp_path: Path) -> None:
    result, _ = _sync_one_download(tmp_path, _zip_with([("note.txt", b"x"), ("a.tcx", b"<x/>")]))

    assert result.outcome.completed and result.outcome.downloaded == 0
    assert len(result.outcome.failures) == 1


def test_t_sync_zip_with_two_activity_files_is_one_attempt_never_two_records(
    tmp_path: Path,
) -> None:
    from tests.fixtures.generators.import_files import fit_run  # noqa: PLC0415

    two = _zip_with([("1000000_ACTIVITY.fit", fit_run(0)), ("1000000_ACTIVITY2.fit", fit_run(7))])
    result, _ = _sync_one_download(tmp_path, two)

    assert result.outcome.completed
    assert result.outcome.downloaded + len(result.outcome.failures) == 1


@pytest.mark.parametrize(
    "name",
    ["../evil.fit", "..\\evil.fit", "/abs/evil.fit", "C:\\evil.fit", "a/../../b.fit", "con.fit",
     "x" * 5000 + ".fit", "n\x00ul.fit"],
)  # fmt: skip
def test_t_sec_03_zip_entry_names_never_become_paths_in_the_archive(
    tmp_path: Path, name: str
) -> None:
    from tests.fixtures.generators.import_files import fit_run  # noqa: PLC0415

    before = set(tmp_path.rglob("*"))
    try:
        content = _zip_with([(name, fit_run(0))])
    except (ValueError, OSError):
        pytest.skip("il generatore di ZIP non scrive questo nome")
    result, _ = _sync_one_download(tmp_path / "w", content)

    assert result.outcome.completed
    created = set(tmp_path.rglob("*")) - before
    assert not [p for p in created if "evil" in p.name.lower()]
    assert not (tmp_path / "evil.fit").exists() and not (tmp_path.parent / "evil.fit").exists()


def test_t_sync_a_bomb_from_garmin_fails_the_entry_and_the_sync_goes_on(tmp_path: Path) -> None:
    from tests.fixtures.generators.zip_writer import zero_bomb  # noqa: PLC0415

    bomb = zero_bomb(200 * 1024 * 1024)
    result, counts = _sync_one_download(tmp_path, bomb)

    assert result.outcome.completed and result.outcome.downloaded == 0
    assert counts[SyncStatus.DOWNLOADED] == 0


def test_t_sync_the_json_of_the_listing_is_data_never_executed() -> None:
    # il sorgente (JSON letto da un client finto) non passa mai per eval/pickle: una stringa
    # che sembra codice resta una stringa scartata
    payload = json.loads("[{\"activityId\": \"__import__('os').system('x')\"}]")

    assert page_of(payload) == (0, 1)
