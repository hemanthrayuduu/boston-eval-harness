"""Scoring must be a pure function of the trace, and metric applicability must be
decided by ground truth rather than by what the model chose to answer."""

from __future__ import annotations

import random

import pytest

from harness.score import (
    SCORER_VERSION,
    GroundTruth,
    score,
    scores_to_jsonl,
    summarize,
)
from harness.trace import (
    LLMCall,
    SubmittedVerdict,
    Termination,
    ToolCall,
    Trajectory,
    TraceWriter,
    read_trajectories,
)
from specs.labels import Label, LabelDecision


def truth(
    claim_id: str = "c1",
    derived: Label = Label.UNDERDETERMINED,
    final: Label | None = None,
    value_range: tuple[float, float] | None = (-5.0, 10.0),
    limitations: frozenset[str] = frozenset(),
) -> GroundTruth:
    decision = LabelDecision(
        final=final or derived,
        derived=derived,
        provenance="derived",
        support_fraction=0.5,
        n_specs=8,
        n_computable=8,
        dominant_driver="denominator",
        value_range=value_range,
        rationale="fixture",
    )
    return GroundTruth(
        claim_id=claim_id, decision=decision, required_limitation_ids=limitations
    )


def trajectory(
    claim_id: str = "c1",
    verdict: str | None = "underdetermined",
    *,
    spec_sensitive: bool = False,
    computed_value: float | None = None,
    limitation_ids: tuple[str, ...] = (),
    termination: Termination = Termination.SUBMITTED,
    **overrides,
) -> Trajectory:
    submitted = (
        None
        if verdict is None
        else SubmittedVerdict(
            verdict=verdict,
            spec_sensitive=spec_sensitive,
            computed_value=computed_value,
            limitation_ids=limitation_ids,
        )
    )
    base = {
        "claim_id": claim_id,
        "run_id": "r" * 8,
        "config_hash": "h" * 8,
        "verdict": submitted,
        "termination": termination,
        "tool_calls": (ToolCall(step=0, tool="query", ok=True),),
        "llm_calls": (
            LLMCall(
                step=0, model="claude-opus-5-2026-01-15", prompt_hash="p", cost_usd=0.01
            ),
        ),
    }
    base.update(overrides)
    return Trajectory(**base)


class TestPurity:
    def test_scoring_twice_is_byte_identical(self) -> None:
        trajectories = [trajectory(f"c{i}") for i in range(10)]
        truths = {f"c{i}": truth(f"c{i}") for i in range(10)}
        assert scores_to_jsonl(score(trajectories, truths)) == scores_to_jsonl(
            score(trajectories, truths)
        )

    def test_input_order_does_not_change_output(self) -> None:
        """Concurrency means the runner finishes claims in arbitrary order. If that
        leaked into scoring, re-scoring would not be reproducible."""
        trajectories = [trajectory(f"c{i}") for i in range(20)]
        truths = {f"c{i}": truth(f"c{i}") for i in range(20)}

        baseline = scores_to_jsonl(score(trajectories, truths))
        shuffled = list(trajectories)
        random.Random(0).shuffle(shuffled)
        assert scores_to_jsonl(score(shuffled, truths)) == baseline

    def test_replicates_are_ordered_deterministically(self) -> None:
        trajectories = [
            trajectory("c1", replicate_index=i) for i in reversed(range(5))
        ]
        records = score(trajectories, {"c1": truth("c1")})
        assert [r.replicate_index for r in records] == [0, 1, 2, 3, 4]

    def test_scoring_from_disk_matches_scoring_in_memory(self, tmp_path) -> None:
        """The contract: every number is reconstructible from trajectories.jsonl."""
        trajectories = [trajectory(f"c{i}") for i in range(5)]
        truths = {f"c{i}": truth(f"c{i}") for i in range(5)}

        path = tmp_path / "trajectories.jsonl"
        TraceWriter(path).extend(trajectories)

        from_disk = score(list(read_trajectories(path)), truths)
        assert scores_to_jsonl(from_disk) == scores_to_jsonl(score(trajectories, truths))

    def test_missing_ground_truth_is_loud(self) -> None:
        with pytest.raises(KeyError, match="no ground truth"):
            score([trajectory("orphan")], {})


