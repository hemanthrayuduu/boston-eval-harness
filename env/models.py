"""Model adapters for the agent loop.

* :class:`ScriptedModel` replays a fixed list of responses. Tests use it, and so
  does anything that needs a deterministic agent -- a demo, a sanity baseline.
* :class:`OllamaModel` talks to a local Ollama server's ``/api/chat`` with tool
  calling.
* :class:`OpenAICompatibleModel` speaks the OpenAI chat-completions protocol with
  tool calling, which OpenRouter, Groq and most hosted providers (and Ollama's
  ``/v1``) share. One adapter, no SDK: ``PROVIDERS`` holds the presets, and API
  keys come from the environment or a gitignored ``.env``.

Every adapter returns :class:`env.loop.ModelResponse` and never interprets the
answer: turning tool calls into actions is the loop's job.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from env.loop import Message, ModelResponse, ToolRequest
from env.tools import ToolSpec

__all__ = ["ScriptedModel", "OllamaModel", "OpenAICompatibleModel", "PROVIDERS", "ProviderError"]


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


# ---------------------------------------------------------------------------
# OpenAI-compatible providers

PROVIDERS: dict[str, dict[str, str | None]] = {
    # Free models carry a ":free" suffix; the list changes often, so check
    # https://openrouter.ai/api/v1/models (pricing 0, "tools" in supported_parameters).
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},
}


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _api_key(name: str | None) -> str | None:
    """From the environment, else from a ``.env`` file in the working directory."""
    if not name:
        return None
    if os.environ.get(name):
        return os.environ[name]
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            key, _, value = line.strip().partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    return None


def _post_chat_with_requests(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    import requests

    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        retry_after = response.headers.get("Retry-After")
        raise ProviderError(
            f"HTTP {response.status_code}: {response.text[:300]}",
            status=response.status_code,
            retry_after=float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None,
        )
    return response.json()


@dataclass
class OpenAICompatibleModel:
    """Chat completions with tools, on any OpenAI-compatible endpoint.

    Rate limits (HTTP 429) and transient server errors are retried a bounded
    number of times, honouring Retry-After; anything else raises, and the loop
    records it as a fatal error for the episode. Free endpoints are not pinned
    to a dated version -- fine for development, not for published numbers.
    """

    model: str
    provider: str = "openrouter"
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    temperature: float = 0.0
    seed: int | None = 0
    max_tokens: int = 2048
    timeout_s: float = 180.0
    max_retries: int = 4
    post_chat: Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]] = _post_chat_with_requests
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        preset = PROVIDERS.get(self.provider, {})
        self.base_url = (self.base_url or preset.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise ValueError(f"unknown provider {self.provider!r} and no base_url given; have {sorted(PROVIDERS)}")
        if self.api_key is None:
            self.api_key = _api_key(preset.get("key_env"))
        if preset.get("key_env") and not self.api_key:
            raise ValueError(f"no API key: set {preset['key_env']} in the environment or in .env")

    @staticmethod
    def _messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [
                    {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": json.dumps(t.arguments)}}
                    for t in m.tool_calls
                ]
            if m.role == "tool":
                item["tool_call_id"] = m.tool_call_id
            out.append(item)
        return out

    def respond(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages),
            "tools": [t.as_function() for t in tools],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.provider == "openrouter":
            payload["usage"] = {"include": True}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(self.max_retries + 1):
            try:
                data = self.post_chat(f"{self.base_url}/chat/completions", payload, headers, self.timeout_s)
                break
            except ProviderError as err:
                retryable = err.status in (429, 500, 502, 503, 504)
                if not retryable or attempt == self.max_retries:
                    raise
                self.sleep(err.retry_after if err.retry_after is not None else min(60.0, 2.0 ** (attempt + 1)))
        if data.get("error"):
            raise ProviderError(f"provider error: {data['error']}")

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError:
                args = {"_unparsed": raw}
            calls.append(ToolRequest(id=call.get("id") or uuid.uuid4().hex[:8], name=fn.get("name", ""), arguments=args))
        usage = data.get("usage") or {}
        return ModelResponse(
            content=message.get("content") or "",
            tool_calls=tuple(calls),
            model=data.get("model", self.model),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            cost_usd=float(usage.get("cost") or 0.0),
            response_id=data.get("id"),
            provider_fingerprint=data.get("provider") or data.get("system_fingerprint"),
        )
