# Review of `roadmap.md` (v2)

Reviewed 2026-07-28. Verdict, then findings ordered by how much damage they do if unfixed.

## Verdict

This is a genuinely strong plan — better than the large majority of eval-harness portfolio
projects, and the parts that are good are good for the right reasons: a deterministic oracle
anchoring softer axes, judge validation against real labels, versioned snapshots, a data-refresh
regression gate, and DoDs that force numbers into the README early. Keep the skeleton.

But it has three classes of problem:

1. **Two headline claims are not supported by the design as specified.** The
   `recall → execution_accuracy` correlation is confounded and underpowered, and
   `execution_correct` is treated as a clean oracle when it is a noisy one whose noise you
   haven't planned to measure.
2. **Two architectural decisions will cost weeks if discovered late** — the duplicated
   runner (DeepEval *and* `harness/runner.py`), and the missing generate/score split.
3. **The central framing is dated for 2026.** "The schema does not fit in a prompt" is the
   load-bearing premise of half the project, and in 2026 it is probably false. It needs to be
   replaced with a claim you can actually defend.

Plus a set of verified technical bugs (§C) and eval-hygiene omissions (§B4, §B5) that are
individually small and collectively the difference between "built an eval harness" and
"knows how evals go wrong."

Nothing here requires abandoning the project. Most fixes are cheap if made now.

---

## A. Framing problems that change what you build

### A1. "The schema does not fit in a prompt" is probably false, and Phase 1 is designed to prove a premise instead of test it

Phase 1 asks you to count tokens and "confirm it exceeds a reasonable prompt budget." Sixty
Analyze Boston datasets with column cards is on the order of 50k–200k tokens. That fits
comfortably in 2026 frontier context windows. An interviewer will say so within thirty seconds,
and the Phase 1 DoD ("token count exceeds prompt budget") will read as a premise you assumed and
then went looking for evidence for.

The defensible 2026 claim is not *doesn't fit* but *fits and is a bad idea*:

- it costs 30–100x more per query,
- long-context schema linking degrades with distractor count (the well-documented
  lost-in-the-middle / distractor-density effect), and
- it makes per-query latency unusable for an interactive app.

**Fix.** Make the full-schema-in-context arm a **mandatory baseline in Phase 6**, not a rhetorical
aside. Report accuracy, cost/query, and p50/p95 latency for:

| Arm | What it tests |
|---|---|
| `no_retrieval_full_schema` | the honest competitor |
| `no_retrieval_table_names_only` | how much of the win is just knowing table names |
| `oracle_context` (gold `required_tables` cards only) | ceiling — isolates generator error |
| `anti_oracle` (distractors only, gold tables withheld) | floor — proves the metric is sensitive |
| your best retriever | the thing you built |

Either retrieval wins on accuracy (a real finding, because long-context degradation is real at
high distractor counts) or it loses on accuracy and wins 30x on cost (also a real finding, and
the more likely one). Both are publishable. Asserting "doesn't fit" is not.

This single change is the highest-value edit in the document. `oracle_context` and `anti_oracle`
in particular do more work than the entire correlation analysis in Phase 6 — see A2.

### A2. The `recall → execution_accuracy` correlation is confounded and underpowered

Phase 6 says: Spearman/Pearson between `context_recall` and `execution_accuracy`, with bootstrap
CIs, and the Phase 14 DoD escalates that to *"retrieval recall explains X% of the variance."*
As designed, that number will not survive scrutiny:

- **n is ~12–16, not 90.** You are correlating *arm-level aggregates*. Twelve to sixteen points,
  and a bootstrap CI on Spearman at n=14 spans roughly ±0.5. You cannot report a variance-explained
  figure off that.
- **It's an ecological correlation.** Arm-level covariation tells you nothing reliable about the
  per-question mechanism.
- **Recall is not randomized.** Questions where retrieval succeeds are *also the easier questions*.
  Difficulty confounds both variables, so any raw correlation is partly spurious.
- **"Explains X% of variance"** is a causal-sounding claim from observational arm-level data.

**Fix — change the unit of analysis and add an intervention.**

1. **Per-question, paired.** Every arm runs every question, so you have a
   (question × arm) matrix. Report **P(exec_correct | recall=1) − P(exec_correct | recall=0)**
   with question fixed effects — a mixed-effects logistic regression with random intercepts per
   question (`statsmodels` / `pymer4`), or the simpler and very legible version: restrict to
   questions that *flip* recall status across arms and run McNemar. Within-question comparison
   is what kills the difficulty confound, and the ablation design already gives it to you for free.
