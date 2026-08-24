"""What a claim actually asserts, and whether a computed number satisfies it.

Kept separate from computation on purpose. The evaluator returns a single float
whose *meaning* is assertion-specific -- a percent change, a level, a signed
difference, a rank -- and the assertion decides whether that number vindicates the
claim. Nothing here touches a database, so the whole judgment layer is testable
against fixtures.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChangeAssertion",
    "LevelAssertion",
    "ComparisonAssertion",
    "RankAssertion",
    "Assertion",
]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChangeAssertion(_Base):
    """'Shootings are up 30% this year.'

    ``value`` is the percent change. A claim that states a magnitude must match it
    within ``tolerance_pct``; a claim that only states a direction ('crime is up')
    leaves ``stated_pct`` unset and needs only the sign.
    """

    kind: Literal["change"] = "change"
    direction: Literal["up", "down", "flat"]
    stated_pct: float | None = None
    tolerance_pct: float = Field(default=5.0, gt=0)
    flat_band_pct: float = Field(default=1.0, ge=0)
    """Percent change treated as no change. Without a band, 'flat' is unfalsifiable."""

    def holds(self, value: float) -> bool:
        if self.direction == "up" and not value > self.flat_band_pct:
            return False
        if self.direction == "down" and not value < -self.flat_band_pct:
            return False
        if self.direction == "flat" and abs(value) > self.flat_band_pct:
            return False
        if self.stated_pct is not None:
            return abs(value - self.stated_pct) <= self.tolerance_pct
        return True


class LevelAssertion(_Base):
    """'There were 116 shootings in 2025.' ``value`` is the computed level."""

    kind: Literal["level"] = "level"
    stated_value: float
    rel_tol: float = Field(default=0.05, ge=0)
    abs_tol: float = Field(default=0.0, ge=0)

    def holds(self, value: float) -> bool:
        tolerance = max(self.abs_tol, abs(self.stated_value) * self.rel_tol)
        return abs(value - self.stated_value) <= tolerance


class ComparisonAssertion(_Base):
    """'Dorchester has more crime than Back Bay.'

    ``value`` is the signed difference (subject minus reference), in whatever units
    the spec's denominator implies -- which is exactly why the denominator choice
    can flip this one.
    """

    kind: Literal["comparison"] = "comparison"
    direction: Literal["greater", "less", "equal"]
    margin: float = Field(default=0.0, ge=0)
    """Difference treated as a tie."""

    def holds(self, value: float) -> bool:
        if self.direction == "greater":
            return value > self.margin
        if self.direction == "less":
            return value < -self.margin
        return abs(value) <= self.margin


class RankAssertion(_Base):
    """'Roxbury is the most dangerous neighborhood.' ``value`` is the 1-based rank."""

    kind: Literal["rank"] = "rank"
    stated_rank: int = Field(default=1, ge=1)
    tolerance: int = Field(default=0, ge=0)
    """Ranks within this distance count as satisfying the claim. Non-zero is
    appropriate when the claim is loose ('one of the highest')."""

    def holds(self, value: float) -> bool:
        return abs(round(value) - self.stated_rank) <= self.tolerance


Assertion = Annotated[
    Union[ChangeAssertion, LevelAssertion, ComparisonAssertion, RankAssertion],
    Field(discriminator="kind"),
]
