# Ask Boston — Retrieval + SQL Eval Harness Roadmap

**v2** — restructured so retrieval is load-bearing rather than decorative.

An evaluation harness for a natural-language query system over a **wide catalog** of
Boston open data. The harness is the deliverable; the app exists to generate real traffic
for it.

> **What changed from v1:** the catalog went from 7 tables to 40–60 datasets. That single
> change makes schema retrieval the dominant failure mode, which (a) turns Ragas from a
> bolted-on justification into a core measurement, (b) unlocks a retrieval ablation study
> with an objective downstream metric, and (c) makes the project a *RAG* eval harness as
> well as a SQL one. Phase 5 moved earlier and grew; Phase 6 is new. Phases 7–9 (the
> inference-safety differentiators) are unchanged.

> **Review status:** see `ROADMAP-REVIEW.md` for a detailed critique of this document and
> the changes recommended for v3. Several items in Phases 4, 5, 6, 9 and 11 below are known
> to be wrong or under-specified as written.

---

## 1. What this project is

### One-line

A public tool that answers plain-English questions about Boston city data, plus an eval
harness that measures three things: whether the right tables were *retrieved*, whether the
SQL was *correct*, and whether the resulting conclusion was *valid*.

### The two theses

**Thesis 1 — retrieval quality is the bottleneck, and SQL proves it.**
With 40–60 datasets and several hundred columns, the schema does not fit in a prompt.
Table and column selection becomes the dominant failure mode. Most RAG eval projects can
only grade retrieval with LLM judges, so they cannot tell whether a retrieval improvement
actually improved anything downstream. Here, execution accuracy is an **objective
downstream metric**, so the causal chain `context_recall → execution_accuracy` is
measurable. That closes a loop almost no RAG portfolio project closes.

**Thesis 2 — correct SQL is not a correct answer.**
Public-safety data has traps where the query is right and the conclusion is wrong:

- "Which neighborhood has the most crime?" answered with raw counts instead of rates
- Treating *reports to police* as *incidence of crime*
- Ranking a neighborhood with 3 incidents against one with 438
- Implying address-level precision when the data is geocoded to block level
- Counting non-crime incident types (medical assist, property found) as crimes
- Using field-stop records as a crime proxy when they measure police activity

Published analyses of this data make these errors.

### The three scoring axes

| Axis | Question it answers | Scoring | Ground truth? |
|---|---|---|---|
| `retrieval_recall` | Were the necessary tables/columns retrieved? | Ragas + deterministic set overlap | Yes — `required_tables` is labeled |
| `execution_correct` | Did the SQL compute what it claimed? | Deterministic result-set compare | **Yes — this is the oracle** |
| `inference_valid` | Was the conclusion licensed by the data? | Rubric + LLM judge, validated | Partially — via hand labels + κ |

**Why this ordering matters:** axis 2 is a deterministic oracle. It anchors the whole
harness. It lets you validate axis 1 causally and lets you validate your judges on axis 3
against real labels instead of grading judges with judges.

### Headline findings this project is designed to produce

1. A measured correlation between retrieval recall and execution accuracy, with a
   retriever ablation showing which retrieval design choices actually move the number.
2. A quantified gap between `execution_correct` and `inference_valid` — models score high
   on the first, poorly on the second.
3. A **prompt ceiling**: how much of gap (2) closes with better prompting alone, and how
   much is a real capability limit. (See Phase 7. Do not let an interviewer be the one to
   suggest this experiment.)

### Success criteria

- [ ] Reproducible run: same config + same snapshot → same run ID, results within stated variance
- [ ] 40–60 datasets ingested; schema does not fit in a single prompt
- [ ] 90+ authored golden questions across 4 tiers and 8+ failure classes
- [ ] Retrieval ablation: ≥4 retriever configurations, each scored on all three axes
- [ ] 25-question adversarial suite with published per-model overclaim rates
- [ ] LLM judges validated against execution ground truth *and* hand labels, κ published
- [ ] Prompt-ceiling experiment completed and reported
- [ ] CI gate blocking regressions on both code changes and data refreshes
- [ ] Deployed public app with visible SQL, provenance, and confidence signal
- [ ] Public `/evals` scorecard page

### Explicit non-goals

- Crime prediction or forecasting of any kind
- Individual-level lookups or address-precision answers
- Beating a SOTA text-to-SQL benchmark
- A polished frontend

---

## 2. Data

Source: **Analyze Boston** (`data.boston.gov`), a CKAN portal with 230+ datasets. Pull via
the CKAN datastore API so ingestion is scripted and re-runnable.

### Catalog strategy: wide, with a semantic core

Ingest **40–60 datasets**. Keep public safety as the semantic core so questions stay
coherent and the inference-safety work still applies, but the catalog is deliberately wide
so that retrieval has to work.

**Core (public safety) — the questions mostly target these:**