2. **Intervene rather than observe.** `oracle_context` vs `anti_oracle` vs real retrieval *sets*
   recall instead of measuring it. `accuracy(oracle) − accuracy(anti_oracle)` is the causal
   effect of retrieval on this task; real retrievers slot in between. This turns "we found a
   correlation" into "we bounded the effect and measured where our retriever sits inside it."
3. **Cluster your bootstrap.** Resample *questions*, not (question, arm) rows. Rows within a
   question are correlated; a naive bootstrap will understate CIs by a lot.
4. **Drop "explains X% of variance"** from the Phase 14 DoD. Replace with: *"Perfect retrieval is
   worth +N points of execution accuracy on this catalog (95% CI …); our best retriever captures
   M of those N points at 1/K the cost of full-schema context."* That sentence is stronger, is
   actually supported, and is the kind of thing a hiring manager repeats to someone else.

### A3. The novelty claim is aimed at the wrong axis

"Closes a loop almost no RAG portfolio project closes" — schema linking with downstream
execution accuracy is a well-studied setting. Spider 2.0 (2025) is *specifically* the
large-real-schema text-to-SQL benchmark; BIRD has been doing execution accuracy plus a
cost/efficiency metric (VES) since 2023. Someone who knows that literature will read the claim
as unfamiliarity with it.

Your actual novel contributions are:

1. **Axis 3 — `inference_valid`.** No text-to-SQL benchmark scores whether the *conclusion* was
   licensed by the data. This is the genuinely new thing.
2. **The prompt ceiling.** Deliberately measuring how much of a capability gap is a prompting
   artifact is rare, methodologically mature, and pre-empts the obvious rebuttal.
3. **The data-refresh regression gate.** Almost nobody builds eval gating on *data* drift. This
   is the item most directly relevant to production eval-infra work.

**Fix.** Reposition: *"this is Spider 2.0's schema-linking problem on a real municipal catalog,
with two axes no text-to-SQL benchmark measures."* Cite Spider 2.0 and BIRD explicitly, and put
your catalog stats (tables, columns, total schema tokens) in a table next to theirs. Claim
**rigor** on retrieval and **novelty** on axes 2–3. Note that v2's restructure pushed effort
toward your *least* novel axis — the retrieval work is worth keeping because it's what RAG-eval
job descriptions screen for, but don't let it eat the differentiators.

---

## B. Methodology gaps that undercut the numbers

### B1. No dev/test split — you will overfit your own benchmark

The document has no train/dev/test discipline anywhere. You author the golden set, then tune
prompts (Phase 7), retrievers (Phase 6), and judge rubrics (Phase 9) against it, and publish
the resulting numbers as measurements. That is the most basic eval-hygiene failure, and it is
the one an eval-focused interviewer is most likely to probe.

**Fix.** Split at authoring time, stratified by tier and `failure_tag`: **~60 dev / ~30 test**.
Touch test *once*, at the end of Phase 12. Report both, and report the dev−test gap — a visible
overfitting gap that you measured and disclosed is a *credential*, not an embarrassment. Same
discipline for judge rubrics (see B5) and for the adversarial suite.

### B2. `execution_correct` is a noisy oracle, and `gold_result` as a frozen row list won't work

Two distinct problems.

**(a) The comparison contract is undefined.** "Polars order-insensitive compare" is not a spec.
Real cases you will hit within the first ten questions:

- model returns `neighborhood, incidents, pop, rate_per_1k`; gold has `neighborhood, rate_per_1k`
  → extra columns, correct answer, scored as fail
- `rate_per_1k` = 12.4 vs 12.400000001 → float equality fails
- `"Roxbury"` vs `"ROXBURY"` vs `" Roxbury"`
- NULL vs 0 for a neighborhood with no incidents
- ties at the top of a ranking, where top-1 is genuinely ambiguous
- bag vs set semantics when the model omits a GROUP BY

**Fix.** Replace frozen `gold_result` rows with an explicit per-question **answer contract**:

