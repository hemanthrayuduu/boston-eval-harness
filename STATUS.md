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
| Tests | **413, all passing on macOS** (2026-09-23). All offline. Linux sandbox path not re-run since B1 fix (see B1). |
| Data | **Snapshot built and sealed 2026-09-23.** Pull: 53 resources, 2,262,459 rows, 54 MB Parquet in `data/raw/`. Build: 12 tables (11 loaded + `offense_codes` derived), 1,906,360 rows, `data/boston.duckdb` (83 MB, 5s). Both gitignored. `data/manifest.json` is committed and sealed. See §4a and §4b. |
| Claims | **Corpus v0.2.0: 151 claims.** 108 from BPD's weekly reports (`claims/corpus/bpd.jsonl`, selected from 29,661 candidates) and 43 hand-sourced from 11 news and official pages (`claims/hand_sourced.toml` → `claims/corpus/hand.jsonl`). See §4g–4i and `claims/CHANGELOG.md`. |
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
| 1 | Limitations corpus: 15 docs with stable `LIM-*` IDs, plus a validating loader | `corpus/limitations/`, `env/limitations.py` | Done (§4f) |
| 1 | Offense-code lookup (UCR part + crime flag per (code, description)) | `ingest/offense_codes.py`, `ingest/offense_code_labels.csv` | Done. Derived table `offense_codes`, 308 pairs. **85 hand labels are drafts, not yet reviewed** (§4c) |
| 2 | Dimensions with written justifications | `specs/dimensions.py` | Done (measure, window, geography, denominator, offense_set, multi_offense, missing_geo) |
| 2 | Per-claim spec space, capped at 48 | `specs/space.py` | Done |
| 2 | Claim assertions (change / level / comparison / rank) | `specs/assertions.py` | Done |
| 2 | Curve computation plus driver attribution | `specs/curve.py` | Done. Takes an `Evaluator` protocol |
| 2 | Label derivation (0.95 / 0.05 thresholds) | `specs/labels.py` | Done |
| 2 | The real snapshot evaluator plus per-claim spec spaces | `specs/compute.py` | Done (§4j). Trusted SQL on a verified read-only connection |
| 2 | Curves and labels for the whole corpus | `specs/run.py` (`python -m specs.run`) → `specs/curves.jsonl`, `specs/summary.json` | Done (§4j). **Labels are v0 and provisional** |
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
5. **Missing geo on 2.5–7.2% of rows per year** (5.1% overall). District is blank or `External`
   on under 1%. **Correction:** I first wrote "null, 0, -1, or out of range". There are *no* 0 or
   -1 placeholders (checked); unusable locations are NULL, and only 7 rows fall outside Boston's
   latitude range.
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

## 4j. Spec curves v0 (2026-09-23)

