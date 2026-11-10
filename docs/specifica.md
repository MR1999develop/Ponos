# Specification: what the user sees and does

Describes the observable behaviour of Ponos Coach, the version planned for the
end of development. Formulas, thresholds and technical rules are not here:
they are in the project skill, in
[`.claude/skills/ponos-allenamento/`](../.claude/skills/ponos-allenamento/SKILL.md).
The work phases are in `PLAN.md`.

Interface labels are quoted in their English form; the Italian version is
given in brackets where it matters. The Italian labels of the plan, injuries,
map and Garmin calendar menus are in the translation file.

Ponos Coach is not a medical device and makes no diagnosis.

## 1. Profiles and folder

- A profile is a person who uses the app. You can have several profiles.
- At the first start, with no profiles, the "New profile" dialog ("Nuovo
  profilo") opens.
- At creation the user chooses the profile folder. All their data is there:
  database, archive of the original FIT files, backup.
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
File, Settings..., Garmin account page (section 3).

## 3. Synchronisation with Garmin

Technical rules: [data and synchronisation](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).
Decisions: [ADR 006](adr/006-token-e-sincronizzazione.md). The login is not
official (ADR 002): it can stop working, and file import is always available.

### Garmin menu and Garmin account settings

The account is a setting of the profile: it is connected, disconnected and
configured in File, Settings..., "Garmin account" page (see "Settings
window"). The Garmin menu has only the commands that act (no "Garmin account
settings..." entry: the page is a category of the Settings window).

| Entry | Shortcut | When it is available |
|---|---|---|
| Synchronise | F5 | account connected, no other operation |
| Full check | Ctrl+F5 | as Synchronise |
| Stop synchronisation | Shift+F5 | only during a synchronisation |

A greyed-out entry gives the reason in the tooltip (no profile, account not
connected, login to redo, operation in progress, keychain not available).

The Italian labels are: Sincronizza,
Verifica completa, Interrompi sincronizzazione.

The "Garmin account" page shows what is known about the
account (one sentence: connected, not connected, sign-in to redo, keychain not
available, no profile), the buttons "Connect Garmin account..." and
"Disconnect Garmin account" ("Collega account Garmin...", "Scollega account
Garmin"), with the same availability and tooltips as the commands they trigger,
and the check box "Synchronise when a profile opens".

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
  Settings, Profile page, Garmin account) is on: it is on by default and the choice is
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
  When Garmin answers other requests (the list, other downloads) but never
  the download of one activity, that run counts for the activity; after 3
  such runs it is set aside ("Garmin lists this activity but does not provide
  its file; Full check tries again") and no longer holds up the others.
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

## 4. Import of FIT files

Ponos Coach works only with FIT files (decision of 2026-11-09, ADR 014). GPX and
other formats are not read, and ZIP files are not imported (a ZIP is still read
for the original that Garmin sends during synchronisation, section 3, and for
the profile backup).

- Three ways, all on request and only with a profile open:
  - "Import files..." (Ctrl+I): choose one or more `.fit` files (the filter of
    the dialog is `*.fit`);
  - "Import from folder..." (Ctrl+Shift+I): choose a folder;
  - dragging files or a folder onto the window.
- Only `.fit` files are imported (the extension is matched regardless of case).
  In a folder every other file (GPX, ZIP, TCX, text...) is ignored without a
  message. A file chosen by hand with another extension is reported as an
  unsupported format.
- The FIT file is checked at the boundary (signature, CRC, structure, limits) and
  is rejected with the reason if it is damaged or is not an activity.
- An activity whose start or end is outside the period 1990-2099 (UTC) is
  rejected as a damaged file.
- With a folder, the app examines it before importing (names and sizes,
  subfolders included). It skips, reporting them, symbolic links, junctions
  and special files, names not allowed on Windows and paths that are too long.
- Caps: 50,000 activity files, 500,000 entries examined, 32 levels of
  subfolders, 50 MB per file. If one of the first three is exceeded, nothing is
  imported and the app asks to choose a subfolder. Files over 50 MB are
  reported and skipped.
- The files are copied into the profile archive. The source folder or file is
  never modified.
- Duplicates are skipped. Running the import again changes nothing.
- The import does not block the window. The status bar shows the progress and
  "Stop import" (Esc). An interrupted import is resumed by running it again.
- Result per file:
  - imported;
  - already present;
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

### Profiles that contain activities imported from GPX

A profile created before this change may contain activities imported from GPX
files. It is migrated when it is opened (schema version 11):

1. a consistent copy of the database is made in the `backup` folder of the profile
   (`ponos-v10-v11.sqlite3`; without the copy nothing is migrated);
2. in a single transaction the GPX activities are removed from the database, the
   `file_format` constraint admits only `fit`, the load cache is emptied (it is
   derived and is recomputed from the files), the threshold tests of the removed
   activities are removed, and the synchronisation journal entries that pointed
   to a removed activity go back to "pending" (the next synchronisation
   downloads Garmin's original again: a FIT becomes an activity, a GPX-only
   original fails as an unsupported format);
3. after the commit, the GPX files are removed from the archive. A file left
   behind by an interruption is a harmless orphan; a record is never left without
   its file;
4. the app tells the user, once, when the profile opens, how many activities were
   removed ("Activities removed") and that the pre-migration copy of the database
   is in the backup folder.

A migration interrupted at any point leaves the database as it was (version 10)
and the files untouched; opening the profile again runs it from the start. A
backup made before this change may contain GPX activities: restoring it gives a
profile that is migrated the same way when opened.

### Profiles with the old weekly availability (schema version 12)

A profile whose availability was saved as minutes per weekday is migrated when
it is opened ([ADR 016](adr/016-disponibilita-giorni-e-sedute.md)), after the
usual copy of the database in the `backup` folder (`ponos-v11-v12.sqlite3`),
in one transaction: the available days are those that had minutes, the
sessions per week are as many as the days, the day of the long session is
added to the days if it was not one, and the weekly maximum is kept. A saved
availability with no day with minutes becomes "nothing set". The minutes of
each day are lost; the copy keeps them. A backup made before this change
restores to a profile that is migrated the same way when opened.

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
- **Edit profile** (Settings, Profile page; Profile menu, Ctrl+E, opens it; in
  Italian "Modifica profilo"):
  - at the top the name, editable (1 to 60 characters; an invalid name is
    reported on the field), and the fixed data, read only (height, date of
    birth, sex); the new name is written first to the profile database (source
    of truth), then to the profile list; if the list cannot be updated the name
    stays valid and the list realigns at the next opening; the window title
    and the profile menus update at once;
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

- Settings, Sport weights page (Profile menu, "Sport weights..." ("Pesi degli
  sport...", Ctrl+Shift+W), opens it): a
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

- The Overview shows the PMC chart (CTL, ATL, TSB, daily load, plan
  projection, goal lines); today's session is in the calendar. The form (TSB) is in the same
  chart as the load (CTL, ATL), on one y axis.
- The Overview shows one chart at a time, chosen in a short list on its left:
  "Fitness and fatigue", "Weekly load" and "Time in zones". The dates stay in
  step between the three.
- Other charts: weekly load by sport, time in zones (both in the Overview) and
  activity HR (in the activity detail).
- Dragging with the right button stretches or squeezes a chart vertically.
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
- One series per sport with load, each with its own fixed colour (seven sports,
  seven colours): no sport is grouped into "Other", which holds only the
  activities of sport "Other" (user's request of 2026-11-09). No red or orange
  colour, reserved for errors and warnings.
- Unit: hrTSS per week. The tooltip on the segment gives sport and value.

### Time in zones

- Stacked bars in hours per week, with Z1 at the bottom and Z5 at the top, over
  the same 26 weeks.
- The five zones are defined on the threshold of the sport (on the bike, the
  bike threshold), as in the 5th edition of the reference text: Z1 below 78%,
  Z2 from 78% to 86%, Z3 from 87% to 93%, Z4 from 94% to 99%, Z5 from 100%
  (since phase 5; before, the percentages were 85, 90, 95 and 100). A sample
  on the boundary belongs to the higher zone. The change moves only the seconds
  in each zone and the planned load of a session, not the load of an activity
  already done ([ADR 007](adr/007-dati-del-profilo-e-zone.md)).
- The colour scale has increasing lightness: the order can be read even
  without the colours, and the legend gives the name and the boundaries of
  each zone.
- The seconds in zone count the same samples as the load: samples outside
  minimum and maximum HR are discarded and gaps over 10 s do not count. An
  activity without HR has no time in zone.

### Activity detail

- It opens with a double click or Enter on a row of the Activities list, or
  from the Profile menu, "Activity detail..." ("Dettaglio attività...",
  Ctrl+D); there is no button for it. The window is not modal and can be kept open while using the app.
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
  load (heart rate or estimate) and file format (always FIT).
- Numbers right-aligned, units in the header, no lines. The cell is empty where
  the data is missing (for example the distance of an indoor session).
- Without activities, the page invites you to import files.
- Filters (date, duration, distance, ascent, sport) are above the table and
  are shared with the Map (section 15).
- A double click or Enter on a row opens the activity detail (section 6); the
  double click also centres the map on that activity.

## 7. Threshold test

- The app proposes a running threshold test at the start and proposes it again
  every 6–8 weeks, in a recovery week of the plan when there is one (section
  9). Decision: [ADR 009](adr/009-test-di-soglia.md).
- Two protocols, chosen in the result dialog and remembered (30 minutes is the
  default):
  - **30 minutes**, as described below: the threshold is read from the last 20
    minutes of the 30.
  - **20 minutes** (5th edition): the segment is 20 minutes; the threshold HR
    is 95% of the mean HR, rounded; the threshold pace is the mean pace plus
    5% of the time per kilometre. Shorter than 20:00 is rejected. Every test is
    saved with its protocol.
  Where the text below says "30 minutes" and "the last 20 minutes", with the
  20-minute protocol the window is the 20 minutes.

### Instructions

- There is no "Threshold test" menu. Profile menu: "Use as threshold test..."
  (Ctrl+Shift+U, also in the activity detail window) and "Threshold tests..."
  (Ctrl+Shift+R, opens Settings, Training plan page, where the history is);
  Help menu: "Test instructions..." (Ctrl+Shift+H). The undo and redo of the
  last applied test are buttons of the history, in Settings, Training plan.
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
- Applying can be undone from the history (Settings, Training plan) during the session
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
  the first. Only the most recent test counts.
- The test is a session of the plan (ADR 015). With no test, with the retest
  window open or with the window gone, the plan puts it in one of its first
  weeks (section 9). With a recent test the plan has none; it only says, in an
  easy week of the 3:1 cycle that touches the window (not in the taper before a
  race), where a retest could go; if none can be placed, only the dates are
  given.
- The history, on the Settings, Training plan page ("Threshold tests..." opens
  it), lists the tests (most recent first: date, activity, threshold HR, pace,
  method) and has the retest message below it, with the buttons Undo, Redo and
  "Test instructions..."; with no test, an empty state with the first step. The retest message is also in the status
  bar, as text.

Protocol: [threshold test](../.claude/skills/ponos-allenamento/references/test-soglia.md).

## 8. Goals

- Types: race with date and priority A/B/C and target time; fitness level
  (CTL) at a date. Several goals together.
- The calendar shows the goals next to done and planned.
- Adding, editing and deleting a goal can be undone and redone.
- The app states whether the target time is realistic, ambitious or not
  reachable by the date.
- The plan is on the calendar, so its commands are in the menu of a day (right
  button on the day, or the Menu key); there is no "Plan" menu and no "Calendar"
  menu (user's request of 2026-11-09). In the menu of a day the commands of the day come first ("Add
  race..." (Ctrl+R), "Add form goal..." (Ctrl+Shift+F) on that day, "Change goal..."
  (Ctrl+Shift+E) and "Remove goal" (Ctrl+Delete) on a day with a goal, then the
  injuries), then, in a group of their own, the commands of the whole plan:
  "Goals and plan status..." (Ctrl+Shift+P), "Training settings..."
  (Ctrl+Shift+T), Undo and Redo of the goals and the settings. The shortcuts
  work without opening the menu; moving about the month and removing the goal
  act only while the calendar has the focus.
  A disabled entry gives the reason in the tooltip. Two races on the same day
  are refused.
- A race has a date, a priority A, B or C, a distance and an optional target
  time; a form goal has a date and a CTL.

## 9. Adaptive plan

- Settings, Training plan page (the menu of a day, "Training settings...", opens it)
  holds the weekdays on which the user can train (check boxes), the number of
  sessions a week, the day of the long session, the most hours of training in a
  week and the session types the plan may use (decision: [ADR 016](adr/016-disponibilita-giorni-e-sedute.md);
  before, the page asked for minutes on each day, which are gone). The page
  starts with suggestions, nothing is saved until "Save changes". Below, the
  "Threshold test" section chooses the protocol that the test dialog starts with
  (30 or 20 minutes), remembered between sessions and applied at once. The plan never exceeds the availability and schedules only those
  types.
- Which days get a session (when there are fewer sessions than available
  days): the day of the long session first; then, each time, the available
  day farthest from the ones already chosen, counting the week as a circle
  (Sunday is next to Monday), the lowest weekday on a tie. The same
  availability always gives the same days. The long day is always one of the
  available days; the sessions are between 1 and the number of days.
- The length of a day is not set by the user: a day holds at most 240 minutes
  (the longest session type) and each session at most the maximum of its type.
  The long session is always the longest of the week: easy and recovery
  sessions last at most 75% of it and the others are shorter than it; when the
  hours or the load do not allow all, the other sessions are shortened first.
- The plan starts tomorrow and reaches the last goal, at most 52 weeks ahead; a
  goal further away stays in the list with a warning. It has one session a day
  and one sport (that of the first A race).
- The plan is refused, with the reason, when there are no goals, a goal is today
  or in the past, two races are on the same day, there is no availability or no
  session type for the sport. Otherwise it always exists and what is not right
  goes in warnings (for example a half marathon without a long session, two A
  races three weeks apart, a CTL that cannot be reached at the maximum ramp).
- The threshold test, when one is due, is a session of the plan (ADR 015):
  - due with no test, with the retest window open or gone; not with a recent
    test, with an open injury or illness (or one closed within 28 days), or
    after two planned tests went by undone;
  - the protocol is the one chosen in the settings (30 minutes: 15 warm-up,
    the test, 10 cool-down, 55 in all; 20 minutes: 45 in all), in the sport of
    the plan if it is running, trail or cycling;
  - it goes in the first week of the plan, on the earliest day with time for
    it that is not next to the long session or another hard day (so it follows
    and is followed by an easy day or a rest) and not in the 72 hours before a
    race; if that week has no such day, in the second week; never in a taper,
    recovery or easy week, and even if the user chose no threshold session;
  - it is a hard session: it counts in the weekly limits of intense sessions
    and hard days and its load is part of the week's load and of the projection;
  - it is sent to the Garmin calendar like the other sessions, as the workout
    "Threshold test <minutes> min";
  - when the activity of that day is applied as a test the plan has no more
    tests; if the day goes by with no test applied, the test comes back once,
    in the week after, and then the plan stops asking.
- The CTL grows at most 5 points a week (3 with an injury or illness open, or
  closed within 28 days). Every goal A has a taper before it and every goal B
  a short one; three weeks of load are followed by one of recovery.
- Where the plan is seen:
  - the Calendar shows the planned session and its load on the days ahead, and,
    for the days already past, what was planned that day next to what was done;
  - the PMC chart has the plan's projection of CTL, ATL and TSB (dotted) and the
    races as markers; with a projection it opens on the last three months and
    the plan;
  - the plan is rebuilt after each synchronisation, import or change of the
    data; if it fails, the rest of the app keeps working.
- An empty profile starts from CTL zero and still gets a plan.
- If a goal requires an excluded type, the plan reports it and explains the
  effect, but does not add it.
- The plan is deterministic: same data and parameters, same plan.
- After every synchronisation the app compares done and planned and
  recalculates from tomorrow. The past remains history.
- The frozen history of the plan is not erased (decision of phase 5): the days that
  became past stay as they were planned, in a table that the database itself
  refuses to update or delete. There is no "clear the history" command and none
  will be added; the only way to remove it is to delete the profile, which is
  the user's own data folder. A frozen day that cannot be read back (damaged
  file) is shown as such in the adherence, with what was done and without the
  planned load, and the current plan never replaces it.
- In case of strong fatigue the app inserts recovery. Lost load is not made up
  beyond the maximum ramp.
- Every session has duration, zones and estimated load.

Rules and thresholds: [plan](../.claude/skills/ponos-allenamento/references/piano.md).

## 10. Injuries

- The Injuries section lists injuries and illnesses with the state of the one
  selected. Injuries menu: "Report an injury..." (Ctrl+J), "Report illness..."
  (Ctrl+Shift+J, one illness at a time), "Daily check-in..." (Ctrl+K),
  "Medical review done" (Ctrl+Shift+V, only when a warning sign waits for a
  visit), "Close injury or illness...", then Undo and Redo. A disabled entry
  gives the reason in the tooltip.
- The user describes the injury with a questionnaire: area and side, onset,
  pain, activities that do not hurt, the doctor's indications (free text, only
  local).
- With a warning sign the plan for the affected area stops and the app asks to
  see a doctor. No diagnosis.
- Otherwise the app proposes a step-by-step return path and does a check-in
  every day. It goes up a step only with low pain and a normal next morning;
  it goes down if things get worse.
- Alternative activities without pain keep fitness. After the return the load
  growth is more cautious: the weekly ramp is 3 while a problem is open and for
  28 days after its closing day, also when that day is dated ahead.
- With a warning sign or a systemic illness the plan has no session at all,
  with the reason and the invitation to see a doctor. After a medical review the
  path starts again from the first step.
- The app never decides the return: when the path is done it says the injury
  could be closed, and closing is the user's.
- Adding, changing and closing can be undone and redone. The doctor's notes and
  the pain scores are not in the saved plan or in the logs.

Rules: [injuries](../.claude/skills/ponos-allenamento/references/infortuni.md).

## 11. Garmin calendar (phase 7)

- The user chooses what to send: the whole plan from today on, the week of the
  selected day or the session of the selected day. Garmin menu: "Send plan to
  Garmin..." (Ctrl+Shift+S), "Send this session to Garmin" (Ctrl+Shift+Y),
  "Garmin calendar..." (Ctrl+Shift+C), "Recreate deleted workouts"
  (Ctrl+Shift+D), "Stop Garmin calendar task" (Ctrl+Shift+X). The work runs in
  the background with a progress bar and Stop.
- A workout is made of flat steps with the heart-rate range in bpm and, for a
  run with a threshold pace, the pace range. In a running step that has a pace
  range the pace is the main target on the watch and the heart rate the
  secondary one (the heart rate lags too much in intervals); a step without a
  pace range keeps the heart rate as main target (ADR 013). The app lists the
  pace before the heart rate. An interval session shows every repetition on
  the watch.
- A send can be repeated: a workout with the same name on the same day is not
  created again, also after a lost answer. At most 60 workouts per send (the
  sessions not yet on the calendar, earliest first); the summary says how many
  were left, and sending again continues.
- "Garmin calendar..." lists the future workouts scheduled on Garmin, those of
  the app and the athlete's own, and lets the user delete some. The
  confirmation ("Delete workouts (n)", the other button "Keep them") shows the
  list. The app first saves a local copy of each, then removes it; at most 100
  at a time; a day before today is never touched. A workout of the athlete is
  only taken off the calendar and stays in the library; one of the app is also
  deleted from the library. If Garmin refuses, nothing is deleted and no copy
  waits.
- "Recreate deleted workouts" makes them again from the copies, only if the
  current plan still has that session on that day; the new workout has new
  ids. When the plan has changed since a send, the app offers to delete the
  workouts of days no longer planned, with the same confirmation.
- Workouts created by the app have the prefix "Ponos ·" in the name.
- The shape of the Garmin responses has not been checked on the live service
  yet (`T-MAN-01`).

## 12. Backup

- A profile can be saved to a versioned backup file (`.ponosbackup`) and
  restored to a new folder, with the same content (activities, parameters,
  goals, plan, injuries). Format: [backup format](backup-format.md).
- The backup contains health data and GPS positions and **is not encrypted**:
  it must be kept as such, in a private place and not in a synchronised
  folder. It does not contain the Garmin tokens.

### Interface (phase 8)

- File menu: "Back up profile..." (Ctrl+Shift+B) and "Restore profile from
  backup..." (Ctrl+Shift+O). The first needs an open profile (the tooltip
  says so); both are off while a backup or a restore is running.
- Backup: a save dialog proposes `<profile name> <date>.ponosbackup`; the
  folder is remembered apart from the one of "Open profile from folder". The
  suffix is added if missing. A file that exists is never replaced.
- Restore: first the backup file, then a new or empty folder (each dialog
  remembers its own folder). A folder with anything in it is refused and
  nothing is changed.
- The work runs on its own thread; the window stays usable and the profile
  can stay open. After one second a progress indicator appears in the status
  bar (no end in sight: the use cases report no steps and cannot be
  cancelled). Logs hold only the types of the errors.
- When it ends, a box shows the size and the number of activity files. After a
  restore it offers to open the profile ("Open profile" / "Not now"); if the
  profile list already has a profile with the same id, it says so and asks
  whether to replace that entry ("Replace entry" / "Keep the list as it is");
  the old folder is never deleted.
- One message per reason, in plain words: file exists, destination inside the
  profile folder, writing failed, not a backup, too large, damaged (checksum
  or database), unsafe content, backup of a newer version, folder not empty,
  folder in use.

## 13. General behaviour

- Interface in Italian and English. The default language is English, not that
  of the system. Settings, Language page has "English" and "Italiano" (the names
  are not translated) and the choice is remembered at once: it applies at the
  next start, and the page says so.
  `ponos-coach --lang en` or `--lang it` takes precedence. If the Italian
  catalogue is missing, the app starts in English and writes it in the log.
- Complete menus with shortcuts; everything usable from the keyboard.
- **Settings window** (File menu, "Settings..." ("Impostazioni..."), Ctrl+, or the
  standard Preferences key of the system). One window, not modal, one instance
  (opening it again brings it forward on the page it showed). On the left the
  categories, on the right the page: Profile (name, measurements and history),
  Garmin account (status, connect and disconnect, synchronise when a profile
  opens), Sport weights, Training plan (and the threshold test protocol and
  the history of the tests), Nutrition, Map, Language. Each page keeps the way
  of saving that its content always had (changes that apply at once, with their
  own undo, or edits that wait to be saved). The window has a "Save changes"
  button (Ctrl+S) always in view at the bottom, next to "Close": it saves the
  page in view and is enabled only when that page has unsaved edits. It exists
  because a page's own button could sit below the fold (the Profile page is
  taller than the window, and a threshold with a past date was never saved).
  The pages that read the profile read
  it when they open; without a profile open they say "Create or open a profile
  first." instead of showing fields. A category with unsaved edits has an
  asterisk and a tooltip; closing the window (or quitting) with unsaved edits
  asks "Some settings have changes that are not saved." with the buttons Save,
  Discard and Keep editing (default). The commands that open a page from where
  the user looks for them (Profile menu, the menu of a day, the
  map notice and the day page) open the same window on that page; the Map
  and Nutrition settings are reached only from Settings itself, the map notice
  and the day page (no menu entry); there is no other dialog for
  the same setting.
- Undo and redo for goals and calendar.
- The app remembers window, panels, last profile and last folders. In phase 1:
  size and position of the window (an unreadable geometry gives the default
  window), the position of the three dividers between the panes (an unreadable
  value gives equal halves), the folders of imports, of the profile choice and of
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

- The Calendar pane shows one month. Each day shows its activities and
  load. The days of a nutrition goal period have a colour; the legend, compact
  and wrapped onto several lines, says which. A day with no colour is outside
  any period and is treated as maintenance.
- "Previous month" (Ctrl+Left), "Next month" (Ctrl+Right), "Today" (Ctrl+T) and
  "Open day" (Ctrl+Return) are in the menu of a day and act while the calendar
  has the focus. The buttons under the day panel are gone. A click or Enter on a day
  opens its page. The calendar has no "Today" button (user's request of
  2026-11-09): Ctrl+T and the menu of the day still go to today.
- In the window of a day, a click (or Enter) on an activity of its list opens
  the window of that activity, or brings it forward if it is already open; the
  planned sessions of the plan open nothing.
- Past days, today and the days of the plan can be opened. A day with no plan
  (no plan, a refused plan, past the last goal or the horizon) says why.

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

- Settings, Nutrition page (there is no menu entry: the day page and the notices
  that need it open it). Three parts:
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

### Days ahead (phase 5)

- The page of a future day is built from the saved plan with the same model:
  the planned session is at the typical time of that kind of session, with the
  energy of its estimated effort, and a race is a session of its day with the
  time the plan expects. It is a guide, not a forecast, and says so.
- No deficit in the week of an A or B race, nor in the taper before it: the day
  is a maintenance day, and when a weight-loss period overlaps, the page says
  so. A C race pauses nothing.
- A change of the plan changes the days ahead only. The same data give the same
  page.

### Lean mass

- In "Nutrition settings..." the user may enter a measured lean mass with its
  day. It replaces the estimate (Boer 1984) in the energy availability limit
  from that day on, if it is plausible (between 40% and 95% of the weight of
  the day); otherwise the estimate is used. It can be undone.

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

- The foods come from a small table of common foods. Its values come from the
  French food composition table CIQUAL (ANSES, 2025 version, Licence Ouverte
  2.0), one CIQUAL entry per food, checked by `experiments/verify_foods.py`
  (`source.verified_on`), and compared with the Italian CREA tables, which are
  not copied ([dependency register](dipendenze.md), ADR 008). While a table is
  not verified a note above the meals says so. Sports drinks, gels and bars are
  not in the table yet.
- The names of the foods are in English until translated.

## 15. Map and filters (phase 5b)

Decision: [ADR 010](adr/010-mappa.md).

### Filters

- Above the Activities table and the map: date (from–to), duration, distance,
  positive ascent and sport. Minimum and maximum are optional (empty means no
  limit); a minimum above the maximum are swapped; negative values are refused
  with a message. Input is tolerant: comma or point, duration as "1:30" or "90".
- One state of the filters, shared by the table and the map, remembered for the
  next start. Map menu: "Filter activities" (Ctrl+F), "Clear filters"
  (Ctrl+Shift+K).

### Map

- The main window has four panes, all visible and all resizable, and no
  sidebar: the Overview at the top left, the Calendar at the bottom left, the
  Activities list (under its filter bar) at the top right and the Map at the
  bottom right. One divider between the columns and one in each column; none
  can collapse a pane away, and where they were left is remembered. The View
  menu moves the keyboard focus to each pane: "Go to Overview" (Ctrl+1), "Go to
  Calendar" (Ctrl+2), "Go to Activities" (Ctrl+3), "Go to Map" (Ctrl+4). The map
  has a minimum height. Selecting an activity in the list selects it on the map
  and the other way round.
- Map menu: "Show activities and map" (Ctrl+M), "Show all activities" (Ctrl+0),
  Zoom in and out, "Filter activities", "Clear filters", "Clear map cache..."
  (Ctrl+Shift+Backspace). There is no "Map settings..." entry: the Map page of
  Settings opens from the button of the map notice and from Clear map cache.
- At low zoom the starts of the filtered activities are grouped in circles with
  the number, which split and merge at each level; a click on a group zooms to
  its area. Close up the starts are drawn bigger, and starts at the same place
  fan out around their group when it is clicked at the highest zoom, each one
  clickable. Only the track of the selected activity is drawn, as a line
  coloured by sport, with a legend (never colour alone). A double click on an
  activity in the list centres the map on it.
- Above the map, the count and the totals (km, ascent, hours) of the activities
  that start in the framed area, after the filters, updated at each move or
  zoom. The separate list of that area is gone, because the table already
  lists the activities: the check box "Only the visible map area", under the
  table's count, narrows the table to the same activities (remembered between
  sessions). Activities without a position (indoor, strength) are counted
  apart.
- The detail of the selected activity has the elevation profile linked to the
  track in both directions, and the track can be coloured by heart-rate zone
  with the legend of the zones of that day. Without elevation the profile says
  why it is missing.
- Settings, Map page: the size of the tile cache (4 GB by default); "Clear map
  cache" empties it. There is no key to enter: since 2026-11-09 the background
  comes from OpenStreetMap (`tile.openstreetmap.org`), at the user's request
  (ADR 010, amendment).
- The page has no network: the tiles come through the app. The map's last view
  is not saved. The tile server sees which areas are looked at, and it is a
  shared service with no guarantee: it can block the app without notice. Without
  network or when the server refuses, the map keeps working on a neutral
  background with a line that says why; list and filters keep working.
- The app follows the server's usage policy:
  - each request has the `User-Agent` `PonosCoach/<version>` with the contact
    address of the project;
  - "© OpenStreetMap contributors" is on the map and on the map of the activity
    detail;
  - a tile is kept as long as the response headers allow, 7 days when they say
    nothing and at most 30 days; an expired tile is asked again with a
    conditional request (`ETag`, `Last-Modified`) and, if the server says it has
    not changed, kept;
  - only the tiles that are looked at are asked: no pre-loading and no bulk
    download;
  - when the server refuses (403 or 429) the app asks for nothing for 300
    seconds.
- The tile cache (a file in the system cache folder, outside the profile) has
  a format of its own, version 2, with the validators of each tile; an older file
  is rebuilt empty, since it only holds copies.

## Open points

- Three foods use an approximate CIQUAL entry (Greek yoghurt 0%, part-skim
  mozzarella, black beans) and three choices are to be reviewed by hand
  (Medjool dates, roast chicken, white bread): `ciqual_note` in the table.
- The Garmin workout and calendar responses are not verified live (section 11).
  The OpenStreetMap usage policy is read again before the release (section 15).
