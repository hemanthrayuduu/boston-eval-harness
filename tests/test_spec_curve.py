"""Tests for the specification-curve ground-truth engine.

All offline: the evaluator is a fixture, so the judgment layer is exercised
without a database. Real evaluators go through DuckDB, but nothing in the label
derivation should care.
"""

from __future__ import annotations

import pytest

from specs.assertions import (
    ChangeAssertion,
    ComparisonAssertion,
    LevelAssertion,
    RankAssertion,
)
from specs.curve import compute_curve
from specs.dimensions import DIMENSIONS, Dimension, Option, get_dimension
from specs.labels import (
    CONTRADICT_THRESHOLD,
    SUPPORT_THRESHOLD,
    Label,
    derive_label,
)
from specs.space import MAX_SPECS_PER_CLAIM, SpecSpace


class TestDimensions:
    def test_every_shipped_option_carries_a_justification(self) -> None:
        for dimension in DIMENSIONS.values():
            for option in dimension.options:
                assert len(option.justification) >= 20, (
                    f"{dimension.key}.{option.key} is undefended"
                )

    def test_undefended_option_rejected(self) -> None:
        with pytest.raises(ValueError, match="justification"):
            Option("acs_5yr", "because")

    def test_dimension_needs_at_least_two_options(self) -> None:
        with pytest.raises(ValueError, match="at least two"):
            Dimension(
                key="x",
                question="?",
                options=(Option("only", "a" * 25),),
            )

    def test_duplicate_option_keys_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            Dimension(
                key="x",
                question="?",
                options=(Option("a", "j" * 25), Option("a", "k" * 25)),
            )

    def test_unknown_dimension_lookup_lists_available(self) -> None:
        with pytest.raises(KeyError, match="denominator"):
            get_dimension("nonexistent")


class TestSpecSpace:
    def test_size_is_the_cross_product(self) -> None:
        space = SpecSpace.build(
            denominator=("none", "acs_5yr", "decennial"),
            window=("ytd_vs_ytd", "trailing_12mo"),
        )
        assert space.size == 6
        assert len(space.enumerate()) == 6

    def test_pinned_dimension_is_recorded_not_dropped(self) -> None:
        space = SpecSpace.build(denominator=("none", "acs_5yr"), window="ytd_vs_ytd")
        assert space.size == 2
        assert space.varying_dimensions == ("denominator",)
        # The pinned choice still appears in every spec: a decision, not an omission.
        assert all(spec["window"] == "ytd_vs_ytd" for spec in space.enumerate())

    def test_specs_are_unique_and_hashable(self) -> None:
        space = SpecSpace.build(
            denominator=("none", "acs_5yr"), geography=("bpd_district", "tract_rollup")
        )
        specs = space.enumerate()
        assert len(set(specs)) == len(specs) == 4

    def test_spec_id_is_stable_and_readable(self) -> None:
        space = SpecSpace.build(denominator="acs_5yr", window="trailing_12mo")
        assert space.enumerate()[0].spec_id == "denominator=acs_5yr|window=trailing_12mo"

    def test_oversized_space_rejected(self) -> None:
        with pytest.raises(ValueError, match="over the cap"):
            SpecSpace.build(
                measure=DIMENSIONS["measure"].option_keys,
                window=DIMENSIONS["window"].option_keys,
                geography=DIMENSIONS["geography"].option_keys,
                denominator=DIMENSIONS["denominator"].option_keys,
                offense_set=DIMENSIONS["offense_set"].option_keys,
            )

    def test_full_cross_product_would_exceed_cap(self) -> None:
        """Guards the cap against being quietly raised past what it is protecting."""
        total = 1
        for dimension in DIMENSIONS.values():
            total *= len(dimension.options)
        assert total > MAX_SPECS_PER_CLAIM

    def test_unknown_option_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown options"):
            SpecSpace.build(denominator=("none", "per_household"))

    def test_unknown_dimension_rejected(self) -> None:
        with pytest.raises(KeyError):
            SpecSpace.build(phase_of_moon=("waxing", "waning"))

    def test_empty_space_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one dimension"):
            SpecSpace(dimensions=())


