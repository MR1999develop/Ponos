# 002. Access to Garmin Connect

- Status: Accepted
- Date: 2026-11-05

## Context

Garmin does not offer an official API to private users. Activities can be
obtained only with an unofficial login or through a third-party service. In
March 2026 Garmin changed the login and broke the `garth` library. The
`garminconnect` library, since version 0.3, has a login of its own, with MFA.
The access may break again and may be against Garmin's terms of use.

## Options

1. **Direct unofficial login with `garminconnect`.**
   - Pro: no intermediary, complete data (original FIT files, calendar), MFA
     supported.
   - Con: it can stop working at every Garmin change; possible violation of the
     terms of use; risk of a temporary block (429); the password goes through
     the app.
2. **intervals.icu as an intermediary (official API of a third party).**
   - Pro: documented and stable API, no Garmin password in the app.
   - Con: a second account is needed; the data goes through a third party;
     depends on the synchronisation between the two services and on its
     availability; the original FIT files and the Garmin calendar might not be
     available (to be verified).
3. **Both selectable by the user.**
   - Pro: fallback if one of the two breaks.
   - Con: two adapters to maintain and test; more attack surface.

## Decision

Option 1, `garminconnect`, with the version pinned in the lockfile and updated
only after a trial. Option 3 is not excluded: if the `ActivitySource` port
allows it, a second adapter can be added with a new ADR.

Mitigations:

- adapter isolated behind the `ActivitySource` port (and `WorkoutCalendar`);
- contract tests on recorded and anonymised responses, and on a fake;
- manual import of FIT files and from a folder always available;
- few requests: only at start-up and by hand, pauses with jitter, backoff with
  a cap, no endless retries;
- the password is entered in a dialog of the app, goes only to
  `garminconnect` and is not saved; the tokens are in the system keychain.

This decision is also an exception written in `CLAUDE.md`: the login is done in
the app instead of in the system browser with PKCE, because no official flow
exists.

## Consequences

- Synchronisation can stop without warning; the app stays usable with FIT
  files.
- The README warns that the access is not official.
- The password is treated as sensitive data in memory and in logs (never
  recorded).
- Every update of `garminconnect` is a job with a trial and contract tests.

## How compliance is verified

- `lint-imports`: only the `garmin` package imports `garminconnect`.
- T-SYNC-03 and T-SYNC-04 (errors, backoff, login); T-SEC-03 (hostile
  responses); T-SEC-07 (no secret in the folder, configuration and logs).
- Phase 0 spike and contract tests on the recorded responses at every update
  of the library.