```json
"answer_spec": {
  "key_columns": ["neighborhood"],
  "value_columns": [{"name": "rate_per_1k", "tol": 0.01, "rel_tol": 0.001}],
  "match": "set",                    // set | bag | ordered
  "extra_columns": "ignore",
  "key_normalization": "casefold_strip",
  "null_equals_zero": false,
  "top_k": 1,
  "tie_policy": "accept_any_tied"
}
```

Score by projecting the model's result onto `key_columns + value_columns` and comparing under
those rules. Extra columns are ignored; missing ones fail. This is roughly how BIRD/Spider handle
it and it removes an entire class of false failures. For a handful of questions where multiple
output shapes are legitimately valid, allow `"gold_predicate": "path.to.fn"` — a Python
function over the result frame — instead of a row set.

**(b) You never measure the oracle's own error rate.** Every serious benchmark reports its label
noise; yours is the anchor for the whole harness, so its noise propagates into all three axes.

**Fix.** Two cheap additions:
- Hand-audit **30 exec-accuracy failures and 20 passes** and report false-fail / false-pass rates.
  Budget half a day. State the number in the README: *"execution accuracy has a measured ~4%
  false-failure rate; differences under ~5 points are not meaningful."*
- **Re-derive gold SQL for 10 questions blind, two weeks later** (or have someone else do it) and
  report your own label disagreement rate. Reporting your benchmark's label noise is a
  differentiator almost nobody bothers with.

### B3. Sample sizes are chosen by vibes, and most per-cell numbers will be statistically empty

- 15-question tier, accuracy near 0.5 → 95% Wilson CI ≈ **±25 points**.
- 8+ failure classes over 90 questions → ~10 per class → CI ≈ **±30 points**.
- 25 adversarial items → an OverclaimRate of 40% has CI ≈ **±19 points**; two models 15 points
  apart are indistinguishable, so the "per-model OverclaimRate table" — billed as the single most
  interview-legible artifact — may show nothing at all.

**Fix, three parts.**

1. **State your MDE up front.** "With n=90 paired and α=0.05, we can detect an 8-point
   within-model difference at 80% power." Sizing the set from the effect you want to detect,
   rather than picking round numbers, is a strong signal on its own.
2. **Scale with templates.** Keep ~60 hand-authored *seeds*, then parameterize over
   neighborhood × offense-category × year to reach 300–600 instances. Gold SQL comes from the
   same template, so it is verified by construction — this is exactly how the big text-to-SQL
   sets scale. Two caveats to state explicitly: templated instances are correlated, so
   **cluster the bootstrap by template**; and report seed-level *and* instance-level numbers,
   since instance-level n flatters you.
3. **Grow the adversarial suite to ~60** (parameterizable too — the same trap works over
   several neighborhoods), or explicitly downgrade the per-model comparison to descriptive with n
   stated. Given it's your headline artifact, grow it.

### B4. `abstention` is measured in only one direction

`abstention.py` scores "correct refusal on the `answerable: false` set." But the failure mode
that actually destroys a system like this is **over-refusal on answerable questions** — and
Phase 7's caveat-loaded prompts will push hard in exactly that direction. If prompt condition 4
"closes the gap," it may have done so by making the model refuse everything, and nothing in the
current design would catch that.

**Fix.** Score abstention as a 2×2 over the full set — correct refusal, over-refusal
(false abstention on answerable), missed refusal, correct answer — and report the **over-refusal
rate on the answerable set alongside every prompt-ceiling condition**. A condition that improves
`inference_valid` while doubling over-refusal has not improved anything; that finding is more
interesting than the one you set out to get.

### B5. Judge validation has a leakage bug and κ will mislead you

Three problems in Phase 9:

- **"If κ < 0.6, revise the rubric and re-label. Report both attempts."** Iterating the rubric
  against the same 40 labels overfits the judge to those labels. The published κ will be
  optimistic and you won't know by how much. **Fix:** label **60**, tune the rubric on 30, report
  final κ on the untouched 30 — and report *both* numbers so the tuning delta is visible.
- **κ collapses under skewed prevalence** (the kappa paradox): if 85% of answers lack the required
  caveat, you can have 90% raw agreement and κ ≈ 0.3, and you'll "fix" a rubric that was fine.
  **Fix:** pre-commit to reporting raw agreement + prevalence + per-class precision/recall/F1
  + **Gwet's AC1** (or Krippendorff's α) alongside κ. Naming the κ paradox in the writeup is a
  strong signal by itself.