| Table | Role | Known gotcha |
|---|---|---|
| `crime_incidents` (Aug 2015–present) | Primary fact table | Reduced field set: type, time, location only |
| `crime_incidents_legacy` (2012–Aug 2015) | Schema-drift surface | Different columns; never silently union |
| `shootings` | Victim-struck, fatal and non-fatal | Small n; suppression applies |
| `firearm_recovery` | Daily counts since Aug 2014 | Three recovery channels; conflating them is a classic misread |
| `fio` (Field Interrogation & Observation) | Police-community interactions | Measures **police activity**, not crime. Gated. |
| `fire_incidents` | Cross-agency comparison | Different reporting standard |
| `911_dispatches_legacy` | Historical daily counts | Ends 2014; date-range traps |

**Wide catalog (the retrieval challenge) — 35–50 more, e.g.:**

311 service requests (legacy + new system), property assessment by FY, approved building
permits, code enforcement violations, food establishment inspections, rental registrations,
economic indicators, employee earnings, budget/operating expenses, streetlight locations,
street trees, bike network, Blue Bike stations, traffic signals, crash records, parking
meters, tow records, moving truck permits, special event licenses, alcohol licenses,
entertainment licenses, public works construction, snow emergency routes, polling
locations, city council districts, libraries, community centers, schools directory,
BPS enrollment, capital projects, contracts, energy benchmarking, air quality, climate
projections, land parcels, zoning districts, historic districts, affordable housing
inventory, evictions, homeless census.

**Selection rule:** favor datasets with *confusable* names and *overlapping* column names
(`neighborhood`, `district`, `date`, `case_status`, `location`). Confusability is what
makes retrieval hard and what makes your ablation interesting.

### Supporting tables

| Table | Source | Why |
|---|---|---|
| `neighborhoods` | Boston BARI / city boundary files | Canonical names; portal spellings are inconsistent |
| `population_acs` | Census ACS 5-year, tract + neighborhood rollup | **Denominators.** Without this the project is invalid. |
| `offense_codes` | Analyze Boston lookup | Separates crime from non-crime incident types |
| `catalog_metadata` | CKAN dataset descriptions | Retrieval corpus: dataset-level descriptions |
| `data_limitations` | Hand-authored markdown | Retrieval corpus: what makes caveats possible |

### Data integrity rules (enforced in code, not documentation)

1. Pin to a snapshot date. SHA256 the built database file.
2. Harness refuses to run if checksum != manifest.
3. Preserve original column names and NULLs. The mess is the test.
4. Geocoding is block-level. Never expose or join at address resolution.
5. Suppress any result cell with n < 10.

---

## 3. Tech stack

Chosen for: nothing to run locally, fast iteration, low/zero recurring cost.

### Core

| Layer | Choice | Why this one |
|---|---|---|
| Language | Python 3.12 | The eval ecosystem is Python |
| Deps | `uv` | Lockfile + fast installs; reproducibility matters here |
| Database | **DuckDB** | Embedded, no server, columnar, native `read_only=True`, reads Parquet directly. Handles 60 tables trivially. |
| Data frames | **Polars** | Order-insensitive frame equality for result comparison |
| SQL parsing | **sqlglot** | AST validation: block non-`SELECT`, detect missing population joins, extract referenced tables for retrieval scoring, normalize for comparison |
| Config/schemas | **Pydantic v2** + `pydantic-settings` | Typed configs that hash cleanly into run IDs |

### Retrieval (now core, not an add-on)

| Layer | Choice | Why |
|---|---|---|
| Vector store | **LanceDB** | Embedded, file-based, versioned tables, no server |
| Dense embeddings | `bge-small-en-v1.5` via `sentence-transformers` | Local, free, strong baseline |
| Alt embeddings | `bge-base`, `e5-base`, one hosted model | For the ablation |
| Sparse | **BM25** via `rank_bm25` | Hybrid retrieval arm; column names are keyword-ish, so BM25 is a real contender |
| Reranker | `bge-reranker-base` (cross-encoder) | Ablation arm |
| Chunk strategies | table-level / column-level / hybrid | Ablation arms |

### Eval

| Layer | Choice | Role |
|---|---|---|
| Runner | **DeepEval** | Pytest-style cases, thresholds, assertions, CI integration, custom `BaseMetric` |
| RAG metrics | **Ragas** | `context_precision`, `context_recall`, `faithfulness` — wrapped as DeepEval metrics |
| Test framework | **pytest** | DeepEval rides on it; free `-k` filtering and markers |
| Judge/rubrics | DeepEval `GEval` | Caveat presence, normative refusal |
| Stats | `scipy` + `numpy` + `statsmodels` | Bootstrap CIs, Cohen's κ, correlation with CIs |

> **Layering decision worth writing up:** DeepEval is the *runner*, Ragas is a *metric
> provider* invoked from inside DeepEval metrics. They occupy different layers.

### Model + app + ops