class TestApplicability:
    def test_spec_sensitivity_only_applies_to_sensitive_claims(self) -> None:
        sensitive = score(
            [trajectory()], {"c1": truth(derived=Label.UNDERDETERMINED)}
        )[0]
        assert sensitive.metrics["spec_sensitivity_recall"] is not None

        determinate = score([trajectory()], {"c1": truth(derived=Label.SUPPORTED)})[0]
        assert determinate.metrics["spec_sensitivity_recall"] is None
        assert determinate.metrics["overclaim_rate"] is None

    def test_over_abstention_only_applies_to_determinate_claims(self) -> None:
        determinate = score([trajectory()], {"c1": truth(derived=Label.SUPPORTED)})[0]
        assert determinate.metrics["over_abstention_rate"] is not None

        sensitive = score([trajectory()], {"c1": truth(derived=Label.UNDERDETERMINED)})[0]
        assert sensitive.metrics["over_abstention_rate"] is None

    def test_applicability_is_not_controlled_by_the_model(self) -> None:
        """Whatever the model answers, the metric set is the same -- otherwise a
        model could dodge a metric by refusing to answer."""
        truths = {"c1": truth(derived=Label.UNDERDETERMINED)}
        answered = score([trajectory(verdict="supported")], truths)[0]
        refused = score([trajectory(verdict=None, termination=Termination.STEP_BUDGET)], truths)[0]

        applicable = lambda r: {k for k, v in r.metrics.items() if v is not None}  # noqa: E731
        assert applicable(answered) == applicable(refused)


class TestMetrics:
    def test_verdict_accuracy(self) -> None:
        truths = {"c1": truth(derived=Label.UNDERDETERMINED)}
        right = score([trajectory(verdict="underdetermined")], truths)[0]
        wrong = score([trajectory(verdict="supported")], truths)[0]
        assert right.metrics["verdict_accuracy"] == 1.0
        assert wrong.metrics["verdict_accuracy"] == 0.0

    def test_running_out_of_steps_scores_zero_not_none(self) -> None:
        """Giving up is a wrong answer. Scoring it None would let a model raise its
        average by abandoning the claims it finds hard."""
        record = score(
            [trajectory(verdict=None, termination=Termination.STEP_BUDGET)],
            {"c1": truth(derived=Label.UNDERDETERMINED)},
        )[0]
        assert record.metrics["verdict_accuracy"] == 0.0
        assert record.metrics["spec_sensitivity_recall"] == 0.0

    def test_spec_sensitivity_credited_for_flag_or_verdict(self) -> None:
        truths = {"c1": truth(derived=Label.UNDERDETERMINED)}
        by_verdict = score([trajectory(verdict="underdetermined")], truths)[0]
        by_flag = score(
            [trajectory(verdict="supported", spec_sensitive=True)], truths
        )[0]
        missed = score([trajectory(verdict="supported")], truths)[0]

        assert by_verdict.metrics["spec_sensitivity_recall"] == 1.0
        assert by_flag.metrics["spec_sensitivity_recall"] == 1.0
        assert missed.metrics["spec_sensitivity_recall"] == 0.0

    def test_overclaim_and_over_abstention_are_complementary_failures(self) -> None:
        """The pairing that stops a metric being gamed.

        A model that always answers 'underdetermined' scores a perfect zero on
        overclaiming and a perfect one on over-abstention. Reporting either alone
        would make that model look good.
        """
        always_abstains = trajectory(verdict="underdetermined")

        on_sensitive = score(
            [always_abstains], {"c1": truth(derived=Label.UNDERDETERMINED)}
        )[0]
        on_determinate = score(
            [always_abstains], {"c1": truth(derived=Label.SUPPORTED)}
        )[0]

        assert on_sensitive.metrics["overclaim_rate"] == 0.0
        assert on_determinate.metrics["over_abstention_rate"] == 1.0

    def test_overclaim_fires_on_a_confident_wrong_answer(self) -> None:
        record = score(
            [trajectory(verdict="supported")], {"c1": truth(derived=Label.UNDERDETERMINED)}
        )[0]
        assert record.metrics["overclaim_rate"] == 1.0

    @pytest.mark.parametrize(
        "value,expected", [(0.0, 1.0), (-5.0, 1.0), (10.0, 1.0), (11.0, 0.0), (-6.0, 0.0)]
    )
    def test_value_in_range_includes_endpoints(self, value: float, expected: float) -> None:
        record = score(
            [trajectory(computed_value=value)], {"c1": truth(value_range=(-5.0, 10.0))}
        )[0]
        assert record.metrics["value_in_range"] == expected

    def test_value_not_applicable_without_a_range(self) -> None:
        record = score([trajectory(computed_value=3.0)], {"c1": truth(value_range=None)})[0]
        assert record.metrics["value_in_range"] is None

    def test_uncomputed_value_on_a_computable_claim_scores_zero(self) -> None:
        record = score([trajectory(computed_value=None)], {"c1": truth()})[0]
        assert record.metrics["value_in_range"] == 0.0

    def test_limitation_citation_f1(self) -> None:
        required = frozenset({"LIM-SMALL-N", "LIM-REPORTS-VS-INCIDENCE"})

        perfect = score(
            [trajectory(limitation_ids=tuple(required))], {"c1": truth(limitations=required)}
        )[0]
        assert perfect.metrics["limitation_citation_f1"] == 1.0

        half = score(
            [trajectory(limitation_ids=("LIM-SMALL-N",))],
            {"c1": truth(limitations=required)},
        )[0]
        assert half.metrics["limitation_citation_f1"] == pytest.approx(2 / 3)

        wrong = score(
            [trajectory(limitation_ids=("LIM-UNRELATED",))],
            {"c1": truth(limitations=required)},
        )[0]
        assert wrong.metrics["limitation_citation_f1"] == 0.0

    def test_citation_not_applicable_when_nothing_is_required(self) -> None:
        record = score([trajectory(limitation_ids=("LIM-X",))], {"c1": truth()})[0]
        assert record.metrics["limitation_citation_f1"] is None


