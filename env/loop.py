"""The agent loop: one claim, one episode, one trajectory.

The loop is deliberately small and visible -- the roadmap's "hand-rolled, ~400
lines, no framework" -- because the trajectory is the object under study and a
framework would hide exactly the parts that are scored: every model turn, every
tool call with its arguments, errors, and latency, and how the episode ended.

Shape of an episode:

1. The scaffold writes the opening messages for the claim.
2. Each step, the model sees the conversation and the tools the scaffold offers,
   and answers with text and/or tool calls. Each tool call runs against the
   environment and its result -- error or not -- goes back into the conversation.
3. A valid ``submit_verdict`` ends the episode.
4. A turn with no tool call gets one nudge, and counts against the budget.
5. When the step budget runs out, the model gets up to two final turns with
   only ``submit_verdict`` available and a tool call required. If it still does
   not submit, the episode ends without a verdict (``step_budget_exhausted``);
   scoring counts that as a miss. A verdict written as prose is never parsed.

The model is anything implementing :class:`Model`; the scaffold anything
implementing :class:`Scaffold`. Phase 8 swaps both.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

from env.tools import Environment, ToolResult, ToolSpec
from harness.trace import LLMCall, SubmittedVerdict, Termination, ToolCall, Trajectory

__all__ = [
    "Message",
    "ToolRequest",
    "ModelResponse",
    "Model",
    "Task",
    "Scaffold",
    "ReactScaffold",
    "SingleShotScaffold",
    "run_episode",
]


@dataclass(frozen=True)
class ToolRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolRequest, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    """For tool messages: which tool produced this result."""

    def canonical(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [[t.id, t.name, t.arguments] for t in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolRequest, ...] = ()
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    response_id: str | None = None
    provider_fingerprint: str | None = None
    cached: bool = False
    finish_reason: str | None = None
    """Why the provider stopped, e.g. "tool_calls", "stop", or "length" (output cap hit)."""


class Model(Protocol):
    def respond(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec], tool_choice: str | None = None
    ) -> ModelResponse:
        """``tool_choice="required"`` asks the provider to force a tool call; the
        loop sends it only on the final, submit-only turn. Adapters without the
        feature may ignore it."""
        ...


@dataclass(frozen=True)
class Task:
    """What the agent is told about a claim: what a careful reader would see."""

    claim_id: str
    claim: str
    source: str
    published: date
    snapshot_date: date


class Scaffold(Protocol):
    name: str

    def opening(self, task: Task, env: Environment, budget: int) -> list[Message]: ...

    def tools_for_step(self, step: int, budget: int, env: Environment) -> tuple[str, ...] | None:
        """Tool names offered at this step; None means every enabled tool."""
        ...


_INSTRUCTIONS = """You are checking a published claim about crime in Boston against a pinned snapshot of the city's open data.

Use the tools to inspect the data and compute what the claim asserts. Then call submit_verdict with one of:
- supported: the data backs the claim under the defensible ways of computing it.
- contradicted: the data goes against the claim under the defensible ways of computing it.
- underdetermined: the claim holds under some defensible analytic choices and fails under others (what counts, which records, which geography or window).
- misleading: the arithmetic holds but the conclusion the claim implies is not licensed by the data.
- unverifiable: this data cannot address the claim.

Set spec_sensitive to true when the verdict depends on such choices. Cite the limitation IDs that bear on the claim (read_limitation_doc lists them). Report computed_value: the percent change, count, or rank the claim is about, as you computed it.

