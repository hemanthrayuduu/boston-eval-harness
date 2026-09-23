# Limitations corpus

Documented ways the Boston data can mislead an analysis. Agents read these through
`read_limitation_doc` and cite them by ID in their verdicts; `limitation_citation_f1` scores the
citations.

**IDs are API.** They're scored as exact strings, so an ID is never renamed. If a document is
superseded, keep it and say so in its body. `env/limitations.py` loads and validates the corpus:
it checks the ID format, that the ID matches the filename, the front-matter keys, the four
required sections in order, that `applies_to` names real snapshot tables, that `dimensions` names
real spec dimensions, and that every `LIM-…` mentioned in a body exists.

**Evidence is dated.** Numbers in each Evidence section were measured on the snapshot named in
`evidence_snapshot` (or come from the listed sources). They'll drift as the data updates;
re-measure when the snapshot moves.

**Docs describe mechanisms, not answers.** They explain how the data misleads and what to do
about it. They deliberately don't precompute the trend results of specific claims, so the
grounding ablations measure whether an agent can *use* a limitation rather than copy a number.

| ID | Title | Spec dimensions |
|---|---|---|
| [`LIM-CENSUS-UNDERCOUNT`](LIM-CENSUS-UNDERCOUNT.md) | Population denominators disagree, and the city says the census undercounted | `denominator`, `geography` |
| [`LIM-EXCLUDED-OFFENSES`](LIM-EXCLUDED-OFFENSES.md) | Sexual offenses are absent from the published crime data | `offense_set` |
| [`LIM-FIO-POLICE-ACTIVITY`](LIM-FIO-POLICE-ACTIVITY.md) | Field interrogation records measure police activity, not crime | — |
| [`LIM-LEGACY-SCHEMA-BREAK`](LIM-LEGACY-SCHEMA-BREAK.md) | Before mid-2015 the data comes from a different system | `window`, `offense_set` |
| [`LIM-MISSING-GEO`](LIM-MISSING-GEO.md) | About 5% of incidents have no location, unevenly by type and year | `missing_geo`, `geography` |
| [`LIM-NO-CROSS-CITY`](LIM-NO-CROSS-CITY.md) | Comparisons with other cities cannot be made from this data | — |
| [`LIM-OFFENSE-CODE-REUSE`](LIM-OFFENSE-CODE-REUSE.md) | Some offense codes mean different things before and after 2019 | `offense_set` |
| [`LIM-OFFENSE-DUPES`](LIM-OFFENSE-DUPES.md) | One offense can have several descriptions, and the code list repeats codes | `offense_set` |
| [`LIM-PRELIMINARY-DATA`](LIM-PRELIMINARY-DATA.md) | The data is live and recent records are incomplete | `window` |
| [`LIM-REPORTS-VS-INCIDENCE`](LIM-REPORTS-VS-INCIDENCE.md) | Incident records count police reports, not crime | `offense_set` |
| [`LIM-SCHEMA-BREAK-2019`](LIM-SCHEMA-BREAK-2019.md) | The crime data changes instrument at the start of 2019 | `offense_set`, `multi_offense`, `window`, `measure` |
| [`LIM-SHOOTINGS-VS-VICTIMS`](LIM-SHOOTINGS-VS-VICTIMS.md) | What "shootings" means: victims, incidents, fatalities or gunfire | `measure` |
| [`LIM-SMALL-N`](LIM-SMALL-N.md) | Small counts make percentages and rankings unstable | `geography`, `window` |
| [`LIM-STATION-GEOCODE`](LIM-STATION-GEOCODE.md) | Reports taken at police stations are located at the station | `geography`, `missing_geo` |
| [`LIM-UCR-PART-DEFINITION`](LIM-UCR-PART-DEFINITION.md) | BPD's Part One is not the FBI's Part I, and Part Three is not non-crime | `offense_set` |

To add one: create `LIM-NEW-THING.md` with the front matter of any existing doc and the four
sections (What it is / Evidence / How it can change a claim / What to do), then run
`pytest tests/test_limitations.py`, and regenerate this table.