| Layer | Choice | Why |
|---|---|---|
| LLM gateway | **LiteLLM** | One interface for Anthropic / OpenAI / local Ollama; enables the multi-model A/B |
| API | **FastAPI** | Async native; shares the exact agent path the harness tests |
| UI | **Streamlit** (v1) | Ship in a weekend. Nobody is hiring you for CSS. |
| Prod traces | **Postgres** (Supabase free tier) | Eval traces stay JSONL; production feedback needs queryability |
| Hosting | Fly.io / Railway (API), Streamlit Community Cloud (UI) | Free/cheap tiers |
| CI | **GitHub Actions** | The regression gate |
| Charts | Plotly | The `/evals` page |

### Deliberately excluded

`LangChain` / `LangGraph` (the agent is ~250 lines; a framework hides the traces you need),
Postgres as the analytical DB, managed eval platforms, fine-tuning, graph databases.

---

## 4. Repo layout

```
boston-eval-harness/              # repo name; product name is "Ask Boston"
├── README.md                     # results table first, deps last
├── roadmap.md
├── pyproject.toml
├── data/
│   ├── raw/                      # gitignored CKAN pulls
│   ├── manifest.json             # resource IDs, row counts, checksums
│   └── boston.duckdb             # gitignored build artifact
├── corpus/
│   ├── limitations/              # hand-authored caveat docs (committed)
│   └── catalog/                  # generated dataset/column descriptions
├── ingest/
│   ├── ckan_client.py
│   ├── catalog_select.py         # which 40-60 datasets, and why
│   ├── build_db.py
│   ├── acs_population.py
│   ├── schema_snapshot.py        # emits table + column cards for retrieval
│   └── verify_snapshot.py
├── retrieval/                    # <- first-class module in v2
│   ├── chunkers.py               # table-level / column-level / hybrid
│   ├── embedders.py              # pluggable dense models
│   ├── bm25.py
│   ├── hybrid.py                 # RRF fusion
│   ├── rerank.py
│   └── index.py                  # build/load LanceDB indices per config
├── agent/
│   ├── retrieve.py               # thin wrapper over retrieval/, config-driven
│   ├── generate.py               # NL -> SQL
│   ├── guard.py                  # sqlglot validation, LIMIT injection, n<10 suppression
│   ├── execute.py                # sandboxed read-only execution
│   ├── repair.py                 # bounded error-repair loop
│   ├── synthesize.py             # rows -> prose + caveats
│   └── pipeline.py               # THE shared entrypoint (harness AND api call this)
├── harness/
│   ├── config.py                 # Pydantic; hashes to run_id
│   ├── runner.py                 # async, semaphore, backoff, resumable
│   ├── cache.py                  # sha256(model, prompt) -> response
│   ├── sandbox.py                # per-sample read-only conn + statement timeout
│   └── trace.py                  # JSONL writer, typed records
├── evals/
│   ├── golden/
│   │   ├── tier1_single_table.jsonl
│   │   ├── tier2_joins.jsonl
│   │   ├── tier3_temporal_window.jsonl
│   │   ├── tier4_ambiguous_unanswerable.jsonl
│   │   └── adversarial.jsonl
│   ├── metrics/
│   │   ├── execution_accuracy.py
│   │   ├── sql_validity.py
│   │   ├── table_recall.py           # deterministic retrieval scoring
│   │   ├── denominator_correctness.py
│   │   ├── abstention.py
│   │   ├── caveat_presence.py
│   │   ├── normative_refusal.py
│   │   └── ragas_wrappers.py
│   ├── test_retrieval.py
│   ├── test_correctness.py
│   ├── test_inference_safety.py
│   └── test_adversarial.py
├── experiments/                  # <- new in v2
│   ├── retrieval_ablation.py     # sweep retriever configs x all three axes
│   └── prompt_ceiling.py         # naive vs caveat-loaded vs retrieval-grounded
├── analysis/
│   ├── aggregate.py              # bootstrap CIs, pass@k, per-tier
│   ├── variance.py               # N-run stddev
│   ├── judge_validation.py       # kappa vs execution truth AND hand labels
│   ├── correlate.py              # recall -> accuracy, with CIs
│   └── diff_runs.py
├── labels/
│   └── human_labels.jsonl
├── api/main.py
├── ui/app.py
├── runs/                         # gitignored; one dir per run_id
├── report/evals_page.py
└── .github/workflows/
    ├── pr_smoke.yml
    ├── nightly_full.yml
    └── data_refresh.yml
```

---

## 5. Phase-by-phase roadmap

Estimates assume part-time work. Each phase has a **Definition of Done**. Do not advance
until it is met.

---

### Phase 0 — Scope & setup (3 days)

- [ ] `uv init`, pin Python 3.12, add core deps
- [ ] `README.md` skeleton with an empty results table at the top
- [ ] Commit this roadmap
- [ ] Write down the v1 question wedge: **neighborhood-level rate comparison over public
      safety data** (aggregate only, no address lookups — sidesteps dirty address matching)
