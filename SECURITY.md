# Security

## How to report a vulnerability

Do not open a public issue.

1. Use "Report a vulnerability" in the Security tab of the GitHub repository
   (private vulnerability reporting).
2. If it is not available, write to `MR1999develop@users.noreply.github.com`.

State the version, the steps to reproduce the problem and the effect. Do not
attach real health data, passwords or tokens: if needed, use test data. There
is no encrypted channel.

## What is in scope

- FIT file reader and folder import.
- Garmin login and token handling in the system keychain.
- Processing of Garmin JSON responses.
- Charts web page (`QWebEngineView`).
- Profile folder and database.
- Distributed packages and release process.

Out of scope: vulnerabilities of Garmin Connect, of Qt or of other
dependencies (report them to their projects; do tell us, though, if the app
makes them exploitable), machines that are already compromised.

## Indicative times

These are the times of a personal project, not contractual commitments.

- Acknowledgement of receipt: within 7 days.
- First assessment: within 30 days.
- Fix: depends on severity; memory corruption and credential leaks come
  first.

The fix is published before the details of the vulnerability.

## Rewards

There is no reward. Whoever wishes can be credited in the release notes.
