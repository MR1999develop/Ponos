# Garmin response fixtures

**Built on the real structure recorded by the 2026-11-06 spike**
(`experiments/garmin_spike_shapes.json`): same keys, types, nesting and
string lengths as a real entry of the list; the values are invented
(ids, dates, numbers, names such as "Atleta di prova"), none is real. The three keys
with an id in the name are anonymised in the recording and here stay
with the recording's `<id-key-N>` placeholder. In the real response `averageHR` is always a number; the cases
`null`, 0, missing, id as a string and time with decimals are covered by the tests
with hand-built entries.

| File | Content |
|---|---|
| `activity_list_page.json` | A valid page of 5 entries with the complete real structure, from the most recent; keys not read are ignored |
| `activity_list_hostile.json` | Hostile entries (T-SEC-03): non-objects, non-numeric ids or ids with paths, missing, malformed or out-of-window times, missing, null, negative, non-finite or textual durations, missing sport type or with `../`, repeated id, names with HTML or SQL (which are text and do not count). Contains the `NaN` and `Infinity` literals, which Python's `json` accepts |
| `count_activities.json` | Count response: a bare integer, as in reality |

## Workouts and calendar (phase 7)

**Not recorded: written from the code of `garminconnect` 0.3.20** (no network call was made), with
invented ids and names; the real responses may differ (`experiments/garmin_workouts.md`).

| File | Content |
|---|---|
| `scheduled_workouts_by_period.json` | Three calendar entries: two made by the app (name with the "Ponos · " prefix) and one of the athlete |
| `scheduled_workouts_hostile.json` | Entries that must be left out: non-objects, missing `workout`, ids as text, negative or boolean, impossible or path-like dates, names empty, too long or with control characters; one with HTML and SQL in the name (it is text and is kept) |
| `workout_definition.json` | A library definition, with the ids, owner and dates of the original |
| `workout_upload_response.json`, `workout_schedule_response.json` | Responses of creating and of scheduling a workout |

`load_json(name)` in `__init__.py` reads a file. Deeply nested or
huge structures cannot be versioned and are built in the tests.
