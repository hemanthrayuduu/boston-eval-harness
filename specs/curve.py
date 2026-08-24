"""Run a claim under every defensible specification and collect the outcomes.

The output is the project's ground truth. It is also the artifact that makes a
finding legible to someone who does not trust you: a reader can see the claim
hold under one set of defensible choices and fail under another, without taking
anyone's word for it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from specs.assertions import Assertion
from specs.space import Spec, SpecSpace

__all__ = ["Evaluator", "SpecOutcome", "SpecCurve", "compute_curve"]


class Evaluator(Protocol):
    """Computes the claim's quantity under one specification.

    Returns ``None`` when the claim is *not computable* under that specification --
    a missing denominator, a measure the data does not record, a window that
    predates the series. Not computable is a third outcome, distinct from false,
    and collapsing the two would turn every unanswerable claim into a refuted one.
    """

    def __call__(self, claim_id: str, spec: Spec) -> float | None: ...


@dataclass(frozen=True)
class SpecOutcome:
    spec: Spec
    value: float | None
    holds: bool | None
    """None when the claim is not computable under this spec."""

    @property
    def computable(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class SpecCurve:
    claim_id: str
    outcomes: tuple[SpecOutcome, ...]
    varying_dimensions: tuple[str, ...]

    @property
    def n_specs(self) -> int:
        return len(self.outcomes)

    @property
    def computable(self) -> tuple[SpecOutcome, ...]:
        return tuple(o for o in self.outcomes if o.computable)

    @property
    def n_computable(self) -> int:
        return len(self.computable)

    @property
    def n_supporting(self) -> int:
        return sum(1 for o in self.outcomes if o.holds is True)

    @property
    def n_contradicting(self) -> int:
        return sum(1 for o in self.outcomes if o.holds is False)

    @property
    def support_fraction(self) -> float | None:
        """Share of *computable* specifications under which the claim holds.

        ``None`` when nothing is computable -- which is the signal for
        ``unverifiable``, not for a support fraction of zero.
        """
        if self.n_computable == 0:
            return None
        return self.n_supporting / self.n_computable

    @property
    def value_range(self) -> tuple[float, float] | None:
        values = [o.value for o in self.computable if o.value is not None]
        return (min(values), max(values)) if values else None

    def driver_influence(self) -> dict[str, float]:
        """How much each dimension alone moves the verdict.

        For each varying dimension, computes the support rate within each of its
        options and returns the spread (max minus min). A dimension scoring 1.0
        flips the claim by itself; 0.0 means it does not matter here.

        This is what makes a finding actionable rather than merely true. "Your
        answer depends entirely on which population denominator you picked" is a
        sentence someone can act on.
        """
        influence: dict[str, float] = {}
        for dimension in self.varying_dimensions:
            grouped: dict[str, list[bool]] = defaultdict(list)
            for outcome in self.outcomes:
                if outcome.holds is None:
                    continue
                option = outcome.spec.get(dimension)
                if option is not None:
                    grouped[option].append(outcome.holds)

            rates = [sum(v) / len(v) for v in grouped.values() if v]
            influence[dimension] = max(rates) - min(rates) if len(rates) >= 2 else 0.0
        return influence

    def dominant_driver(self) -> str | None:
        """The dimension most responsible for the claim's instability, if any."""
        influence = self.driver_influence()
        if not influence:
            return None
        best = max(influence, key=lambda k: influence[k])
        return best if influence[best] > 0 else None


def compute_curve(
    claim_id: str,
    assertion: Assertion,
    space: SpecSpace,
    evaluator: Evaluator,
) -> SpecCurve:
    """Evaluate ``assertion`` under every specification in ``space``."""
    outcomes = []
    for spec in space.enumerate():
        value = evaluator(claim_id, spec)
        holds = None if value is None else assertion.holds(value)
        outcomes.append(SpecOutcome(spec=spec, value=value, holds=holds))

    return SpecCurve(
        claim_id=claim_id,
        outcomes=tuple(outcomes),
        varying_dimensions=space.varying_dimensions,
    )
