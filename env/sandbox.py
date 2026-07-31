"""Bounded, isolated execution of a single validated query.

DuckDB has no ``statement_timeout`` -- verified on 1.5.5, where
``SET statement_timeout='10s'`` raises ``CatalogException: unrecognized
configuration parameter``. That is a Postgres setting. The alternatives are a
watchdog thread calling ``connection.interrupt()`` or process isolation; this
module uses process isolation, because it also caps memory and gives clean
``error_class`` attribution for timeouts and OOMs, which the traces need anyway.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ExecutionResult", "execute"]

_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_MEMORY_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    error_class: str | None = None
    error: str | None = None
    latency_ms: float = 0.0

    @property
    def timed_out(self) -> bool:
        return self.error_class == "timeout"


def execute(
    db_path: str | Path,
    sql: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_memory_bytes: int = _DEFAULT_MAX_MEMORY_BYTES,
) -> ExecutionResult:
    """Execute ``sql`` against ``db_path`` in a disposable subprocess.

    Assumes ``sql`` has already been through :func:`env.guard.validate`. This is
    containment, not validation -- the two layers are independent on purpose.
    """
    request = json.dumps(
        {
            "db_path": str(db_path),
            "sql": sql,
            "max_memory_bytes": max_memory_bytes,
        }
    )

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "env._worker"],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            ok=False,
            error_class="timeout",
            error=f"exceeded {timeout_s}s",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    latency_ms = (time.perf_counter() - started) * 1000

    if completed.returncode != 0:
        # Killed by the OS (OOM killer, signal) -- the worker never got to report.
        return ExecutionResult(
            ok=False,
            error_class="worker_died",
            error=(completed.stderr or "").strip()[:500] or f"exit {completed.returncode}",
            latency_ms=latency_ms,
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ExecutionResult(
            ok=False,
            error_class="bad_worker_output",
            error=(completed.stdout or "")[:500],
            latency_ms=latency_ms,
        )

    if not payload.get("ok"):
        return ExecutionResult(
            ok=False,
            error_class=payload.get("error_class", "sql_error"),
            error=payload.get("error"),
            latency_ms=latency_ms,
        )

    return ExecutionResult(
        ok=True,
        columns=payload["columns"],
        rows=payload["rows"],
        row_count=payload["row_count"],
        latency_ms=latency_ms,
    )
