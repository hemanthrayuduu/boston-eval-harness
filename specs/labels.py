"""Deriving a label from a spec curve.

Three of the five labels are computed from the curve and need no human. That is
the point of the whole design: ``underdetermined`` -- the interesting label, the
one a model is most likely to get wrong -- is a property of the claim against the
data, not a judgment call, so it carries no annotator noise and needs no judge.

The two that do need a human are the two the curve cannot express:

``misleading``
    The arithmetic holds under every specification and the claim is still not
    licensed -- a raw count presented as risk, a ranking over three incidents,
    reports presented as incidence.

``unverifiable``
    Usually derived (nothing computes), but sometimes needs a human, because a
    claim can be computable and still out of scope -- a cross-city comparison
    where the local half computes fine.

A hand label may only ever *add* one of those two. It may not overturn a derived
``supported`` into ``contradicted`` or collapse an ``underdetermined`` into a
clean verdict, because that would smuggle opinion back into the part of the label
space that is supposed to be mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from specs.curve import SpecCurve

__all__ = ["Label", "LabelDecision", "derive_label", "SUPPORT_THRESHOLD", "CONTRADICT_THRESHOLD"]


# Committed thresholds. Deliberately strict: a claim that fails under even one in
# twenty defensible specifications is not "supported", it is fragile, and the
# whole project exists to say so. Changing these changes the answer key, so they
# are versioned with the scorer.
SUPPORT_THRESHOLD = 0.95
CONTRADICT_THRESHOLD = 0.05


class Label(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNDERDETERMINED = "underdetermined"
    MISLEADING = "misleading"
    UNVERIFIABLE = "unverifiable"


HAND_ONLY_LABELS = frozenset({Label.MISLEADING, Label.UNVERIFIABLE})


@dataclass(frozen=True)
class LabelDecision:
    """A label plus everything needed to argue with it."""

    final: Label
    derived: Label
    provenance: str
    """``derived`` or ``hand_override``."""
    support_fraction: float | None
    n_specs: int
    n_computable: int
    dominant_driver: str | None
    value_range: tuple[float, float] | None
    rationale: str

    @property
    def is_spec_sensitive(self) -> bool:
        """Whether the claim's truth depends on defensible analytic choices.

        The target of the benchmark's headline metric: of the claims that are
        spec-sensitive, how many does a model notice?
        """
        return self.derived == Label.UNDERDETERMINED


def derive_label(curve: SpecCurve, hand_label: Label | None = None) -> LabelDecision:
    """Derive a label from ``curve``, optionally overlaid with a hand label."""
    support = curve.support_fraction
    driver = curve.dominant_driver()

    if curve.n_computable == 0:
        derived = Label.UNVERIFIABLE
        rationale = (
            f"not computable under any of {curve.n_specs} specifications; "
            "the data cannot address this claim"
        )
    elif support is not None and support >= SUPPORT_THRESHOLD:
        derived = Label.SUPPORTED
        rationale = (
            f"holds under {curve.n_supporting}/{curve.n_computable} computable "
            f"specifications (>= {SUPPORT_THRESHOLD:.0%})"
        )
    elif support is not None and support <= CONTRADICT_THRESHOLD:
        derived = Label.CONTRADICTED
        rationale = (
            f"fails under {curve.n_contradicting}/{curve.n_computable} computable "
            f"specifications (<= {CONTRADICT_THRESHOLD:.0%} support)"
        )
    else:
        derived = Label.UNDERDETERMINED
        driver_note = f"; driven mainly by '{driver}'" if driver else ""
        rationale = (
            f"holds under {curve.n_supporting}/{curve.n_computable} computable "
            f"specifications -- the verdict flips across defensible choices{driver_note}"
        )

    final = derived
    provenance = "derived"

    if hand_label is not None:
        if hand_label not in HAND_ONLY_LABELS:
            raise ValueError(
                f"hand label {hand_label!r} is not permitted: only "
                f"{sorted(HAND_ONLY_LABELS)} may be applied by hand. The mechanical "
                "labels come from the curve, and overriding them would put opinion "
                "back into the part of the answer key that is meant to be computed."
            )
        final = hand_label
        provenance = "hand_override"
        rationale = f"{rationale}; hand-labelled {hand_label.value}"

    return LabelDecision(
        final=final,
        derived=derived,
        provenance=provenance,
        support_fraction=support,
        n_specs=curve.n_specs,
        n_computable=curve.n_computable,
        dominant_driver=driver,
        value_range=curve.value_range,
        rationale=rationale,
    )
