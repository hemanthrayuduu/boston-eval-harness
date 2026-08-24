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

129 tests, all offline (no network, no API keys, no data snapshot required).

- [x] Feasibility check on claim sourcing (ClaimBench §0)
- [x] Harness core — `env/guard.py`, `env/sandbox.py`, `harness/config.py`
- [x] Spec-curve ground-truth engine — `specs/`
- [ ] Claim corpus + ingest (needs network)
- [ ] Agent environment and run/score split

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
