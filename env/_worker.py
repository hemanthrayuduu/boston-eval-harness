"""Subprocess worker that executes one query and exits.

Runs in its own process so a runaway query can be killed by wall clock and capped
by an address-space rlimit without taking the harness down with it. Reads a JSON
request on stdin, writes a JSON response on stdout. Never raises to the caller --
failures come back as a structured ``error_class``.
"""

from __future__ import annotations

import json
import resource
import sys
from typing import Any


def _apply_memory_cap(max_bytes: int) -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = max_bytes if hard == resource.RLIM_INFINITY else min(max_bytes, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))


def _run(request: dict[str, Any]) -> dict[str, Any]:
    import duckdb

    _apply_memory_cap(int(request["max_memory_bytes"]))

    conn = duckdb.connect(
        request["db_path"],
        read_only=True,
        # The real sandbox. Blocks read_csv/read_parquet/httpfs/COPY-to-file at the
        # engine, below anything the SQL guard can see.
        config={"enable_external_access": False},
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

    try:
        response = _run(request)
    except MemoryError:
        response = {"ok": False, "error_class": "memory_exceeded", "error": "memory cap hit"}
    except Exception as err:
        name = type(err).__name__
        error_class = "sql_error"
        if "Permission" in name or "permitted" in str(err) or "not allowed" in str(err):
            error_class = "external_access_denied"
        elif "Catalog" in name:
            error_class = "catalog_error"
        elif "Parser" in name or "Syntax" in name:
            error_class = "syntax_error"
        response = {"ok": False, "error_class": error_class, "error": f"{name}: {err}"}

    # default=str so DECIMAL/date/timestamp survive the JSON hop rather than
    # failing the whole query at serialization time.
    json.dump(response, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
