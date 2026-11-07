# Ponos Coach

Ponos Coach is a desktop app for Windows and Ubuntu, with several user
profiles. It downloads activities from Garmin Connect, computes the training
load from heart rate and shows fitness and fatigue (ATL, CTL, TSB). It builds
an adaptive training plan towards one or more goals in the calendar and guides
recovery from injuries.

**Status:** in development, phase 3 (load and charts, on top of profiles,
archive, import and Garmin synchronisation). There are no released versions
yet: run it from source.

## Notice: not a medical device

Ponos Coach is not a medical device. It makes no diagnosis and does not
replace a doctor or a coach. When an injury shows warning signs, the plan for
the affected area stops and the app asks you to see a doctor.

## Notice: Garmin Connect access

Garmin does not offer an official API to private users. The app uses an
unofficial login (the `garminconnect` library). It may stop working when
Garmin changes the login or the APIs. It may be against Garmin's terms of
use: use it at your own risk. Manual import of FIT files is always available.

## Planned features

- Several profiles, each with a folder chosen by the user for all its data.
- Synchronisation of activities from Garmin Connect, resumable (phase 2), and
  import of FIT and GPX files, also inside ZIP archives, from files or from a
  folder.
- To try the app without your own data: `uv run python -m
  tests.fixtures.generators.demo_history <folder>` creates a synthetic
  history of FIT files, to load into a profile with "Import from folder...".
- Load from heart rate and charts of ATL, CTL and TSB (phase 3), weekly load
  by sport, time in zones and the detail of each activity.
- 30-minute threshold test and training zones.
- Goals (A/B/C races, target time, fitness level) and an adaptive plan.
- Recovery path from injuries.
- Sending workouts to the Garmin calendar (later phase).
- Interface in English and Italian.

The planned behaviour is described in `docs/specifica.md`, the decisions in
`docs/adr/` and the work plan in `PLAN.md`.

## Requirements

- Windows or Ubuntu.
- Python 3.13.
- [uv](https://docs.astral.sh/uv/).

## First profile

At the first start the "New profile" dialog opens. You need:

1. the profile name;
2. the data folder, chosen with "Browse...": if it is empty the app uses it
   as it is, otherwise it creates a subfolder with the profile name inside it
   and shows where the data will go; avoid synchronised folders such as
   OneDrive (on Windows often Documents too);
3. height, weight and date of birth;
4. resting and maximum heart rate; the threshold is estimated at 90% of the
   maximum until you set it with the test;
5. sex (the load formula needs it).

Height, weight and date of birth are health data: they stay in the profile
database and never go to the logs. Height and date of birth cannot be changed
afterwards. Weight and heart rates are updated with a validity date from
Profile, Edit profile... (Ctrl+E), where the history is also shown; a profile
created before phase 3 fills in height and date of birth there, once. The
rules are in the specification (section 5).

Profile, Sport weights... (Ctrl+Shift+W) changes how much each sport counts
in the load; each activity is recalculated on its own when needed.

More profiles: File, New profile... (Ctrl+N). A profile moved or copied to
another PC is opened with File, Open profile from folder... (Ctrl+O). With
File, Switch profile you move from one to another. "Remove profile from list"
does not touch the data; "Delete profile folder" deletes it for good, after a
confirmation, and only if the folder contains nothing but files created by the
app: if there is anything else (for example your own files) it deletes
nothing and says so. When the app is closed while a job is running (an
import), the app stops it and waits for it to finish; if it does not finish
within 10 seconds, it leaves the window open and warns you, and you only need
to close it again. The app remembers the open section, the window and the
last folders used.

## Connecting the Garmin account

A profile must be open. Connecting is optional: file import always works.

1. Garmin menu, Connect Garmin account... (Ctrl+G).
2. Enter your Garmin Connect email and password. They go only to Garmin and
   the app does not save them.
3. If your account uses two-step verification, enter the code Garmin sends
   you. You have 5 minutes; if time runs out or you cancel, nothing is saved.

The login is not official (see the notice above): if Garmin changes it,
synchronisation may stop working. One account per profile; to use another,
Garmin, Disconnect Garmin account (Ctrl+Shift+G).

**First synchronisation.** After connecting it starts by itself and downloads
the whole history, with the original files. It can take a long time (about
a one-second pause per activity, plus the download time): the status bar
shows the progress and the estimated time, and "Stop synchronisation"
(Shift+F5) stops everything without losing anything, because the next time it
resumes where it stopped. Meanwhile you can use the app. If Garmin limits the
requests or does not answer, the app waits and tries again a few times, then
stops with a message.

The following times: Synchronise (F5) downloads only the missing activities.
Full check (Ctrl+F5) lists the whole history again, to find activities
uploaded late with old dates. Activities you had already imported from files
are neither downloaded again nor duplicated. At the end, the status bar and a
system notification tell you how many new activities came in.

**Where the tokens are.** The access tokens are never in the profile folder or
in the backups. On Windows they are a file encrypted with DPAPI, for your
user, in `secrets/` inside the app's local data folder. On Ubuntu they are in
the Secret Service (for example GNOME Keyring or KeePassXC); if there is none,
the app does not connect the account and does not fall back to a plain file.
Other programs running as your user can read them. A profile moved to another
PC asks for the login again.

**Optional spike.** `experiments/garmin_spike.py` tries the Garmin login live
and writes the structure (without values) of the real responses, useful to
update the test fixtures. It is not needed to use the app: see
`experiments/README.md`. It asks for email, password and code in your
terminal, and leaves files with heart rate and GPS in plain text in
`experiments/spike-data/`: delete them with `--logout` at the end of the
test.

## Importing activities

A profile must be open. You can import FIT and GPX files, also inside ZIP
files, such as the archive you download from the Garmin site.

- File, Import files... (Ctrl+I): choose one or more `.fit`, `.gpx` or `.zip`
  files.
- File, Import from folder... (Ctrl+Shift+I): choose a folder; the app looks
  for files in the subfolders too. If there are too many (over 50,000
  activity files, 500,000 entries or 32 levels) it imports nothing and asks
  you to choose a subfolder.
- Or drag files or a folder onto the window.

The original files are not modified: the app keeps a copy in the profile.
Activities already present are skipped, so running the import again is safe.
At the end a summary says how many activities were imported and, for each
file not imported, the reason. If an activity was already there but its file
in the archive was missing or altered, importing the same file again puts it
right: the summary counts it as "repaired". Activities appear in the
Activities page and in the Overview chart. TCX files are not supported.

## Charts and activity detail

The Overview shows today's form and three charts: fitness and fatigue (CTL,
ATL, TSB), weekly load by sport and time in heart-rate zones. A double click
(or Enter) on an activity in the Activities page, or Profile, Activity
detail... (Ctrl+D), opens the heart-rate curve with the zones and the time in
each. All charts can be exported to SVG and PNG, in the light or dark theme,
and do not use the network. The zones are percentages of the threshold chosen
by the project (see the specification, section 6, and
`docs/adr/007-dati-del-profilo-e-zone.md`).

