"""Compute every corpus claim's spec curve and derived label.

    uv run python -m specs.run

Verifies the snapshot against its manifest, then for each claim in the corpus:
declares its spec space (``specs.compute.space_for``), evaluates it under every
specification (``specs.compute.SnapshotEvaluator``), and derives a label
(``specs.labels.derive_label``). Writes:

* ``specs/curves.jsonl`` -- one record per claim: the space, every outcome with
  its value, the counts behind it or the reason it is not computable, and the
  label decision.
* ``specs/summary.json`` -- the label distribution, overall and by source kind,
  with ``unverifiable`` split by *why*: out of scope for the data, outside the
  data's coverage, or an engine gap still to close. Only the first is a finding
  about the claims; the last is a to-do list.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from claims.corpus import load_corpus
from ingest.manifest import Manifest
from specs.compute import SnapshotEvaluator, space_for
from specs.curve import compute_curve
from specs.labels import Label, derive_label

__all__ = ["run", "summarize"]


def _json_number(value: float | None) -> float | str | None:
    if value is None:
        return None
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return round(value, 4)


def run(corpus_dir: Path | None, db: Path, manifest: Path) -> list[dict[str, Any]]:
    claims = load_corpus(corpus_dir) if corpus_dir else load_corpus()
    evaluator = SnapshotEvaluator.open(db, manifest, claims)
    records = []
    for claim_id, claim in sorted(claims.items()):
        space = space_for(claim)
        curve = compute_curve(claim_id, claim.assertion, space, evaluator)
        decision = derive_label(curve)
        outcomes = []
        reasons: Counter[str] = Counter()
        for outcome in curve.outcomes:
            result = evaluator.evaluate(claim_id, outcome.spec)
            if result.reason:
                reasons[result.reason] += 1
            outcomes.append(
                {
                    "spec": outcome.spec.as_dict(),
                    "value": _json_number(outcome.value),
                    "holds": outcome.holds,
                    "counts": {k: _json_number(v) for k, v in result.counts.items()},
                    "reason": result.reason,
                }
            )
        records.append(
            {
                "claim_id": claim_id,
                "source_kind": claim.source.kind,
                "space": {key: list(options) for key, options in space.dimensions},
                "outcomes": outcomes,
                "label": decision.derived.value,
                "support_fraction": decision.support_fraction,
                "dominant_driver": decision.dominant_driver,
                "driver_influence": curve.driver_influence(),
                "rationale": decision.rationale,
                "not_computable": dict(reasons),
            }
        )
    return records


def _why_unverifiable(record: dict[str, Any]) -> str:
    kinds = {reason.split(":", 1)[0] for reason in record["not_computable"]}
    if "engine_gap" in kinds:
        return "engine_gap"
    if "coverage" in kinds:
        return "coverage"
    return "out_of_scope"


def summarize(records: list[dict[str, Any]], snapshot: str) -> dict[str, Any]:
    labels = Counter(r["label"] for r in records)
    unverifiable = [r for r in records if r["label"] == Label.UNVERIFIABLE]
    computable = [r for r in records if r["label"] != Label.UNVERIFIABLE]
    by_kind: dict[str, Counter[str]] = {}
    for r in records:
        by_kind.setdefault(r["source_kind"], Counter())[r["label"]] += 1
    drivers = Counter(r["dominant_driver"] for r in records if r["label"] == Label.UNDERDETERMINED)
    reasons = Counter(reason for r in unverifiable for reason in r["not_computable"])
    return {
        "snapshot_content_sha256": snapshot,
        "claims": len(records),
        "labels": dict(sorted(labels.items())),
        "unverifiable_by_why": dict(sorted(Counter(_why_unverifiable(r) for r in unverifiable).items())),
        "unverifiable_reasons": dict(reasons.most_common()),
        "computable_claims": len(computable),
        "underdetermined_share_of_computable": (
            round(labels[Label.UNDERDETERMINED] / len(computable), 3) if computable else None
        ),
        "underdetermined_drivers": dict(drivers.most_common()),
        "labels_by_source_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute spec curves and labels for the claim corpus.")
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=Path("data/boston.duckdb"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--out", type=Path, default=Path("specs"))
    args = parser.parse_args(argv)

    records = run(args.corpus, args.db, args.manifest)
    snapshot = Manifest.read(args.manifest).content_sha256 or ""
    summary = summarize(records, snapshot)

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "curves.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