class TestSummary:
    def test_means_computed_over_applicable_records_only(self) -> None:
        trajectories = [
            trajectory("c1", verdict="underdetermined"),
            trajectory("c2", verdict="supported"),
        ]
        truths = {
            "c1": truth("c1", derived=Label.UNDERDETERMINED),
            "c2": truth("c2", derived=Label.SUPPORTED),
        }
        summary = summarize(score(trajectories, truths))

        assert summary.n_claims == 2
        assert summary.metrics["verdict_accuracy"].mean == 1.0
        # Each conditional metric applies to exactly one of the two claims.
        assert summary.metrics["spec_sensitivity_recall"].n_applicable == 1
        assert summary.metrics["over_abstention_rate"].n_applicable == 1
        assert summary.total_cost_usd == pytest.approx(0.02)

    def test_terminations_are_counted(self) -> None:
        trajectories = [
            trajectory("c1"),
            trajectory("c2", verdict=None, termination=Termination.STEP_BUDGET),
            trajectory("c3", verdict=None, termination=Termination.STEP_BUDGET),
        ]
        truths = {f"c{i}": truth(f"c{i}") for i in (1, 2, 3)}
        summary = summarize(score(trajectories, truths))
        assert summary.terminations == {"submitted": 1, "step_budget_exhausted": 2}

    def test_empty_input(self) -> None:
        assert summarize([]).n_claims == 0

    def test_mixed_scorer_versions_refused(self) -> None:
        """Aggregating across scorer versions averages numbers computed different
        ways, which is how a regression gate reports a change that never happened."""
        truths = {"c1": truth("c1")}
        old = score([trajectory("c1")], truths, scorer_version="0.9.0")
        new = score([trajectory("c1")], truths, scorer_version="1.0.0")
        with pytest.raises(ValueError, match="multiple scorer versions"):
            summarize(old + new)

    def test_summary_serialises_with_sorted_keys(self) -> None:
        summary = summarize(score([trajectory("c1")], {"c1": truth("c1")}))
        payload = summary.as_dict()
        assert payload["scorer_version"] == SCORER_VERSION
        assert list(payload["metrics"]) == sorted(payload["metrics"])