`python -m specs.run` verifies the snapshot, then computes a curve and a derived label for all
151 claims. It takes 6 seconds and writes `specs/curves.jsonl` (every outcome with its value,
its counts, or the reason it isn't computable) and `specs/summary.json`.

**How it evaluates** (`specs/compute.py`):
- **Windows come from the claim's stated dates**, including five-year averages, year-by-year
  ranks, and partial-year-vs-full-year comparisons.
- **The spec varies only what the claim leaves open:**
  - `offense_mapping`: new, by description vs by BPD code range.
  - `missing_geo`: incidents with no district dropped vs allocated.
  - `multi_offense`: varied only if the window reaches before 2019.
  - `measure`: for shootings claims that don't say victims or incidents. `non_fatal_only` is new.
- **Counts are distinct incidents.**
- **"Not computable" carries a reason:** `out_of_scope` (the data can't address it), `coverage`
  (the window falls outside the data), or `engine_gap` (not built yet). The summary keeps
  them apart.

**Results, v0:**

| Label | Claims |
|---|---|
| supported | 67 |
| contradicted | 46 |
| underdetermined | 6 |
| unverifiable | 32 |

The 32 unverifiable claims:
- **20 out of scope:** 5 cross-city, 5 rape, 9 domestic/non-domestic aggravated assault, 1
  arrests.
- **6 coverage:** 4 shootings claims past the data's end (2026-09-05), 2 ranks reaching before
  2015.
- **6 engine gaps:** 4 neighborhood, 1 gun-recovery channels, 1 population rate.

**Underdetermined: 6 of 119 computable claims (5%).** Drivers: `offense_mapping` 3, `measure`
2, `missing_geo` 1. The two `measure`-driven ones are the purest cases:
- The Herald's "116 vs 120 shootings" holds counted as incidents (−3.4%) and fails counted as
  victims (+2.1%).
- The Globe's "64 vs 67" does the reverse.

**What the 46 contradictions are.** They're mostly **gaps between official figures and the
open data**, not spec sensitivity:
- **Homicides:** the open data has 24 "murder" incidents in 2025 against the 31 officials
  report. Every official 2025 homicide-change claim fails.
- **Part One totals:** 9–14% lower in the open data (no rape), and growing faster in some
  years.
- **Small district counts:** percentages on counts like 2 → 8 fail a ±5-point tolerance.
- **Direction flips:** WBUR's "violent crime −2% in 2024" is +3.1% in the open data; the
  Globe's "property crime −3%" is +3.4%.

These are real findings, but they're the Phase 3 reproducibility audit's subject. The audit can
now run directly on the 305 extracted BPD reports.

**Two design issues for you:**
1. **The tolerance is doing a lot of work.** `ChangeAssertion`'s absolute ±5-point tolerance is
   absurdly tight for "+300%" on 2 → 8. It also makes knife-edge labels: 2024 gunfire vs its
   5-year average comes out −42.2% or −41.9% against a stated −37%, so one spec holds and one
   fails. A count-based or relative tolerance would fix both, but it changes the answer key,
   so it's your call.
2. **The v0 space is narrow, which is why only 5% are underdetermined.** With windows pinned to
   stated dates, the implemented choices rarely flip a claim: the two mappings agree except
   around reused codes, and missing districts are about 0.5% of incidents. The choices most
   likely to move labels aren't modeled yet:
   - how to read partial-year-vs-full-year windows (WBUR's "31 so far vs 24" style)
   - homicide counting by ruling date (not in the data)
   - neighborhood boundaries
   - population denominators

   **The 5% is a floor for this engine, not a finding about Boston claims yet.**

Correction: my §4h hand-verification of "shooting victims, 2026 year-to-date: holds" was
invalid, because the shootings data ends 2026-09-06. The engine now refuses such windows
(`coverage:after_data_end`).

---

## 4i. Hand-sourced claims (2026-09-23)

43 claims from 11 pages:

| Source | Date |
|---|---|
| City of Boston year-end release | 2025-12-19 |
| WBUR | 2024-12-27, 2025-12-15 |
| GBH News | 2024-10-07, 2025-12-15 |
| Boston.com | 2024-12-29, 2026-01-12 |
| Boston Globe | 2025-08-15 |
| Boston Herald (via Police1) | 2025-12-06 |
| CBS Boston | 2026-07-06 |
| NBC Boston | 2026-08-15 |

They're authored in `claims/hand_sourced.toml` (readable, commented) and built by
`python -m claims.hand_claims`. A test fails if `hand.jsonl` drifts from the TOML. Unknown
sources, unknown fields, unused sources and duplicate IDs all fail the build.

**How they were sourced.** Search results and a summarizing fetch tool were used only to *find*
claims. Every claim's publication date and key phrases were then confirmed in the page's raw
HTML. That caught three summarizer errors:
- It gave the city release's date as Dec 22; the page says Dec 19.
- It reported a "9th of 50 largest cities" ranking. GBH actually says "eight other cities had
  even lower rates", so the rank is implied and is now marked as such.
- It missed that GBH's "4 homicides" came with "against 18 a year earlier". That claim is now
  a change claim.

Axios (2026-08-14, "homicides down 52%") returns 403 to automated fetches. It's left out even
though search results quoted it, because it couldn't be read.

**What the corpus now covers.**
- **Assertions:** 30 change, 7 rank ("lowest since 1957", "safest major city", FBI and MCCA
  rankings), 4 level, 2 comparison ("fewer than 200 victims three years running", "more than
  100 survived").
- **Windows:** 6 kinds, including partial-year-vs-full-year and vs-five-year-average.
- **Geography:** citywide, two named neighborhoods without official boundaries (Downtown,
  Mass & Cass), a district group (B-2 + B-3), a district, and 5 cross-city claims.
- **Unverifiable by construction:** cross-city rankings, arrests, rape, per-capita rates (no
  population yet), and history before 2012.

Findings:
32. **Published news claims contradict their own counts.** The Globe (2025-08-15) says
    non-fatal shooting victims "declined by 14 percent: 60… compared to 74", which is −18.9%,
    and shootings "declined by 3 percent… 64… compared to 67", which is −4.5%. Confirmed
    verbatim in the raw page.
33. **2025's homicide count depends on the date you ask:**
    - 24 (Globe, through Aug 13)
    - 30 (Herald, through Nov 23, "+36%" against 22)
    - 31 "to date" (WBUR and GBH, Dec 15, against *all* of 2024's 24)
    - 31 for the full year (Boston.com, Jan 12, "+30%")

    The 2024 baseline is 22 year-to-date or 24 for the full year. Window choice is visibly
    doing work in published claims.
34. **The CBS weekend claim reproduces exactly:** 13 people shot, 2 fatally, in B2 and C11 on
    July 4–5, 2026. But the data records 6 incidents where CBS says "five shootings".

---

## 4h. Claim corpus v0.1.0 (2026-09-23)

**Schema** (`claims/schema.py`, Pydantic):
- **Source:** kind, organization, URL, published and retrieved dates, document plus SHA-256,
  and a cell locator.
- **Content:** a paraphrase (not a quote), measure, geography (BPD label plus open-data code,
  e.g. `B02` → `B2`), and window (`ytd_vs_prior_ytd` or `calendar_year`).
- **Assertion:** the spec engine's own `ChangeAssertion`.
- **Audit fields:** the numbers as published, `source_checks_failed` (where BPD's document
  contradicts itself), notes, `cluster_id` (same measure and place across reports, for the
  clustered bootstrap), and `selection_rule`.
- **No labels.** Those come from spec curves later.

**Layout:** `claims/corpus/*.jsonl`, one file per source, loaded and validated together by
`claims.corpus.load_corpus()` (unique IDs across files). This departs from the roadmap's single
`claims.jsonl`, so that the BPD generator can't overwrite hand-sourced claims.

**Generation** (`python -m claims.bpd_claims`):
- Every claimable report row becomes a candidate: 29,661, written to `claims/candidates/`
  (gitignored).
- Weekly year-to-date reports are near-duplicates, so the corpus is *selected* by six
  documented rules defined relative to the data. The table is in `claims/CHANGELOG.md`.
- Where BPD printed a percent, the claim asserts the printed one, and a contradiction with its
  own counts is flagged.

**Bug found and fixed while hand-verifying:** `offense_codes.ucr_part` collided with
`crime_incidents.UCR_PART`, because DuckDB column names are case-insensitive. A natural
`JOIN … USING (…) WHERE ucr_part = 'Part One'` silently read the crime table's column (blank from
2019) and returned 0.
- Renamed to `ucr_category` and `ucr_category_source`.
- The build now **refuses** any lookup column that shadows a `crime_incidents` column.
- My earlier §4c numbers used an explicit `oc.` prefix and were unaffected.

**Five claims hand-verified against the snapshot** (Phase 1 DoD). Each uses a single naive
spec, not a curve:

| Claim | BPD | Open data | Holds? |
|---|---|---|---|
| Shooting incidents, 2023 vs 2022 | 145 → 110 | 143 → 109 | yes |
| Shooting victims, Jan 1–Sep 20, 2026 vs 2025 | 94 → 95 | ~~94 → 94~~ | **invalid check**: the shootings data ends 2026-09-06, so the 2026 side was undercounted. `specs.compute` now marks it not computable (§4j) |
| Part One, district A1, Jan 1–Dec 28, 2025 vs 2024 | 2,258 → 2,174 (−3.7%) | 2,075 → 1,993 (−4.0%) | yes |
| Homicides, 2023 vs 2022 | 40 → 37 (−7.5%) | 40 → 33 (−17.5%) | **no** |
| Robbery, Jan 1–Sep 20, 2026 vs 2025 | 549 → 532 (−3.1%) | 468 → 473 (+1.1%) | **no: direction flips** |

29. **Open-data counts run lower than BPD's** (Part One A1 about 8% lower, robbery about 12%
    lower). Part of that is rape, which BPD includes and the open data lacks. Part is category
    mapping, which is exactly what the spec engine will vary.
30. **The homicide gap fits BPD's ruling-date counting** (finding 24).
31. **The robbery claim changes direction** between BPD's report and a straightforward
    open-data count: the first candidate spec-sensitive claim observed in the wild.

---

## 4g. BPD crime-stats archive (2026-09-23)

`python -m claims.bpd_scrape --out claims/sources/bpd` reads the WordPress REST API
(`/wp-json/wp/v2/posts?categories=21`), one request per second, with an identifying
User-Agent. `robots.txt` allows it. Every post is recorded in `posts.jsonl` (committed): id,
publication and modification dates, link, title, rendered content, and each linked PDF's URL,
file, size and SHA-256. The PDFs are gitignored and can be re-fetched and verified against the
hashes. Re-runs skip PDFs already on disk unless the post's `modified_gmt` changed.

What the archive actually is:
- **639 posts, but not continuous.**
  - **2005–2011:** 480 "UPDATED CRIME DATA" posts. Their content was lost in a site migration
    and links nothing.
  - **2012–2022: nothing.**
  - **2023–2026:** 159 weekly "Crime Statistics: January 1 – {date} vs. {prior year}" posts.
  - The roadmap's feasibility check assumed a continuous multi-year archive. **Only the 159
    posts from 2023 on are usable**, and they all fall inside the snapshot's coverage.
- **The numbers are in linked PDFs, not the posts:** 2–3 per post (Part One crime by offense
  and district; shooting victims; firearm arrests/recoveries "FARR"). They're rendered by SQL
  Server Reporting Services with real text and cell borders, and `pdfplumber.extract_tables()`
  recovers them as clean rows (prototyped). Filenames are inconsistent, so the report type
  must come from the PDF's own title.
- **12 PDFs are lost:** four posts from June–July 2023 linked `bpdnews.squarespace.com`, which
  now returns 404. The Internet Archive has only the redirect, not the file, and the Squarespace
  CDN copy is gone too. Recorded in `posts.jsonl` with the error. Unrecoverable.
- **420 PDFs downloaded** (2023: 72 of 84, 2024: 137, 2025: 139, 2026: 72).

Findings from the prototype parse (2026-09-20 report):
23. **BPD's official Part One includes rape** (128 → 108 year-to-date). The open data has no
    rape rows (finding 18), so open-data Part One can never match BPD's published totals.
24. **BPD counts homicides by the date they were ruled a homicide**, not when they occurred.
    Per the footnote, the 2025 year-to-date total includes 3 incidents from prior years. That's
    another definitional choice the open data doesn't make.
25. **BPD's reports contain arithmetic errors.** In the Part One totals, the N/D row goes
    86 → 59 and is labeled "0%" (should be −31%). In the shootings PDF, total 2026 victims are
    95 in the first table and 94 in the second, in the same document.

### Extraction

`python -m claims.bpd_extract` recognizes tables by header content (a `District` column means
Part One; `Shooting Category` or `Incident Type` means shootings), not by position, title or
filename. It writes:
- `figures.jsonl`: 113,556 figures, 64 MB, gitignored. Each is one cell with post, PDF hash,
  page, table, row, column, raw text, and period (prior / current / five_year_avg / change /
  pct_change / pct_change_vs_avg).
- `extraction.json` (committed): the per-PDF outcome and every failed consistency check.

Every PDF is in exactly one bucket: **304 parsed** (153 Part One, 152 shootings, with one
combined file carrying both), **114 firearm reports** (police activity, not parsed), and
**2 image-only scans** (would need OCR: 2025-06-30 and 2025-11-19). None were unrecognized.
Weekly coverage runs from 2023-06-11 to 2026-09-20.

**Consistency checks recompute BPD's own arithmetic.** They found 312 failures in 156 reports,
all verified as BPD's rather than the parser's:
- Zero subtotal or grand-total failures across all reports, so the parse aligns.
- The failing A15 row was checked against the raw page text.

26. **BPD's template always prints 0% change for the N/D (no district) row**, in 151 of 153
    Part One reports.
27. **Five reports' two shootings tables disagree on current-year victims:** 2023-08-28 (109
    vs 101), 2024-05-29, 2024-06-10, 2025-05-19, 2026-09-21. The published total depends on
    which table you read.
28. **The full-year 2023 report (posted 2024-01-02) prints wrong percent changes for five
    districts.** For example, A15 goes 298 → 209 but is printed as −43% instead of −29.9%.

---

## 4f. Limitations corpus (2026-09-23)

`corpus/limitations/` has 15 docs, indexed in its `README.md`. `env/limitations.py` loads them
and enforces the rules: the ID format and filename match, the front-matter keys, four sections in
order, `applies_to` naming real snapshot tables, `dimensions` naming real spec dimensions, and no
dangling `LIM-…` cross-references. A test checks that **every spec dimension is covered by at
least one doc**, which `limitation_citation_f1` needs in order to derive required citations from
a curve's driver.

Every Evidence number was measured on the 2026-09-23 snapshot or comes from the city's own
dataset descriptions (sources listed per doc). The docs describe mechanisms, not claim answers,
so the oracle/anti-oracle ablation measures whether an agent can *use* a limitation.

IDs: `LIM-REPORTS-VS-INCIDENCE`, `LIM-SCHEMA-BREAK-2019`, `LIM-OFFENSE-CODE-REUSE`,
`LIM-UCR-PART-DEFINITION`, `LIM-EXCLUDED-OFFENSES`, `LIM-LEGACY-SCHEMA-BREAK`,
`LIM-STATION-GEOCODE`, `LIM-MISSING-GEO`, `LIM-SHOOTINGS-VS-VICTIMS`, `LIM-PRELIMINARY-DATA`,
`LIM-SMALL-N`, `LIM-FIO-POLICE-ACTIVITY`, `LIM-NO-CROSS-CITY`, `LIM-CENSUS-UNDERCOUNT`,
`LIM-OFFENSE-DUPES`. The roadmap's `LIM-BLOCK-GEOCODE` is **not** written: I found no source
saying how BPD geocodes, and the station finding below is what the data actually shows.

New findings while writing them:
17. **Reports taken at police stations are geocoded to the station.** In all 12 districts, the
    most common coordinate is 19–71 m from that district's station (checked against the city's
    `boston-police-stations-bpd-only` dataset). Those 12 points hold 111,203 rows, 12.3% of
    geocoded rows, from 5.8% of A1's to 18.4% of E5's. This inflates any neighborhood or tract
    containing a station.
18. **Sexual offenses are absent from the crime data.** Zero rape, sexual-assault or
    indecent-assault rows in any year, though the published code list has 26 rape codes. The
    city says records under MGL ch.41 §98F are excluded. **Correction:** I first said the
    legacy system had 816 sexual-offense rows, so they "vanish at the 2015 system change". That
    was wrong: 814 of those are sex-offender *registrations*, and only 2 are "Rape and
    Attempted". Both systems effectively exclude them.
19. **Domestic-violence and restraining-order descriptions:** 477–533 a year in 2016–2018, none
    in 2019, 230 in 2020, none in 2021–2025.
20. **The crime table's `SHOOTING` flag changed meaning in 2019:** 170 flagged incidents (2018),
    then 810 (2019). From 2019 it's carried by INVESTIGATE PROPERTY, BALLISTICS EVIDENCE/FOUND
    and VANDALISM rows, so it tracks gunfire generally, not people struck. The `shootings`
    table counts victims struck (e.g. 2024: 127 victims in 102 incidents).
21. **The legacy and new systems overlap:** 182 of the 200 legacy incidents dated on or after
    2015-06-15 are also in `crime_incidents`. Combining the tables double-counts them.
22. **Homicide counts are tiny by district:** 57 of 61 district-years (2019–2025) are under 10,
    with a median of 2.

---

## 4e. Population denominators: CKAN estimates vs Census (2026-09-23), BLOCKED

**The Census API now requires a key.** Keyless requests to `api.census.gov` redirect to
`missing_key.html`. Keys are free and instant at https://api.census.gov/data/key_signup.html.
That's a signup with your email, so it's yours to do.

Is the city's own data on CKAN enough instead? **No, not for `acs_5yr` or `decennial`:**
- `2025-boston-population-estimates-neighborhood-level`: the Planning Department's estimate
  for Jan 1, 2025. One vintage, **24** tract-approximated neighborhoods (our `neighborhoods`
  table from the city boundary file has **26**), and no district level.
- `historical-boston-population-estimates-1950-2020-{tract,neighborhood}-level` (ZIP of
  per-decade JSON/XLSX): decennial counts 1950–2010 adjusted to 2020 tracts. **But its 2020 is
  the city's estimate, not the census count.** The city states that **the 2020 Decennial Census
  and later ACS releases undercounted Boston**, and its estimates correct for that.

So:
- **ACS 5-year by tract per year** (for year-matched rates) and **2020 decennial by tract** both
  need the API key.
- **Recommendation:** add the city estimate as a *third* denominator option (`city_estimate`),
  not a substitute. The census-vs-city gap is exactly the kind of defensible choice that flips
  per-capita claims. Also add a `LIM-CENSUS-UNDERCOUNT` doc. Not done yet: it changes
  `specs/dimensions.py`, and the data isn't loaded.
- **Geography mismatch to resolve:** 26 city-boundary neighborhoods vs 24 tract-approximated
  ones. The `tract_rollup` geography option should use the 24 (the dataset
  `boston-neighborhood-boundaries-approximated-by-2020-census-tracts`).
- **Rollup to police districts still needs tract geometry.** TIGERweb serves it as GeoJSON
  without a key (not yet tested). Area weighting needs DuckDB's spatial extension at build time.

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
records where it came from (`ucr_category_source`, `is_crime_source` = observed/hand/rule), plus a
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
- [ ] **BLOCKED on you: get a Census API key** (https://api.census.gov/data/key_signup.html) and put it in `.env` as `CENSUS_API_KEY=...` (`.env` is gitignored)
- [ ] `ingest/acs_population.py` (Census API, tract level, area-weighted to neighborhood and district). Decided (§4e): the CKAN estimates are *not* enough for `acs_5yr`/`decennial`; they're a candidate third option `city_estimate`
- [ ] Add the city population datasets and the tract-approximated neighborhood boundaries to `ingest/catalog.txt`. The historical data is a ZIP (unsupported format), so it needs a parser or a manual extract
- [x] `corpus/limitations/*.md`: 15 docs with stable `LIM-*` IDs, plus a validating loader (§4f)
- [ ] Manual exploration, 3+ hours. Write `notes/surprises.md` with 20 entries. 22 are already recorded in §4a, §4c and §4f from my queries; this item is your own hands-on pass
- [x] `claims/sources/`: scrape the BPD weekly crime-stats archive with dates preserved (§4g). 159 usable posts (2023–2026), 420 PDFs
- [x] `claims/bpd_extract.py`: parse the PDFs into figures with provenance, classify report type from content, recompute BPD's arithmetic and flag mismatches (§4g)
- [ ] Optional: OCR the 2 image-only PDFs, and parse the firearm-arrest reports if firearm claims are wanted
- [x] Claim schema and BPD claim generation: `claims/schema.py`, `claims/bpd_claims.py`, `claims/corpus/bpd.jsonl` (108 claims, v0.1.0, §4h)
- [x] Hand-verify 5 BPD claims against the snapshot (§4h): 3 hold, 2 don't on a naive spec
- [x] Hand-source about 40 claims: 43 from 11 pages (§4i). Not yet covered: Universal Hub, council statements, and forums (r/boston). Forums were skipped for now because they attribute to individuals
- [x] **DoD:** 5 BPD claims hand-verified (§4h); **151 claims** in the corpus (§4i). Phase 1's other items (population denominators, your label review and exploration pass) remain open

### Phase 2 finish: the real evaluator
- [x] `specs/compute.py`: `(claim, spec) -> float | None`, counting distinct incidents and joining `offense_codes`, plus `space_for(claim)` (§4j). It uses a verified read-only connection rather than `env/sandbox`, which exists to contain *model* SQL
- [x] CLI: `python -m specs.run` writes `specs/curves.jsonl` and `specs/summary.json` in one step (instead of the roadmap's two)
- [x] **DoD:** every corpus claim has a curve. Headline, v0: **6 of 119 computable claims (5%) are underdetermined** (§4j). That's provisional: the space is still narrow
- [ ] **You: decide the change-claim tolerance** (§4j, issue 1). An absolute ±5 points is too tight for small counts and creates knife-edge labels. Options: count-based tolerance, or a tolerance relative to the stated magnitude
- [ ] Widen the spec space (§4j, issue 2): window reading for partial-year vs full-year claims; homicide counting (the open data has no ruling-date field); neighborhood geography (spatial join at build time); population denominators (needs the Census key)
- [ ] Engine gaps: gun-recovery channels (`firearm_recovery`), neighborhood geography (4 claims)

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
| 2026-09-23 | `ae8d93d` | Counting-grain decision: distinct incidents always, `multi_offense` dimension added (§4d). 300 tests |
| 2026-09-23 | `38f56de` | Population denominators investigated (§4e): Census API needs a key (blocked); city estimates correct a census undercount, so they're a third option, not a substitute |
| 2026-09-23 | `9514fd1` | Limitations corpus: 15 docs, validating loader, 6 new findings, 2 of my earlier claims corrected. 319 tests |
| 2026-09-23 | `552a1ad` | BPD archive scraper: 639 posts indexed, 420 PDFs, 12 lost to link rot (§4g). 329 tests |
| 2026-09-23 | `2c0467d` | BPD figure extraction: 113,556 figures from 305 reports, 312 failed consistency checks in BPD's own reports. 351 tests |
| 2026-09-23 | `9b8b7df` | Claim corpus v0.1.0: schema, 108 BPD claims from 29,661 candidates, 5 hand-verified; `ucr_part` shadowing bug fixed; extractor Grand Total fix. 365 tests |
| 2026-09-23 | `562ba63` | Corpus v0.2.0: 43 hand-sourced claims from 11 verified pages (151 total), schema v2, `ChangeAssertion.bound`. 384 tests |
| 2026-09-23 | (this commit) | `specs/compute.py` plus `specs/run.py`: curves and v0 labels for all 151 claims; `offense_mapping` dimension; 5% underdetermined (provisional). 413 tests |

---

## 7. Working conventions (from the codebase)

- Everything in `tests/` runs offline: no network, no keys, no snapshot. Ingest code sits behind the
  `Transport` protocol so it can be faked.
- Scoring is pure. Any change to a metric bumps `SCORER_VERSION`.
- `not computable` (`None`) is distinct from `false` throughout the spec engine.
- Commit messages follow `Add <thing> (<N> tests)`.
