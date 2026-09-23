"""The agent's tools: typed arguments, typed results, errors as data.

An agent verifying a claim can look at the snapshot (``list_tables``,
``describe_table``), run read-only SQL (``query``), read the limitations corpus
(``read_limitation_doc``), and end the episode (``submit_verdict``). Nothing else:
no network, no files, no other tables.

Every tool returns a :class:`ToolResult`. Failures -- SQL the guard refuses, a
query that times out, arguments that do not validate, an unknown limitation ID
-- come back as ``ok=False`` with an ``error_class``, never as an exception.
An agent that sees its own error can recover; a harness that crashes on one
cannot measure recovery at all.

``query`` runs through both containment layers, independently: ``env.guard``
(allowlist AST validation, a row limit) and ``env.sandbox`` (a subprocess with a
timeout, a memory cap, and ``enable_external_access=false``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from env.guard import GuardError, enforce_limit, validate
from env.limitations import LimitationDoc
from env.sandbox import execute

__all__ = [
    "VERDICTS",
    "ToolResult",
    "ToolSpec",
    "Environment",
    "SubmitVerdictArgs",
]

VERDICTS = ("supported", "contradicted", "underdetermined", "misleading", "unverifiable")
_MAX_ROWS = 50
_MAX_CHARS = 6000


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class DescribeTableArgs(_Args):
    table: str = Field(description="A table name from list_tables.")


class QueryArgs(_Args):
    sql: str = Field(description="One read-only DuckDB SELECT over the snapshot tables.")


class ReadLimitationDocArgs(_Args):
    doc_id: str | None = Field(
        default=None, description="A limitation ID such as LIM-SMALL-N. Omit it to list every ID with its title."
    )


class SubmitVerdictArgs(_Args):
    verdict: str = Field(description=f"One of: {', '.join(VERDICTS)}.")
    computed_value: float | None = Field(
        default=None,
        description="The number the claim asserts, as you computed it: a percent change for a change claim, "
        "a count for a count claim, a rank for a rank claim. Omit if you could not compute it.",
    )
    spec_sensitive: bool = Field(
        default=False,
        description="True if the verdict depends on defensible analytic choices (what counts, which window, "
        "which geography) such that another careful analyst could reasonably reach a different verdict.",
    )
    limitation_ids: list[str] = Field(default_factory=list, description="IDs of limitations that bear on the claim.")
    sql: list[str] = Field(default_factory=list, description="The queries your verdict rests on.")
    reasoning: str = Field(default="", description="A short explanation of the verdict.")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    """JSON Schema for the arguments, generated from the Pydantic model."""

    def as_function(self) -> dict[str, Any]:
        """The common function-calling shape (OpenAI / Ollama)."""
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: Any = None
    """JSON-serialisable payload returned to the model."""
    error_class: str | None = None
    error: str | None = None
    terminal: bool = False
    """True for a valid submit_verdict: the episode ends."""
    verdict: SubmitVerdictArgs | None = None

    def for_model(self) -> str:
        if self.ok:
            text = json.dumps(self.content, default=str)
        else:
            text = json.dumps({"error_class": self.error_class, "error": self.error})
        return text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 20] + '..." [truncated]'

    def summary(self, limit: int = 300) -> str:
        text = self.for_model()
        return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class _Tool:
    spec: ToolSpec
    args_model: type[_Args]
    run: Callable[[Any], ToolResult]


@dataclass
class Environment:
    """The tools for one snapshot. Stateless across episodes except for caches."""

    db_path: Path
    limitations: Mapping[str, LimitationDoc]
    query_timeout_s: float = 10.0
    row_limit: int = 500
    enabled: tuple[str, ...] | None = None
    """Tool names offered to the agent; None means all. submit_verdict is always on."""
    _tables: dict[str, int] = field(default_factory=dict, init=False)
    _descriptions: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        with duckdb.connect(str(self.db_path), read_only=True) as conn:
            names = [r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1"
            ).fetchall()]
            self._tables = {n: conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
        tools = {
            "list_tables": _Tool(ToolSpec("list_tables", "List the snapshot's tables with row counts.", NoArgs.model_json_schema()), NoArgs, self._list_tables),
            "describe_table": _Tool(ToolSpec("describe_table", "Columns of one table: name, type, share null, and a few example values.", DescribeTableArgs.model_json_schema()), DescribeTableArgs, self._describe_table),
            "query": _Tool(ToolSpec("query", f"Run one read-only DuckDB SELECT. Results are capped at {_MAX_ROWS} rows shown.", QueryArgs.model_json_schema()), QueryArgs, self._query),
            "read_limitation_doc": _Tool(ToolSpec("read_limitation_doc", "Read a documented limitation of the data by ID, or list them all.", ReadLimitationDocArgs.model_json_schema()), ReadLimitationDocArgs, self._read_limitation_doc),
            "submit_verdict": _Tool(ToolSpec("submit_verdict", "Submit your verdict on the claim. Ends the task.", SubmitVerdictArgs.model_json_schema()), SubmitVerdictArgs, self._submit_verdict),
        }
        if self.enabled is not None:
            unknown = set(self.enabled) - set(tools)
            if unknown:
                raise ValueError(f"unknown tools enabled: {sorted(unknown)}")
            tools = {k: v for k, v in tools.items() if k in self.enabled or k == "submit_verdict"}
        self._tools = tools

    # -- public -------------------------------------------------------------

    @property
    def tables(self) -> frozenset[str]:
        return frozenset(self._tables)

    def specs(self, names: tuple[str, ...] | None = None) -> list[ToolSpec]:
        return [t.spec for n, t in self._tools.items() if names is None or n in names]

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error_class="unknown_tool", error=f"no tool {name!r}; have {sorted(self._tools)}")
        try:
            args = tool.args_model.model_validate(dict(arguments or {}))
        except ValidationError as err:
            return ToolResult(False, error_class="bad_arguments", error=_short(err))
        return tool.run(args)

    # -- tools --------------------------------------------------------------

    def _list_tables(self, _: NoArgs) -> ToolResult:
        return ToolResult(True, [{"table": n, "rows": r} for n, r in sorted(self._tables.items())])

    def _describe_table(self, args: DescribeTableArgs) -> ToolResult:
        table = args.table.strip().lower()
        if table not in self._tables:
            return ToolResult(False, error_class="unknown_table", error=f"no table {args.table!r}; have {sorted(self._tables)}")
        if table not in self._descriptions:
            with duckdb.connect(str(self.db_path), read_only=True) as conn:
                cols = conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
                    [table],
                ).fetchall()
                columns = []
                for name, dtype in cols:
                    q = f'"{name}"'
                    nulls = conn.execute(f'SELECT avg(({q} IS NULL)::DOUBLE) FROM "{table}"').fetchone()[0]
                    samples = [r[0] for r in conn.execute(
                        f'SELECT DISTINCT {q}::VARCHAR FROM "{table}" WHERE {q} IS NOT NULL LIMIT 3'
                    ).fetchall()]
                    columns.append({
                        "column": name,
                        "type": dtype,
                        "share_null": round(nulls or 0.0, 4),
                        "examples": [s if len(s) <= 60 else s[:57] + "..." for s in samples],
                    })
            self._descriptions[table] = {"table": table, "rows": self._tables[table], "columns": columns}
        return ToolResult(True, self._descriptions[table])

    def _query(self, args: QueryArgs) -> ToolResult:
        try:
            guarded = validate(args.sql, self.tables)
            sql = enforce_limit(guarded.sql, self.row_limit)
        except GuardError as err:
            return ToolResult(False, error_class=f"guard:{err.code}", error=err.message)
        result = execute(self.db_path, sql, timeout_s=self.query_timeout_s)
        if not result.ok:
            return ToolResult(False, error_class=result.error_class, error=(result.error or "")[:500])
        shown = result.rows[:_MAX_ROWS]
        return ToolResult(True, {
            "columns": result.columns,
            "rows": shown,
            "row_count": result.row_count,
            "truncated": result.row_count > len(shown),
        })

    def _read_limitation_doc(self, args: ReadLimitationDocArgs) -> ToolResult:
        if args.doc_id is None:
            return ToolResult(True, [{"id": d.id, "title": d.title} for d in self.limitations.values()])
        doc = self.limitations.get(args.doc_id.strip().upper())
        if doc is None:
            return ToolResult(False, error_class="unknown_limitation", error=f"no limitation {args.doc_id!r}; call with no doc_id to list them")
        return ToolResult(True, {"id": doc.id, "title": doc.title, "text": doc.body.strip()})

    def _submit_verdict(self, args: SubmitVerdictArgs) -> ToolResult:
        verdict = args.verdict.strip().lower()
        if verdict not in VERDICTS:
            return ToolResult(False, error_class="bad_verdict", error=f"verdict must be one of {list(VERDICTS)}")
        unknown = sorted(set(args.limitation_ids) - set(self.limitations))
        if unknown:
            return ToolResult(False, error_class="unknown_limitation", error=f"unknown limitation IDs {unknown}")
        clean = args.model_copy(update={"verdict": verdict})
        return ToolResult(True, {"accepted": True}, terminal=True, verdict=clean)


def _short(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in err.errors())[:500]