- **No inter-judge check.** A gap that only one judge model sees is not a finding. **Fix:** run
  `caveat_presence` under **two different judge models** (different families) and report
  judge-judge agreement. Report axis-3 headline numbers as a range across judges, not a point.

Also worth stating: the Phase 7 `execution_correct` vs `inference_valid` gap is partly a
*judge artifact* whenever the judge's κ is mediocre. Make as much of axis 3 deterministic as you
can (denominator ✓, small-n ✓, plus regex/NLI-lite proposition detection for required caveats,
and a refusal classifier validated against hand labels), and report clearly which fraction of
axis 3 is deterministic versus judged. Judged-only gaps get a caveat.

### B6. Memorization is an uncontrolled confound (and a free experiment)

Boston crime data is public and heavily analyzed. Models plausibly know `crime_incidents` and
its column names from pretraining. That directly inflates the "low recall but correct SQL"
bucket in Phase 6 — you attribute it to lucky guessing or label bugs, but a third explanation
is memorized schema.

**Fix.** Add a **perturbed-schema arm**: rename a subset of tables/columns to semantically
neutral synonyms (`crime_incidents` → `municipal_event_log_a`) with an identity mapping applied to
gold SQL. If accuracy drops a lot with retrieval held constant, the model was leaning on
memorized names. Cheap, striking, and it *strengthens* the retrieval thesis rather than
threatening it.

### B7. Small-n suppression collides with execution accuracy

`guard.py` suppresses cells with n < 10, and `execution_accuracy` compares to `gold_result`. If
suppression fires and gold contains the suppressed cell, correct SQL scores as a failure. The
design has these two rules pointed at each other and never resolves it.

**Fix.** Make suppression a **presentation-layer transform applied after scoring**, and score it
as its own metric on the pre-suppression result frame. Pipeline order:
`execute → raw_frame → [score exec_accuracy] → suppress → [score suppression_applied] → synthesize`.
Trace both frames. Document the ordering — it's a nice example of eval/product tension.

### B8. No golden-set versioning, no scorer versioning

Phase 7 grows the set 50 → 90, Phase 6 revises `distractor_tables`, Phase 9 revises judge
rubrics. Every one of those silently invalidates earlier published numbers and there is no
mechanism to notice.

**Fix.** `golden_set_version` and `scorer_version` as first-class, in the config, in the run ID,
and in every trace record. `diff_runs.py` must **refuse** to compare runs across differing
versions unless `--force`. Keep a `evals/golden/CHANGELOG.md`. When the set changes, re-score
the old traces under the new scorer (free — see C1) and publish both.

---

## C. Technical issues — verified against DuckDB 1.5.5 / sqlglot

### C1. Architectural: split generation from scoring (and pick *one* runner)

Two related problems.

**(a) You are building two runners.** Phase 3 makes DeepEval the runner; Phase 4 builds
`harness/runner.py` with concurrency, backoff, caching, resumability, and tracing — all of which
DeepEval's `evaluate()` also does. Which one produces the published numbers? The document never
says, and you will find out the hard way in Phase 6 when the ablation needs to drive 14 arms ×
90 questions and DeepEval's pytest integration is not the right shape for that.

**(b) Metrics run inside the generation loop.** Because scoring happens during the run, any
change to a metric or judge rubric means regenerating LLM responses. That makes Phase 9's rubric
iteration expensive and Phase 6's re-scoring painful.

**Fix — two-stage design, and make it explicit in the roadmap:**

```
harness/runner.py   run(config)  -> runs/{run_id}/traces.jsonl     # expensive, all LLM calls
harness/score.py    score(traces, scorer_version) -> scores.jsonl + summary.json   # pure, cheap
evals/test_*.py     pytest assertions over scores.jsonl            # CI gate only, zero LLM calls
```

`harness/runner.py` is the engine. Scoring is a **pure function of traces** (judge calls cached
and keyed, so re-scoring is near-free). DeepEval/pytest becomes a thin assertion layer over
already-computed scores. Payoffs: re-scoring is free, rubric changes don't cost regeneration,
the ablation gets cheap, the Phase 10 CI gate becomes a *replay* with no API keys and no
flakiness, and the Phase 4 DoD ("every number reconstructible from traces.jsonl alone") becomes
literally true instead of aspirational.

This is worth restructuring Phases 3–4 around. It's the single biggest engineering improvement
available, and it's also the thing that reads as "has done this before."

