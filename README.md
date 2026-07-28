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

- [x] Feasibility check on claim sourcing (see ClaimBench §0)
- [ ] Phase 0 — setup
