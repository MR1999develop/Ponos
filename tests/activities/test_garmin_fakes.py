# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""I fake di Garmin (elenco, download, account): ciò che promettono ai test di sincronizzazione."""

from __future__ import annotations

import io
import zipfile

import pytest

from ponos_coach.activities.decoding import ActivityFileError, decode_activity_file
from ponos_coach.activities.domain import (
    MAX_DOWNLOAD_BYTES,
    GarminActivityId,
    ListingCursor,
    SessionTokens,
    advance_cursor,
)
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    InvalidCredentialsError,
    MfaCancelledError,
    RateLimitedError,
    ServiceUnavailableError,
    TransientSourceError,
)
from tests.fakes.garmin import (
    FIRST_ACTIVITY_ID,
    PLACEHOLDER_TOKEN,
    TEST_EMAIL,
    TEST_MFA_CODE,
    TEST_PASSWORD,
    Call,
    FakeActivitySource,
    FakeGarminAccount,
    OriginalKind,
    make_remote_history,
)


def _id(number: int) -> GarminActivityId:
    return GarminActivityId(FIRST_ACTIVITY_ID + number)


def _source(count: int = 10) -> FakeActivitySource:
    return FakeActivitySource(make_remote_history(count))


class TestFakeActivitySource:
    def test_pages_come_newest_first_with_the_declared_total(self) -> None:
        source = _source(10)

        page = source.list_page(0, 4)

        assert [item.garmin_id for item in page.items] == [_id(9), _id(8), _id(7), _id(6)]
        assert (page.offset, page.raw_count, page.declared_total) == (0, 4, 10)

    def test_walking_the_pages_with_the_cursor_reads_the_whole_history(self) -> None:
        source = _source(25)
        cursor = ListingCursor.start()
        seen: list[GarminActivityId] = []
        while not cursor.completed:
            page = source.list_page(cursor.next_offset, 10)
            seen += [item.garmin_id for item in page.items]
            cursor = advance_cursor(cursor, page, 10)

        assert seen == [_id(n) for n in range(24, -1, -1)]
        assert source.call_count(Call.LIST_PAGE) == 3

    def test_a_source_that_does_not_declare_the_total_still_ends(self) -> None:
        source = FakeActivitySource(make_remote_history(7), declares_total=False)

        page = source.list_page(0, 10)

        assert page.declared_total is None
        assert advance_cursor(ListingCursor.start(), page, 10).completed

    def test_malformed_entries_count_as_raw_but_are_not_items(self) -> None:
        source = _source(10)
        source.malformed([_id(9), _id(7)])

        page = source.list_page(0, 4)

        assert page.raw_count == 4
        assert [item.garmin_id for item in page.items] == [_id(8), _id(6)]
        assert page.skipped_count == 2

    def test_the_original_is_a_zip_with_a_fit_that_the_importer_can_read(self) -> None:
        source = _source(3)

        data = source.download_original(_id(2), MAX_DOWNLOAD_BYTES)

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            [name] = archive.namelist()
            activity = decode_activity_file(archive.read(name), name)
        remote = source.activities[_id(2)]
        assert activity.start == remote.start
        assert activity.elapsed_s == remote.duration_s

    def test_each_kind_of_original_is_what_it_says(self) -> None:
        source = _source(2)
        contents = {}
        for kind in OriginalKind:
            source.set_kind(_id(0), kind)
            contents[kind] = source.download_original(_id(0), MAX_DOWNLOAD_BYTES)

        with zipfile.ZipFile(io.BytesIO(contents[OriginalKind.GPX])) as archive:
            assert archive.namelist()[0].endswith(".gpx")
        with zipfile.ZipFile(io.BytesIO(contents[OriginalKind.TCX])) as archive:
            assert archive.namelist()[0].endswith(".tcx")
        with zipfile.ZipFile(io.BytesIO(contents[OriginalKind.EMPTY_ZIP])) as archive:
            assert archive.namelist() == []
        with zipfile.ZipFile(io.BytesIO(contents[OriginalKind.CORRUPT_FIT])) as archive:
            [name] = archive.namelist()
            with pytest.raises(ActivityFileError):
                decode_activity_file(archive.read(name), name)
        for broken in (OriginalKind.TRUNCATED_ZIP, OriginalKind.NOT_A_ZIP):
            with pytest.raises(zipfile.BadZipFile):
                zipfile.ZipFile(io.BytesIO(contents[broken]))

    def test_a_download_over_the_limit_is_refused(self) -> None:
        source = _source(2)

        with pytest.raises(DownloadTooLargeError):
            source.download_original(_id(0), 100)

    def test_an_unknown_activity_is_a_definitive_error(self) -> None:
        with pytest.raises(DefinitiveSourceError):
            _source(2).download_original(_id(50), MAX_DOWNLOAD_BYTES)

    def test_a_failure_hits_only_the_numbered_call_and_only_once(self) -> None:
        source = _source(10)
        source.fail_call(Call.LIST_PAGE, 2, RateLimitedError(30.0))

        source.list_page(0, 4)
        with pytest.raises(RateLimitedError) as error:
            source.list_page(4, 4)
        again = source.list_page(4, 4)

        assert error.value.retry_after_s == 30.0
        assert again.offset == 4
        assert source.call_count(Call.LIST_PAGE) == 3

    def test_a_download_failure_can_be_limited_in_number_or_permanent(self) -> None:
        source = _source(4)
        source.fail_download_of(_id(1), TransientSourceError("rete"), times=2)
        source.fail_download_of(_id(2), AuthenticationRequiredError("scaduti"))

        for _ in range(2):
            with pytest.raises(TransientSourceError):
                source.download_original(_id(1), MAX_DOWNLOAD_BYTES)
        assert source.download_original(_id(1), MAX_DOWNLOAD_BYTES)
        for _ in range(3):
            with pytest.raises(AuthenticationRequiredError):
                source.download_original(_id(2), MAX_DOWNLOAD_BYTES)
        assert source.downloads.count(_id(1)) == 3

    def test_a_slow_answer_advances_the_clock_given_by_the_test(self) -> None:
        elapsed: list[float] = []
        source = FakeActivitySource(
            make_remote_history(3), latency_s=2.5, on_latency=elapsed.append
        )

        source.list_page(0, 2)
        source.download_original(_id(0), MAX_DOWNLOAD_BYTES)

        assert elapsed == [2.5, 2.5]

    def test_activities_can_appear_and_disappear(self) -> None:
        source = _source(3)
        late = make_remote_history(4)[3]

        source.add_activity(late)
        source.remove_activity(_id(0))

        assert [i.garmin_id for i in source.list_page(0, 10).items] == [_id(3), _id(2), _id(1)]

    def test_the_arguments_are_checked(self) -> None:
        with pytest.raises(ValueError, match="limite"):
            _source(1).list_page(0, 0)


