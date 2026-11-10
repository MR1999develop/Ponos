# Ponos Coach

Ponos Coach is a desktop app for Windows and Ubuntu, with several user
profiles. It downloads activities from Garmin Connect, computes the training
load from heart rate and shows fitness and fatigue (ATL, CTL, TSB). It builds
an adaptive training plan towards one or more goals in the calendar and guides
recovery from injuries.

**Status:** in development; the phases and what each one delivers are in
`PLAN.md`. A packaged build for Windows and Ubuntu is attached to each
tagged release (see "Install from a package"); until the first release you
can run the app from source.

## Notice: not a medical device

Ponos Coach is not a medical device. It makes no diagnosis and does not
replace a doctor or a coach. When an injury shows warning signs, the plan for
the affected area stops and the app asks you to see a doctor.

## Notice: Garmin Connect access

Garmin does not offer an official API to private users. The app uses an
unofficial login (the `garminconnect` library). It may stop working when
Garmin changes the login or the APIs. It may be against Garmin's terms of
use: use it at your own risk. Manual import of FIT files is always available.

## Features

- Several profiles, each with a folder chosen by the user for all its data.
- Synchronisation of activities from Garmin Connect, resumable, and import of
  FIT files, from files or from a folder (FIT only: GPX and ZIP files are not
  imported).
- To try the app without your own data: `uv run python -m
  tests.fixtures.generators.demo_history <folder>` creates a synthetic
  history of FIT files, to load into a profile with "Import from folder...".
- Load from heart rate and charts of ATL, CTL and TSB, weekly load by sport,
  time in zones and the detail of each activity.
- 30-minute threshold test: from a run you choose, threshold heart rate and
  pace, heart-rate and pace zones, history of the tests and a retest every
  6-8 weeks; applying a test can be undone.
- Nutrition of the day: energy needs, deficit within safe limits, and each
  meal's calories and macronutrients.
- Goals (A/B/C races with distance, elevation gain and target time, fitness
  level) and an adaptive, deterministic training plan, with hill sessions for
  a steep A race.
- Search and map of the activities: filters, tracks coloured by sport,
  clustered starts, elevation profile linked to the track.
- Recovery path from injuries, with warning signs that stop the plan of the
  affected area and point to a doctor.
- Sending the planned workouts to the Garmin calendar, and removing them
  selectively (a local copy is kept so they can be created again).
- Backup and restore of a profile in a versioned file (format in
  `docs/backup-format.md`); Garmin tokens are never part of it.
- Interface in English and Italian.

The planned behaviour is described in `docs/specifica.md`, the decisions in
`docs/adr/` and the work plan in `PLAN.md`.

## Install from a package

Each release has, for Windows (x64) and Ubuntu (x64), an archive
(`PonosCoach-<version>-windows-x64.zip`, `PonosCoach-<version>-linux-x64.tar.gz`)
and a `SHA256SUMS` file. The app is a folder: nothing is installed, and
nothing is written outside it except your profiles' folders, the list of
profiles, the settings, the Garmin tokens and the log. The archive is about 250 MB (the web engine that draws
the charts and the map is in it).

1. **Verify the download.** Download the archive and `SHA256SUMS` from the
   same release page, then compare the hash.
   - Windows (PowerShell, in the download folder):
     `(Get-FileHash .\PonosCoach-<version>-windows-x64.zip -Algorithm SHA256).Hash.ToLower()`
     must equal the first column of the line of that file in `SHA256SUMS`.
   - Ubuntu: `sha256sum --check --ignore-missing SHA256SUMS`.
   If the hash differs, do not run the file.
2. **Unpack** the archive anywhere you can write (not `Program Files`).
3. **Start it:** `PonosCoach\ponos-coach.exe` on Windows, `PonosCoach/ponos-coach`
   on Ubuntu.

**SmartScreen on Windows.** The executables are not signed: this is a
personal project without a code-signing certificate, and it will stay so.
Windows SmartScreen may therefore show "Windows protected your PC". After
you have verified the hash as above, choose "More info", then "Run anyway".
The hash check is what tells you the file is the one that was released.

**Ubuntu libraries.** The app needs the usual Qt and Chromium system
libraries. On a minimal system install them with:

```
sudo apt-get install libegl1 libgl1 libopengl0 libglib2.0-0 libdbus-1-3 \
  libfontconfig1 libxkbcommon0 libxkbcommon-x11-0 libnss3 libxcomposite1 \
  libxdamage1 libxrandr2 libxtst6 libxcursor1 libasound2t64 libxcb-cursor0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-shape0 libxcb-xinerama0
```

To check that an unpacked package is complete, run
`ponos-coach --self-check report.txt` (the file must not exist yet): it checks the
translations, web resources, time-zone data and the web engine,
writes one line per check to the report and exits with 0 if all passed.

To remove the app, delete its folder. Your profiles are in the folders you
chose and are not touched.

## Run from source

