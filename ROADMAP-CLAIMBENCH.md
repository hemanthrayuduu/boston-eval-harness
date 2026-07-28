# BostonClaimBench — Roadmap

**An agentic benchmark for statistical claim verification over Boston public-safety data.**

Alternative to `roadmap.md` (the text-to-SQL harness). Same data, different task, stronger
eval story. See `ROADMAP-REVIEW.md` for the critique of v2 that motivated this.

The deliverable is a **benchmark plus harness**, not an evaluation of an app. Other people can
run it against their own models. That is the difference between an artifact that travels and a
project that has to be explained.

---

## 0. Feasibility check (done 2026-07-28, before committing to the plan)

I verified the load-bearing assumption — that enough real, checkable claims exist — before
writing anything else. Findings, with the caveat that this environment's network policy blocked
direct fetches of `data.boston.gov` and `police.boston.gov` (search only), so **every number
below must be re-verified against primary sources in Phase 1**:

**1. There is a large, structured, official claim corpus.** BPD publishes **weekly** crime
statistics posts — "Crime Statistics: January 1, {year} – {date} vs. {prior year}" — at
`police.boston.gov/category/crime-stats/`, apparently continuously for years, with the current
one covering Jan 1 – Jul 19 2026 vs 2025. Each post is a set of dated, numeric, year-over-year
claims with official provenance. That is potentially **hundreds of claims you did not author**,
which answers the "you wrote your own benchmark" critique structurally rather than rhetorically.

**2. The flagship claim is a live, multi-year, documented dispute.** Mayor Wu's "safest major
city in the country" claim has been fact-checked by GBH in both 2024 and 2025, disputed by the
police union, criticized in a Globe opinion piece, and called "inherently risky" framing by a
Northeastern criminologist. Crucially, it is **unverifiable from Boston open data alone** — it
requires cross-city MCCA comparisons — which makes it a perfect anchor for the `unverifiable`
label rather than a problem.

**3. Real claims conflict with each other in exactly the way the benchmark needs.** Search
snippets alone surfaced: "116 shootings, down from a 5-year average of 173"; "30 homicides in
2025, up from 22 in 2024 — a 36% increase"; "31 homicides, up from the 67-year low of 26";
"shooting victims fell from 58 to 50 (14%)"; "homicides declined from 21 to 10." Those are
different windows, different base years, and — critically — *different measures* conflated as
one: **shootings ≠ shooting victims ≠ gunfire incidents ≠ homicides**. Published discourse
already conflates them. That conflation is the benchmark material.

**4. Documented data-quality problems to build tasks on.** Duplicate offense descriptions
arising from misspellings, extra spaces, and inconsistent hyphenation; ~711 duplicate rows and
~78k missing values in a ~404k-row snapshot; and the portal's own warning that data "may be
preliminary and subject to change."

**5. A finding fell out of the feasibility check itself.** BPD's weekly published figures are
computed from internal systems, **not** from the Analyze Boston open dataset. So official
published numbers may not reproduce from the open data at all. That is not an obstacle — it is
Phase 3, and it is a publishable, journalism-relevant result that involves no LLM.

**Conclusion: the corpus exists. Proceed.** The main sourcing risk is not volume, it is
*checkability* — see Risks.