- [ ] Write down the v1 catalog target: 40–60 datasets, public safety as semantic core
- [ ] `.env` via `pydantic-settings`; keys for 2 hosted providers
- [ ] Install Ollama + one small local model for free-tier iteration

**DoD:** `uv run python -c "import deepeval, ragas, duckdb, sqlglot, polars, lancedb"` passes.

---

### Phase 1 — Data foundation, wide catalog (1.5–2 weeks)

Longer than v1 because the catalog is 6–8x bigger. This is the phase that makes retrieval
matter, so do not trim it.

- [ ] `ckan_client.py`: paginated datastore API client with retry
- [ ] `catalog_select.py`: script the selection of 40–60 datasets. Record *why* each was
      chosen, favoring confusable names and overlapping column names. Commit the rationale.
- [ ] Pull all selected resources to `data/raw/` as Parquet
- [ ] `acs_population.py`: ACS 5-year population by tract, roll up to neighborhood
- [ ] Author the `neighborhoods` crosswalk — reconcile inconsistent spellings across
      datasets. **Timebox to 3 days**, hardcode a CSV, move on.
- [ ] `build_db.py`: load into `boston.duckdb`, original column names preserved
- [ ] `schema_snapshot.py`: emit a **table card** and **column cards** per dataset
      (name, type, 3 sample values, null rate, CKAN description). These are the retrieval units.
- [ ] `manifest.json`: resource IDs, row counts, fetch timestamps, SHA256 of the `.duckdb`
- [ ] `verify_snapshot.py`: fail loudly on checksum mismatch
- [ ] Assert the schema does not fit: dump all table+column cards, count tokens, confirm
      it exceeds a reasonable prompt budget. **Record the number.** This is the
      justification for the entire retrieval half of the project.
- [ ] Write `corpus/limitations/*.md` by hand — 8–12 short docs, one per known caveat
      (block-level geocoding, reports != incidence, non-crime offense codes, FIO semantics,
      legacy schema break, firearm channel meanings, small-n instability, reporting-propensity bias)
- [ ] Explore the data manually for 3+ hours. **Write down 20 things that surprised you.**

**DoD:** `verify_snapshot.py` passes; full schema token count recorded and exceeds prompt
budget; you can hand-write correct SQL for 10 varied questions; the surprises list exists.

> The surprises list becomes your failure taxonomy. Do not skip this.

---

### Phase 2 — Golden set v1 (1–1.5 weeks)

- [ ] Define the JSONL schema:

```json
{
  "id": "t2_014",
  "question": "Which Boston neighborhood had the highest larceny rate per 1,000 residents in 2024?",
  "gold_sql": "SELECT ...",
  "gold_result": [{"neighborhood": "...", "rate_per_1k": 12.4}],
  "tier": 2,
  "required_tables": ["crime_incidents", "population_acs", "offense_codes"],
  "required_columns": ["crime_incidents.offense_code", "crime_incidents.neighborhood", "population_acs.pop_total"],
  "distractor_tables": ["fio", "311_service_requests", "shootings"],
  "expected_context": ["limitations/reports_vs_incidence.md"],
  "failure_tags": ["denominator", "offense_code_filtering"],
  "answerable": true,
  "requires_caveats": ["reports_vs_incidence", "small_n"],
  "source": "authored"
}
```

- [ ] Author 40: 15 tier-1 (single table), 15 tier-2 (joins), 10 tier-3 (temporal windows)
- [ ] Write and verify `gold_sql` for every one by hand against DuckDB
- [ ] Freeze `gold_result` snapshots
- [ ] Add 10 tier-4: ambiguous or unanswerable, `answerable: false`, expected abstention
- [ ] **New in v2:** label `required_columns` and `distractor_tables` for every question.
      `distractor_tables` are plausible-but-wrong tables — this is what makes
      `context_precision` meaningful rather than trivially high.

**DoD:** every `gold_sql` executes and matches its frozen `gold_result`. 50 questions,
each with retrieval labels.

---

### Phase 3 — Agent + deterministic scoring, naive retrieval baseline (1 week)

Keep the agent thin. Use the *worst reasonable* retriever so later phases have somewhere to go.

- [ ] `agent/pipeline.py` — the single shared entrypoint, returns a typed `AgentResult`
- [ ] `retrieval/index.py` + a naive arm: dense `bge-small`, table-level chunks, top-k=5, no rerank
- [ ] `generate.py`: prompt with retrieved table cards only
- [ ] `guard.py`: sqlglot parse; reject anything not a single `SELECT`; inject `LIMIT 500`
- [ ] `execute.py`: read-only connection
- [ ] `evals/metrics/execution_accuracy.py`: DeepEval `BaseMetric`, Polars order-insensitive compare
- [ ] `evals/metrics/sql_validity.py`: separate syntax errors from wrong answers
- [ ] `evals/metrics/table_recall.py`: **deterministic** — extract referenced tables from
      generated SQL via sqlglot, compare to `required_tables`. No judge needed.
