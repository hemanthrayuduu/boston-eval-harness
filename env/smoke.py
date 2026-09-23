"""Run one claim end to end with a real model, and score it.

    uv run python -m env.smoke --provider openrouter --model qwen/qwen3.8-27b:free
    uv run python -m env.smoke --provider groq --model openai/gpt-oss-120b --claim <claim_id>

A development tool, not the runner: one episode, printed step by step, appended
to ``runs/smoke/trajectories.jsonl``, then scored by the pure scorer against the
claim's ground truth recomputed from the snapshot. The runner (Phase 5) adds
concurrency, caching, resumability and run identity.

The API key comes from the environment or ``.env`` (``OPENROUTER_API_KEY``,
``GROQ_API_KEY``). Free models are rate limited -- OpenRouter's ``:free``
models allow 20 requests a minute and 50 a day without purchased credit -- and
an episode uses one request per step.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from claims.corpus import load_corpus
from env.limitations import load_limitations
from env.loop import ReactScaffold, Task, run_episode
from env.models import OpenAICompatibleModel
from env.tools import Environment
from harness.score import GroundTruth, score
from harness.trace import TraceWriter
from ingest.manifest import Manifest
from specs.compute import SnapshotEvaluator, space_for
from specs.curve import compute_curve
from specs.labels import derive_label

DEFAULT_CLAIM = "hand-herald-20251206-shootings-116-vs-120"


class _Recording:
    """Wraps a model to keep its last response, for printing."""

    def __init__(self, inner):
        self.inner, self.last = inner, None

    def respond(self, messages, tools, tool_choice=None):
        self.last = self.inner.respond(messages, tools, tool_choice=tool_choice)
        return self.last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one claim end to end with a real model.")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", required=True)
    parser.add_argument("--claim", default=DEFAULT_CLAIM)
    parser.add_argument("--budget", type=int, default=10)
    parser.add_argument("--db", type=Path, default=Path("data/boston.duckdb"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--out", type=Path, default=Path("runs/smoke/trajectories.jsonl"))
    args = parser.parse_args(argv)

    corpus = load_corpus()
    claim = corpus[args.claim]
    manifest = Manifest.read(args.manifest)
    evaluator = SnapshotEvaluator.open(args.db, args.manifest, corpus)  # verifies the snapshot
    docs = load_limitations()
    env = Environment(args.db, docs)
    model = _Recording(OpenAICompatibleModel(args.model, provider=args.provider))
    task = Task(claim.claim_id, claim.paraphrase, claim.source.organization, claim.source.published,
                date.fromisoformat(manifest.snapshot_date))

    print(f"claim: {claim.paraphrase}\nmodel: {args.provider}/{args.model}\n")
    trajectory = run_episode(task, env, model, ReactScaffold(), step_budget=args.budget,
                             run_id=f"smoke-{args.provider}", config_hash=f"{args.provider}/{args.model}")
    for call in trajectory.tool_calls:
        shown = json.dumps(call.args)[:140]
        print(f"  step {call.step:>2} {call.tool:<20} {'ok ' if call.ok else 'ERR'} {shown}")
        if not call.ok:
            print(f"           -> {call.error_class}")
    print(f"\ntermination: {trajectory.termination.value}" + (f" ({trajectory.error})" if trajectory.error else ""))
    if trajectory.verdict:
        v = trajectory.verdict
        print(f"verdict: {v.verdict}  spec_sensitive={v.spec_sensitive}  value={v.computed_value}  cites={list(v.limitation_ids)}")
        print(f"reasoning: {v.reasoning[:400]}")
    print(f"tokens: {trajectory.total_tokens:,}  cost: ${trajectory.total_cost_usd:.4f}  model calls: {len(trajectory.llm_calls)}")
    if model.last is not None and model.last.content.strip():
        # The trace keeps no message text; for a smoke run the model's last words help.
        print(f"model's last message (finish_reason={model.last.finish_reason}): {model.last.content.strip()[:600]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    TraceWriter(args.out).append(trajectory)

    decision = derive_label(compute_curve(claim.claim_id, claim.assertion, space_for(claim), evaluator))
    required = frozenset(d.id for d in docs.values() if decision.dominant_driver in d.dimensions)
    [record] = score([trajectory], {claim.claim_id: GroundTruth(claim.claim_id, decision, required)})
    print(f"\nground truth: {decision.derived.value} (driver: {decision.dominant_driver})")
    print("scores:", json.dumps({k: v for k, v in record.as_dict()["metrics"].items() if v is not None}))
    return 0 if trajectory.verdict else 1


if __name__ == "__main__":
    sys.exit(main())