class TestAssertions:
    @pytest.mark.parametrize(
        "value,expected",
        [(30.0, True), (33.0, True), (27.0, True), (40.0, False), (-30.0, False)],
    )
    def test_change_with_stated_magnitude(self, value: float, expected: bool) -> None:
        assert ChangeAssertion(direction="up", stated_pct=30.0).holds(value) is expected

    @pytest.mark.parametrize("value,expected", [(0.5, False), (5.0, True), (-5.0, False)])
    def test_change_direction_only(self, value: float, expected: bool) -> None:
        # No stated magnitude: only the sign has to hold, outside the flat band.
        assert ChangeAssertion(direction="up").holds(value) is expected

    def test_flat_requires_a_band_to_be_falsifiable(self) -> None:
        flat = ChangeAssertion(direction="flat", flat_band_pct=1.0)
        assert flat.holds(0.4) is True
        assert flat.holds(3.0) is False

    def test_level_uses_relative_tolerance(self) -> None:
        assertion = LevelAssertion(stated_value=116, rel_tol=0.05)
        assert assertion.holds(116) is True
        assert assertion.holds(121) is True  # 4.3% off
        assert assertion.holds(130) is False

    def test_level_abs_tol_dominates_near_zero(self) -> None:
        assertion = LevelAssertion(stated_value=0.0, rel_tol=0.05, abs_tol=2.0)
        assert assertion.holds(1.5) is True
        assert assertion.holds(3.0) is False

    def test_comparison_respects_margin(self) -> None:
        assertion = ComparisonAssertion(direction="greater", margin=0.5)
        assert assertion.holds(1.0) is True
        assert assertion.holds(0.2) is False
        assert assertion.holds(-1.0) is False

    def test_rank_tolerance(self) -> None:
        assert RankAssertion(stated_rank=1).holds(1) is True
        assert RankAssertion(stated_rank=1).holds(2) is False
        # 'one of the highest' is a looser claim and should be scored as one.
        assert RankAssertion(stated_rank=1, tolerance=2).holds(3) is True


def evaluator_from(values: dict[str, float | None]):
    """Build an evaluator that looks a value up by spec_id."""

    def evaluate(claim_id: str, spec) -> float | None:
        return values[spec.spec_id]

    return evaluate


class TestSpecCurve:
    def test_counts_and_support_fraction(self) -> None:
        space = SpecSpace.build(denominator=("none", "acs_5yr", "decennial", "daytime_population"))
        curve = compute_curve(
            "c1",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from(
                {
                    "denominator=none": 10.0,
                    "denominator=acs_5yr": -2.0,
                    "denominator=decennial": -1.0,
                    "denominator=daytime_population": -5.0,
                }
            ),
        )
        assert curve.n_specs == 4
        assert curve.n_computable == 4
        assert curve.n_supporting == 1
        assert curve.n_contradicting == 3
        assert curve.support_fraction == 0.25
        assert curve.value_range == (-5.0, 10.0)

    def test_not_computable_is_excluded_not_counted_as_false(self) -> None:
        """The distinction the whole label space rests on."""
        space = SpecSpace.build(denominator=("none", "acs_5yr", "decennial"))
        curve = compute_curve(
            "c1",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from(
                {
                    "denominator=none": 10.0,
                    "denominator=acs_5yr": None,
                    "denominator=decennial": None,
                }
            ),
        )
        assert curve.n_computable == 1
        assert curve.n_supporting == 1
        assert curve.n_contradicting == 0
        # 1/1, not 1/3 -- uncomputable specs must not dilute support.
        assert curve.support_fraction == 1.0

    def test_support_fraction_is_none_when_nothing_computes(self) -> None:
        space = SpecSpace.build(denominator=("none", "acs_5yr"))
        curve = compute_curve(
            "c1",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from({"denominator=none": None, "denominator=acs_5yr": None}),
        )
        assert curve.support_fraction is None
        assert curve.value_range is None

    def test_driver_influence_isolates_the_deciding_dimension(self) -> None:
        space = SpecSpace.build(
            denominator=("none", "acs_5yr"), window=("ytd_vs_ytd", "trailing_12mo")
        )
        # Raw counts support the claim, per-capita refutes it, regardless of window.
        curve = compute_curve(
            "c1",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from(
                {
                    "denominator=none|window=ytd_vs_ytd": 10.0,
                    "denominator=none|window=trailing_12mo": 12.0,
                    "denominator=acs_5yr|window=ytd_vs_ytd": -3.0,
                    "denominator=acs_5yr|window=trailing_12mo": -4.0,
                }
            ),
        )
        influence = curve.driver_influence()
        assert influence["denominator"] == 1.0
        assert influence["window"] == 0.0
        assert curve.dominant_driver() == "denominator"

    def test_no_dominant_driver_when_verdict_is_unanimous(self) -> None:
        space = SpecSpace.build(denominator=("none", "acs_5yr"))
        curve = compute_curve(
            "c1",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from({"denominator=none": 10.0, "denominator=acs_5yr": 8.0}),
        )
        assert curve.dominant_driver() is None