The snapshot was taken on {snapshot_date}; the claim was published on {published}. You have {budget} steps."""
# Deliberately neutral: nothing here about how to count or which pitfalls exist.
# Those are what the limitations corpus documents and what the benchmark tests.


@dataclass
class ReactScaffold:
    """Tools every step; the model reasons and acts in the same turns."""

    name: str = "react"

    def opening(self, task: Task, env: Environment, budget: int) -> list[Message]:
        system = _INSTRUCTIONS.format(snapshot_date=task.snapshot_date, published=task.published, budget=budget)
        user = f"Claim (published by {task.source}): {task.claim}"
        return [Message("system", system), Message("user", user)]

    def tools_for_step(self, step: int, budget: int, env: Environment) -> tuple[str, ...] | None:
        return None


@dataclass
class SingleShotScaffold:
    """No data access: the model answers from the claim alone. The floor for the
    tool ablation, and a memorization probe."""

    name: str = "single_shot"

    def opening(self, task: Task, env: Environment, budget: int) -> list[Message]:
        system = _INSTRUCTIONS.format(snapshot_date=task.snapshot_date, published=task.published, budget=1)
        system += "\n\nYou have no data access in this task: answer from the claim alone."
        return [Message("system", system), Message("user", f"Claim (published by {task.source}): {task.claim}")]

    def tools_for_step(self, step: int, budget: int, env: Environment) -> tuple[str, ...] | None:
        return ("submit_verdict",)


_NUDGE = "Use a tool to inspect the data, or call submit_verdict if you are done."
_FORCE = "You are out of steps. Call submit_verdict now with your best verdict."
_FORCE_AGAIN = "Respond only with a submit_verdict tool call. No other text."
FORCED_ATTEMPTS = 2


def _prompt_hash(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> str:
    payload = {"messages": [m.canonical() for m in messages], "tools": [t.name for t in tools]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class _State:
    messages: list[Message]
    tool_calls: list[ToolCall] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    verdict: SubmittedVerdict | None = None


def run_episode(
    task: Task,
    env: Environment,
    model: Model,
    scaffold: Scaffold,
    *,
    step_budget: int,
    run_id: str,
    config_hash: str,
    replicate_index: int = 0,
    max_seconds: float | None = None,
) -> Trajectory:
    """Run one claim to a verdict (or to the end of the budget). Never raises for
    model or tool failures: they end up in the trajectory."""
    state = _State(messages=scaffold.opening(task, env, step_budget))
    started = time.perf_counter()

    def finish(termination: Termination, error: str | None = None) -> Trajectory:
        return Trajectory(
            claim_id=task.claim_id,
            run_id=run_id,
            config_hash=config_hash,
            replicate_index=replicate_index,
            tool_calls=tuple(state.tool_calls),
            llm_calls=tuple(state.llm_calls),
            verdict=state.verdict,
            termination=termination,
            error=error,
        )

    def turn(step: int, offered: tuple[str, ...] | None, force: bool = False) -> bool | str:
        """One model turn. True when a verdict was accepted; an error string on a fatal failure."""
        tools = env.specs(offered)
        prompt_hash = _prompt_hash(state.messages, tools)
        t0 = time.perf_counter()
        try:
            if force:
                response = model.respond(state.messages, tools, tool_choice="required")
            else:
                response = model.respond(state.messages, tools)
        except Exception as err:  # the model is outside our control; its failure is data
            return f"{type(err).__name__}: {err}"
        state.llm_calls.append(LLMCall(
            step=step, model=response.model or "unknown", prompt_hash=prompt_hash,
            tokens_in=response.tokens_in, tokens_out=response.tokens_out, cost_usd=response.cost_usd,
            latency_ms=(time.perf_counter() - t0) * 1000, cached=response.cached,
            response_id=response.response_id, provider_fingerprint=response.provider_fingerprint,
        ))
        state.messages.append(Message("assistant", response.content, tool_calls=response.tool_calls))
        if not response.tool_calls:
            state.messages.append(Message("user", _NUDGE))
            return False

        allowed = {t.name for t in tools}
        for request in response.tool_calls:
            t1 = time.perf_counter()
            if request.name not in allowed:
                result = ToolResult(False, error_class="tool_not_offered", error=f"{request.name!r} is not available at this step")
            else:
                result = env.call(request.name, request.arguments)
            state.tool_calls.append(ToolCall(
                step=step, tool=request.name, args=dict(request.arguments), ok=result.ok,
                result_summary=result.summary(), error_class=result.error_class,
                latency_ms=(time.perf_counter() - t1) * 1000,
            ))
            state.messages.append(Message("tool", result.for_model(), tool_call_id=request.id, name=request.name))
            if result.terminal and result.verdict is not None:
                v = result.verdict
                state.verdict = SubmittedVerdict(
                    verdict=v.verdict, computed_value=v.computed_value, spec_sensitive=v.spec_sensitive,
                    limitation_ids=tuple(v.limitation_ids), sql=tuple(v.sql), reasoning=v.reasoning,
                )
                return True
        return False

    for step in range(step_budget):
        if max_seconds is not None and time.perf_counter() - started > max_seconds:
            return finish(Termination.TIMEOUT)
        outcome = turn(step, scaffold.tools_for_step(step, step_budget, env))
        if isinstance(outcome, str):
            return finish(Termination.FATAL_ERROR, outcome)
        if outcome:
            return finish(Termination.SUBMITTED)

    # Out of steps: submit-only turns, a tool call required.
    for attempt in range(FORCED_ATTEMPTS):
        if not (attempt and state.messages[-1].content == _NUDGE):
            state.messages.append(Message("user", _FORCE if attempt == 0 else _FORCE_AGAIN))
        else:
            state.messages[-1] = Message("user", _FORCE_AGAIN)
        outcome = turn(step_budget + attempt, ("submit_verdict",), force=True)
        if isinstance(outcome, str):
            return finish(Termination.FATAL_ERROR, outcome)
        if outcome:
            return finish(Termination.SUBMITTED)
    return finish(Termination.STEP_BUDGET)