### C2. `SET statement_timeout='10s'` does not exist in DuckDB — verified

```
CatalogException: unrecognized configuration parameter "statement_timeout"
Did you mean: "TimeZone"
```

`duckdb_settings()` has no timeout or interrupt knob. That's a Postgres setting. Phase 4's
sandbox item cannot be implemented as written.

**Fix.** Either (a) a watchdog thread calling `connection.interrupt()` after N seconds, or
(b) — better for an eval harness — execute in a **subprocess with a hard timeout and a memory
cap**, so a runaway cross-join can't take the runner down with it. (b) also gives you clean
`error_class` attribution for timeouts and OOMs, which you want in the traces anyway.

### C3. `read_only=True` + "is it a SELECT?" is not a sandbox — verified, and this is a live vulnerability for the public app

On a `read_only=True` DuckDB connection:

```python
ro.execute("SELECT * FROM read_csv('secret.csv')").fetchall()
# -> [(1, 'hunter2')]           arbitrary local file read

ro.execute("COPY (SELECT 1) TO 'out.csv'")
# -> succeeds                   filesystem WRITE from a read-only connection
```

And sqlglot does not save you — for `SELECT * FROM read_csv('secret.csv')`:

```
isinstance(expr, exp.Select) -> True
[t.name for t in expr.find_all(exp.Table)] -> ['']      # empty string, no table name
```

So a naive "single SELECT, and every referenced table is in my allowlist" guard **passes this
query**, because `find_all(Table)` yields one table with an empty name. On a public app with
`httpfs` available, the same shape supports reading remote URLs and exfiltrating data via
`COPY ... TO`. This is the most serious concrete bug in the document.

**Fix.** `guard.py` must be an **allowlist, not a denylist**:

- reject any `exp.Table` whose resolved name is not in the snapshot's table set — **including
  empty-name nodes** (that check alone blocks the above)
- reject all table-valued functions: `read_csv`, `read_parquet`, `read_json`, `glob`,
  `read_text`, `read_blob`, `parquet_scan`, `csv_scan`, `duckdb_*` introspection
- reject `COPY`, `ATTACH`, `DETACH`, `INSTALL`, `LOAD`, `PRAGMA`, `SET`, `EXPORT`, `IMPORT`,
  `CALL`, and any DDL/DML
- reject nested statements: multiple statements, CTEs wrapping forbidden nodes, subqueries in
  `FROM` that reach functions
- set `enable_external_access=false` in the DuckDB config at connect time (this is the real
  defense; the AST guard is defense-in-depth), and don't install `httpfs`
- run as a low-privilege user in a container with the db file mounted read-only

Then add these as **harness self-tests** (see C6): ~15 known-malicious queries that must all be
rejected. That test file is a strong portfolio artifact in its own right — "I tested my sandbox"
is rarer than it should be.

Note this also breaks `table_recall`: function-based sources and CTE aliases yield no usable
table names. Resolve CTE aliases and normalize `catalog.schema.table` before comparing to
`required_tables`, or your retrieval metric silently miscounts.

### C4. `run_id = sha256(config)` contradicts the variance experiment

Success criteria say *"same config + same snapshot → same run ID."* Phase 9 wants **5 identical
runs at temp 0** to measure stddev. Under this scheme all five collide on one run ID.

**Fix.** Two separate identifiers:

```
config_hash = sha256(canonical_json(config))         # groupable across replicates
run_id      = sha256(config_hash + snapshot_sha256 + code_version
                     + scorer_version + golden_set_version + replicate_index)
```

`code_version` = git SHA + dirty flag. Also record, per trace: prompt-template hash, the
**dated model ID** (`claude-opus-5-2026-xx-xx`, not the alias — aliases drift under you and that
silently breaks reproducibility), the provider's returned model/fingerprint string, and the
response ID. Without the returned model string you cannot later explain why last month's numbers
moved.

### C5. The response cache will fabricate `stddev = 0`

`cache.py` keys on `sha256(model, prompt, params)`. Phase 9's five identical runs will hit cache
on runs 2–5 and report **stddev exactly 0** — which you'd then publish as "deterministic at
temp 0." It isn't: frontier models are non-deterministic at temp 0 for reasons unrelated to
sampling (batching, MoE routing, kernel non-determinism).

