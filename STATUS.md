# STATUS — BostonClaimBench

Where the project stands, and what to do next. This is the working checklist.
`ROADMAP-CLAIMBENCH.md` is the design; this file tracks progress against it.
Update it at the end of every work session.

**Last updated:** 2026-09-23 · **Branch:** `claude/eval-harness-roadmap-review-yr1bxk` (not merged to `main`)

---

## 1. Snapshot

| | |
|---|---|
| Plan in force | `ROADMAP-CLAIMBENCH.md` (the claim-verification benchmark). `roadmap.md` is the fallback. |
| Code | ~2.9k LOC in 4 packages (`env/`, `harness/`, `specs/`, `ingest/`) plus ~2.5k LOC of tests |
| Tests | **300, all passing on macOS** (2026-09-23). All offline. Linux sandbox path not re-run since B1 fix (see B1). |
| Data | **Snapshot built and sealed 2026-09-23.** Pull: 53 resources, 2,262,459 rows, 54 MB Parquet in `data/raw/`. Build: 12 tables (11 loaded + `offense_codes` derived), 1,906,360 rows, `data/boston.duckdb` (83 MB, 5s). Both gitignored. `data/manifest.json` is committed and sealed. See §4a and §4b. |
| Claims | None. No `claims/` directory. |
| LLM calls | None yet. No agent loop, no runner, no LiteLLM dependency. |
| CI | None. No `.github/workflows/`. |

**In one line:** the parts that need no data and no LLM are built and tested: guard, sandbox, run
identity, spec-curve engine, trace schema, pure scorer, CKAN ingest, and DB build. Real data is now
on disk. Nothing has run against a real model yet.

Hitting the network was blocked in the environment where the code was written. **It works from
this Mac**, which unblocks Phase 1 (verified 2026-09-22, see §4).

---

## 2. Built so far (mapped to roadmap phases)

| Phase | Item | File(s) | State |
|---|---|---|---|
| 0 | Project setup | `pyproject.toml` | Partial. Deps pinned. No `uv.lock` (gitignored on purpose), no `.env`, no LiteLLM or Ollama. |
| 1 | CKAN client, datastore plus direct-download fallback | `ingest/ckan_client.py`, `ingest/http.py` | Done, tested offline against fakes |
| 1 | Pull to Parquet, seal manifest | `ingest/pull.py` (`python -m ingest.pull`), `ingest/manifest.py` | Done. **Run live** 2026-09-23: 0 failures, 9 alternate renderings skipped and listed |
| 1 | XLSX parsing (all sheets), fallback when the datastore is empty | `ingest/ckan_client.py` | Done. Needed for the offense-code table and field dictionaries |
| 1 | Parquet → DuckDB with measured cast loss and dual checksums | `ingest/build_db.py` (`python -m ingest.build_db`) | Done. Multi-resource tables, strptime formats, coverage check, seals the manifest |
| 1 | Snapshot table definitions | `ingest/tables.py` | Done. 11 tables from 20 resources; 33 resources listed as unused, each with a reason |
| 1 | Snapshot verification | `ingest/verify_snapshot.py` (`python -m ingest.verify_snapshot`) | Done |
| 1 | Offense-code lookup (UCR part + crime flag per (code, description)) | `ingest/offense_codes.py`, `ingest/offense_code_labels.csv` | Done. Derived table `offense_codes`, 308 pairs. **85 hand labels are drafts, not yet reviewed** (§4c) |
| 2 | Dimensions with written justifications | `specs/dimensions.py` | Done (measure, window, geography, denominator, offense_set, multi_offense, missing_geo) |
| 2 | Per-claim spec space, capped at 48 | `specs/space.py` | Done |
| 2 | Claim assertions (change / level / comparison / rank) | `specs/assertions.py` | Done |
| 2 | Curve computation plus driver attribution | `specs/curve.py` | Done. Takes an `Evaluator` protocol |
| 2 | Label derivation (0.95 / 0.05 thresholds) | `specs/labels.py` | Done |
| 2 | **`specs/compute.py`, the real DuckDB evaluator** | — | **Missing.** Only synthetic evaluators in tests |
| 4 | Allowlist SQL guard | `env/guard.py` | Done, with malicious-query suite |
| 4 | Subprocess sandbox (timeout, DuckDB `memory_limit`, plus `RLIMIT_AS` where the OS allows) | `env/sandbox.py`, `env/_worker.py` | Done. Portable since B1 fix |
| 4 | Tools, agent loop | `env/tools.py`, `env/loop.py` | **Missing** |
| 5 | Config hash vs run ID, dated model IDs | `harness/config.py` | Done |
| 5 | Typed trajectory JSONL, resumable writer | `harness/trace.py` | Done |
| 5 | Pure scorer, 9 metrics incl. over-abstention pairing | `harness/score.py` | Done (`SCORER_VERSION = 1.0.0`) |
| 5 | Runner, cache | `harness/runner.py`, `harness/cache.py` | **Missing** |

