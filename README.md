# boston-eval-harness

Planning stage. Two candidate project designs over the same Boston open-safety data, plus the
review that led from one to the other.

| Doc | What it is |
|---|---|
| [`ROADMAP-CLAIMBENCH.md`](ROADMAP-CLAIMBENCH.md) | **Recommended.** BostonClaimBench — an agentic benchmark for statistical claim verification. Ground truth is *computed* via specification curves rather than hand-labeled or LLM-judged. ~18.5 weeks. |
| [`roadmap.md`](roadmap.md) | "Ask Boston" v2 — a text-to-SQL + RAG eval harness with a public app. ~24 weeks realistically. Still viable; safer if targeting roles that lead with RAG evaluation. |
| [`ROADMAP-REVIEW.md`](ROADMAP-REVIEW.md) | Engineering review of `roadmap.md`, ordered by severity. Includes verified DuckDB/sqlglot findings and a 20-item edit list. Its fixes are already folded into ClaimBench. |

## The short version

Both plans use the same data. The difference is the task.

`roadmap.md` asks *"can a model write correct SQL over a messy municipal catalog?"* — single-turn,
generate-and-check, with the novel axis (was the conclusion licensed by the data?) scored by an
unvalidated LLM judge.

`ROADMAP-CLAIMBENCH.md` asks *"can a model tell when the data cannot settle a question?"* — an
agent verifies real published claims about Boston crime against a pinned snapshot, and the answer
key is derived by computing each claim under every defensible analytic specification. When the
verdict flips across specifications, the claim is *underdetermined*, and that label is produced by
code rather than opinion. Six of seven metrics need no judge.

The headline it is built to produce: **what fraction of real published claims about Boston crime
don't survive their own defensible specifications — and what fraction of those do frontier models
notice?**

## Status

169 tests, all offline (no network, no API keys, no data snapshot required).

- [x] Feasibility check on claim sourcing (ClaimBench §0)
- [x] Harness core — `env/guard.py`, `env/sandbox.py`, `harness/config.py`
- [x] Spec-curve ground-truth engine — `specs/`
- [x] Trace schema + pure scoring — `harness/trace.py`, `harness/score.py`
- [ ] Agent loop and tool surface — `env/tools.py`, `env/loop.py`
- [ ] Claim corpus + DuckDB evaluator (needs network)

### What the spec curve does

```
claim    : "Roxbury has more crime than Back Bay"
label    : UNDERDETERMINED   (provenance: derived)
support  : 25% of 8 computable specs
driver   : denominator
rationale: holds under 2/8 computable specifications -- the verdict flips
           across defensible choices; driven mainly by 'denominator'

influence by dimension:
  denominator    1.00
  offense_set    0.00
```

The label is computed, not judged. `underdetermined` — the class a model is most
likely to get wrong — carries no annotator noise and needs no LLM judge, and the
driver attribution says *which* analytic choice the claim's truth hangs on.

### Why the metrics come in pairs

A model that answers `underdetermined` to everything scores perfectly on the
headline metric:

| metric | model A (always confident) | model B (always abstains) |
|---|---|---|
| `spec_sensitivity_recall` | 0.00 | **1.00** |
| `overclaim_rate` (lower better) | 1.00 | **0.00** |
| `over_abstention_rate` (lower better) | **0.00** | 1.00 |
| `verdict_accuracy` | 0.40 | 0.40 |

Neither model is good. Reporting overclaiming without over-abstention would make
B look like a breakthrough, so the two are always reported together.

### Run and score are separate

`run` makes every LLM call and writes `trajectories.jsonl`. `score` is a pure
function of that file — no network, no clock, no randomness — so re-scoring is
free, changing a metric costs nothing, and the CI gate is a replay that needs no
API keys and cannot flake.