**Fix.** `--no-cache` / `cache_mode: bypass` for variance runs, include `replicate_index` in the
cache key for those, and state in the writeup that temp-0 non-determinism is a property of the
serving stack. If you *do* measure stddev ≈ 0 on a local Ollama model and > 0 on a hosted one,
that contrast is a nice small finding.

Second cache issue: cache hit rate across ablation arms is high (different retrievers often
retrieve the same context → identical prompts). Good for cost, but **report cache hit rate per
arm** — a reviewer will want to know that arm differences aren't cache artifacts.

### C6. No tests of the harness itself

Every test in the layout evaluates the *model*. Nothing tests the *harness*. For eval-infra
work this is the gap that matters most, because it's the actual job.

**Fix.** Add `tests/` (distinct from `evals/`), fast, no network, synthetic fixtures:

- `execution_accuracy` returns 0 on known-wrong SQL, 1 on a known-equivalent-but-differently-
  written query, and handles each `answer_spec` rule (float tolerance, extra columns, ties, NULL)
- the guard rejects all ~15 malicious queries from C3
- resumability: kill at sample 27, resume, assert zero duplicate API calls (mock the client and
  count)
- `run_id` changes when snapshot / prompt / retriever / scorer changes, and *only* then
- trace round-trip: `scores.jsonl` recomputed from `traces.jsonl` is byte-identical
- suppression fires at n=9 and not at n=10 (boundary)
- κ / bootstrap functions reproduce known values on textbook inputs

Wire it into `pr_smoke.yml` ahead of the eval gate. Cheap, and it's the most credible evidence
in the repo that you build harnesses rather than use them.

---

## D. Sequencing and timeline

### D1. The timeline is ~1.7x optimistic, concentrated in two phases

16.5 weeks part-time at 10–15 h/wk is ~6 months. Two specific underestimates:

- **Phase 2 (1–1.5 wk) is really 2.5–3 wk.** Hand-writing *and verifying* gold SQL against messy
  municipal data, plus `required_columns` / `distractor_tables` / `failure_tags` /
  `requires_caveats` / `answer_spec` labels, is 30–45 min per question once you include the
  iteration where you discover your gold SQL was wrong. 50 × 40 min ≈ 33 h *after* the schema
  design settles.
- **Phase 1 (2 wk) is tight.** A meaningful fraction of Analyze Boston resources are **not
  datastore-active** — `datastore_search` 404s and you must fall back to direct resource download
  (CSV/XLSX/GeoJSON) and parse locally. Budget for the client needing that fallback, plus
  per-resource encoding/delimiter/header quirks, and expect ~25–30% of chosen resources to need
  hand-holding. The ACS pipeline (tract → neighborhood rollup with area-weighted apportionment)
  is its own multi-day job; the roadmap gives it one bullet.

**Fix.** Rebase estimates to ~24 weeks, or cut the catalog to 30 datasets and the golden set to
60 seeds + templates. Do not silently absorb the overrun by skipping Phase 1's manual data
exploration — that's the item with the highest ratio of value to effort in the whole plan.

### D2. Phase-gated sequencing risks having nothing to show at week 9

All four money artifacts (ablation table, OverclaimRate table, judge κ, CI gate) land after
week 9, and "do not advance until DoD is met" means a stall in Phase 2 leaves you with nothing
demonstrable. A half-finished Phase 7 shows a hiring manager nothing.

**Fix — ship a thin vertical slice, then deepen.** Get *all four* artifacts existing at reduced
scale by ~week 6: 15 datasets, 25 questions, 4 arms (including `oracle_context` and
`no_retrieval_full_schema`), 1 judge, a working CI gate, a generated scorecard. Then widen the
catalog, grow the golden set, add arms, add judges. New rule to replace the phase gates:
**every phase ends with `report/evals_page.py` regenerated and committed.** The scorecard's git
history then becomes a visible record of the project improving — which is itself a compelling
artifact, and it protects you against the ~40% of side projects that die at 70%.

### D3. `distractor_tables` are guessed in Phase 2 but only knowable in Phase 5

You can't know which tables are confusable until a retriever confuses them. Labeling them in
Phase 2 means guessing.

**Fix.** Phase 2 labels distractors provisionally (name/column overlap heuristics — cheap and
scriptable from the schema cards). Phase 5/6 **mines actual false positives** from retrieval
traces and revises them. Bump `golden_set_version` when you do (see B8) and re-score prior runs.
Say this out loud in the writeup: mining distractors from observed failures is what makes
`context_precision` non-trivial, and it's a technique worth naming.

