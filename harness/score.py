"""Scoring: a pure function of trajectories and ground truth.

No network, no LLM calls, no clock, no randomness. Given the same
``trajectories.jsonl`` and the same ``SCORER_VERSION``, this produces
byte-identical output forever. That property buys three things:

* changing a metric costs nothing -- re-score, do not re-run
* the CI gate is a replay, so it needs no API keys and cannot flake
* a number that moved can be attributed, because the trace is fixed

Every metric returns ``None`` when it does not apply to a claim, and applicability
is decided by the *ground truth*, never by what the model happened to answer --
otherwise a model could opt out of a metric by refusing to answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from harness.trace import Trajectory
from specs.labels import Label, LabelDecision

__all__ = [
    "SCORER_VERSION",
    "GroundTruth",
    "ScoreRecord",
    "Metric",
    "METRICS",
    "score",
    "summarize",
    "scores_to_jsonl",
]

# Bump on any change to a metric's definition. Runs scored under different
# versions are not comparable, and `comparable_with` on RunIdentity enforces it.
SCORER_VERSION = "1.0.0"

# Verdicts that assert a definite answer. Giving one of these on a claim whose
# truth flips across defensible specifications is the failure the benchmark exists
# to measure.
_DEFINITE = frozenset({Label.SUPPORTED.value, Label.CONTRADICTED.value})
_ABSTAINING = frozenset({Label.UNDERDETERMINED.value, Label.UNVERIFIABLE.value})


@dataclass(frozen=True)
class GroundTruth:
    claim_id: str
    decision: LabelDecision
    required_limitation_ids: frozenset[str] = frozenset()

    @property
    def is_spec_sensitive(self) -> bool:
        return self.decision.is_spec_sensitive

    @property
    def is_determinate(self) -> bool:
        """The curve gave a clean verdict, so abstaining here is over-abstention."""
        return self.decision.derived in (Label.SUPPORTED, Label.CONTRADICTED)


class Metric(Protocol):
    name: str

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None: ...


class VerdictAccuracy:
    name = "verdict_accuracy"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        if trajectory.verdict is None:
            # Running out of steps is a wrong answer, not an absent one. Scoring
            # it None would let a model improve its average by giving up.
            return 0.0
        return float(trajectory.verdict.verdict == truth.decision.final.value)


class SpecSensitivityRecall:
    """The headline metric: of the claims that genuinely are spec-sensitive, how
    many did the model notice?

    Credited for either flagging ``spec_sensitive`` or answering
    ``underdetermined`` -- both demonstrate the model knows the data cannot settle
    the question, which is what is being measured.
    """

    name = "spec_sensitivity_recall"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        if not truth.is_spec_sensitive:
            return None
        if trajectory.verdict is None:
            return 0.0
        flagged = (
            trajectory.verdict.spec_sensitive
            or trajectory.verdict.verdict == Label.UNDERDETERMINED.value
        )
        return float(flagged)


class OverclaimRate:
    """Confident answers to questions the data cannot settle. Lower is better."""

    name = "overclaim_rate"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        if not truth.is_spec_sensitive:
            return None
        if trajectory.verdict is None:
            return 0.0  # never answered, so it did not overclaim
        return float(trajectory.verdict.verdict in _DEFINITE)


class OverAbstentionRate:
    """Refusing to answer questions the data *does* settle. Lower is better.

    Always reported next to ``overclaim_rate``. A prompt that drives overclaiming
    to zero by making the model answer 'underdetermined' to everything has
    improved nothing, and without this metric it would look like a triumph.
    """

    name = "over_abstention_rate"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        if not truth.is_determinate:
            return None
        if trajectory.verdict is None:
            return 1.0
        return float(trajectory.verdict.verdict in _ABSTAINING)


class ValueInRange:
    """Did the model's number land inside the range the defensible specs produce?"""

    name = "value_in_range"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        value_range = truth.decision.value_range
        if value_range is None:
            return None
        if trajectory.verdict is None or trajectory.verdict.computed_value is None:
            return 0.0  # a computable claim left uncomputed
        low, high = value_range
        return float(low <= trajectory.verdict.computed_value <= high)


class LimitationCitationF1:
    name = "limitation_citation_f1"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        if not truth.required_limitation_ids:
            return None
        if trajectory.verdict is None:
            return 0.0
        cited = set(trajectory.verdict.limitation_ids)
        required = set(truth.required_limitation_ids)
        if not cited:
            return 0.0
        true_positives = len(cited & required)
        if true_positives == 0:
            return 0.0
        precision = true_positives / len(cited)
        recall = true_positives / len(required)
        return 2 * precision * recall / (precision + recall)


class StepsUsed:
    name = "steps_used"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        return float(trajectory.n_steps)


class ToolErrorRate:
    name = "tool_error_rate"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        return trajectory.tool_error_rate