class TestFakeGarminAccount:
    def _account(self, *, mfa: bool = True) -> FakeGarminAccount:
        return FakeGarminAccount(_source(3), mfa_required=mfa)

    def test_login_with_mfa_gives_a_session_and_the_placeholder_token(self) -> None:
        account = self._account()

        session = account.login(TEST_EMAIL, TEST_PASSWORD, lambda: TEST_MFA_CODE)

        assert session.tokens.reveal() == PLACEHOLDER_TOKEN
        assert session.source.list_page(0, 2).raw_count == 2
        assert account.mfa_prompts == 1

    def test_login_without_mfa_never_asks_for_a_code(self) -> None:
        account = self._account(mfa=False)

        account.login(TEST_EMAIL, TEST_PASSWORD, lambda: pytest.fail("non doveva chiedere"))

        assert account.mfa_prompts == 0

    def test_wrong_credentials_are_refused(self) -> None:
        with pytest.raises(InvalidCredentialsError):
            self._account().login(TEST_EMAIL, "sbagliata", lambda: TEST_MFA_CODE)

    def test_a_wrong_mfa_code_is_refused(self) -> None:
        with pytest.raises(InvalidCredentialsError):
            self._account().login(TEST_EMAIL, TEST_PASSWORD, lambda: "000000")

    def test_a_cancelled_mfa_prompt_ends_the_login_without_tokens(self) -> None:
        account = self._account()

        with pytest.raises(MfaCancelledError):
            account.login(TEST_EMAIL, TEST_PASSWORD, lambda: None)

        with pytest.raises(AuthenticationRequiredError):
            account.resume(SessionTokens(PLACEHOLDER_TOKEN))

    def test_resume_works_with_the_issued_tokens_and_fails_after_a_revocation(self) -> None:
        account = self._account()
        tokens = account.login(TEST_EMAIL, TEST_PASSWORD, lambda: TEST_MFA_CODE).tokens

        assert account.resume(tokens).tokens == tokens
        account.revoke_tokens()
        with pytest.raises(AuthenticationRequiredError):
            account.resume(tokens)

    def test_rate_limit_and_outage_can_be_simulated(self) -> None:
        account = self._account()
        account.rate_limit(60.0)
        with pytest.raises(RateLimitedError) as limited:
            account.login(TEST_EMAIL, TEST_PASSWORD, lambda: TEST_MFA_CODE)
        assert limited.value.retry_after_s == 60.0

        account.go_offline()
        with pytest.raises(ServiceUnavailableError):
            account.login(TEST_EMAIL, TEST_PASSWORD, lambda: TEST_MFA_CODE)
        with pytest.raises(ServiceUnavailableError):
            account.resume(SessionTokens(PLACEHOLDER_TOKEN))

        account.fail_login_with(None)
        account.fail_resume_with(None)
        session = account.login(TEST_EMAIL, TEST_PASSWORD, lambda: TEST_MFA_CODE)
        assert session.tokens.reveal() == PLACEHOLDER_TOKEN
