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

`load_json(name)` in `__init__.py` reads a file. Deeply nested or
huge structures cannot be versioned and are built in the tests.