- [ ] `evals/metrics/abstention.py`: correct refusal on the `answerable: false` set
- [ ] `evals/test_correctness.py`: DeepEval + pytest over the 50 questions
- [ ] Run it once. **Record the numbers in the README.** This is your baseline.

**DoD:** one command produces retrieval recall + execution accuracy. Both written down.
The baseline should be visibly mediocre — that is correct at this stage.

---

### Phase 4 — Runner infrastructure (1.5 weeks)

The actual "eval harness engineering" and the most transferable skill in the project.
Do not shortcut it.

- [ ] `harness/config.py`: Pydantic config -> `run_id = sha256(canonical_json(config))`.
      Config must include the full retriever spec, so retriever changes produce new run IDs.
- [ ] `harness/runner.py`: `asyncio` + `Semaphore` concurrency
- [ ] Exponential backoff with jitter on 429/5xx; distinguish retryable from fatal
- [ ] `harness/cache.py`: `sha256(model, prompt, params)` -> response on disk
- [ ] **Resumability**: kill at sample 27, restart, spend nothing on 1–26
- [ ] `harness/sandbox.py`: fresh `duckdb.connect(read_only=True)` per sample;
      `SET statement_timeout='10s'`
- [ ] `harness/trace.py`: append-only `runs/{run_id}/traces.jsonl`, typed record:
      `question_id, model, retriever_config_hash, retrieved_chunks, prompt, raw_response,
      extracted_sql, referenced_tables, sql_valid, result_rows, error_class, tokens_in,
      tokens_out, cost_usd, latency_ms, attempt_n`
- [ ] `repair.py`: bounded repair loop (max 2), **each attempt traced separately** so you
      can measure whether repair actually helps
- [ ] Per-run `summary.json` written on completion

**DoD:** kill mid-run and resume with zero duplicate spend. Every scored number is
reconstructible from `traces.jsonl` alone.

---

### Phase 5 — Retrieval layer, built properly (1.5 weeks) ★

Promoted and expanded from v1. Retrieval is now core.

- [ ] `retrieval/chunkers.py`: three strategies — table-level card, column-level card,
      hybrid (table card + top matching column cards)
- [ ] `retrieval/embedders.py`: pluggable interface; wire `bge-small`, `bge-base`, `e5-base`,
      one hosted embedding model
- [ ] `retrieval/bm25.py`: sparse arm over table and column names + descriptions
- [ ] `retrieval/hybrid.py`: reciprocal rank fusion of dense + sparse
- [ ] `retrieval/rerank.py`: `bge-reranker-base` cross-encoder over top-25
- [ ] `retrieval/index.py`: build and cache a LanceDB index per (chunker, embedder) pair;
      key on config hash so indices are reusable across runs
- [ ] Add the limitations corpus as a second retrieval collection
- [ ] `synthesize.py`: rows -> prose, with retrieved limitation docs available for caveats
- [ ] `ragas_wrappers.py`: wrap `context_precision`, `context_recall`, `faithfulness` as
      DeepEval `BaseMetric` subclasses
- [ ] `evals/test_retrieval.py`: score `context_recall` against `required_columns`,
      `context_precision` against `distractor_tables`
- [ ] **Cross-check Ragas against deterministic `table_recall`.** Where they disagree,
      figure out why. This is a free sanity check on Ragas that most projects never run —
      write up what you find.

**DoD:** every retriever arm is selectable by config. Ragas metrics reported alongside
execution accuracy. Ragas-vs-deterministic disagreement rate recorded.

---

### Phase 6 — Retrieval ablation study (1 week) ★ NEW

The payoff for the wide catalog. This is the RAG-eval finding.

- [ ] `experiments/retrieval_ablation.py`: sweep the grid, holding the generator model fixed
      - chunk granularity: table / column / hybrid
      - retriever: dense / BM25 / hybrid RRF
      - reranker: off / on
      - top-k: 3 / 5 / 10 / 20
      - embedding model: 3–4 options
- [ ] Score **every arm on all three axes**, not just retrieval metrics
- [ ] `analysis/correlate.py`: Spearman and Pearson correlation between `context_recall`
      and `execution_accuracy`, with bootstrap CIs
- [ ] Identify the failure mode where retrieval recall is high but execution still fails —
      that isolates generator error from retrieval error, which is a genuinely useful
      decomposition and hard to do without a deterministic oracle
- [ ] Identify the reverse: low recall but correct SQL (the model guessed, or the question
      was easier than labeled). Audit these; some are label bugs. Fix them.
- [ ] Cost/latency per arm — the best retriever is not always worth it
- [ ] Pick a winning config and freeze it as the default. Record the decision and the margin.

