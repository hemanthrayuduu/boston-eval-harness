"""Subprocess worker that executes one query and exits.

Runs in its own process so a runaway query can be killed by wall clock and capped
on memory without taking the harness down with it. Reads a JSON request on stdin,
writes a JSON response on stdout. Never raises to the caller -- failures come back
as a structured ``error_class``.

Memory is capped twice. DuckDB's ``memory_limit`` is portable, bounds the buffer
manager, and fails cleanly with ``OutOfMemoryException``. That is the primary cap
everywhere. ``RLIMIT_DATA`` bounds the heap on top of it, as defence against
allocations the engine does not track, and only Linux honours it: macOS rejects
any finite value with ``ValueError: current limit exceeds maximum limit``. The
response reports which applied.

**Not ``RLIMIT_AS``.** It bounds total *virtual* address space, and DuckDB 2.x
reserves a large virtual arena at connect time regardless of how much it will
actually use. Under ``RLIMIT_AS`` at 256MB, 512MB or 1GB the process dies with
SIGSEGV before running a query (verified on DuckDB 2.0.0 / glibc 2.39); 2GB is
the first value that survives. That trades a clean, catchable
``OutOfMemoryException`` for a signal the worker cannot report, which is strictly
worse than no rlimit at all. ``RLIMIT_DATA`` bounds the data segment instead and
behaves.
"""

from __future__ import annotations

import json
import resource
import sys
from typing import Any


# Share of the cap given to DuckDB's buffer manager. The rest is headroom for
# allocations it does not track, so the clean engine error fires before the rlimit.
_ENGINE_SHARE = 0.75


def _apply_memory_cap(max_bytes: int) -> bool:
    """Cap the heap. Returns whether the OS accepted it.

    ``RLIMIT_DATA``, not ``RLIMIT_AS`` -- see the module docstring. Linux honours
    this; macOS raises and we fall back to DuckDB's own limit alone.
    """
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
        limit = max_bytes if hard == resource.RLIM_INFINITY else min(max_bytes, hard)
        resource.setrlimit(resource.RLIMIT_DATA, (limit, hard))
    except (ValueError, OSError):
        return False
    return True


def _run(request: dict[str, Any]) -> dict[str, Any]:
    import duckdb

    engine_limit = int(int(request["max_memory_bytes"]) * _ENGINE_SHARE)
    conn = duckdb.connect(
        request["db_path"],
        read_only=True,
        config={
            # The real sandbox. Blocks read_csv/read_parquet/httpfs/COPY-to-file at
            # the engine, below anything the SQL guard can see.
            "enable_external_access": False,
            "memory_limit": f"{engine_limit}B",
        },
    )
    try:
        cursor = conn.execute(request["sql"])
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return {
            "ok": True,
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
        }
    finally:
        conn.close()


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as err:
        json.dump({"ok": False, "error_class": "bad_request", "error": str(err)}, sys.stdout)
        return 0

    rlimit_applied = False
    try:
        # Imported before the rlimit so the cap bounds the query, not the import.
        import duckdb  # noqa: F401

        rlimit_applied = _apply_memory_cap(int(request["max_memory_bytes"]))
        response = _run(request)
    except MemoryError:
        response = {"ok": False, "error_class": "memory_exceeded", "error": "memory cap hit"}
    except Exception as err:
        name = type(err).__name__
        error_class = "sql_error"
        if "OutOfMemory" in name:
            error_class = "memory_exceeded"
        elif "Permission" in name or "permitted" in str(err) or "not allowed" in str(err):
            error_class = "external_access_denied"
        elif "Catalog" in name:
            error_class = "catalog_error"
        elif "Parser" in name or "Syntax" in name:
            error_class = "syntax_error"
        response = {"ok": False, "error_class": error_class, "error": f"{name}: {err}"}

    response["rlimit_applied"] = rlimit_applied
    # default=str so DECIMAL/date/timestamp survive the JSON hop rather than
    # failing the whole query at serialization time.
    json.dump(response, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
