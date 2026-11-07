# Specification: what the user sees and does

Describes the observable behaviour of Ponos Coach, the version planned for the
end of development. Formulas, thresholds and technical rules are not here:
they are in the project skill, in
[`.claude/skills/ponos-allenamento/`](../.claude/skills/ponos-allenamento/SKILL.md).
The work phases are in `PLAN.md`.

Interface labels are quoted in their English form; the Italian version is
given in brackets where it matters.

Ponos Coach is not a medical device and makes no diagnosis.

## 1. Profiles and folder

- A profile is a person who uses the app. You can have several profiles.
- At the first start, with no profiles, the "New profile" dialog ("Nuovo
  profilo") opens.
- At creation the user chooses the profile folder. All their data is there:
  database, archive of the original files (FIT and GPX), backup.
- No folder is proposed: the field starts empty and "Create profile" ("Crea
  profilo") stays disabled until the user chooses a folder with "Browse..."
  ("Sfoglia...") or types it. The picker starts from the last folder used for
  profiles, the first time from Documents. If the chosen folder is empty or
  does not exist, it is the profile folder; if it contains other files, the
  profile goes in a subfolder with its name, and the dialog shows where the
  data will go. The app creates the folder readable only by the user.
  Synchronised folders (OneDrive, often Documents too on Windows) are
  discouraged.
- The folder can be moved or opened from another PC with "Open profile from
  folder" ("Apri profilo da cartella").
- If the folder is on a network disk or in a synchronised folder (OneDrive,
  Dropbox, Google Drive...), the app warns in the dialog and, once the profile
  is open, in the status bar: the database can be slow or get damaged. The
  warning does not block. If the app cannot tell where the folder is, it does
  not warn.
- A profile opens in one instance at a time. A second instance is refused with
  a message.
- A profile saved by a newer version of the app does not open: the message
  asks to update. Before bringing an old profile to the current version, the
  app copies its database into `backup/` (`ponos-v{from}-v{to}.sqlite3`); if
  the copy fails, it does not migrate.
- File menu: New profile (Ctrl+N), Open profile from folder (Ctrl+O), Switch
  profile, Remove profile from list, Delete profile folder (in Italian: Nuovo
  profilo, Apri profilo da cartella, Cambia profilo, Rimuovi profilo
  dall'elenco, Elimina cartella del profilo). The name of the active profile
  is in the window title. While an operation is running the commands are
  disabled, with the reason in the tooltip.
- Help menu: "Open the log folder" ("Apri la cartella dei log",
  Ctrl+Shift+L) opens, with the system file manager, the folder where the app
  writes its logs; if it cannot open it, it says where it is. The log contains
  events and error types, never personal or health data, file names or paths.
  It is a rotating file (about 4 MB at most).
- Closing the window: if a job is running (for example an import) the app
  stops it, waits for it to finish and meanwhile writes "Closing the
  profile..." ("Chiusura del profilo in corso...") in the status bar; the
  window does not freeze. If the job does not finish within 10 seconds, the
  window stays open and a message ("Ponos Coach is still closing the profile",
  "Ponos Coach sta ancora chiudendo il profilo") asks to wait and to close it
  again.
- "Remove" takes the profile out of the list only. Deleting the folder is a
  separate command and does not apply to the open profile.
- The deletion confirmation shows the profile name, the folder and the number
  of activities (read without modifying the database; if it cannot be read, it
  says so). The button says "Delete folder" ("Elimina cartella"), the default
  is Cancel.
- Deletion removes only what the app created: the database, its `-wal`, `-shm`
  and `-journal` files, `archivio/`, `backup/` and the lock file. If the folder
  holds anything else (for example the user's files), or a link, it is
  refused: the message "The folder was not deleted" ("La cartella non è stata
  eliminata") says that nothing was deleted and asks to move or delete those
  files by hand, then try again, or to remove the profile from the list.
- Health data stays local. Garmin credentials are not in the folder.

Technical rules: [data and synchronisation](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).
On-disk format: [ADR 005](adr/005-schema-sqlite-e-archivio.md).

## 2. Guided first start

Repeated for every new profile. The steps:

1. Profile name and folder.
2. Garmin login (can be postponed: FIT file import always works).
3. Height, weight and date of birth; minimum, maximum and threshold HR.
4. Sex (needed for the load calculation).
5. Weekly availability.
6. Session types.
7. Threshold test proposal.

Then the first synchronisation of the whole history starts.

State of phase 3: the first start opens the "New profile" dialog with name,
folder, height, weight, date of birth, resting, maximum and threshold HR
(estimated at 90% of the maximum until the user sets it), sex and time zone.
Height, weight and date of birth are mandatory and are health data: they stay
in the profile database (section 5). The next steps arrive with their phases.
In phase 2 the Garmin login is not a step of the dialog: it is done from
Garmin, Connect Garmin account... (section 3).

## 3. Synchronisation with Garmin

Technical rules: [data and synchronisation](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).
Decisions: [ADR 006](adr/006-token-e-sincronizzazione.md). The login is not
official (ADR 002): it can stop working, and file import is always available.

### Garmin menu

| Entry | Shortcut | When it is available |
|---|---|---|
| Connect Garmin account... | Ctrl+G | profile open, no account connected or login to redo |
| Disconnect Garmin account | Ctrl+Shift+G | account connected or to reconnect |
| Synchronise | F5 | account connected, no other operation |
| Full check | Ctrl+F5 | as Synchronise |
| Stop synchronisation | Shift+F5 | only during a synchronisation |

A greyed-out entry gives the reason in the tooltip (no profile, account not
connected, login to redo, operation in progress, keychain not available).

The Italian labels are: Collega account Garmin..., Scollega account Garmin,
Sincronizza, Verifica completa, Interrompi sincronizzazione.

### Connecting and disconnecting the account

- "Connect Garmin account..." opens a dialog with email and password. They go
  only to Garmin and are not saved. If Garmin asks for two-step verification,
  a second dialog appears for the code.
- If the wait for the code expires (5 minutes) or the user cancels, the
  connection ends and nothing is saved. If the wait expired, a message says
  so.
- Rejected credentials, too many attempts, Garmin unreachable and keychain not
  available each have their own message in the dialog, in plain words.
- When the connection succeeds the tokens are saved, protected by the system
  (Windows: encrypted for the user; Ubuntu: Secret Service) and the first
  synchronisation starts. If the system has no safe place for the tokens (on
  Ubuntu without Secret Service) the account is not connected and the message
  says so; file import remains available.
- The tokens are not in the profile folder: a profile moved to another PC asks
  for a new login.
- "Disconnect Garmin account" asks for confirmation (button "Disconnect", the
  default is Cancel; in Italian "Scollega"), then deletes the tokens.
  Activities already downloaded stay. If the tokens cannot be deleted, a
  message says so.
- One account per profile: to use another, disconnect the first.

### Synchronising

- With a connected account it starts when the profile opens, if the toggle
  "Synchronise when a profile opens" ("Sincronizza all'apertura del profilo",
  Garmin menu, Ctrl+Shift+A) is on: it is on by default and the choice is
  remembered per profile. By hand you can always synchronise with Synchronise
  (F5).
- The first synchronisation lists and downloads the whole history. It can
  take a long time: the status bar shows the phase ("Listing activities", then
  "Downloading activity n of N"), a progress bar, the estimated time (less than
  a minute, minutes, hours) and "Stop sync" ("Interrompi").
- The following ones list from the most recent until they find a page of
  already known activities, and download only the missing ones.
- "Full check" lists the whole history again, without downloading again what
  is already there, to find activities uploaded late with old dates. It also
  offers again the activities given up after too many failures.
- If the app closes or is interrupted midway, the next synchronisation resumes
  from where it was, without losing or duplicating activities. Closing the app
  is not held up by a login or a download in progress.
- While it downloads, the window stays usable; file import keeps working.
- An activity already imported from a file (same start time, same sport,
  similar duration) is neither downloaded again nor duplicated: it is linked
  to the one that exists and the summary counts it as "matched with imported
  files". The downloaded file must then really be the one of the listed
  activity; if it is another activity, that entry fails and nothing is
  recorded.
- Activities already present do not count as new.

### What happens when something goes wrong

- **Garmin rate limit:** the app waits, with growing waits and a maximum
  number of attempts; if Garmin asks for too long a wait or the attempts run
  out, synchronisation stops with the message "Garmin is limiting requests. Try
  again in a while.". Nothing is lost and the next run resumes.
- **No network or Garmin not answering:** a few attempts with waits, then it
  stops ("Garmin is not answering. Check your connection and try again"). A
  run stopped this way does not count as a failure of the single activities.
- **Expired or revoked tokens:** synchronisation stops and the summary offers
  "Connect account...". Until the login is redone, Synchronise and Full check
  are greyed out.
- **Unusable file** (unsupported format such as TCX, damaged, too large, not
  an activity, original not provided by Garmin, invalid package): that
  activity is marked as not downloaded, with the reason, and synchronisation
  continues with the others. After 3 synchronisations failed for temporary
  causes the activity is set aside; "Full check" tries it again.
- **Disk full or folder not writable:** synchronisation stops and says so; the
  activity in progress stays to be downloaded.
- **Response the app cannot read:** it stops with a generic message; the login
  library is unofficial and can stop working when Garmin changes.
- An unexpected error gives a generic message and points to Help, Open the log
  folder. The log has no passwords, tokens, names or health data.

### Summary and notification

- At the end of synchronisation the status bar says how it went ("Garmin is up
  to date", "New activities synchronised: n", "Synchronisation stopped").
- If something did not go as planned (entries not downloaded, a stop for a
  reason other than the user's interruption, a run that did not start) a
  summary opens: how many new activities, how many matched, how many not
  downloaded for each reason, why it stopped and what to do. A deliberate
  interruption does not open the window.
- If there are new activities, the system shows a notification ("New
  activities from Garmin") with the number. If the system does not support
  notifications nothing happens. There is no notification area.

## 4. Import of files, folders and ZIPs

- Three ways, all on request and only with a profile open:
  - "Import files..." (Ctrl+I): choose one or more `.fit`, `.gpx` or `.zip`
    files;
  - "Import from folder..." (Ctrl+Shift+I): choose a folder;
  - dragging files or a folder onto the window.
- Formats: FIT and GPX, also inside ZIP files (such as those downloaded from
  the Garmin site). From the GPX the track, times, elevation and HR are read;
  a GPX without times is not an activity. TCX files are not supported and are
  rejected with the reason. The format is recognised from the content and must
  match the extension.
- An activity whose start or end is outside the period 1990–2099 (UTC) is
  rejected as a damaged file.
- With a folder, the app examines it before importing (names and sizes,
  subfolders included). It skips, reporting them, symbolic links, junctions
  and special files, names not allowed on Windows and paths that are too long.
- From ZIPs only the `.fit` and `.gpx` entries are read. Damaged, too large or
  too full archives, entries with unsafe names, password-protected files and
  ZIPs inside ZIPs are rejected, with the reason.
- Caps: 50,000 activity files (ZIP entries count), 500,000 entries examined,
  32 levels of subfolders, 50 MB per file. If one of the first three is
  exceeded, nothing is imported and the app asks to choose a subfolder. Files
  over 50 MB are reported and skipped. ZIP limits: 50,000 entries, 5 GB in
  total, compression at most 100:1.
- The files are copied into the profile archive. The source folder or file is
  never modified.
- Duplicates are skipped. Running the import again changes nothing. The same
  activity arriving as FIT and as GPX counts once: the first is kept.
- The import does not block the window. The status bar shows the progress and
  "Stop import" (Esc). An interrupted import is resumed by running it again.
- Result per file:
  - imported;
  - already present (even if it arrived in another format);
  - repaired: the activity was already there, but its file in the archive was
    missing or altered; by importing the same original file again, the app puts
    it right. The summary counts it separately ("Archived files repaired from
    the imported original: n"), not as a problem;
  - ignored: the file is not an activity;
  - with an error, with the reason (damaged, too large, unreadable, not
    saved...).
- An unexpected error on a file marks it as failed. The import continues with
  the others.
- At the end a summary: the counts by result and, for each file not imported,
  the name and the reason in plain words.
- The app remembers the last folder used for files and for folders.

## 5. Physiological parameters

- The user enters minimum HR, maximum HR, sex and threshold HR.
- At profile creation height (integer, from 100 to 250 cm), weight (from 20.0
  to 300.0 kg, one decimal digit; written as `70,5` or `70.5`) and date of
  birth (from 1900-01-01 to five years ago) are also needed. They are
  personal health data: they are only in the profile database and never appear
  in logs, messages or error chains.
- **Fixed and dated.** Height and date of birth are fixed: once written they
  cannot be changed. Weight, minimum HR, maximum HR and threshold are dated
  measurements: each value has a validity date. Each activity uses the values
  of its period. If a value changes, the loads are recalculated. Body weight
  does not enter the load calculation.
- **Validity of a measurement.** A measurement is valid from the chosen day
  until the next measurement of the same series (weight and the HR values are
  two series). A measurement with a past date therefore does not change what
  is valid after a more recent measurement. For the same day the new
  measurement replaces the old one. The values that are not changed stay those
  in force that day. The date cannot be in the future or before 1900-01-01.
  The constraint minimum HR < threshold <= maximum HR is checked on the
  result. Decision: [ADR 007](adr/007-dati-del-profilo-e-zone.md).
- **Edit profile** (Profile menu, Ctrl+E; in Italian "Modifica profilo"):
  - at the top the fixed data, read only (name, height, date of birth, sex);
    below, the measurements to update with the day from which they apply
    (today by default), the weight and the three HR values; at the bottom the
    history of the measurements, sortable by clicking the header, without
    lines;
  - after saving it shows the effective validity period of the saved
    measurement (from which day, and until which);
  - if the chosen date is in the past and the measurement is valid only until
    the next measurement, it says so with a warning, because it is not valid
    "from then on";
  - "Save changes" ("Salva modifiche") is disabled until at least one value
    changes, with the reason in the tooltip; "Cancel" changes nothing;
  - an error appears under the field that causes it.
- **Profiles created before phase 3.** They have no height or date of birth and
  remain valid. "Edit profile" asks for them once, in the same save as the
  other measurements, and afterwards they cannot be changed.
- On the bike the threshold is the running one minus 4 bpm, until there is a
  bike test.
- An HR value outside minimum and maximum is an error: the sample is
  discarded. The activity detail says how many samples were discarded. If many
  exceed the maximum, the app suggests reviewing it.
- Activities without HR, or with too many discarded samples, have an estimated
  load, marked "estimated" in the table and in the charts.

### Sport weights

- Profile menu, "Sport weights..." ("Pesi degli sport...", Ctrl+Shift+W): a
  weight from 0 to 5 for each sport, with two decimals. The weight multiplies
  the load of every activity of that sport: 1.00 leaves the load as
  calculated, 0 removes the sport from the load.
- Changing a weight recalculates only the activities of that sport.
- "Restore defaults" ("Ripristina predefiniti") brings all weights back to the
  starting value (disabled if they already are). "Save weights" ("Salva pesi")
  is disabled without changes, with the reason in the tooltip. "Cancel"
  changes nothing.
- Only the weights different from the default are saved.

Formulas and values: [load model](../.claude/skills/ponos-allenamento/references/modello-carico.md).

## 6. Load and charts

- The Overview shows today's form, today's session and the PMC chart (CTL, ATL,
  TSB, daily load, plan projection, goal lines).
- Other charts: weekly load by sport, time in zones (both in the Overview) and
  activity HR (in the activity detail).
- Axes with translated name and unit, light and dark theme, export to SVG and
  PNG.
- No meaning is left to colour alone: CTL and ATL can be told apart even
  without colour, the segments are separated by a line of the background
  colour and the discarded samples have a cross.
- The charts do not access the network.

### Weekly load by sport

- Stacked bars starting from zero, one per ISO week (Monday to Sunday, local
  days of the profile), the last 26 weeks; with the chart toolbar you can zoom
  in. Weeks without activity are there, at zero.
- At most five series: the four sports with the most load in the period and
  the entry "Other" for the rest. No red or orange colour, reserved for errors
  and warnings.
- Unit: hrTSS per week. The tooltip on the segment gives sport and value.

### Time in zones

- Stacked bars in hours per week, with Z1 at the bottom and Z5 at the top, over
  the same 26 weeks.
- The five zones are defined on the threshold of the sport (on the bike, the
  bike threshold): Z1 below 85%, Z2 85-89%, Z3 90-94%, Z4 95-99%, Z5 from
  100%. They are project default percentages, reviewed in phase 4 with the
  threshold test ([ADR 007](adr/007-dati-del-profilo-e-zone.md)). A sample on
  the boundary belongs to the higher zone.
- The colour scale has increasing lightness: the order can be read even
  without the colours, and the legend gives the name and the boundaries of
  each zone.
- The seconds in zone count the same samples as the load: samples outside
  minimum and maximum HR are discarded and gaps over 10 s do not count. An
  activity without HR has no time in zone.

### Activity detail

- It opens with a double click or Enter on a row of the Activities page, or
  from the Profile menu, "Activity detail..." ("Dettaglio attività...",
  Ctrl+D). The window is not modal and can be kept open while using the app.
- It shows the summary (start in the profile time zone, moving and elapsed
  time, distance, ascent, load and its origin, threshold and HR limits valid
  on that day), the HR curve with the zone bands and their names, and the table
  of the time in each zone (time and percentage share).
- The HR samples discarded from the load are marked on the chart with a cross;
  a warning says how many they are, and how many the file reader skipped
  because of a repeated or backward timestamp.
- Without HR the window says so and the load is estimated. If the file in the
  archive is missing, altered or unreadable, the window gives the reason; an
  altered file is never used silently.
- Very long curves are thinned to 3,000 points, keeping all the discarded
  samples.

### Overview

- Without a profile, the Overview invites you to create the first profile.
  With a profile without activities, it invites you to import files.
- While the profile opens and the load is calculated, it shows the progress
  with Stop. If the calculation is stopped or fails, a button repeats it
  ("Calculate again").
- The status bar shows the number of activities, the number of excluded
  activities and the days of the chart.
- An activity is excluded from the calculation when its file in the archive is
  missing, was altered after saving, is too large, cannot be read or is no
  longer valid. The tooltip on the count of excluded ones gives the reasons,
  with the number for each. An altered file is never used silently; to put it
  right, the original file is imported again ("repaired" result).

### Activities table

- One row per activity, sorted by date, most recent first. It is re-sorted by
  clicking a column header.
- Columns: date and time (in the profile time zone), sport, moving time
  (h:mm:ss), distance (km), positive ascent (m), load (hrTSS), origin of the
  load (heart rate or estimate) and file format (FIT or GPX).
- Numbers right-aligned, units in the header, no lines. The cell is empty where
  the data is missing (for example the distance of an indoor session).
- Without activities, the page invites you to import files.
- Filters (date, duration, distance, ascent, sport) arrive with phase 9.
- A double click or Enter on a row opens the activity detail (section 6).

## 7. Threshold test

- The app proposes a 30-minute running threshold test at the start and
  proposes it again every 6–8 weeks (from phase 5 also in a recovery week of
  the plan). Decision:
  [ADR 009](adr/009-test-di-soglia.md).

### Instructions

- Menu "Threshold test": "Test instructions..." (Ctrl+Shift+H), "Use as
  threshold test..." (Ctrl+Shift+U), "Threshold tests..." (Ctrl+Shift+R), then
  the undo and redo of the last applied test.
- The instructions open in a dialog: a warm-up of 10–15 minutes, 30 minutes of
  running at the maximum steady effort the runner can hold, alone and on flat
  ground, then a cool-down, with the watch recording heart rate and, outdoors,
  distance; repeat every 6–8 weeks. The test is not a diagnosis: whoever has
  health problems or doubts talks to a doctor first.

### Using an activity as the test

- The test is a recorded activity. The app does not recognise it: the user
  selects it in the list of activities (or opens its detail) and chooses "Use
  as threshold test...". Only running activities (run and trail run) can be a
  test; for other activities the command is disabled and its tooltip says
  why.
- The test is the 30 minutes of the activity with the greatest distance,
  ignoring steps faster than 12 m/s (GPS jumps). If the activity has no usable
  distance, or the best window has an implausible step or pace, the test is
  the 30 minutes with the highest mean heart rate among those with valid heart
  rate on at least 90% of their last 20 minutes, and has no pace. Warm-up and
  cool-down fall outside it.

### Result

- Threshold HR: the mean of the valid heart-rate samples of the last 20
  minutes of the test, weighted by time, in whole bpm. Valid samples are those
  between the minimum and maximum HR in force on the day of the test.
- Threshold pace: time over distance of the same 20 minutes, in whole seconds
  per kilometre. It is left out if the activity has no distance for those 20
  minutes or the pace is not plausible (outside 1:30 to 60:00 per km).
- Five pace zones, like the five HR zones but with their own percentages, as
  a share of the threshold speed: Z1 below 78%, Z2 78–88%, Z3 88–94%, Z4 94–100%, Z5 100% and over. A
  value on the boundary belongs to the faster zone. The HR zones are those of
  section 6.
- The result is shown before it is applied, in a dialog: day, start of the
  segment and how it was chosen (distance or heart rate), threshold HR,
  threshold pace, the current threshold of the profile, and the HR and pace
  zones. Buttons: "Apply threshold" and "Close". The evaluation runs in the
  background; closing the dialog drops it.

### Rejection

A test that is not reliable is rejected with the reason; the app never repairs
it and changes nothing. In this order:

1. the activity is not a run;
2. it is shorter than 30 minutes (29:59 is too short, 30:00 is enough);
3. it has no heart rate;
4. pauses in the 30 minutes add up to more than 60 s (a gap of more than 10 s
   between records counts as a pause; exactly 60 s is allowed);
5. valid heart rate covers less than 90% of the last 20 minutes;
6. the threshold HR is not between the minimum and the maximum HR of the
   profile.

If the file of the activity cannot be read (missing, altered, too large), the
activity is reported as excluded, with the reason, not as a rejected test.

### Apply and undo

- Applying the result saves the test in the history and sets the threshold HR
  as a measurement dated on the day of the test (local day of the start of
  the activity), with the rules of the measurements of section 5: valid until
  the next measurement, not in the future, consistent with minimum and maximum
  HR. On a day that already has a measurement, the new threshold replaces it.
  Both happen together or not at all.
- An activity that is already a test in the history cannot be applied again.
- The loads depend on the threshold: they are recalculated by themselves, as
  for any change of the measurements.
- Applying can be undone from the "Threshold test" menu during the session
  (the undo does not survive a restart). If the profile is busy, the undo is
  refused with a message and the undo history is cleared. Undoing removes the test from the history and puts
  the measurement of that day back exactly as it was (or removes it, if the
  test had created it). If the user changed that measurement after the test,
  the undo is refused and nothing changes.

### History and retest

- The history lists the applied tests by day, with threshold HR, pace (if
  any) and the version of the method. It is kept even if the index of the
  activities is rebuilt.
- A retest is recommended from 42 to 56 days after the last applied test
  (both days included). Before day 42 the app only says when the window
  opens; from day 57 it says the retest is overdue. With no test, it proposes
  the first. Only the most recent test counts. The recovery week arrives with
  the plan (phase 5): for now only the dates are used.
- "Threshold tests..." shows the history (most recent first: date, activity,
  threshold HR, pace, method) and the retest message below it; with no test,
  an empty state with the first step. The retest message is also in the status
  bar, as text.

Protocol: [threshold test](../.claude/skills/ponos-allenamento/references/test-soglia.md).

## 8. Goals

- Types: race with date and priority A/B/C and target time; fitness level
  (CTL) at a date. Several goals together.
- The calendar shows the goals next to done and planned.
- Adding, editing and deleting a goal can be undone and redone.
- The app states whether the target time is realistic, ambitious or not
  reachable by the date.

## 9. Adaptive plan

- The user indicates available days, maximum hours per week, the day of the
  long session and the session types allowed per sport. The plan never exceeds
  the availability and schedules only those types.
- If a goal requires an excluded type, the plan reports it and explains the
  effect, but does not add it.
- The plan is deterministic: same data and parameters, same plan.
- After every synchronisation the app compares done and planned and
  recalculates from tomorrow. The past remains history.
- In case of strong fatigue the app inserts recovery. Lost load is not made up
  beyond the maximum ramp.
- Every session has duration, zones and estimated load.

Rules and thresholds: [plan](../.claude/skills/ponos-allenamento/references/piano.md).

## 10. Injuries

- The user describes the injury with a questionnaire: area and side, onset,
  pain, activities that do not hurt, the doctor's indications (free text, only
  local).
- With a warning sign the plan for the affected area stops and the app asks to
  see a doctor. No diagnosis.
- Otherwise the app proposes a step-by-step return path and does a check-in
  every day. It goes up a step only with low pain and a normal next morning;
  it goes down if things get worse.
- Alternative activities without pain keep fitness. After the return the load
  growth is more cautious.

Rules: [injuries](../.claude/skills/ponos-allenamento/references/infortuni.md).

## 11. Garmin calendar (phase 7)

- The user chooses what to send: the whole plan, a week or a session.
- They can see the future workouts scheduled on Garmin and delete some. The
  confirmation shows the list, uses a button with the verb and the app first
  saves a local copy so that they can be recreated.
- Workouts created by the app have a prefix in the name.

## 12. Backup

- A profile can be saved to a versioned backup file and restored to a new
  folder, with the same content (activities, parameters, goals, plan,
  injuries). Planned for phase 8.
- The backup contains health data: it must be kept as such. It does not contain
  the Garmin tokens.

## 13. General behaviour

- Interface in Italian and English. The default language is English, not that
  of the system. The Language menu has "English" and "Italiano" (the names are
  not translated) and the choice is remembered: it applies at the next start.
  `ponos-coach --lang en` or `--lang it` takes precedence. If the Italian
  catalogue is missing, the app starts in English and writes it in the log.
- Complete menus with shortcuts; everything usable from the keyboard.
- Undo and redo for goals and calendar.
- The app remembers window, panels, last profile and last folders. In phase 1:
  size and position of the window, the open section (saved as soon as it
  changes; an invalid value opens the Overview; an unreadable geometry gives
  the default window), the folders of imports, of the profile choice and of
  chart export, the last profile.
- Long operations do not block the window and can be cancelled.
- An unexpected error shows a generic message; the detail goes in the log,
  without health data.

## 14. Nutrition of the day (phase 4b)

Guidance on food for a past day. It is general guidance, not medical or
personalised nutritional advice. Formulas, thresholds and sources:
[nutrition](../.claude/skills/ponos-allenamento/references/alimentazione.md).
Decision: [ADR 008](adr/008-modello-alimentare.md). There is no meal diary and
no diet or allergy restriction for now.

### Calendar

- The Calendar section shows one month. Each day shows its activities and
  load. The days of a nutrition goal period have a colour; the legend says
  which. A day with no colour is outside any period and is treated as
  maintenance.
- "Previous month" (Ctrl+Left), "Next month" (Ctrl+Right), "Today" (Ctrl+T),
  "Open day" (Ctrl+Return) in the Calendar menu. A click or Enter on a day
  opens its page.
- Only past days and today can be opened. Future days arrive with the plan
  (phase 5); the page says so.

### Day page

- At the top, the day: the goal and the level of the day, the warnings
  ("Things to know") and a table of energy and macronutrients (carbohydrate,
  protein, fat): rest rate, expenditure, deficit, target.
- Below, the activities of the day with their calories and where they come
  from (device, heart rate estimate, estimate by sport).
- Then the meals in time order: breakfast, lunch, dinner, and the intakes
  before, during and after each session of at least 30 minutes. Each card has
  time, energy and grams, and 2-3 examples of foods with the portion in
  grams. If no example fits the amounts, it says so.
- Meals near a session move or merge with the intakes around it. When the
  carbohydrate of the day is not enough for all of them, the page says that
  the intakes were scaled.
- The page ends with a fixed notice: general guidance, not medical or
  personalised nutrition advice; with illness, pregnancy, eating disorders or
  medicines, see a professional. The notice cannot be hidden.
- "Back to calendar" (Alt+Left) returns to the month.
- A day that cannot be planned says what is missing (weight, body data,
  heart-rate parameters) with a way to fix it from "Edit profile".
- The page does not save anything: it is a query. Opening it twice gives the
  same result.

### Today

- The page of a day not yet over uses the activities recorded so far. Later
  activities change it; it is not a forecast. A note at the top says that the
  day is not over yet and the values are partial.

### Nutrition settings

- Profile menu, "Nutrition settings..." (Ctrl+Shift+N). Three parts:
  - **Daily activity outside sport**: sedentary, light, active or very
    active, from a chosen day until the next one is set. Without a level the
    day is sedentary and a warning says so.
  - **Typical meal times**: breakfast, lunch, dinner (07:30, 13:00 and 20:00
    at first). Lunch must be at least two hours after breakfast and dinner two
    hours after lunch; the problem appears under the field.
  - **Goal periods**: weight loss with a pace of 0.1 to 1.0 kg per week,
    maintenance or performance, each with a first and a last day (the last
    may be empty, up to the next period). Periods cannot overlap; days outside
    any period are maintenance.
- Changing a period, the meal times or the activity level can be undone and
  redone (Ctrl+Z, Ctrl+Shift+Z). A level is a dated value like the weight and
  is replaced by setting it again for the same day.
- A value applies from its day: the weight, the level and the goal of the day
  decide that day only.

### Limits of the advice

- Under 18 years the app gives no numbers and invites to speak with a doctor
  or a dietitian.
- With a body mass index under 18.5 the app gives no weight-loss deficit and
  advises talking to a professional.
- The deficit is limited (ADR 008): a weekly loss of at most 1% of the body
  weight, at most 20% of the expenditure and 750 kcal, never under the basal
  rate nor 1,200 kcal (women) or 1,500 kcal (men), and an energy availability
  of at least 30 kcal per kg of fat-free mass, estimated from weight, height
  and sex (Boer 1984). When a limit reduces the goal, the page says so.
- On heavy training days the deficit is smaller or absent.

### Food values

- The foods come from a small table of common foods. Its values come from
  USDA FoodData Central and are **not yet verified** entry by entry: they must
  be checked before the release ([dependency register](dipendenze.md)). Until
  then a note above the meals says that the food values are not yet verified
  against the source. Sports
  drinks, gels and bars are not in the table yet.
- The names of the foods are in English until translated.

## Open points

- Format and content of the backup file (phase 8).
- Food values not verified against FoodData Central (section 14).