## Language

The interface starts in English. For Italian: Language menu, Italiano; the
choice is remembered and applies from the next start. `--lang it` or
`--lang en` takes precedence.

## Where the data is

All the data of a profile is in the folder chosen at creation:

- `ponos.sqlite3`: the database, with activities, parameters, weight,
  personal data and sport weights (with the `-wal` and `-shm` files while the
  app is open);
- `archivio/<year>/`: the original files, named by their SHA-256;
- `backup/`: the copies of the database made before a format upgrade
  (`ponos-v{from}-v{to}.sqlite3`);
- `ponos.lock`: prevents opening the same profile twice.

Do not move or edit these files by hand while the app is open. The list of
profiles is in the app's configuration folder (`profiles.json`). The Garmin
tokens are never in the profile folder (see "Connecting the Garmin account").

## Data and privacy

The data of each profile (activities, heart rate, height, weight, date of
birth, GPS positions, injuries) stays on the computer, in the folder chosen
for the profile, unencrypted and readable only by the system user. To protect
it from disk theft too, turn on disk encryption (BitLocker on Windows, LUKS
on Ubuntu). Avoid network or synchronised folders (OneDrive, NAS): if you
choose one, the app warns you, but the database may get damaged. `archivio/`
and `backup/` also contain health data and GPS positions.

## Log

The app writes a rotating log (at most about 4 MB) in the user's log folder.
To open it: Help, Open the log folder (Ctrl+Shift+L). The log has events and
error types, without paths, file names or health data. If something goes
wrong, attach it to a report after reading it.

## Running from source

```
uv sync
uv run --locked nox -s i18n
uv run ponos-coach
```

The command `nox -s i18n` generates the Italian translations (`.qm`); without
it the app stays in English even if you choose Italian.

## Checks

```
uv run --locked nox
```

It runs formatting and lint, `mypy --strict`, the check of dependencies
between modules, tests, translation tests and `pip-audit`.

## Security

To report a vulnerability see `SECURITY.md`.

## Licence

GPL-3.0-only. The full text is in the `LICENSE` file.