def curve_with_support(n_supporting: int, n_total: int):
    """A curve with a chosen support fraction, for threshold tests."""
    space = SpecSpace.build(
        denominator=("none", "acs_5yr"),
        window=DIMENSIONS["window"].option_keys[: n_total // 2],
    )
    specs = space.enumerate()
    assert len(specs) == n_total
    values = {
        spec.spec_id: (1.0 if i < n_supporting else -1.0) for i, spec in enumerate(specs)
    }
    return compute_curve(
        "c1", ComparisonAssertion(direction="greater"), space, evaluator_from(values)
    )


class TestLabelDerivation:
    def test_unanimous_support_is_supported(self) -> None:
        decision = derive_label(curve_with_support(8, 8))
        assert decision.final == Label.SUPPORTED
        assert decision.provenance == "derived"
        assert not decision.is_spec_sensitive

    def test_unanimous_failure_is_contradicted(self) -> None:
        assert derive_label(curve_with_support(0, 8)).final == Label.CONTRADICTED

    def test_split_verdict_is_underdetermined(self) -> None:
        decision = derive_label(curve_with_support(4, 8))
        assert decision.final == Label.UNDERDETERMINED
        assert decision.is_spec_sensitive
        assert "flips across defensible choices" in decision.rationale

    def test_one_failure_in_eight_is_not_supported(self) -> None:
        """The strictness is the point: 7/8 is fragile, not supported."""
        assert derive_label(curve_with_support(7, 8)).final == Label.UNDERDETERMINED

    def test_thresholds_are_the_committed_values(self) -> None:
        # These define the answer key, so they are asserted rather than assumed.
        assert SUPPORT_THRESHOLD == 0.95
        assert CONTRADICT_THRESHOLD == 0.05

    @pytest.mark.parametrize(
        "n_supporting,expected",
        [
            (16, Label.SUPPORTED),
            (15, Label.UNDERDETERMINED),  # 0.9375 -- fragile, not supported
            (1, Label.UNDERDETERMINED),  # 0.0625 -- fragile, not contradicted
            (0, Label.CONTRADICTED),
        ],
    )
    def test_near_threshold_verdicts_fall_to_underdetermined(
        self, n_supporting: int, expected: Label
    ) -> None:
        """A single dissenting specification is enough to lose a clean verdict.

        With 16 specifications the reachable fractions bracket the thresholds
        without landing on them, which is the case that matters: 15/16 support is
        strong evidence and still not 'supported'.
        """
        space = SpecSpace.build(
            measure=DIMENSIONS["measure"].option_keys,
            window=DIMENSIONS["window"].option_keys,
        )
        specs = space.enumerate()
        assert len(specs) == 16
        values = {
            spec.spec_id: (1.0 if i < n_supporting else -1.0)
            for i, spec in enumerate(specs)
        }
        curve = compute_curve(
            "c1", ComparisonAssertion(direction="greater"), space, evaluator_from(values)
        )
        assert derive_label(curve).final == expected

    def test_nothing_computable_is_unverifiable(self) -> None:
        space = SpecSpace.build(denominator=("none", "acs_5yr"))
        curve = compute_curve(
            "cross_city",
            ComparisonAssertion(direction="less"),
            space,
            evaluator_from({"denominator=none": None, "denominator=acs_5yr": None}),
        )
        decision = derive_label(curve)
        assert decision.final == Label.UNVERIFIABLE
        assert decision.support_fraction is None
        assert "cannot address this claim" in decision.rationale

    def test_decision_carries_the_evidence(self) -> None:
        decision = derive_label(curve_with_support(4, 8))
        assert decision.n_specs == 8
        assert decision.n_computable == 8
        assert decision.value_range == (-1.0, 1.0)
        assert decision.dominant_driver is not None


class TestHandOverride:
    def test_misleading_may_be_added_by_hand(self) -> None:
        decision = derive_label(curve_with_support(8, 8), hand_label=Label.MISLEADING)
        assert decision.final == Label.MISLEADING
        # The derived verdict survives alongside it: the arithmetic did hold.
        assert decision.derived == Label.SUPPORTED
        assert decision.provenance == "hand_override"

    def test_unverifiable_may_be_added_by_hand(self) -> None:
        decision = derive_label(curve_with_support(8, 8), hand_label=Label.UNVERIFIABLE)
        assert decision.final == Label.UNVERIFIABLE
        assert decision.derived == Label.SUPPORTED

    @pytest.mark.parametrize(
        "label", [Label.SUPPORTED, Label.CONTRADICTED, Label.UNDERDETERMINED]
    )
    def test_mechanical_labels_cannot_be_overridden_by_hand(self, label: Label) -> None:
        """Otherwise opinion re-enters the part of the answer key that is computed."""
        with pytest.raises(ValueError, match="not permitted"):
            derive_label(curve_with_support(4, 8), hand_label=label)

    def test_spec_sensitivity_tracks_the_derived_label(self) -> None:
        # A hand 'misleading' overlay must not hide that the claim was also fragile.
        decision = derive_label(curve_with_support(4, 8), hand_label=Label.MISLEADING)
        assert decision.final == Label.MISLEADING
        assert decision.is_spec_sensitive


class TestRealisticClaim:
    def test_raw_count_versus_rate_flips_a_neighborhood_comparison(self) -> None:
        """'Roxbury has more crime than Back Bay.'

        True on raw counts, false per capita. This is the canonical trap in the
        source data, and the curve should call it underdetermined and name the
        denominator as the reason -- not quietly pick a side.
        """
        space = SpecSpace.build(
            denominator=("none", "acs_5yr", "decennial"),
            offense_set=("part_one", "exclude_non_crime"),
            geography="neighborhood_boundary",
        )
        assert space.size == 6

        counts = {"none": 1200.0, "acs_5yr": -4.2, "decennial": -3.8}
        values = {
            spec.spec_id: counts[spec["denominator"]] for spec in space.enumerate()
        }

        curve = compute_curve(
            "roxbury_vs_backbay",
            ComparisonAssertion(direction="greater"),
            space,
            evaluator_from(values),
        )
        decision = derive_label(curve)

        assert decision.final == Label.UNDERDETERMINED
        assert decision.is_spec_sensitive
        assert decision.dominant_driver == "denominator"
        assert curve.driver_influence()["offense_set"] == 0.0
        assert decision.support_fraction == pytest.approx(1 / 3)

    def test_cross_city_claim_is_unverifiable_not_contradicted(self) -> None:
        """'Boston is the safest major city.'

        Nothing in a Boston-only snapshot can compute this. The benchmark must
        say so rather than scoring it false, because a model that answers
        'contradicted' here is wrong in a way that matters.
        """
        space = SpecSpace.build(
            measure=("shooting_incidents", "victims_struck"),
            denominator=("acs_5yr", "decennial"),
        )
        curve = compute_curve(
            "safest_major_city",
            RankAssertion(stated_rank=1),
            space,
            evaluator_from({spec.spec_id: None for spec in space.enumerate()}),
        )
        decision = derive_label(curve)
        assert decision.final == Label.UNVERIFIABLE
        assert decision.derived == Label.UNVERIFIABLE