Phases 3 and 6–12 have not started.

---

## 3. Bugs and gaps found in this review

B1–B4 fixed 2026-09-22, B5 fixed 2026-09-23. B6 still open. B7 is new.

- ~~**B1. Sandbox fails on macOS.**~~ **Fixed.** `env/_worker.py::_apply_memory_cap` calls
  `setrlimit(RLIMIT_AS, …)`. Darwin rejects that with `ValueError: current limit exceeds maximum
  limit`, and it rejects `RLIMIT_DATA` too. So every sandboxed query fails with `sql_error`, which
  breaks `test_valid_query_returns_rows` and `test_timeout_kills_runaway_query`. Fix: wrap the
  rlimit in `try/except (ValueError, OSError)` and fall back to DuckDB's own
  `config={"memory_limit": ...}`, which is portable and arguably the better primary cap. Keep
  `RLIMIT_AS` as defense-in-depth on Linux. Record which cap was applied in `ExecutionResult`.
  *Done:* the worker always sets DuckDB `memory_limit` to 75% of the cap and tries `RLIMIT_AS`
  (it's skipped on macOS). `ExecutionResult.rlimit_applied` reports which happened, and
  `OutOfMemoryException` now maps to `memory_exceeded`. There are 2 new tests. **Not yet re-run
  on Linux:** Docker is installed but the daemon wasn't running. Run
  `docker run --rm -v $PWD:/w -w /w python:3.12 sh -c "pip install -q -e '.[dev]' && pytest"`
  once to confirm.
- ~~**B2. `pyproject.toml` only packages `env` and `harness`.** `specs/` and `ingest/` are left out
  of the wheel. Tests pass only because they run from the repo root. Add both to
  `[tool.hatch.build.targets.wheel] packages`.~~ **Fixed.** The wheel now builds with all 4 packages plus `catalog.txt`.
- ~~**B3. Two catalog slugs are wrong** (`ingest/catalog.txt`).~~ **Fixed.** See §4.
- ~~**B4. README is stale.**~~ **Fixed.** It says "169 tests" and doesn't list ingest as done. It should say 249
  tests and add the ingest line.
- ~~**B5. No `build_db` or `verify_snapshot` CLI.**~~ **Fixed.** Both exist (see §4b).
- **B6. Toolchain.** `uv` isn't installed on this Mac, and the system `python3` is 3.9. The
  project needs `>=3.12` (`/opt/homebrew/bin/python3.12` is present). Tests were run from a
  throwaway venv: `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`.
- **B7. DuckDB spills to disk under `memory_limit`.** A group-by past the limit completed by
  spilling instead of raising. Spilling is fine, but on a file-backed snapshot the spill goes to
  `<db>.tmp` next to it, with no size bound. Before running agents at scale, set
  `max_temp_directory_size`, and possibly `temp_directory` to a per-run scratch dir, in
  `env/_worker.py`.

---

## 4. Catalog verification (live, 2026-09-22)

Checked each slug in `ingest/catalog.txt` against `https://data.boston.gov/api/3/action/package_show`:

| Slug | Result |
|---|---|
| `crime-incident-reports-august-2015-to-date-source-new-system` | OK. 11 resources, CSV, datastore-active |
| `crime-incident-reports-july-2012-august-2015-source-legacy-system` | OK. CSV + XLSX |
| `shootings` | OK. 2 CSV |
| `firearm-recovery-counts` | **Not found.** Replaced with `boston-police-department-firearm-recovery-counts` |
| `boston-police-department-fio` | OK. 28 resources |
| `fire-incident-reporting` | OK. 5 CSV |
| `boston-neighborhoods` | **Not found.** Replaced with `bpda-neighborhood-boundaries` |
| `police-districts` | OK. GeoJSON (not in datastore) + CSV |

Also on CKAN and relevant to the spec curve:
- `2025-boston-population-estimates-neighborhood-level`,
  `historical-boston-population-estimates-1950-2020-{neighborhood,tract}-level`. A possible
  second denominator source that avoids some of the ACS area-weighting work.
- `boston-neighborhood-boundaries-approximated-by-2020-census-{tracts,block-groups1}`. These are
  tract-aligned neighborhoods and would make the census-tract-rollup geography option cheap.

---

## 4a. First live pull (2026-09-23)

`python -m ingest.pull --datasets ingest/catalog.txt --out data/` took 172s, exit 0, peak memory 2.8 GB.

The first attempt with the original code got 50 resources and 12 failures:
- 3 XLSX data dictionaries are marked datastore-active but serve 0 rows there. Fix: fall back to the file.
- XLSX wasn't parsed at all. Fix: `fastexcel`, reading every sheet.
- 8 boundary resources (KML, SHP, ArcGIS, HTML) plus 1 PDF are alternate renderings of data
  that's already fetched. Fix: list them as skipped. A dataset that yields nothing still fails.
- **Correction:** I first wrote that the boundary CSVs carry geometry as `shape_wkt`. That was
  wrong, inferred from column names without checking values: `shape_wkt` is **empty at source**.
  The geometry exists only in the GeoJSON, and the parser was dropping it. Fixed: GeoJSON
  geometry is now kept as a `_geometry` column, and the snapshot uses the GeoJSON resources for
  `districts` and `neighborhoods`.

Other checks:
- **Both fetch paths agree:** for `shootings`, the datastore and direct download produce identical
  content hashes.
- **Paging is fast:** about 0.7s per 10k rows.

### Data findings (seed for `notes/surprises.md`)

1. **`UCR_PART` and `OFFENSE_CODE_GROUP` are 100% blank from 2019 on** (BPD's move to a new
   records system). The `offense_set=part_one` spec option can't be read off the row after 2018.
   **Correction:** I first wrote that mapping by code recovers 97.6% of 2019+ rows. That
   overstated it, because **codes were reused with new meanings** (see §4c). Keyed on the
   (code, description) pair: 78.1% of 2019+ rows match a pre-2019 pair exactly, 19.5% reuse a
   code under a different description, and 2.4% use new codes.
2. **Row grain changes in 2019.** In 2015–2018, rows exceed distinct `INCIDENT_NUMBER`s by about
   12% (one row per offense). From 2019 on, rows equal incidents. Counting rows versus incidents
   manufactures a trend break at 2019. That's a candidate for a new spec dimension (count unit)
   and a limitation doc (`LIM-SCHEMA-BREAK-2019`).
3. **Timestamp format changes:** `2016-01-01 00:30:00` in the yearly files versus
   `2026-09-20 02:41:00+00` in the "2023 to Present" file (and in `shootings`). **The `+00` is a
   mislabel. These are local times.** The hour-of-day distribution of `+00` rows matches the
   unstamped rows hour for hour, where UTC would shift it 4–5h. Of 1,946 shootings that match a
   crime incident, 997 carry the identical timestamp and 5 differ by 4–5h. The snapshot loads
   all of them as `TIMESTAMP` (no zone) in local time.
4. **`SHOOTING` has three encodings:** `0`/`1`, `Y`, and NULL (NULL on 351k rows).
5. **Bad or missing geo** (null, 0, -1, or out of range) on 2.5–7% of rows per year. District is
   blank or `External` on under 2%.
6. **All 9 yearly crime files share one 17-column schema.** Coverage runs from 2015-06-15 to
   2026-09-20.
7. **FIO ships as paired contact and person files per year** (28 resources, with names changing
   across systems: RMS vs Mark43). One XLSX key has no header row, so its first data row became
   the header.
8. **The 2019 grain break in one query:** from 2018 to 2019, rows fall 12% (98,888 → 87,184)
   while distinct incidents *rise* 0.5% (86,734 → 87,184). "Crime fell 12% in 2019" and "crime
   was flat in 2019" are both computable from the same table. That's a ready-made
   spec-sensitive claim.
9. **312 of 2,258 shootings (14%) have no matching `INCIDENT_NUMBER` in `crime_incidents`.**
10. **Offense codes are zero-padded in some years' files and not others** (353k rows). All of
    them cast cleanly to `INTEGER`, which is the join key to `offense_codes_source`.
11. **Boundary geometry is only in the GeoJSON.** See the correction above.

---

## 4d. Decision: counting grain across 2019 (2026-09-23)

The facts:
- **Before 2019, `crime_incidents` has one row per offense.** About 20% of rows sit in
  multi-row incidents. 31,132 incidents list several distinct offense codes, and only 354 have
  exact duplicate rows.
- **From 2019, exactly one row per incident.**
- **Rows overstate incidents** by about 13% overall and 3.7–4.6% for Part One before 2019.

Decided:
1. **The spec engine always counts distinct `INCIDENT_NUMBER`s, never rows.** Rows change
   meaning at the break (2018→2019: rows −12%, incidents +0.5%), so counting rows is an error,
   not a defensible choice. It's deliberately *not* a spec option: that would flip every
   multi-year claim on an artifact, the straw-man failure `dimensions.py` warns about. Instead
   it goes into `LIM-SCHEMA-BREAK-2019` and is a source of `misleading` claims.
2. **New dimension `multi_offense`**: `any_offense` (NIBRS style) vs `most_serious_offense`
   (UCR hierarchy rule). For Part One totals the two give identical counts (verified
   2016–2018). They differ only for single-category claims spanning 2019. Which offense BPD
   keeps after 2019 is undocumented.

---

## 4c. Offense-code lookup (2026-09-23)

`offense_codes` is a derived table built after the loads. It's keyed on the exact
`(OFFENSE_CODE, OFFENSE_DESCRIPTION)` pair from `crime_incidents`, so agents join with
`USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)`. It covers all 956,125 crime rows. Every label
records where it came from (`ucr_part_source`, `is_crime_source` = observed/hand/rule), plus a
`rationale` and a `reviewed` flag for hand labels.

- **UCR part:** BPD's own pre-2019 label where the pair was observed (223 + 21 pairs), otherwise
  a hand label (64 pairs).
- **`is_crime`:** a rule (Part One and Two are crimes, Part Three isn't) unless a hand label
  overrides it. "Other" has no default and must be hand-labeled.
- **The build refuses:** unlabeled pairs, hand labels contradicting BPD, stale labels, and pairs
  with two observed parts. A new code in a refresh stops the build.

**Hand labels: 85 rows in `ingest/offense_code_labels.csv`, drafted by Claude, all
`reviewed=no`.** They cover 199,208 crime rows (21%). The ones that matter most:
- **1831 "SICK ASSIST" (33,545 rows) and 1832 (5,446).** Before 2019 these codes were "DRUGS -
  SICK ASSIST" (Part Two). The draft keeps Part Two for continuity but sets `is_crime=false`.
  This one decision moves the drug trend: counting Part Two drug codes, drug incidents
  *triple* from 3,438 (2019) to 11,196 (2025); counting only crimes, they go 3,020 → 2,688.
- **Code reuses:** 530 (commercial burglary became B&E of a motor vehicle) and 3305
  (demonstrations/riot became drunkenness).
- **`is_crime` overrides against the Part rule:** hit-and-runs 3830/3831 (53k rows) and witness
  intimidation 3170 count as crimes. Pre-2019 drug sick assists, CHINS, truancy/runaway,
  ballistics found, and stolen-then-recovered don't.
- **New codes labeled by analogy:** human trafficking (1610/1620) is Part One per the FBI (no
  BPD analog). Justifiable homicide (990) isn't a crime.

**Series check.** Through the lookup, Part One incidents run 17,214 (2018) → 16,646 (2019), and
crimes run 45,116 → 45,321. Neither series shows a cliff at the system change.

Findings from building it:
12. **Codes were reused with new meanings in 2019:** 1831 (drug sick assist became plain sick
    assist), 530 (burglary became B&E of a motor vehicle), 3305 (riot became drunkenness), 1848
    (Class D became Class B). Mapping by code alone would silently mislabel 19.5% of 2019+ rows.
13. **BPD's `UCR_PART` isn't the FBI's.** ARSON is "Other", though it's FBI Part I. Negligent
    manslaughter and B&E-no-property-taken are also "Other". The lookup follows BPD's labels,
    because `part_one` is defined as "what BPD reports".
14. **Part Three isn't non-crime.** It holds hit-and-runs (57k rows) and witness intimidation.
15. **The published code list is ambiguous:** 576 rows but only 425 distinct codes, some with
    conflicting names (301 is both "ROBBERY - STREET" and "ROBBERY - FIREARM - BANK"). Also, 30
    codes seen in the data aren't in the list. `offense_codes.published_names` keeps all names.
16. **The drug trend depends on one label (1831).** See above. That's a ready-made
    spec-sensitive claim.

---

## 4b. Snapshot build (2026-09-23)

`python -m ingest.build_db` then `python -m ingest.verify_snapshot`. The build takes 5s and
produces 11 tables with 1,906,052 rows. Every cast was measured lossless before it went into
`ingest/tables.py`.

| Table | Rows | From |
|---|---|---|
| `crime_incidents` | 956,125 | 9 yearly resources, identical columns |
| `crime_incidents_legacy` | 268,056 | Jul 2012 – Aug 2015, older system, `FROMDATE` via strptime |
| `offense_codes_source` | 576 | `rmsoffensecodes.xlsx` |
| `shootings` | 2,258 | |
| `firearm_recovery` | 3,701 | |
| `fire_incidents` | 596,508 | 2014 onward |
| `fire_incidents_legacy` | 78,448 | 2012 + 2013, older column layout |
| `fire_incident_types`, `fire_property_uses` | 188, 154 | code lists |
| `districts`, `neighborhoods` | 12, 26 | GeoJSON, with `_geometry` |

Left out, each with a reason in `ingest/tables.py`: all 28 FIO resources (deferred, since the
schemas need harmonising), 3 data dictionaries, and the 2 boundary CSVs (no geometry).

- **Coverage is enforced.** The build refuses to run if any pulled resource is neither loaded nor
  listed as unused. A new catalog file can't slip through.
- **Rebuilds move the file hash.** The file hash changed between two consecutive builds of the
  same data at the same path, while the content hash held. The old docstring claim ("same path
  is byte-identical") holds only for small tables. Any rebuild re-seals, which the CLI does.
- **End to end works:** a real query goes through `env.guard` and `env.sandbox` against the
  snapshot in 134 ms, and the guard still blocks `read_csv(...)`.
- **Spatial joins will need precomputing.** The sandbox runs with `enable_external_access=false`,
  which likely blocks loading DuckDB's spatial extension. Neighborhood assignment would then
  need to happen at build time rather than in agent SQL (see §5).

---

## 5. Next tasks, in order

Work top-down. Tick boxes and move items to §6 as they land.

### Now: unblock and clean up (about half a day)
- [x] **B1**: make the sandbox portable (DuckDB `memory_limit` plus a guarded rlimit). 251/251 on macOS.
- [ ] Re-run the suite on Linux (Docker one-liner in B1)
- [x] **B2**: add `specs` and `ingest` to wheel packages
- [x] **B3**: fix the two catalog slugs. Remove the UNVERIFIED banner from `catalog.txt`.
- [x] **B4**: update the README test count and status list
- [ ] **B6**: install `uv`, then `uv sync --extra dev`. Commit to `uv run pytest` as the single entry point.

### Phase 1: data and claims (the risky track, so start it first)
- [x] First live pull: `python -m ingest.pull` into `data/raw/` (§4a)
- [x] `ingest/build_db.py` CLI plus `TableSpec`s for the core tables (§4b)
- [x] `ingest/verify_snapshot.py` CLI
- [ ] Assign each crime incident to a neighborhood at build time (point-in-polygon against `neighborhoods._geometry`), since agent SQL likely can't load the spatial extension. Needed for the `geography=neighborhood` spec option.
- [ ] FIO harmonisation (RMS vs Mark43, contact vs person files), then load as tables
- [x] `ingest/offense_codes.py`: UCR part and crime flag per (code, description), description variants normalized, originals kept (§4c)
- [ ] **You: review the 85 draft labels** in `ingest/offense_code_labels.csv` and flip `reviewed` to `yes` as you go. Start with 1831/1832 (39k rows, moves the drug trend), then 530, 3305, and the `is_crime` overrides. Rebuild afterwards (`python -m ingest.build_db`)
- [x] Decide incident vs offense counting grain across the 2019 break (§4d): distinct incidents always; new `multi_offense` dimension
- [ ] `ingest/acs_population.py` (Census API, tract level, area-weighted to neighborhood and district). Decide first whether the CKAN population estimates (§4) are enough for denominator #2.
- [ ] `corpus/limitations/*.md`: 10–14 docs with stable `LIM-*` IDs
- [ ] Manual exploration, 3+ hours. Write `notes/surprises.md` with 20 entries (7 seeded in §4a)
- [ ] `claims/sources/`: scrape the BPD weekly crime-stats archive with dates preserved
- [ ] `claims/extract.py` and the `claims.jsonl` schema (measure, window, geography, direction, magnitude, source URL, pub date, retrieval date, paraphrase)
- [ ] Hand-source about 40 claims from GBH, Globe, WBUR, Universal Hub, and council statements
- [ ] **DoD:** 5 BPD claims hand-verified against the snapshot; at least 150 candidates

### Phase 2 finish: the real evaluator
- [ ] `specs/compute.py`: `(claim, spec) -> float | None` as parameterized SQL over DuckDB, going through `env/sandbox`. Cached. Count `DISTINCT INCIDENT_NUMBER` (§4d), join `offense_codes` for `offense_set`, honor `multi_offense`
- [ ] CLI: `python -m specs.curve --corpus … --out specs/curves.jsonl` and `python -m specs.labels`
- [ ] **DoD:** every corpus claim has a curve. Report the headline share of `underdetermined` claims.

### Phase 3: reproducibility audit (first finding with no model)
- [ ] `experiments/reproducibility_audit.py`: BPD figure vs best-matching spec, with deltas by category and year and diagnosed causes

### Phase 4–5: agent and runner
- [ ] Add `litellm` dependency; `.env` via `pydantic-settings`; a hello-world call per provider, plus Ollama
- [ ] `env/tools.py`: list_tables, describe_table, query, compute, search_schema, read_limitation_doc, submit_verdict
- [ ] `env/loop.py`: step budget, forced submit, pluggable scaffold
- [ ] `harness/runner.py`: async, semaphore, backoff, k rollouts, resumable using `trace.completed_claim_ids`
- [ ] `harness/cache.py`: keyed on (model, params, prompt) plus `replicate_index` under bypass
- [ ] **B7**: bound the DuckDB spill directory before running agents at scale
- [ ] Stream datastore pages to Parquet instead of holding all records in memory (pull peaks at 2.8 GB)
- [ ] Record skipped resources in the manifest, not just the pull report (needs `MANIFEST_VERSION` 3)
- [ ] **DoD:** one claim end-to-end; kill and resume with zero duplicate spend

### Later (see roadmap for details)
- [ ] Phase 6: metrics CLI, dev/test split (100/50), first frontier run, numbers in README
- [ ] Phase 7: corpus to 250, hand labels, second labeler on 40, MDE
- [ ] Phase 8: experiment battery (model compare, tool ablation incl. oracle and anti-oracle, scaffolds, step budget, prompt ceiling, memorization)
- [ ] Phase 9: clustered bootstrap, variance, `sql_faithfulness` judge validation (AC1 + κ)
- [ ] Phase 10: GitHub Actions (pr_smoke replay, nightly, data_refresh), `analysis/drift.py`, `diff_runs.py`, suppression-boundary test
- [ ] Phase 11: leaderboard static site, claim explorer, datasheet, one-command reproduction
- [ ] Phase 12: run the test split once, writeup, Inspect AI port

---

## 6. Done log

| Date | Commit | What |
|---|---|---|
| — | `9638362`…`521a1bf` | Roadmaps v2, engineering review, ClaimBench design, and feasibility check (§0) |
| — | `0156aac` | Harness core: SQL guard, sandbox, run identity (78 tests) |
| — | `df365e2` | Spec-curve ground-truth engine (129 tests) |
| — | `b90ae74` | Trace schema and pure scoring split (169 tests) |
| — | `2cf056e` | CKAN ingest with non-datastore fallback (224 tests) |
| — | `255b634` | build_db with measured cast loss and dual checksums (249 tests) |
| 2026-09-22 | — | Status review: found B1–B6 and verified the catalog live |
| 2026-09-22 | `5fb3510` | Cleanup: portable sandbox memory cap (B1), wheel packages (B2), catalog slugs (B3), README (B4). 251 tests |
| 2026-09-23 | `2f14e40` | First live pull: XLSX parsing, empty-datastore fallback, skip list for alternate renderings. Manifest committed, 7 data findings. 260 tests |
| 2026-09-23 | `cde858c` | Snapshot build: `build_db`/`verify_snapshot` CLIs, `ingest/tables.py` (11 tables), coverage check, GeoJSON geometry kept. Sealed manifest. 274 tests |
| 2026-09-23 | `3ec16cb` | Offense-code lookup: `offense_codes` derived table, 85 draft hand labels, derived-table support in `build_db`, failed builds leave no file. 300 tests |
| 2026-09-23 | (this commit) | Counting-grain decision: distinct incidents always, `multi_offense` dimension added (§4d). 300 tests |

---

## 7. Working conventions (from the codebase)

- Everything in `tests/` runs offline: no network, no keys, no snapshot. Ingest code sits behind the
  `Transport` protocol so it can be faked.
- Scoring is pure. Any change to a metric bumps `SCORER_VERSION`.
- `not computable` (`None`) is distinct from `false` throughout the spec engine.
- Commit messages follow `Add <thing> (<N> tests)`.