Requirements: Windows or Ubuntu, Python 3.13 and
[uv](https://docs.astral.sh/uv/).

```
uv sync
uv run --locked nox -s i18n
uv run ponos-coach
```

The command `nox -s i18n` generates the Italian translations (`.qm`); without
it the app stays in English even if you choose Italian.

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
afterwards. Weight and heart rates are updated with a validity date in
File, Settings... (Ctrl+,), Profile page (Profile, Edit profile... or Ctrl+E
open it directly), where the history is also shown; a profile created before
phase 3 fills in height and date of birth there, once. The rules are in the
specification (section 5).

All the settings are in one window, File, Settings... (Ctrl+,), with the
categories on the left: Profile, Garmin account, Training plan
(with the threshold test protocol and the history of the tests, where a test
can be taken back), Nutrition, Map and Language. Each page saves the way it always
did (a Save button, or at once); a category with unsaved changes is marked
with an asterisk, and closing the window asks what to do with them (Save,
Discard or Keep editing). The commands that used to open a dialog (Profile,
Edit profile..., Calendar, Training settings..., Profile,
Threshold tests...) now open the window on the right page; the Map page
(size of the tile cache) is reached from Settings itself.

The load is the TRIMPeq: Banister's TRIMP on the running-equivalent intensity,
so that one hour at the threshold of the sport is 100 in every sport. It uses
the minimum HR, maximum HR and thresholds of the profile; there are no sport
weights.

## The map

The map has three backgrounds, which you choose in Map > Background
(Ctrl+Shift+1, 2, 3) or with the small control at the top right of the map; the
choice is remembered. OpenStreetMap (the default) is the standard road map,
OpenTopoMap shows contour lines and relief (up to zoom 17) and CyclOSM is made
for cycling (up to zoom 20). The map is always shown with the credit the data
asks for: "© OpenStreetMap contributors" for OpenStreetMap, "Map data: ©
OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)" for
OpenTopoMap and "CyclOSM | Map data: © OpenStreetMap contributors" for CyclOSM.
There is no key to get and nothing to set up. The app follows the usage policy
of those servers: it names itself and gives a contact address in each request,
keeps tiles as long as the server says (7 days when it says nothing), asks
again with a conditional request when a tile has expired, asks only for the
tiles you look at (no pre-loading, no bulk download) and stops asking for five
minutes if a server refuses (the other backgrounds keep working). Privacy: the
server of the background in use sees which areas you look at and your IP
address (`tile.openstreetmap.org` for OpenStreetMap, `*.tile.opentopomap.org`
for OpenTopoMap, `*.tile-cyclosm.openstreetmap.fr` for CyclOSM, run by
OpenStreetMap France); the others see nothing while you do not use them. They
are free, shared services with no guarantee: OpenTopoMap is sometimes slow, and
any of them can block the app, and then the map keeps a plain background while
the list, the filters and the tracks keep working. If you had saved a
Tracestrack key in an older build, the app no longer uses it.

More profiles: File, New profile... (Ctrl+N). A profile moved or copied to
another PC is opened with File, Open profile from folder... (Ctrl+O). With
File, Switch profile you move from one to another. "Remove profile from list"
does not touch the data; "Delete profile folder" deletes it for good, after a
confirmation, and only if the folder contains nothing but files created by the
app: if there is anything else (for example your own files) it deletes
nothing and says so. When the app is closed while a job is running (an
import), the app stops it and waits for it to finish; if it does not finish
within 10 seconds, it leaves the window open and warns you, and you only need
to close it again. The window has four panes you can resize (Overview, Calendar,
Activities and Map; Ctrl+1 to Ctrl+4 move the focus between them). The
app remembers the window, the dividers and the last folders used.

## Connecting the Garmin account

A profile must be open. Connecting is optional: file import always works.

1. File, Settings..., Garmin account page, Connect Garmin account....
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

A profile must be open. Only FIT files are imported. GPX files and ZIP
archives are not: in a folder they are ignored, and chosen by hand they are
reported as an unsupported format. A profile created with an earlier version that
contains activities imported from GPX loses them the first time it is opened: the
app first copies the database into the `backup` folder of the profile and tells you
how many activities it removed.

- File, Import files... (Ctrl+I): choose one or more `.fit` files.
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
Activities list and in the Overview chart. TCX files are not supported.

## Charts and activity detail

The Overview shows, chosen from a list on its left, either fitness and fatigue
(CTL, ATL and TSB in one chart) or the weekly load by sport above the time in
heart-rate zones. Dragging with the right button stretches a chart
vertically. A double click (or Enter) on an activity in the Activities list,
or Profile, Activity detail... (Ctrl+D), opens the heart-rate curve with the
zones and the time in each, and centres the map on the activity. All charts can be exported to SVG and PNG, in the light or dark theme,
and do not use the network. The zones are percentages of the threshold chosen
by the project (see the specification, section 6, and
`docs/adr/007-dati-del-profilo-e-zone.md`).

## Language

The interface starts in English. For Italian: File, Settings..., Language,
Italiano; the choice is remembered and applies from the next start. `--lang it` or
`--lang en` takes precedence.

## Where the data is

All the data of a profile is in the folder chosen at creation:

- `ponos.sqlite3`: the database, with activities, parameters, weight,
  personal data (with the `-wal` and `-shm` files while the
  app is open);
- `archivio/<year>/`: the original files, named by their SHA-256;
- `backup/`: the copies of the database made before a format upgrade
  (`ponos-v{from}-v{to}.sqlite3`);
- `ponos.lock`: prevents opening the same profile twice.

A profile can be saved to a single versioned backup file and restored into a
new folder, never over an existing profile (`docs/backup-format.md`). The
backup file is not encrypted: it holds your health data and GPS positions, so
keep it somewhere private and not in a folder synchronized with the cloud.

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

## Checks

```
uv run --locked nox
```

It runs formatting and lint, `mypy --strict`, the check of dependencies
between modules, tests, translation tests and `pip-audit`.

`uv run --locked nox -s dist` builds the PyInstaller package (`release/`),
smoke-tests the executable, checks that no local path or user name is in it
and writes the archive and `SHA256SUMS` to `dist/release/`; it takes a few
minutes. The release workflow (`.github/workflows/release.yml`) runs it on
Windows and Ubuntu when a `v*` tag is pushed and attaches the result to a
draft release; started by hand on a branch (`gh workflow run release.yml --ref
<branch>`) it only builds the packages, kept as workflow artifacts.

## Security

To report a vulnerability see `SECURITY.md`.

## Licence

GPL-3.0-only. The full text is in the `LICENSE` file.