---

## E. Stack currency and smaller notes

**Embedding / reranker models are 2023-era.** `bge-small-en-v1.5`, `bge-base`, `e5-base`,
`bge-reranker-base` will date the project on sight. Keep `bge-small` as the *deliberately weak*
baseline — that's good design — but the ablation needs at least two current models or the sweep
looks like a 2023 replication. Check the current MTEB leaderboard when you get to Phase 5 rather
than trusting any list; as of this review the sensible candidates are `bge-m3`,
`gte-modernbert-base`, `Qwen3-Embedding-0.6B`, `EmbeddingGemma`, `nomic-embed-text-v2` on the
local side, and `voyage-3.x` / `text-embedding-3-large` / `gemini-embedding` hosted. Rerankers:
`bge-reranker-v2-m3`, `Qwen3-Reranker`, `jina-reranker-v2`, or hosted Cohere Rerank.

**Use exact search, not ANN.** A few thousand column cards does not need a vector index.
Brute-force cosine over a numpy array is exact, instant at this scale, and — the actual argument —
**removes a confound**: with ANN, index recall loss is conflated with retriever quality, so your
ablation measures two things at once. Keep LanceDB if you want it on the résumé, but pin exact
search and say why. That reasoning is worth a paragraph in the writeup.

**`rank_bm25` → `bm25s`.** Orders of magnitude faster, same interface shape. `rank_bm25` will be
a real bottleneck across a 14-arm sweep.

**Pin Ragas and check the current metric API.** Ragas restructured its metrics interface
(sample-based evaluation, renamed/reorganized classes); don't assume the old
`context_precision` / `context_recall` call signatures. Pin an exact version in the lockfile and
re-read its docs at Phase 5.

**Be honest that Ragas is mostly redundant here — and make that the finding.** Ragas
`context_recall` works by having an LLM decompose a reference answer into claims and check
attribution against retrieved context. You have *exact labeled* `required_tables` /
`required_columns`, so deterministic **recall@k, precision@k, MRR, nDCG@k** are strictly better —
cheaper, exact, no judge variance. `faithfulness` is the one Ragas metric that genuinely earns
its place (synthesized prose vs retrieved rows/docs).

So restructure the claim: **deterministic IR metrics are primary**; Ragas is (a) faithfulness on
the synthesis step and (b) *an object of study* — "here is what an LLM-judged retrieval metric
gets wrong when you have real labels, quantified." That reframing is more interesting than
"we used Ragas," and it means the Phase 5 cross-check becomes a finding rather than a sanity
check. Report the Ragas-vs-deterministic disagreement rate **and its direction** (does Ragas
over- or under-credit?), and note the per-arm cost of Ragas versus free deterministic metrics.

**`pass@k` is meaningless at temp 0 single-sample.** Either sample k>1 at temp>0 for a subset
(interesting: pass@1 vs pass@5 quantifies how much of "capability" is sampling luck) or drop it
from `aggregate.py`.

**Supabase free tier pauses after ~1 week of inactivity.** For a low-traffic portfolio app the
feedback store will be asleep exactly when someone visits. Use SQLite/Turso, or append JSONL to
a volume. Not worth a dependency.

**Two deploys (Fly API + Streamlit Cloud) buys you CORS and two cold starts.** Collapse to one
FastAPI service; either mount Streamlit or serve a single HTMX page. The "identical code path"
property is preserved either way, and you save several days in a phase you've already marked as
cuttable.

**The confidence signal needs a CI, not a point estimate.** *"Questions like this score 87% on
our eval set (n=14)"* — with n=14, the 95% CI is roughly 62–96%. Show the interval, or bucket into
three coarse confidence tiers. Publishing a point estimate off n=14 in the product contradicts
the statistical care you're applying everywhere else, and a sharp visitor will notice.

**FIO volumes are policy artifacts.** Boston FIO reporting changed substantially over the series
(policy revisions and litigation), so year-over-year FIO comparisons mostly measure policy, not
behavior. That's a *better* adversarial case than the generic "FIO isn't crime" framing — a
question like "did stops of X go up between 2016 and 2019?" has a genuinely correct answer that
is "this data can't tell you that." Add it to Phase 8.