class CostUSD:
    name = "cost_usd"

    def score(self, trajectory: Trajectory, truth: GroundTruth) -> float | None:
        return trajectory.total_cost_usd


METRICS: tuple[Metric, ...] = (
    VerdictAccuracy(),
    SpecSensitivityRecall(),
    OverclaimRate(),
    OverAbstentionRate(),
    ValueInRange(),
    LimitationCitationF1(),
    StepsUsed(),
    ToolErrorRate(),
    CostUSD(),
)


@dataclass(frozen=True)
class ScoreRecord:
    claim_id: str
    run_id: str
    config_hash: str
    replicate_index: int
    scorer_version: str
    metrics: dict[str, float | None]
    # Slicing keys, denormalised so analysis never needs to rejoin ground truth.
    derived_label: str
    final_label: str
    submitted_verdict: str | None
    termination: str

    def as_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "replicate_index": self.replicate_index,
            "scorer_version": self.scorer_version,
            "derived_label": self.derived_label,
            "final_label": self.final_label,
            "submitted_verdict": self.submitted_verdict,
            "termination": self.termination,
            "metrics": {k: self.metrics[k] for k in sorted(self.metrics)},
        }


def score(
    trajectories: list[Trajectory],
    truths: dict[str, GroundTruth],
    metrics: tuple[Metric, ...] = METRICS,
    scorer_version: str = SCORER_VERSION,
) -> list[ScoreRecord]:
    """Score trajectories against ground truth. Pure.

    Trajectories are sorted by ``(claim_id, replicate_index)`` so output order
    does not depend on the order the runner happened to finish them in --
    otherwise concurrency would make re-scoring non-reproducible.
    """
    records = []
    ordered = sorted(trajectories, key=lambda t: (t.claim_id, t.replicate_index))

    for trajectory in ordered:
        truth = truths.get(trajectory.claim_id)
        if truth is None:
            raise KeyError(
                f"no ground truth for claim {trajectory.claim_id!r}. Scoring a "
                "trajectory without an answer key would silently drop it."
            )

        records.append(
            ScoreRecord(
                claim_id=trajectory.claim_id,
                run_id=trajectory.run_id,
                config_hash=trajectory.config_hash,
                replicate_index=trajectory.replicate_index,
                scorer_version=scorer_version,
                metrics={m.name: m.score(trajectory, truth) for m in metrics},
                derived_label=truth.decision.derived.value,
                final_label=truth.decision.final.value,
                submitted_verdict=(
                    trajectory.verdict.verdict if trajectory.verdict else None
                ),
                termination=trajectory.termination.value,
            )
        )
    return records


def scores_to_jsonl(records: list[ScoreRecord]) -> str:
    """Canonical serialisation. Byte-stable, so purity is testable by comparison."""
    return "\n".join(
        json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"))
        for record in records
    )


@dataclass
class MetricSummary:
    name: str
    mean: float | None
    n_applicable: int
    n_total: int

    @property
    def applicability(self) -> float:
        return self.n_applicable / self.n_total if self.n_total else 0.0


@dataclass
class RunSummary:
    scorer_version: str
    n_claims: int
    metrics: dict[str, MetricSummary] = field(default_factory=dict)
    terminations: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "scorer_version": self.scorer_version,
            "n_claims": self.n_claims,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "terminations": dict(sorted(self.terminations.items())),
            "metrics": {
                name: {
                    "mean": summary.mean,
                    "n_applicable": summary.n_applicable,
                    "n_total": summary.n_total,
                }
                for name, summary in sorted(self.metrics.items())
            },
        }


def summarize(records: list[ScoreRecord]) -> RunSummary:
    """Aggregate scores.

    Means only -- no confidence intervals. Those need a bootstrap clustered by
    claim (and by template, for parameterised claims), which lives in ``analysis``.
    Putting a naive CI here would understate it, and a CI that is wrong is worse
    than no CI at all.
    """
    if not records:
        return RunSummary(scorer_version=SCORER_VERSION, n_claims=0)

    versions = {record.scorer_version for record in records}
    if len(versions) > 1:
        raise ValueError(
            f"records span multiple scorer versions {sorted(versions)}; "
            "aggregating across them compares scores computed different ways"
        )

    metric_names = sorted({name for record in records for name in record.metrics})
    summaries: dict[str, MetricSummary] = {}
    for name in metric_names:
        values = [
            record.metrics[name]
            for record in records
            if record.metrics.get(name) is not None
        ]
        summaries[name] = MetricSummary(
            name=name,
            mean=(sum(values) / len(values)) if values else None,
            n_applicable=len(values),
            n_total=len(records),
        )

    terminations: dict[str, int] = {}
    for record in records:
        terminations[record.termination] = terminations.get(record.termination, 0) + 1

    return RunSummary(
        scorer_version=versions.pop(),
        n_claims=len(records),
        metrics=summaries,
        terminations=terminations,
        total_cost_usd=sum(record.metrics.get("cost_usd") or 0.0 for record in records),
    )