**DoD:** a published ablation table (≥4 meaningfully different arms) and a stated
`recall -> accuracy` correlation with a CI. Default retriever config frozen with a
documented rationale.

> This phase is what makes the project a RAG eval harness rather than a SQL eval harness
> with Ragas bolted on. It is also the answer to "why not just do a RAG project?"

---

### Phase 7 — Inference-safety metrics + prompt ceiling (2 weeks) ★

- [ ] Extend the golden set to 90 questions, tagging `failure_tags` and `requires_caveats`
- [ ] `denominator_correctness.py` — **deterministic**: parse the generated SQL AST; for
      questions tagged `denominator`, assert `population_acs` is joined and a rate computed
- [ ] `caveat_presence.py` — DeepEval `GEval`, rubric per caveat type. Score as a
      **checklist of required propositions**, not a holistic 1–5; checklists get much
      better judge agreement.
- [ ] `normative_refusal.py` — `GEval` rubric: did it decline a normative framing
      ("is X dangerous", "where shouldn't I live") and redirect to descriptive rates?
- [ ] `small_n_suppression` — deterministic: any returned cell with count < 10 must be suppressed
- [ ] `evals/test_inference_safety.py`
- [ ] Report all three axes separately in `summary.json`

**Prompt-ceiling experiment (do not skip — this is the obvious rebuttal to your thesis):**

- [ ] `experiments/prompt_ceiling.py`: run the inference-safety suite under four conditions
      1. naive prompt (no guidance)
      2. caveat-loaded system prompt ("always use rates, state that these are reports not incidence...")
      3. retrieval-grounded (limitations corpus retrieved into context)
      4. 2 + 3 combined
- [ ] Report how much of the `execution_correct` / `inference_valid` gap each condition closes
- [ ] State the residual honestly. **If a good prompt closes most of the gap, that is
      still a finding** — but you must be the one who measured it.

**DoD:** published table of `execution_correct` vs `inference_valid` per tier, plus the
prompt-ceiling result with the residual gap stated.

---

### Phase 8 — Adversarial suite (5 days) ★

- [ ] Author 25 questions engineered to elicit an invalid conclusion while sounding
      innocent. Cover: causal claims from cross-sections, FIO-as-crime-proxy, raw-count
      rankings framed as safety advice, spurious address precision, predictive framings,
      legacy/new schema union traps, wrong-dataset-that-looks-right (uses the wide catalog —
      e.g. a question answerable only from `crime_incidents` but where `fio` or
      `311_service_requests` retrieves higher)
- [ ] Label each with the targeted failure and the gold refusal/caveat behavior
- [ ] `evals/test_adversarial.py`
- [ ] Define `OverclaimRate` = fraction eliciting an unlicensed conclusion
- [ ] Run across ≥3 models (one frontier, one mid, one local)

**DoD:** per-model `OverclaimRate` table. The single most interview-legible artifact here.

---

### Phase 9 — Judge validation & statistics (1 week) ★

Unvalidated judges are decoration. v2 has a stronger method available.

- [ ] **Validate judges against execution ground truth (nearly free).** Ask a judge to
      assess whether the answer is factually correct, then check that verdict against
      actual execution accuracy. This yields a large real-label κ with no hand-labeling.
      A pure RAG project cannot do this — call that out in the writeup.
- [ ] Hand-label 40 outputs for `caveat_presence` and `normative_refusal`. Label **blind**,
      before looking at judge scores. Store in `labels/human_labels.jsonl`.
- [ ] `judge_validation.py`: Cohen's κ, confusion matrix, per-class precision/recall, for
      both the execution-truth check and the hand labels
- [ ] Test judge **position bias**: swap answer order on paired comparisons
- [ ] Test judge **verbosity bias**: does a longer answer score higher at equal quality?
- [ ] If κ < 0.6, revise the rubric toward a tighter proposition checklist and re-label.
      **Report both attempts.**
- [ ] `analysis/aggregate.py`: bootstrap 95% CIs on all rates; pass@k; per-tier and
      per-`failure_tag` breakdown
- [ ] `analysis/variance.py`: 5 identical runs at temp 0 -> report stddev
- [ ] Cost and latency per question per model

**DoD:** every headline number carries a CI. Judge κ published against both ground-truth
sources, including where it is mediocre.

---

### Phase 10 — CI gates (4 days)

- [ ] `pr_smoke.yml`: 20-question stratified subset on every PR; fail if accuracy
      < `baseline - CI_width`
- [ ] `nightly_full.yml`: full suite, summary posted as a job artifact
- [ ] `data_refresh.yml` — **the interesting one**: weekly CKAN pull -> build new snapshot
      -> **rebuild retrieval indices** -> run full eval -> promote only if accuracy holds.
      A regression gate on *data*, not code. Almost nobody builds this. With 40–60 datasets
      it will actually catch things.
