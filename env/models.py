"""Model adapters for the agent loop.

* :class:`ScriptedModel` replays a fixed list of responses. Tests use it, and so
  does anything that needs a deterministic agent -- a demo, a sanity baseline.
* :class:`OllamaModel` talks to a local Ollama server's ``/api/chat`` with tool
  calling. It is the zero-cost path for iterating on the loop; hosted providers
  come with the runner (Phase 5).

Every adapter returns :class:`env.loop.ModelResponse` and never interprets the
answer: turning tool calls into actions is the loop's job.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from env.loop import Message, ModelResponse, ToolRequest
from env.tools import ToolSpec

__all__ = ["ScriptedModel", "OllamaModel"]


@dataclass
class ScriptedModel:
    """Replays ``responses`` in order; after the last one, repeats ``fallback``."""

    responses: list[ModelResponse]
    fallback: ModelResponse = field(default_factory=lambda: ModelResponse(content="(no more scripted turns)"))
    seen: list[tuple[list[Message], list[str]]] = field(default_factory=list)
    """What the model was shown each turn: the conversation and the tool names."""

    def respond(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelResponse:
        self.seen.append((list(messages), [t.name for t in tools]))
        return self.responses.pop(0) if self.responses else self.fallback


def _post_json_with_requests(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    import requests

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


@dataclass
class OllamaModel:
    """A local model served by Ollama, with native tool calling.

    ``model`` is the Ollama tag (e.g. ``qwen2.5:7b``). Pin it for a real run with
    the tag's digest (RunConfig.model_digest), since tags are repointed.
    """

    model: str
    base_url: str = "http://localhost:11434"
    temperature: float = 0.0
    seed: int | None = 0
    timeout_s: float = 300.0
    post_json: Callable[[str, dict[str, Any], float], dict[str, Any]] = _post_json_with_requests

    def _messages(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [{"function": {"name": t.name, "arguments": t.arguments}} for t in m.tool_calls]
            if m.role == "tool" and m.name:
                item["tool_name"] = m.name
            out.append(item)
        return out

    def respond(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelResponse:
        options: dict[str, Any] = {"temperature": self.temperature}
        if self.seed is not None:
            options["seed"] = self.seed
        payload = {
            "model": self.model,
            "messages": self._messages(messages),
            "tools": [t.as_function() for t in tools],
            "stream": False,
            "options": options,
        }
        data = self.post_json(f"{self.base_url}/api/chat", payload, self.timeout_s)
        message = data.get("message", {})
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some models return a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_unparsed": args}
            calls.append(ToolRequest(id=call.get("id") or uuid.uuid4().hex[:8], name=fn.get("name", ""), arguments=args))
        return ModelResponse(
            content=message.get("content", "") or "",
            tool_calls=tuple(calls),
            model=data.get("model", self.model),
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            cost_usd=0.0,
            response_id=data.get("created_at"),
        )