Sources:
[BPD crime stats index](https://police.boston.gov/category/crime-stats/) ·
[BPD Nov 2025 report](https://police.boston.gov/2025/11/24/crime-statistics-january-1-2025-november-23-2025-vs-2024/) ·
[GBH, Dec 2025](https://www.wgbh.org/news/politics/2025-12-15/wu-again-claims-boston-is-safest-city-in-us-but-recent-data-suggests-otherwise) ·
[GBH, Oct 2024](https://www.wgbh.org/news/politics/2024-10-07/is-boston-really-americas-safest-major-city-wu-says-yes-but-the-numbers-arent-so-clear) ·
[Globe opinion, Mar 2025](https://www.bostonglobe.com/2025/03/21/opinion/problem-calling-boston-safest-city/) ·
[Analyze Boston crime incidents](https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system) ·
[Boston PD Crime Hub](https://boston-pd-crime-hub-boston.hub.arcgis.com/pages/data)

---

## 1. The idea

### One-line

An agent gets a real published claim about Boston crime and a sandboxed copy of the city's open
data, and must return a verdict. The benchmark's ground truth is **computed, not opinioned** —
by running the claim through every defensible analytic specification and seeing whether the
answer holds up.

### The central mechanism: specification-curve ground truth

This is the whole project. Everything else is scaffolding.

Take a claim: *"Shootings in Dorchester are up 30% this year."* To check it you must choose:

| Choice | Defensible options |
|---|---|
| Measure | shooting incidents / victims struck / fatal only / gunfire reports |
| Window | calendar YTD vs YTD / trailing 12mo / full prior year / same window last year |
| Geography | BPD district / neighborhood boundary / census tract rollup |
| Denominator | raw count / ACS 5-year population / decennial / none (count claim) |
| Offense set | Part One / all incidents / excluding non-crime codes |
| Missing geo | drop / impute / report separately |

You enumerate the space, compute the claim under **every** combination — pure SQL, no LLM — and
get a distribution of verdicts. Then the label is derived mechanically:

| Spec-curve outcome | Label |
|---|---|
| Claim holds under all specs | `supported` |
| Fails under all specs | `contradicted` |
| **Sign or significance flips across specs** | `underdetermined` |
| Arithmetic holds but implied conclusion isn't licensed | `misleading` |
| Not computable from this data at all | `unverifiable` |

**Why this is the good idea:** the hardest and most interesting label — `underdetermined` — stops
being a judgment call and becomes a *computed property of the claim*, with the fraction of
supporting specifications as a continuous score. No LLM judge. No κ. No hand-label noise on the
axis that carries the headline. This is what v2 could not do: its `inference_valid` axis had no
ground truth and leaned on an unvalidated judge for its most important finding.

`misleading` still needs a hand label (2 of 5 classes do), but it is a much narrower and
better-defined labeling job than "did the prose contain adequate caveats," and it is
**auditable** — the spec curve is attached, so a reader can check your label against the numbers.

### What the model is scored on

The agent submits a structured verdict, so almost everything is deterministic:

| Metric | Scoring | Judge? |
|---|---|---|
| `verdict_accuracy` | 5-way classification vs computed/labeled truth; macro-F1 + confusion matrix | No |
| **`spec_sensitivity_recall`** | of claims that *are* spec-sensitive, what fraction did the model flag? | No |
| `overclaim_rate` | fraction of `underdetermined` claims answered as confident `supported`/`contradicted` | No |
| `value_in_range` | is the model's computed number inside the spec-curve plausible range? | No |
| `limitation_citation_F1` | did it cite the limitation IDs that actually bear on this claim? | No |
| `sql_faithfulness` | does the submitted SQL actually compute what the reasoning says? | Yes (small) |
| `trajectory_efficiency` | steps, tool errors, recovery rate, cost, latency | No |

`spec_sensitivity_recall` is the headline metric and it does not exist anywhere else. It asks the
question that actually matters about an AI analyst: **does it know when the data can't settle
the question?**

### Headline findings this is designed to produce

1. **N% of real published claims about Boston crime are spec-sensitive** — their truth value
   flips under defensible analytic choices. This finding requires no LLM and is interesting to
   journalists on its own.
2. **Models flag only M% of those** (M ≪ N expected). The gap is the result.
3. **Per-model overclaim rate on underdetermined claims**, with CIs.
4. **BPD's own published weekly figures reproduce from the open data within X%** — or don't.
5. **Grounding ablation:** removing the limitations corpus moves verdict accuracy by K points —
   an interventional number, not a correlation.
6. **Prompt ceiling:** how much of the gap closes with prompting alone.

### Why this showcases harness skill better than v2

- **Agentic:** multi-step tool loops, trajectory tracing, step budgets, sandboxed tool use,
  partial credit, k-rollout variance. This is the harness engineering eval teams actually build.
- **Verifiable rewards, minimal judging:** 6 of 7 metrics are deterministic. v2's most important
  axis was judge-dependent.
- **Externally sourced tasks:** you didn't invent the claims, which gives a real contamination
  and novelty story.
- **Label drift is a genuine engineering problem here:** crime data updates daily, so a claim's
  computed verdict can legitimately change. A harness that distinguishes *model regression* from
  *ground-truth drift* is rare and directly production-relevant. In v2 the data-refresh gate was
  a nice extra; here it's structural.
- **It's a benchmark.** Reusable, runnable by others, citable.

### Explicit non-goals

- A politician-accountability or lie-tracker product. The subject is **data limitations**, not
  people. (See §8.)
- Crime prediction or forecasting.
- Cross-city comparison (out of scope by construction — it's what makes claims `unverifiable`).
- Beating a SOTA text-to-SQL benchmark.
- Individual-level or address-precision lookups.

---

## 2. Data

Snapshot-pinned, checksummed, rebuilt on a schedule. Much narrower than v2's 40–60 datasets —
the wide catalog existed only to serve the retrieval thesis, which is not this project's thesis.

**Core (~15 tables):** `crime_incidents` (Aug 2015–present), `crime_incidents_legacy`,
`shootings`, `firearm_recovery`, `fio`, `fire_incidents`, `911_dispatches_legacy`,
`offense_codes`, `neighborhoods`, `districts`, `population_acs` (tract + rollups),
`population_decennial`, `acs_demographics`, `catalog_metadata`, `data_limitations`.

**Claim corpus (the new thing):** `claims` — sourced, dated, attributed published claims.

| Source | Volume | Character |
|---|---|---|
| BPD weekly crime-stat posts | high (weekly, multi-year) | precise, numeric, official |
| BPD annual reports | ~10 | aggregate, headline framing |
| City press releases / council statements | moderate | normative framing, vaguer |
| GBH / Globe / WBUR / Universal Hub | moderate | already-contested claims |
| r/boston, neighborhood forums | high | the questions real people ask |
| Campaign / advocacy material | moderate | strongest framing effects |

**Sourcing rule:** a claim enters the corpus only if it is (a) publicly published, (b) dated,
(c) attributable, and (d) *at least potentially* checkable against the snapshot. Claims that turn
out to be uncheckable are kept and labeled `unverifiable` — that's a class, not a rejection.

**Paraphrase, don't quote at length.** Store a short paraphrase, the source URL, the publication
date, and the retrieval date. Attribute to the *organization* where possible rather than the
individual. See §8.

### Data integrity rules (in code, not documentation)

1. Snapshot pinned by date; SHA256 of the built DuckDB file in `manifest.json`.
2. Harness refuses to run on checksum mismatch.
3. Original column names and NULLs preserved. The mess is the test.
4. Every claim carries the snapshot version its labels were computed against.
5. Block-level geocoding only. No address resolution, ever.
6. Suppress result cells with n < 10 at the presentation layer — **after** scoring (see review §B7).

---

## 3. Tech stack

Carried over from v2 where it was right, corrected where the review found problems.

| Layer | Choice | Note |
|---|---|---|
| Language / deps | Python 3.12, `uv` | unchanged |
| Database | **DuckDB** | embedded, columnar, Parquet-native |
| Frames | **Polars** | result comparison |
| SQL guard | **sqlglot** | allowlist AST validation — see §5 Phase 4 |
| Config | **Pydantic v2** + `pydantic-settings` | hashes into run IDs |
| Agent loop | **hand-rolled, ~400 lines** | no LangGraph; you need the trajectory unobscured |
| LLM gateway | **LiteLLM** | multi-provider + local Ollama |
| Runner | **own** `harness/runner.py` | single runner — see review §C1 |
| Assertions | **pytest** | thin layer over `scores.jsonl`, zero LLM calls |
| Judge (one metric only) | LiteLLM + rubric, 2 judge models | `sql_faithfulness` only |
| Stats | `scipy`, `numpy`, `statsmodels` | cluster bootstrap, mixed models, AC1 + κ |
| Sandbox | subprocess + rlimits | DuckDB has no `statement_timeout` (verified) |
| Report | static HTML + Plotly | the leaderboard |
| CI | GitHub Actions | replay-based gates |

**Not used:** DeepEval and Ragas. v2 used DeepEval as a runner while also building a runner, and
Ragas's retrieval metrics are redundant when you have exact labels (review §C1, §E). Dropping
both removes a layer of indirection and a large dependency surface. If you want the
framework-fluency signal, the Phase 12 Inspect AI port covers it better and more honestly.

**Retrieval, kept small and deliberate:** a `search_schema` tool and a `read_limitation_doc`
tool, each with two implementations (BM25 via `bm25s`, dense via a current embedding model —
check the MTEB leaderboard at build time, don't trust a 2023 list). Exact search, not ANN: at a
few thousand chunks ANN adds no speed and adds a confound. That's one ablation arm, not a phase,
and it preserves RAG-eval coverage without the project bending around it.

---

## 4. Repo layout

```
boston-eval-harness/
├── README.md                       # leaderboard first
├── ROADMAP-CLAIMBENCH.md
├── data/{raw,manifest.json,boston.duckdb}
├── corpus/limitations/             # hand-authored caveat docs, stable IDs
├── ingest/
│   ├── ckan_client.py              # datastore API + direct-download fallback
│   ├── build_db.py, acs_population.py, offense_codes.py
│   ├── schema_cards.py
│   └── verify_snapshot.py
├── claims/
│   ├── sources/                    # scraped raw source pages, dated
│   ├── extract.py                  # source -> candidate claim records
│   ├── claims.jsonl                # the corpus (committed)
│   └── CHANGELOG.md                # corpus versioning
├── specs/                          # ★ the ground-truth engine
│   ├── dimensions.py               # measure/window/geo/denominator/offense-set/missing-geo
│   ├── space.py                    # per-claim spec space definition + validation
│   ├── compute.py                  # run a claim under one spec -> value
│   ├── curve.py                    # run all specs -> distribution -> derived label
│   └── labels.py                   # derived label + hand-label merge, with provenance
├── env/                            # ★ the agent environment
│   ├── tools.py                    # list_tables, describe, query, compute,
│   │                               #   search_schema, read_limitation_doc, submit_verdict
│   ├── guard.py                    # allowlist AST validation
│   ├── sandbox.py                  # subprocess + timeout + rlimits
│   └── loop.py                     # the agent loop; scaffolds are pluggable
├── harness/
│   ├── config.py                   # config_hash + run_id (distinct)
│   ├── runner.py                   # async, resumable, k-rollout, cached
│   ├── cache.py                    # keyed incl. replicate for variance runs
│   ├── trace.py                    # trajectory JSONL, typed
│   └── score.py                    # PURE: trajectories -> scores.jsonl
├── metrics/
│   ├── verdict.py, spec_sensitivity.py, overclaim.py
│   ├── value_in_range.py, limitation_citation.py
│   ├── sql_faithfulness.py         # the one judged metric
│   └── trajectory.py
├── experiments/
│   ├── model_compare.py, tool_ablation.py, scaffold_ablation.py
│   ├── step_budget.py, prompt_ceiling.py, memorization.py
│   └── reproducibility_audit.py    # BPD published figures vs open data
├── analysis/
│   ├── aggregate.py                # cluster bootstrap, MDE, per-class
│   ├── variance.py, judge_validation.py, diff_runs.py
│   └── drift.py                    # ★ model regression vs ground-truth drift
├── labels/human_labels.jsonl
├── tests/                          # ★ tests of the HARNESS
│   ├── test_guard_malicious.py     # ~15 queries that must be rejected
│   ├── test_resumability.py, test_run_id.py
│   ├── test_spec_curve.py, test_scoring_purity.py
│   └── test_suppression_boundary.py
├── runs/                           # gitignored
├── report/leaderboard.py
└── .github/workflows/{pr_smoke,nightly,data_refresh}.yml
```

---

## 5. Phases

Each phase ends with **`report/leaderboard.py` regenerated and committed**. That replaces v2's
"don't advance until DoD is met" gate — the scorecard's git history becomes a visible record of
the project improving, and you always have something to show.

---

### Phase 0 — Setup (2 days)

- [ ] `uv init`, Python 3.12, `.env` via `pydantic-settings`, keys for 2 hosted providers
- [ ] Ollama + one small local model
- [ ] README skeleton: empty leaderboard at the top
- [ ] Write down the wedge in one sentence: *neighborhood/district-level public-safety claims,
      verified against a pinned snapshot, aggregate only.*

**DoD:** imports resolve; a hello-world LiteLLM call to each provider succeeds.

---

### Phase 1 — Data foundation + claim corpus v1 (2.5 weeks)

Two tracks in parallel; the claim corpus is the risky one, so start it first.

**Data:**
- [ ] `ckan_client.py`: datastore API **with a direct resource-download fallback** — a meaningful
      fraction of Analyze Boston resources are not datastore-active (review §D1)
- [ ] Pull the ~15 core tables to Parquet; `build_db.py`; `verify_snapshot.py` + `manifest.json`
- [ ] `acs_population.py`: tract-level ACS, area-weighted rollups to neighborhood **and** district.
      This is multi-day work, not one bullet.
- [ ] `population_decennial.py` — a *second* defensible denominator. Needed for the spec curve.
- [ ] `offense_codes.py`: crime vs non-crime classification, with the documented
      misspelling/whitespace/hyphen duplicates normalized *in a lookup*, originals preserved
- [ ] `corpus/limitations/*.md`: 10–14 docs with **stable IDs** (`LIM-REPORTS-VS-INCIDENCE`,
      `LIM-BLOCK-GEOCODE`, `LIM-LEGACY-SCHEMA-BREAK`, `LIM-FIO-POLICE-ACTIVITY`,
      `LIM-SMALL-N`, `LIM-PRELIMINARY-DATA`, `LIM-OFFENSE-DUPES`, `LIM-MISSING-GEO`,
      `LIM-NO-CROSS-CITY`, `LIM-SHOOTINGS-VS-VICTIMS`, …). IDs are scored against, so they're API.
- [ ] Explore manually for 3+ hours; **write down 20 surprises.** Keep this from v2 — highest
      value-per-hour item in either plan.

**Claims:**
- [ ] Scrape the BPD weekly crime-stat archive to `claims/sources/`, preserving dates
- [ ] `extract.py`: parse each post into candidate claim records (measure, window, geography,
      direction, magnitude)
- [ ] Hand-source ~40 more from GBH/Globe/WBUR/Universal Hub/council statements/r/boston
- [ ] Target **150 candidate claims**, deduplicated, each with source URL + publication date +
      retrieval date + paraphrase

**DoD:** `verify_snapshot.py` passes; you can hand-verify 5 BPD claims against the snapshot by
hand; ≥150 candidates in `claims.jsonl`; surprises list exists.

> If the BPD archive turns out to be thin or unparseable, this is where you find out — while
> only two weeks in and with the v2 plan still available as a fallback.

---

### Phase 2 — Spec-curve engine ★ (2 weeks)

The core contribution. Deterministic, no LLM, fully testable — build it before any agent exists.

- [ ] `specs/dimensions.py`: enumerate the choice dimensions and their defensible options.
      Each option needs a **written justification** — "defensible" must mean something.
- [ ] `specs/space.py`: per-claim spec space. Not every dimension applies to every claim; the
      space is claim-specific and declared in the claim record.
- [ ] `specs/compute.py`: `(claim, spec) -> value`, as parameterized SQL. Pure, cached.
- [ ] `specs/curve.py`: run the full space; return the value distribution, the verdict under each
      spec, and the **fraction of specs supporting the claim** (`support_fraction`)
- [ ] `specs/labels.py`: derive the label from the curve. Thresholds committed and justified
      (e.g. `support_fraction ≥ 0.95 → supported`, `≤ 0.05 → contradicted`, else
      `underdetermined`). Merge in hand labels for `misleading` / `unverifiable`.
- [ ] Cap the space at ~48 specs per claim; declare unused dimensions explicitly rather than
      letting the cross-product explode
- [ ] `tests/test_spec_curve.py`: synthetic claims with known curves; boundary tests on the
      thresholds

**DoD:** every claim in the corpus has a computed curve and a derived label. **Report the headline
distribution: what fraction of real published claims are `underdetermined`?** That number is a
finding and you now have it, with zero LLM involvement.

---

### Phase 3 — Reproducibility audit ★ (4 days)

A finding that needs no model. Do it early — it's cheap, it's publishable, and it de-risks the
project by guaranteeing at least one real result exists by week 5.

- [ ] `experiments/reproducibility_audit.py`: for each BPD-published numeric claim, compute the
      best-matching spec and report the delta between BPD's figure and the open data
- [ ] Report the distribution of deltas by offense category and year
- [ ] Diagnose the systematic causes: preliminary-data revision, internal-vs-open scope,
      reclassification, geography definitions
- [ ] Write it up as a standalone section

**DoD:** a published table of official-vs-open-data deltas with diagnosed causes. If they diverge
materially, that is a genuinely newsworthy result and a strong opening for the writeup.

---

### Phase 4 — Environment + sandbox (1.5 weeks)

- [ ] `env/tools.py`: `list_tables`, `describe_table`, `query`, `compute`, `search_schema`,
      `read_limitation_doc`, `submit_verdict`. Typed args, typed returns, errors as data.
- [ ] `env/guard.py` — **allowlist, not denylist.** Verified necessary (review §C3): a
      `read_only=True` DuckDB connection still reads arbitrary local files via `read_csv()` and
      **writes** via `COPY TO`, and sqlglot reports an empty-name table for `read_csv(...)`, so a
      naive "single SELECT + table allowlist" guard passes it. Required:
      - reject any `exp.Table` not in the snapshot table set, **including empty-name nodes**
      - reject all table-valued functions (`read_csv`, `read_parquet`, `read_json`, `glob`,
        `read_text`, `read_blob`, `*_scan`, `duckdb_*`)
      - reject `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `PRAGMA`, `SET`, `EXPORT`, `CALL`, all DDL/DML
      - resolve CTE aliases before checking
      - `enable_external_access=false` at connect time — the real defense; the AST guard is
        defense-in-depth
- [ ] `env/sandbox.py`: **subprocess with hard timeout + memory rlimit.** DuckDB has no
      `statement_timeout` (verified on 1.5.5 — it's a Postgres setting). Subprocess isolation also
      gives clean `error_class` attribution for timeouts and OOMs.
- [ ] `env/loop.py`: the agent loop. Step budget, tool-error surfacing, forced `submit_verdict`
      on budget exhaustion. Scaffold is a strategy object so Phase 8 can swap it.
- [ ] `tests/test_guard_malicious.py`: ~15 attack queries, all must be rejected

**DoD:** the malicious-query suite passes; a runaway cross-join is killed without taking the
runner down; an agent completes one claim end-to-end.

---

### Phase 5 — Harness: run / score split ★ (1.5 weeks)

The most transferable engineering in the project. Structure it right and everything downstream
gets cheap.

```
runner.run(config)            -> runs/{run_id}/trajectories.jsonl   # expensive, all LLM calls
score(trajectories, version)  -> scores.jsonl + summary.json         # PURE, cheap, re-runnable
pytest tests over scores.jsonl                                       # CI gate, zero LLM calls
```

- [ ] `harness/config.py`: **distinct identifiers** (review §C4) —
      `config_hash = sha256(canonical_json(config))`;
      `run_id = sha256(config_hash + snapshot_sha + git_sha + scorer_version + corpus_version + replicate_index)`
- [ ] Record per trajectory: **dated** model ID (never the alias — aliases drift and silently
      break reproducibility), provider fingerprint, response IDs, prompt-template hash
- [ ] `harness/runner.py`: asyncio + semaphore, jittered backoff on 429/5xx, retryable vs fatal
- [ ] **k rollouts per task**, seeded, so pass@k and consistency are real
- [ ] `harness/cache.py`: keyed on `(model, params, prompt)` **plus `replicate_index` when
      `cache_mode=bypass`** — otherwise variance runs return cache hits and report `stddev = 0`,
      which you'd then publish as "deterministic at temp 0" (review §C5). Report cache hit rate
      per arm.
- [ ] Resumability: kill mid-run, resume, zero duplicate spend
- [ ] `harness/trace.py`: full trajectory — every tool call with args, return, latency, error
      class; token counts; cost; the final structured verdict
- [ ] `harness/score.py`: **pure function of trajectories.** Judge calls (one metric) cached and
      keyed, so re-scoring is near-free.

**DoD:** kill and resume with zero duplicate spend; `scores.jsonl` recomputed from
`trajectories.jsonl` is byte-identical; changing a metric requires no regeneration.

---

### Phase 6 — Metrics + first real run (1 week)

- [ ] `verdict.py`: 5-way macro-F1, per-class precision/recall, confusion matrix
- [ ] `spec_sensitivity.py`: **the headline metric** — recall on spec-sensitive claims
- [ ] `overclaim.py`: confident verdicts on `underdetermined` claims
- [ ] `value_in_range.py`: model's number inside the spec-curve range. Needs an explicit
      comparison contract — float tolerance, relative vs absolute, unit normalization
      (review §B2a) — declared per claim, not guessed at compare time.
- [ ] `limitation_citation.py`: F1 against the limitation IDs the curve shows are load-bearing
- [ ] `trajectory.py`: steps, tool-error rate, recovery rate, cost, latency
- [ ] **Dev/test split now, not later:** ~100 dev / ~50 test, stratified by derived label and
      source type. Touch test **once**, in Phase 12. (review §B1)
- [ ] Run one frontier model on dev. **Put the numbers in the README.**

**DoD:** one command produces the full metric set from a run. Baseline committed. Leaderboard
generated.

---

### Phase 7 — Corpus v2: scale, label, audit (2 weeks)

- [ ] Grow to **~250 claims** (150 BPD-derived + ~100 hand-sourced). Parameterize where a claim
      shape recurs across neighborhoods/years — but **cluster the bootstrap by claim template**,
      since templated instances are correlated (review §B3)
- [ ] Hand-label `misleading` and `unverifiable`. Label **blind** to spec curves where possible.
- [ ] **Second labeler on 40 claims** — a classmate, or yourself blind after two weeks. Publish
      the inter-annotator agreement. Reporting your own benchmark's label noise is rare and it is
      a credential.
- [ ] Audit 30 derived labels by hand: does the spec curve's verdict match your judgment? Where
      it doesn't, the spec space is wrong — fix the space, not the label.
- [ ] **State the MDE:** "with n=250 clustered and α=0.05 we can detect a D-point difference at
      80% power." Size the corpus from the effect you want to detect (review §B3).
- [ ] `claims/CHANGELOG.md`; bump `corpus_version`; re-score prior runs (free, per Phase 5)

**DoD:** 250 claims, all labeled with provenance (`derived` vs `hand`); IAA published; MDE stated.

---

### Phase 8 — The experiment battery ★ (2.5 weeks)

Everything here is cheap because scoring is pure and responses are cached.

- [ ] `model_compare.py`: ≥4 models (2 frontier from different families, 1 mid, 1 local), k=3
      rollouts. Per-model verdict F1, `spec_sensitivity_recall`, `overclaim_rate`, cost.
- [ ] `tool_ablation.py` — **the interventional core.** Arms: full toolset / no
      `read_limitation_doc` / no `search_schema` / no `compute` / **oracle limitations** (the
      load-bearing docs injected directly) / **anti-oracle** (only irrelevant docs). Oracle and
      anti-oracle bound the effect of grounding, which turns a correlation into a measured
      interval (review §A2).
- [ ] `scaffold_ablation.py`: single-shot / ReAct / plan-then-execute / plan + self-critique,
      model held fixed. Decomposes scaffold from model.
- [ ] `step_budget.py`: budgets 3/6/12/25 → accuracy-vs-cost Pareto frontier.
- [ ] `prompt_ceiling.py`: naive / limitation-loaded system prompt / retrieval-grounded / both.
      **Report over-abstention alongside** — a prompt that "improves" the score by making the
      model answer `underdetermined` to everything has improved nothing, and that finding is
      better than the one you were looking for (review §B4).
- [ ] `memorization.py`: models may know Boston crime patterns from pretraining. Arms: real
      claims / claims with perturbed table+column names (identity-mapped) / synthetic claims about
      the same data. Quantifies how much performance is memory rather than analysis (review §B6).

**DoD:** every arm scored on all metrics with clustered bootstrap CIs. Leaderboard shows the
Pareto frontier and the grounding interval.

---

### Phase 9 — Statistics + the one judge (1 week)

- [ ] `aggregate.py`: cluster bootstrap by claim (and by template) — resample *claims*, not
      (claim, arm) rows, or CIs are badly understated (review §A2)
- [ ] Paired per-claim analysis across arms with claim fixed effects; McNemar on flips. Do **not**
      correlate arm-level aggregates.
- [ ] `variance.py`: k=5 rollouts, `cache_mode=bypass`, report stddev. Note that temp-0
      non-determinism is a property of the serving stack; contrast hosted vs local Ollama.
- [ ] `sql_faithfulness.py` — the only judged metric. Validate properly: 60 hand labels, **tune
      the rubric on 30, report final agreement on the untouched 30**, and publish both so the
      tuning delta is visible (review §B5). Report raw agreement + prevalence + per-class
      precision/recall + **Gwet's AC1** alongside κ — κ collapses under skewed prevalence and
      will make you "fix" a rubric that was fine. Run **two judge models** from different
      families; report a range, not a point.
- [ ] Position and verbosity bias probes on the judge
- [ ] Reconcile summed trace `cost_usd` against actual provider billing once. Report the delta.

**DoD:** every headline number carries a clustered CI; the judged metric's reliability is
published with both agreement statistics.

---

### Phase 10 — CI gates + drift detection ★ (1 week)

Better motivated here than in v2: the data updates daily, so ground truth genuinely moves.

- [ ] `pr_smoke.yml`: replay-based — score cached trajectories, **zero LLM calls**, no API keys,
      no flakiness. Fails if any metric drops below `baseline − CI_width`.
- [ ] `nightly.yml`: full suite on dev, one model; leaderboard artifact
- [ ] `data_refresh.yml`: weekly CKAN pull → new snapshot → **recompute all spec curves** →
      diff derived labels → run evals → promote only if model scores hold *and* label churn is
      explained
- [ ] `analysis/drift.py` — the interesting piece: when a score moves, attribute it to
      **model change / scaffold change / scorer change / snapshot change / label change.** Emit a
      report. This is the production eval problem, and almost nobody builds it.
- [ ] `diff_runs.py`: per-claim delta table; **refuses** to compare across differing
      `corpus_version` / `scorer_version` without `--force` (review §B8)
- [ ] Remaining harness self-tests: resumability, run-ID sensitivity, scoring purity,
      suppression boundary at n=9/n=10

**DoD:** open a PR that weakens the system prompt → CI blocks it. Refresh the snapshot → the
drift report correctly attributes every changed number.

---

### Phase 11 — Leaderboard + release (1 week)

The benchmark is the product. No app.

- [ ] `report/leaderboard.py` → static site: per-model table with CIs; the Pareto frontier;
      the grounding interval; the spec-sensitivity gap; the reproducibility audit; judge
      reliability; variance; cost per claim
- [ ] Browsable claim explorer: claim, source link, **spec curve visualized**, derived label,
      each model's verdict. The spec curve is the most compelling thing you'll have — a plot
      showing a claim's truth value flipping across defensible choices is instantly legible.
- [ ] Publish the limitations corpus and the failure taxonomy
- [ ] `pip install bostonclaimbench` or a one-command Docker run so someone else can reproduce
      your table. This is what makes it a benchmark.
- [ ] Datasheet: sourcing, labeling protocol, IAA, known biases, intended use, misuse warnings

**DoD:** a third party can run one command and reproduce a leaderboard row within stated variance.

---

### Phase 12 — Test split, writeup, Inspect port (1.5 weeks)

- [ ] **Run the held-out test split. Once.** Report dev and test side by side, and report the
      gap. A measured, disclosed overfitting gap is a credential.
- [ ] README opens with the leaderboard and the findings
- [ ] Long-form post:
      - what fraction of real published crime claims are spec-sensitive
      - the spec-curve method as a way to get ground truth without judges
      - the official-vs-open-data reproducibility audit
      - the spec-sensitivity gap: models get numbers right and uncertainty wrong
      - the grounding interval (oracle vs anti-oracle), scaffold vs model, the cost Pareto
      - the prompt ceiling, and the over-abstention it induces
      - the memorization control
      - drift attribution: separating model regression from ground-truth movement
      - dev/test gap, label noise, judge reliability — the honest section
      - what you'd do differently
- [ ] **Port the verdict suite to Inspect AI**, match the numbers, write up what Inspect does
      better and what you gave up. High-signal for eval-infra roles: it proves you understand
      harnesses rather than having used one.

**DoD:** you can say: *"N% of real published claims about Boston crime don't survive their own
defensible specifications. Frontier models flag M% of them. Here's the interventional bound on
how much grounding helps, here's the cost frontier, and here's the gate that tells me whether a
number moved because the model changed or because the world did."*

---

## 6. Timeline

| Phase | Effort | Cum. |
|---|---|---|
| 0 Setup | 2 d | 0.5 wk |
| 1 Data + claim corpus v1 | 2.5 wk | 3 wk |
| 2 Spec-curve engine ★ | 2 wk | 5 wk |
| 3 Reproducibility audit ★ | 4 d | 5.5 wk |
| 4 Environment + sandbox | 1.5 wk | 7 wk |
| 5 Harness run/score split ★ | 1.5 wk | 8.5 wk |
| 6 Metrics + first run | 1 wk | 9.5 wk |
| 7 Corpus v2 + labeling | 2 wk | 11.5 wk |
| 8 Experiment battery ★ | 2.5 wk | 14 wk |
| 9 Statistics + judge | 1 wk | 15 wk |
| 10 CI + drift ★ | 1 wk | 16 wk |
| 11 Leaderboard + release | 1 wk | 17 wk |
| 12 Test split + writeup + Inspect | 1.5 wk | 18.5 wk |

**~18.5 weeks part-time** — versus v2 at a realistic ~24 (its stated 16.5 was ~1.7x optimistic;
review §D1). Shorter *and* a stronger artifact, mostly because the 40–60 dataset catalog, the
retrieval ablation, and the public app all disappear.

**First real finding by week 5.5** (Phase 3, the reproducibility audit) with no model involved.
**First complete vertical slice by week 9.5.** If you stall after that you still have a
publishable result — which is not true of v2, where nothing lands before week 9.

**If you need to compress:** cut Phase 8 to `tool_ablation` + `model_compare` only, and cut the
Inspect port. Do not cut Phase 2 (the method), Phase 5 (the harness), or Phase 9 (the statistics)
— those three *are* the portfolio.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| **BPD claims aren't reproducible from open data at all** | This is Phase 3 and it's a *finding*, not a failure. But if the delta is huge and undiagnosable, BPD-derived claims lose value as `supported`/`contradicted` items — pivot corpus weight toward news/forum claims and label BPD items `unverifiable` with the reason. Phase 1 hand-verifies 5 claims specifically to find this out in week 3. |
| **The BPD archive is unparseable or thin** | Discovered in Phase 1. Fall back to news + forum sourcing (higher effort per claim, ~40 already identified as sourceable). If total sourceable claims < 120, reconsider — `roadmap.md` remains available. |
| **"Defensible specification" becomes arbitrary** | Every dimension option needs a written justification committed alongside it. Audit 30 derived labels by hand in Phase 7; if the curve disagrees with your judgment, the space is wrong. Publish the space and let readers disagree — it's inspectable, unlike a judge. |
| **Spec space explodes** | Cap at ~48 specs/claim. Declare inapplicable dimensions explicitly rather than taking a full cross-product. Compute is pure SQL and cached, so cost is bounded. |
| **Almost all claims come out `underdetermined`** | Then that *is* the finding, and a striking one. But check the thresholds aren't too tight and that the space doesn't include a straw-man option that flips everything. |
| **Agent loops burn tokens** | Step budgets, cached responses, local Ollama for iteration, hosted frontier only for published runs. Report cache hit rate per arm so reviewers know arm differences aren't cache artifacts. |
| **Claim corpus has a source-selection bias** | Inevitable. Document it in the datasheet, report metrics broken out by source type, and don't claim the corpus is representative of "claims about Boston crime" in general. |
| **Sole-author labeling** | Second labeler on 40 items; publish IAA. Derive as many labels as possible from the curve rather than by hand (only 2 of 5 classes need hands). |
| **Legal/reputational: fact-checking named officials** | §8. Paraphrase, attribute to organizations, subject is data limitations not people, publish the method, offer corrections. |
| **Scope creep back into building an app** | There is no app. The benchmark is the product. |

---

## 8. Ethics and framing

This project checks claims made by identifiable people and institutions. That needs deliberate
handling, and getting it right is part of the work.

1. **The subject is the data, not the speaker.** The finding is "this data cannot settle this
   question," not "this official lied." An `underdetermined` verdict is a statement about
   epistemics, and the framing must make that unmistakable — in the writeup, in the leaderboard,
   and in every metric name.
2. **No lie-tracker, no scorecards for individuals.** Attribute to organizations where possible.
   Never aggregate a per-person accuracy score.
3. **Paraphrase and link.** Short paraphrases plus source URL, publication date, and retrieval
   date. Don't reproduce articles.
4. **Publish the method before the verdicts.** The spec space, thresholds, and labeling protocol
   go in the datasheet. Anyone can recompute and disagree — that's the point of deriving labels
   rather than judging them.
5. **Corrections process.** A documented way to contest a label, and a changelog when one changes.
6. **`n < 10` suppression** in all published output, applied *after* scoring. Small-count
   neighborhood rankings are noise and they stigmatize.
7. **Block-level geocoding only.** No address resolution anywhere, including plots.
8. **Reports ≠ incidence; FIO is police activity, not crime.** Encoded as limitation IDs the
   benchmark scores against, not as disclaimers.
9. **No prediction, no normative ranking.** "Which neighborhood is dangerous" is out of scope by
   construction.
10. **Name the reflexive risk:** a benchmark that rewards saying "underdetermined" could be
    gamed by a model that always says it. That's exactly why `overclaim_rate` is paired with
    over-abstention on determinate claims, and why both are always reported together.

---

## 9. Quick reference

```bash
# snapshot
uv run python -m ingest.build_db && uv run python -m ingest.verify_snapshot

# compute spec curves and derive labels for the whole corpus
uv run python -m specs.curve --corpus claims/claims.jsonl --out specs/curves.jsonl
uv run python -m specs.labels --curves specs/curves.jsonl

# how many published claims don't survive their own specifications?
uv run python -m analysis.aggregate --labels specs/labels.jsonl --report spec_sensitivity

# official figures vs open data (no LLM)
uv run python -m experiments.reproducibility_audit

# one run (expensive) then score (pure, cheap)
uv run python -m harness.runner --config configs/opus_react.yaml --split dev --k 3
uv run python -m harness.score --run runs/<run_id>

# experiments
uv run python -m experiments.tool_ablation    --grid configs/tools.yaml
uv run python -m experiments.scaffold_ablation --grid configs/scaffolds.yaml
uv run python -m experiments.step_budget      --budgets 3,6,12,25
uv run python -m experiments.prompt_ceiling   --model claude-opus-5
uv run python -m experiments.memorization

# variance (cache bypassed, or stddev is a lie)
uv run python -m analysis.variance --config configs/opus_react.yaml --k 5 --cache-mode bypass

# why did this number move?
uv run python -m analysis.drift --from <run_a> --to <run_b>

# harness self-tests (fast, no network)
uv run pytest tests/

# leaderboard
uv run python -m report.leaderboard

# the test split. once.
uv run python -m harness.runner --config configs/final.yaml --split test
```