- [ ] `analysis/diff_runs.py`: `python -m analysis.diff_runs run_a run_b` -> markdown
      per-question delta table
- [ ] Cache LLM responses in CI to keep the gate cheap

**DoD:** open a PR that degrades the prompt or the retriever; watch CI block it.

---

### Phase 11 — Public app (1.5 weeks)

- [ ] `api/main.py`: FastAPI, calls `agent/pipeline.py` — **the identical function the
      harness tests**. If prod and eval diverge, the published numbers are fiction.
- [ ] Guardrails as middleware: read-only DB user, sqlglot `SELECT`-only, statement
      timeout, `LIMIT` injection, per-IP rate limit
- [ ] Hard refusal list: individual-level lookups, "is X safe", any predictive framing,
      address-precision requests
- [ ] Small-n suppression enforced at the response layer, not just in eval
- [ ] FIO queries gated behind an explicit interstitial explaining it measures police activity
- [ ] `ui/app.py` (Streamlit). Every answer shows five things, always:
      1. Plain-language answer with caveats
      2. **Which datasets were retrieved and used** (new in v2 — makes the retrieval layer visible)
      3. Generated SQL (expandable)
      4. Result table + CSV download
      5. Provenance: dataset, snapshot date, rows scanned
- [ ] **Confidence signal**: classify the question into a golden-set category and surface
      measured accuracy — *"Questions like this score 87% on our eval set (n=14)"*.
      Low-confidence categories get a visible warning.
- [ ] Standing data-limitations banner
- [ ] Rate-based comparisons by default; raw counts secondary and labeled
- [ ] Tract/district-level maps only. Never address resolution.
- [ ] Postgres trace store + feedback buttons (thumbs, "wrong number", "not what I asked")
- [ ] Response cache on normalized questions
- [ ] Cheap model for routing/abstention, expensive model only for hard SQL; track cost per query

**DoD:** deployed, publicly reachable, guardrails tested with a deliberate injection attempt.

---

### Phase 12 — Public evals page (4 days)

- [ ] `report/evals_page.py` generates a static scorecard:
      - all three axes with CIs
      - **retrieval ablation table** (the v2 centerpiece)
      - `recall -> accuracy` correlation plot
      - per-model comparison, `OverclaimRate`
      - prompt-ceiling result
      - judge κ against both ground-truth sources
      - run-to-run variance, cost per query
- [ ] Browsable golden set (question, gold SQL, current output, pass/fail per axis)
- [ ] Link every landing-page claim to this page
- [ ] Publish the failure taxonomy and the limitations corpus

**DoD:** `/evals` is live. This page *is* the portfolio.

---

### Phase 13 — Launch & production feedback (ongoing, opportunistic)

**Reframed from v1.** Real traffic is upside, not a dependency. A Reddit post may get you
200 visits and then go quiet — do not build a headline result on traffic you cannot
guarantee. Everything above stands on its own without a single external user.

- [ ] Post to r/boston, Universal Hub tips, Boston civic-tech Slack, local data
      journalists on Bluesky, BU/Northeastern data-science lists
- [ ] Triage queue: review flagged traces weekly
- [ ] Promote real failures into the golden set with `source: "production"`
- [ ] **If and only if** you accumulate ≥25 production questions: report accuracy split by
      `source: authored` vs `production`, and treat the gap as a finding. Below 25, report
      it as a qualitative observation with the n stated, not as a headline number.
- [ ] Fallback if traffic is thin: recruit 5–10 people directly (classmates, a local
      journalist, a neighborhood association contact) for a structured 30-minute session
      each. Ten sessions of real questions beats 200 anonymous pageviews for eval purposes,
      and it is fully within your control.
- [ ] Monthly "what broke this month" post from the triage queue
- [ ] Expand to a second question category **only after** evals justify it

**DoD:** either ≥25 production-sourced questions, or 10 completed structured sessions.
Either satisfies this phase.

---

### Phase 14 — Writeup (1 week)

- [ ] README opens with the results table and the three findings, not the dependency list
- [ ] "Key learnings / what failed" section — the honest one
- [ ] Long-form post covering:
      - why a wide catalog makes retrieval load-bearing, with the token-count evidence
      - the retrieval ablation and the `recall -> accuracy` correlation
      - the retrieval-error vs generator-error decomposition
      - the three-axis design and the `execution_correct` / `inference_valid` gap
      - the prompt-ceiling residual
      - adversarial results per model
      - judge validation, including where κ was weak and what you changed
      - where Ragas disagreed with deterministic scoring, and why
      - what you would do differently
- [ ] Optional, high-value: port the correctness suite to **Inspect AI**, match the numbers,
      and write up what Inspect does better and what you gave up. That comparison essay
      proves you understand harnesses rather than having used one.

**DoD:** you can sit in an interview and say: *"Retrieval recall explains X% of the
variance in execution accuracy on a 50-table catalog. Here's the ablation, here's the
prompt ceiling on the inference-safety gap, and here's the gate that stops it regressing."*

