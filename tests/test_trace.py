"""Trace records must survive a kill, and must carry everything scoring needs."""

from __future__ import annotations

import pytest

from harness.trace import (
    SCHEMA_VERSION,
    LLMCall,
    SubmittedVerdict,
    Termination,
    ToolCall,
    Trajectory,
    TraceWriter,
    completed_claim_ids,
    read_trajectories,
)


def make_trajectory(claim_id: str = "c1", **overrides) -> Trajectory:
    base = {
        "claim_id": claim_id,
        "run_id": "r" * 8,
        "config_hash": "h" * 8,
        "tool_calls": (
            ToolCall(step=0, tool="list_tables", ok=True, latency_ms=1.0),
            ToolCall(step=1, tool="query", ok=True, latency_ms=12.0),
        ),
        "llm_calls": (
            LLMCall(
                step=0,
                model="claude-opus-5-2026-01-15",
                prompt_hash="p1",
                tokens_in=100,
                tokens_out=50,
                cost_usd=0.002,
                latency_ms=800.0,
            ),
        ),
        "verdict": SubmittedVerdict(verdict="underdetermined", spec_sensitive=True),
    }
    base.update(overrides)
    return Trajectory(**base)


class TestRoundTrip:
    def test_write_then_read_preserves_the_record(self, tmp_path) -> None:
        path = tmp_path / "trajectories.jsonl"
        original = make_trajectory()
        TraceWriter(path).append(original)
        assert list(read_trajectories(path)) == [original]

    def test_schema_version_is_recorded(self, tmp_path) -> None:
        path = tmp_path / "t.jsonl"
        TraceWriter(path).append(make_trajectory())
        assert next(read_trajectories(path)).schema_version == SCHEMA_VERSION

    def test_reading_a_missing_file_is_empty_not_an_error(self, tmp_path) -> None:
        assert list(read_trajectories(tmp_path / "nope.jsonl")) == []


class TestResumability:
    def test_completed_ids_drive_resume(self, tmp_path) -> None:
        path = tmp_path / "t.jsonl"
        writer = TraceWriter(path)
        writer.extend([make_trajectory(f"c{i}") for i in range(27)])

        done = completed_claim_ids(path)
        assert len(done) == 27

        remaining = [f"c{i}" for i in range(50) if f"c{i}" not in done]
        assert len(remaining) == 23
        assert "c26" not in remaining

    def test_appending_after_a_kill_does_not_lose_earlier_records(self, tmp_path) -> None:
        path = tmp_path / "t.jsonl"
        TraceWriter(path).extend([make_trajectory(f"c{i}") for i in range(3)])
        # A fresh writer, as a restarted process would create.
        TraceWriter(path).append(make_trajectory("c3"))
        assert completed_claim_ids(path) == {"c0", "c1", "c2", "c3"}

    def test_torn_final_line_is_dropped_not_fatal(self, tmp_path) -> None:
        """A process killed mid-write leaves a partial line.

        Refusing to read the file would make a resumable harness unresumable --
        the exact moment resumability is supposed to help.
        """
        path = tmp_path / "t.jsonl"
        TraceWriter(path).extend([make_trajectory(f"c{i}") for i in range(3)])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"claim_id": "c3", "run_i')

        recovered = list(read_trajectories(path))
        assert len(recovered) == 3
        assert completed_claim_ids(path) == {"c0", "c1", "c2"}

    def test_corruption_in_the_middle_is_fatal(self, tmp_path) -> None:
        """Only the tail is explicable by a kill. Damage elsewhere means the file
        is not trustworthy, and silently skipping it would drop real results."""
        path = tmp_path / "t.jsonl"
        TraceWriter(path).extend([make_trajectory(f"c{i}") for i in range(3)])
        lines = path.read_text().splitlines()
        lines[1] = "{not json"
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(Exception):
            list(read_trajectories(path))


class TestDerivedProperties:
    def test_totals(self) -> None:
        trajectory = make_trajectory()
        assert trajectory.n_steps == 2
        assert trajectory.total_cost_usd == pytest.approx(0.002)
        assert trajectory.total_tokens == 150
        assert trajectory.total_latency_ms == pytest.approx(813.0)

    def test_cache_hit_rate(self) -> None:
        calls = (
            LLMCall(step=0, model="m-2026-01-01", prompt_hash="a", cached=True),
            LLMCall(step=1, model="m-2026-01-01", prompt_hash="b", cached=False),
        )
        assert make_trajectory(llm_calls=calls).cache_hit_rate == 0.5

    def test_cache_hit_rate_is_none_without_llm_calls(self) -> None:
        assert make_trajectory(llm_calls=()).cache_hit_rate is None

    def test_tool_error_rate(self) -> None:
        calls = (
            ToolCall(step=0, tool="query", ok=False, error_class="syntax_error"),
            ToolCall(step=1, tool="query", ok=True),
            ToolCall(step=2, tool="query", ok=True),
            ToolCall(step=3, tool="query", ok=True),
        )
        assert make_trajectory(tool_calls=calls).tool_error_rate == 0.25

    def test_recovery_requires_both_an_error_and_a_verdict(self) -> None:
        failing = (ToolCall(step=0, tool="query", ok=False, error_class="syntax_error"),)

        recovered = make_trajectory(tool_calls=failing)
        assert recovered.recovered_from_tool_error

        gave_up = make_trajectory(
            tool_calls=failing, verdict=None, termination=Termination.STEP_BUDGET
        )
        assert not gave_up.recovered_from_tool_error

        clean = make_trajectory()
        assert not clean.recovered_from_tool_error

    def test_unsubmitted_trajectory_is_representable(self) -> None:
        trajectory = make_trajectory(verdict=None, termination=Termination.STEP_BUDGET)
        assert trajectory.verdict is None
        assert trajectory.termination == Termination.STEP_BUDGET
