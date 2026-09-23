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
| Tests | **260, all passing on macOS** (2026-09-23). All offline. Linux sandbox path not re-run since B1 fix (see B1). |
| Data | **First live pull done 2026-09-23:** 53 resources, 2,262,297 rows, 54 MB Parquet in `data/raw/` (gitignored). `data/manifest.json` committed but unsealed. No DuckDB file yet (needs B5). See §4a. |
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
| 1 | Parquet → DuckDB with measured cast loss and dual checksums | `ingest/build_db.py` | Done as a library. **No CLI entry point** (`build_database()` / `seal_manifest()` only) |
| 1 | Snapshot verification | `ingest/manifest.py::verify_snapshot` | Done as a function. No `verify_snapshot.py` CLI. |
| 2 | Dimensions with written justifications | `specs/dimensions.py` | Done (measure, window, geography, denominator, offense_set, missing_geo) |
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

B1–B4 fixed 2026-09-22. B5 and B6 still open. B7 is new.

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
- **B5. No `build_db` or `verify_snapshot` CLI.** The roadmap's quick-reference commands
  (`python -m ingest.build_db`, `python -m ingest.verify_snapshot`) don't exist yet.
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
- 8 boundary resources (KML, SHP, ArcGIS, HTML) plus 1 PDF are alternate renderings of CSVs
  that are already fetched. The boundary CSVs carry geometry as `shape_wkt`. Fix: list them as
  skipped. A dataset that yields nothing still fails.

Other checks:
- **Both fetch paths agree:** for `shootings`, the datastore and direct download produce identical
  content hashes.
- **Paging is fast:** about 0.7s per 10k rows.

### Data findings (seed for `notes/surprises.md`)

1. **`UCR_PART` and `OFFENSE_CODE_GROUP` are 100% blank from 2019 on** (BPD's move to a new
   records system). The `offense_set=part_one` spec option can't be read off the row after 2018.
   It *can* be recovered by code: 219 offense codes seen in 2015–2018 each map to exactly one UCR
   part, and they cover **97.6% of 2019+ rows**. The other 34 codes (2.4% of rows) need hand
   mapping. This is the spec for `offense_codes.py`.
2. **Row grain changes in 2019.** In 2015–2018, rows exceed distinct `INCIDENT_NUMBER`s by about
   12% (one row per offense). From 2019 on, rows equal incidents. Counting rows versus incidents
   manufactures a trend break at 2019. That's a candidate for a new spec dimension (count unit)
   and a limitation doc (`LIM-SCHEMA-BREAK-2019`).
3. **Timestamp format changes:** `2016-01-01 00:30:00` in older rows versus
   `2026-09-20 02:41:00+00` in newer ones. `build_db` casts must accept both.
4. **`SHOOTING` has three encodings:** `0`/`1`, `Y`, and NULL (NULL on 351k rows).
5. **Bad or missing geo** (null, 0, -1, or out of range) on 2.5–7% of rows per year. District is
   blank or `External` on under 2%.
6. **All 9 yearly crime files share one 17-column schema.** Coverage runs from 2015-06-15 to
   2026-09-20.
7. **FIO ships as paired contact and person files per year** (28 resources, with names changing
   across systems: RMS vs Mark43). One XLSX key has no header row, so its first data row became
   the header.

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
- [ ] `ingest/build_db.py` CLI (`__main__`), plus `TableSpec`s for the core tables. Union the 9 yearly crime files; casts must handle both timestamp formats (§4a #3)
- [ ] `ingest/verify_snapshot.py` CLI wrapping `manifest.verify_snapshot`
- [ ] `ingest/offense_codes.py`: crime vs non-crime lookup, dupes normalized in the lookup, originals kept. **Must include code → UCR part for 2019+** (§4a #1: derive from 2015–18, hand-map the other 34 codes). Source table is `rmsoffensecodes.xlsx` (576 codes, now pulled)
- [ ] Decide incident vs offense counting grain across the 2019 break (§4a #2). Possibly a new `count_unit` dimension in `specs/dimensions.py`
- [ ] `ingest/acs_population.py` (Census API, tract level, area-weighted to neighborhood and district). Decide first whether the CKAN population estimates (§4) are enough for denominator #2.
- [ ] `corpus/limitations/*.md`: 10–14 docs with stable `LIM-*` IDs
- [ ] Manual exploration, 3+ hours. Write `notes/surprises.md` with 20 entries (7 seeded in §4a)
- [ ] `claims/sources/`: scrape the BPD weekly crime-stats archive with dates preserved
- [ ] `claims/extract.py` and the `claims.jsonl` schema (measure, window, geography, direction, magnitude, source URL, pub date, retrieval date, paraphrase)
- [ ] Hand-source about 40 claims from GBH, Globe, WBUR, Universal Hub, and council statements
- [ ] **DoD:** 5 BPD claims hand-verified against the snapshot; at least 150 candidates

### Phase 2 finish: the real evaluator
- [ ] `specs/compute.py`: `(claim, spec) -> float | None` as parameterized SQL over DuckDB, going through `env/sandbox`. Cached.
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
| 2026-09-23 | (this commit) | First live pull: XLSX parsing, empty-datastore fallback, skip list for alternate renderings. Manifest committed, 7 data findings. 260 tests |

---

## 7. Working conventions (from the codebase)

- Everything in `tests/` runs offline: no network, no keys, no snapshot. Ingest code sits behind the
  `Transport` protocol so it can be faked.
- Scoring is pure. Any change to a metric bumps `SCORER_VERSION`.
- `not computable` (`None`) is distinct from `false` throughout the spec engine.
- Commit messages follow `Add <thing> (<N> tests)`.