**Cost reconciliation.** Reconcile summed `cost_usd` from traces against the provider's actual
billing once, and report the delta. Everyone estimates token costs; almost nobody checks. One
afternoon, and it's a memorable line in the writeup.

---

## F. Concrete edit list for v3

Ordered by value per hour of effort.

1. **§1, Phase 1, Phase 6** — replace "schema doesn't fit" with the cost/degradation framing; add
   `no_retrieval_full_schema`, `no_retrieval_names_only`, `oracle_context`, `anti_oracle` as
   mandatory arms. (A1)
2. **Phase 3/4** — restructure into `run` → `traces.jsonl` → `score` → `scores.jsonl` → pytest
   assertions. One runner. Scoring pure and re-runnable. (C1)
3. **Phase 2** — add the `answer_spec` contract; drop bare frozen `gold_result`. Add the
   dev/test split. (B1, B2a)
4. **Phase 6/14** — per-question paired analysis with question fixed effects; cluster bootstrap
   by question/template; delete "explains X% of variance." (A2)
5. **Phase 4/11** — fix the sandbox: allowlist guard incl. empty-name tables and table-valued
   functions, `enable_external_access=false`, subprocess timeout instead of
   `statement_timeout`. (C2, C3)
6. **New `tests/`** — harness self-tests, including the malicious-query suite. (C6)
7. **Phase 4** — split `config_hash` from `run_id`; add snapshot/code/scorer/golden-set/replicate
   to the run ID; record dated model IDs. (C4)
8. **Phase 2/7** — state the MDE; add templated scaling to 300–600 instances; grow adversarial
   to ~60. (B3)
9. **Phase 9** — 60 labels split 30 tune / 30 held-out; add AC1 + prevalence + per-class metrics
   alongside κ; add a second judge model. (B5)
10. **Phase 7** — abstention as a 2×2; report over-refusal rate under every prompt-ceiling
    condition. (B4)
11. **Phase 3/7** — resolve the suppression-vs-scoring order; score suppression on the
    pre-suppression frame. (B7)
12. **Phase 2/8** — `golden_set_version` + `scorer_version` everywhere; `diff_runs` refuses
    cross-version comparison; add `evals/golden/CHANGELOG.md`. (B8)
13. **§1** — reposition against Spider 2.0 / BIRD; claim rigor on retrieval, novelty on axes 2–3.
    (A3)
14. **Phase 6** — add the perturbed-schema (memorization) arm. (B6)
15. **§3** — refresh embedding/reranker candidates; exact search over ANN with the confound
    argument; `bm25s`; pin Ragas; reframe Ragas as faithfulness + object of study. (E)
16. **Phase 2/9** — audit 30 exec-accuracy failures + 20 passes; blind re-derive 10 gold SQL;
    publish your own label-noise rate. (B2b)
17. **§6** — rebase timeline to ~24 weeks; add the week-6 thin vertical slice; make "regenerate
    the scorecard" the exit criterion for every phase. (D1, D2)
18. **Phase 1** — CKAN client needs a non-datastore fallback path (direct resource download);
    budget for it. (D1)
19. **Phase 11** — CIs or coarse tiers on the confidence signal; collapse to one deploy; drop
    Supabase. (E)
20. **Phase 8** — add the FIO-trend-over-policy-change adversarial case. (E)

## G. What not to change

Called out because it's easy to lose good decisions in a long list of criticism:

- The three-axis structure with a deterministic oracle anchoring the soft axes. This is the
  best idea in the document.
- Snapshot pinning + checksum refusal. Correct, and rarer than it should be.
- `agent/pipeline.py` as the single shared prod/eval entrypoint, with the stated reason.
- The Phase 1 manual data exploration and the "20 surprises" list. Highest value-per-hour item
  in the plan.
- The data-refresh CI gate. Your most differentiated piece of infrastructure.
- The prompt-ceiling experiment, including the willingness to publish a result that weakens
  your own thesis.
- Deliberately starting from a bad baseline so improvements are measurable.
- Excluding LangChain with a stated reason.
- Reframing production traffic as upside rather than a dependency, with the structured-session
  fallback.
- Ethical guardrails mapped 1:1 to harness metrics rather than parked in a disclaimer section.
- The optional Inspect AI port. Genuinely high-signal for eval-infra roles — worth keeping even
  if you cut a full phase to fund it.
