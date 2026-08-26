"""Trajectory records: the only durable output of a run.

The contract this module exists to uphold: **every scored number must be
reconstructible from ``trajectories.jsonl`` alone.** If a metric needs something,
it goes in the trace. That is what makes scoring a pure function of the trace,
which in turn makes re-scoring free, rubric changes cheap, and the CI gate a
replay with no API keys.

Append-only JSONL, one line per completed claim. Append-only is what makes a run
resumable: a killed run leaves valid lines behind, and restarting skips the
claim IDs already present rather than paying for them twice.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SCHEMA_VERSION",
    "Termination",
    "ToolCall",
    "LLMCall",
    "SubmittedVerdict",
    "Trajectory",
    "TraceWriter",
    "read_trajectories",
    "completed_claim_ids",
]

# Bumped when the record shape changes incompatibly. Stored on every line so old
# runs can be read, or refused, rather than silently misparsed.
SCHEMA_VERSION = 1


class Termination(StrEnum):
    SUBMITTED = "submitted"
    """The agent called submit_verdict. The only clean ending."""
    STEP_BUDGET = "step_budget_exhausted"
    TIMEOUT = "timeout"
    FATAL_ERROR = "fatal_error"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCall(_Frozen):
    step: int = Field(ge=0)
    tool: str
    args: dict[str, object] = Field(default_factory=dict)
    ok: bool
    result_summary: str | None = None
    """Truncated. Full result sets are not stored -- they are re-derivable from
    the snapshot plus the SQL, which is stored."""
    error_class: str | None = None
    latency_ms: float = 0.0


class LLMCall(_Frozen):
    step: int = Field(ge=0)
    model: str
    prompt_hash: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False
    response_id: str | None = None
    provider_fingerprint: str | None = None
    """Whatever the provider returns identifying the serving stack. Without it,
    a number that moved because the provider changed something looks identical
    to a number that moved because you did."""


class SubmittedVerdict(_Frozen):
    """The agent's structured answer. Scored directly -- no judge, no parsing prose."""

    verdict: str
    computed_value: float | None = None
    spec_sensitive: bool = False
    """Whether the agent flagged the claim as dependent on analytic choices.
    The target of the benchmark's headline metric."""
    limitation_ids: tuple[str, ...] = ()
    sql: tuple[str, ...] = ()
    reasoning: str = ""


class Trajectory(_Frozen):
    schema_version: int = SCHEMA_VERSION
    claim_id: str
    run_id: str
    config_hash: str
    replicate_index: int = Field(default=0, ge=0)

    tool_calls: tuple[ToolCall, ...] = ()
    llm_calls: tuple[LLMCall, ...] = ()
    verdict: SubmittedVerdict | None = None
    termination: Termination = Termination.SUBMITTED
    error: str | None = None

    @property
    def n_steps(self) -> int:
        return len(self.tool_calls)

    @property
    def total_cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.llm_calls)

    @property
    def total_tokens(self) -> int:
        return sum(call.tokens_in + call.tokens_out for call in self.llm_calls)

    @property
    def total_latency_ms(self) -> float:
        return sum(c.latency_ms for c in self.llm_calls) + sum(
            c.latency_ms for c in self.tool_calls
        )

    @property
    def cache_hit_rate(self) -> float | None:
        """Reported per arm, so a reviewer can see an ablation difference is not
        a cache artifact."""
        if not self.llm_calls:
            return None
        return sum(1 for c in self.llm_calls if c.cached) / len(self.llm_calls)

    @property
    def tool_error_rate(self) -> float | None:
        if not self.tool_calls:
            return None
        return sum(1 for c in self.tool_calls if not c.ok) / len(self.tool_calls)

    @property
    def recovered_from_tool_error(self) -> bool:
        """A tool call failed and the agent still reached a verdict.

        Error recovery is most of what separates a usable agent from a brittle
        one, and it is invisible unless the trace keeps failed calls.
        """
        return (
            any(not c.ok for c in self.tool_calls)
            and self.termination == Termination.SUBMITTED
        )


class TraceWriter:
    """Append-only JSONL writer. Flushes per record so a kill loses at most one."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trajectory: Trajectory) -> None:
        line = json.dumps(
            trajectory.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()

    def extend(self, trajectories: list[Trajectory]) -> None:
        for trajectory in trajectories:
            self.append(trajectory)


def read_trajectories(path: str | Path) -> Iterator[Trajectory]:
    """Read a trace file, tolerating a truncated final line.

    A process killed mid-write leaves a partial line. Refusing to read the file
    at all would make a resumable harness unresumable, so the tail is dropped and
    the complete records are returned.
    """
    file = Path(path)
    if not file.exists():
        return

    lines = file.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                return  # torn tail from a kill; everything before it is intact
            raise
        yield Trajectory.model_validate(payload)


def completed_claim_ids(path: str | Path) -> set[str]:
    """Claim IDs already recorded, for resuming without duplicate spend."""
    return {trajectory.claim_id for trajectory in read_trajectories(path)}