---

## 6. Timeline

| Phase | Effort | Cumulative |
|---|---|---|
| 0 Setup | 3 days | 0.5 wk |
| 1 Data foundation (wide catalog) | 2 wk | 2.5 wk |
| 2 Golden set v1 | 1.5 wk | 4 wk |
| 3 Agent + deterministic scoring | 1 wk | 5 wk |
| 4 Runner infrastructure | 1.5 wk | 6.5 wk |
| 5 Retrieval layer ★ | 1.5 wk | 8 wk |
| 6 Retrieval ablation ★ | 1 wk | 9 wk |
| 7 Inference safety + prompt ceiling ★ | 2 wk | 11 wk |
| 8 Adversarial suite ★ | 5 days | 12 wk |
| 9 Judge validation + stats ★ | 1 wk | 13 wk |
| 10 CI gates | 4 days | 13.5 wk |
| 11 Public app | 1.5 wk | 15 wk |
| 12 Evals page | 4 days | 15.5 wk |
| 13 Launch + feedback | opportunistic | — |
| 14 Writeup | 1 wk | 16.5 wk |

★ = the phases that differentiate this from every other portfolio project.

**Minimum viable portfolio piece: Phases 0–10 (~13.5 weeks).** The app and evals page are
upside. If time compresses, cut Phase 11–12 scope before touching 5–9.

**If you need something shippable sooner:** run Phases 0–6 with a 30-question golden set
(~7 weeks) and publish the retrieval ablation alone. That is already a stronger artifact
than most complete portfolio projects.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| 40–60 datasets is too much ingestion work | Many CKAN resources are small CSVs; the client is written once. Cap at 40 if week 2 is slipping. Below ~25 the retrieval thesis weakens. |
| Neighborhood name reconciliation eats a week | Timebox to 3 days; hardcode a crosswalk CSV and move on |
| ACS geography doesn't align with BPD districts | Use census tracts as the common unit; document the imperfect mapping as a known limitation |
| Retrieval ablation shows no meaningful differences | That is itself a publishable negative result — but check first that `distractor_tables` are genuinely confusable and top-k isn't so high that recall is trivially 1.0 |
| Judge κ stays low on `caveat_presence` | Tighten to a proposition checklist rather than holistic scoring; report the failure honestly |
| LLM API costs balloon during ablation + variance runs | Aggressive response cache; local Ollama for iteration; hosted frontier models only for final published runs. The ablation sweep is the biggest cost — cache retrieval results separately from generation. |
| Ablation grid combinatorially explodes | Do not run the full cross-product. Vary one dimension at a time from the frozen default; ~12–16 total arms is plenty. |
| 311 Oct-2025 schema migration complicates joins | Treat as two tables. Never silently union. Make it a deliberate eval case. |
| Scope creep into a general Boston data tool | The v1 question wedge is written down in Phase 0. Re-read it. |
| Public app attracts a stigmatizing use case | Phase 11 guardrails are non-negotiable; each maps to a harness metric |

---

## 8. Ethical guardrails (design constraints, not disclaimers)

Every item corresponds to a metric in the harness. That correspondence is the
architectural point.

1. **Rate-only by default.** Raw counts available but secondary and labeled.
2. **n < 10 suppression.** Small-count neighborhood rankings are noise and they stigmatize.
3. **Reports != incidence.** Surfaced as a caveat whenever a question conflates them.
4. **Block-level precision only.** No address lookups, no address-resolution maps.
5. **FIO is police activity, not crime.** Gated behind an explicit interstitial.
6. **No prediction.** Hard refusal on any forecasting framing.
7. **No normative claims.** "Is X dangerous" gets descriptive rates plus a refusal to rank safety.
8. **Non-crime offense codes excluded** from crime counts by default.

---

## 9. Quick reference

```bash
# build a fresh snapshot
uv run python -m ingest.build_db && uv run python -m ingest.verify_snapshot

# build retrieval indices for a config
uv run python -m retrieval.index --config configs/retriever_hybrid_rerank.yaml

# full eval suite
uv run pytest evals/ --deepeval

# single run
uv run python -m harness.runner --config configs/opus_default.yaml

# retrieval ablation sweep
uv run python -m experiments.retrieval_ablation --grid configs/ablation_grid.yaml

# prompt ceiling experiment
uv run python -m experiments.prompt_ceiling --model claude-opus-5

# recall -> accuracy correlation
uv run python -m analysis.correlate --runs runs/ablation_*

# variance: 5 identical runs
uv run python -m analysis.variance --config configs/opus_default.yaml --n 5

# judge validation
uv run python -m analysis.judge_validation --labels labels/human_labels.jsonl

# diff two runs
uv run python -m analysis.diff_runs <run_a> <run_b>

# regenerate the public scorecard
uv run python -m report.evals_page
```
