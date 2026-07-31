"""Run configuration and run identity.

Two identifiers, deliberately distinct:

``config_hash``
    Hashes only the knobs that define *what the system under test is*. Stable
    across replicates, so five variance runs of the same setup group together.

``run_id``
    Hashes the config together with everything else that can move a number:
    the data snapshot, the code, the scorer, the claim corpus, and the replicate
    index. Two runs that differ in any of those get different IDs.

The v2 roadmap used ``run_id = sha256(config)`` alone, which collides on exactly
the case Phase 9 needs -- five identical runs at temperature 0 to measure
variance would all share one ID. Splitting the two fixes that and makes
"why did this number move?" answerable by diffing identity components.

Note that ``model`` must be a *dated* model ID. Aliases are repointed by
providers without notice, so an alias silently breaks reproducibility.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["RetrieverConfig", "RunConfig", "RunIdentity", "canonical_json", "sha256_of"]

# A pinned hosted model carries an explicit date: claude-opus-5-2026-01-15,
# gpt-5-20260114. A bare version suffix does not count -- "claude-opus-5" ends in
# a digit but is an alias that providers repoint. Self-hosted models have no date,
# so they pin via `model_digest` instead.
_DATED_MODEL = re.compile(r"\d{4}-\d{2}-\d{2}$|\d{8}$")


def canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RetrieverConfig(BaseModel):
    """Retrieval spec. Part of the config hash, so changing it forks the run ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["none", "bm25", "dense", "hybrid"] = "none"
    embedding_model: str | None = None
    top_k: int = Field(default=5, ge=1, le=100)
    chunker: Literal["table", "column", "hybrid"] = "table"
    rerank: bool = False
    # Exact search by default: at a few thousand chunks ANN buys no speed and
    # conflates index recall loss with retriever quality in the ablation.
    exact_search: bool = True


class RunConfig(BaseModel):
    """Everything that defines the system under test for one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    model_digest: str | None = None
    """Content hash pinning a self-hosted model (e.g. an Ollama digest). Required
    when `model` carries no date."""
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    scaffold: Literal["single_shot", "react", "plan_execute"] = "react"
    step_budget: int = Field(default=12, ge=1, le=100)
    prompt_template_id: str = "default"
    retriever: RetrieverConfig = RetrieverConfig()
    tools_enabled: tuple[str, ...] = (
        "list_tables",
        "describe_table",
        "query",
        "read_limitation_doc",
        "submit_verdict",
    )

    @model_validator(mode="after")
    def _require_pinned_model(self) -> RunConfig:
        if not _DATED_MODEL.search(self.model) and not self.model_digest:
            raise ValueError(
                f"model {self.model!r} is not pinned: it carries no date and no "
                "model_digest. Provider aliases are repointed without notice, which "
                "silently breaks reproducibility and leaves no trace of why a "
                "number moved. Use a dated ID or supply model_digest."
            )
        return self

    @field_validator("tools_enabled")
    @classmethod
    def _normalize_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Sorted so that tool *order* never changes the hash, only tool *set*.
        return tuple(sorted(set(value)))

    def config_hash(self) -> str:
        return sha256_of(canonical_json(self.model_dump(mode="json")))


class RunIdentity(BaseModel):
    """Full provenance of one run. ``run_id`` is derived from all of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config: RunConfig
    snapshot_sha256: str
    git_sha: str
    scorer_version: str
    corpus_version: str
    replicate_index: int = Field(default=0, ge=0)
    git_dirty: bool = False

    @property
    def config_hash(self) -> str:
        return self.config.config_hash()

    @property
    def run_id(self) -> str:
        return sha256_of(
            canonical_json(
                {
                    "config_hash": self.config_hash,
                    "snapshot_sha256": self.snapshot_sha256,
                    "git_sha": self.git_sha,
                    "git_dirty": self.git_dirty,
                    "scorer_version": self.scorer_version,
                    "corpus_version": self.corpus_version,
                    "replicate_index": self.replicate_index,
                }
            )
        )

    @property
    def short_run_id(self) -> str:
        return self.run_id[:12]

    def comparable_with(self, other: RunIdentity) -> bool:
        """Whether two runs may be diffed without ``--force``.

        Scores computed against different corpora or scorers are not comparable,
        and silently comparing them is how a regression gate lies to you.
        """
        return (
            self.corpus_version == other.corpus_version
            and self.scorer_version == other.scorer_version
            and self.snapshot_sha256 == other.snapshot_sha256
        )
