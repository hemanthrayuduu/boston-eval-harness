"""Allowlist validation for model-generated SQL.

This is an allowlist, not a denylist, because a denylist does not hold. Two facts
drive the design, both verified against DuckDB 1.5.5 and sqlglot:

1. ``read_only=True`` is not a sandbox. A read-only connection still reads
   arbitrary local files via ``read_csv()`` and still *writes* to the filesystem
   via ``COPY ... TO``.

2. sqlglot parses ``SELECT * FROM read_csv('/etc/passwd')`` as a ``Select`` whose
   single ``Table`` node has an **empty name** -- so the obvious guard ("is it a
   SELECT, and is every referenced table in my allowlist?") passes it, because
   there is no table name to reject.

So the core rule is: every ``Table`` node must carry a non-empty ``Identifier``
naming a table in the snapshot (or a CTE defined in the same statement). That one
rule rejects every table-valued-function source. The function and node-type checks
below are defence in depth.

The real defence is ``enable_external_access=false`` on the connection itself
(see ``env.sandbox``). This module exists so that a bad query is rejected before
it reaches a connection, and so that rejections are attributable to a reason code.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

__all__ = ["GuardError", "GuardedSQL", "validate"]


# Statement roots that may appear at the top level. Anything else -- COPY, ATTACH,
# INSTALL, LOAD, PRAGMA, SET, EXPORT, CALL, any DDL/DML, and sqlglot's catch-all
# Command node for statements it does not model -- is rejected by absence.
_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
)

# Qualifiers we tolerate on an otherwise-valid table reference.
_ALLOWED_QUALIFIERS = frozenset({"", "main", "memory"})

# Exact function names that read or write outside the database.
_BLOCKED_FUNCTIONS = frozenset(
    {
        "glob",
        "sniff_csv",
        "parquet_metadata",
        "parquet_schema",
        "arrow_scan",
        "iceberg_scan",
        "delta_scan",
        "postgres_scan",
        "sqlite_scan",
        "mysql_scan",
        "shapefile_scan",
        "st_read",
        "load_extension",
        "install_extension",
        "getenv",
    }
)

# Prefix/suffix families: read_csv, read_parquet, read_json_auto, read_text,
# read_blob, duckdb_settings, duckdb_extensions, parquet_scan, csv_scan, ...
_BLOCKED_PREFIXES = ("read_", "duckdb_", "pg_", "sqlite_")
_BLOCKED_SUFFIXES = ("_scan",)


class GuardError(Exception):
    """Raised when SQL is not safe to execute.

    ``code`` is a stable machine-readable reason, suitable for grouping in traces
    and for asserting on in tests.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GuardedSQL:
    """SQL that passed validation, plus what it touches."""

    sql: str
    tables: frozenset[str]
    """Snapshot tables referenced, lowercased. CTE names are excluded -- these are
    real tables, which is what retrieval metrics should be scored against."""


def _function_name(node: exp.Expression) -> str | None:
    """Best-effort name for a function node, across sqlglot's two representations.

    Unknown functions parse as ``Anonymous`` carrying the name in ``this``; known
    ones parse as dedicated classes (``read_csv`` -> ``exp.ReadCSV``) that expose
    ``sql_name()``.
    """
    if isinstance(node, exp.Anonymous):
        name = node.this
        return name.lower() if isinstance(name, str) else None
    sql_name = getattr(node, "sql_name", None)
    if callable(sql_name):
        try:
            return str(sql_name()).lower()
        except Exception:  # pragma: no cover - defensive
            return None
    return None


def _is_blocked_function(name: str) -> bool:
    return (
        name in _BLOCKED_FUNCTIONS
        or name.startswith(_BLOCKED_PREFIXES)
        or name.endswith(_BLOCKED_SUFFIXES)
    )


def _cte_names(root: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in root.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            names.add(alias.lower())
    return names


def validate(sql: str, allowed_tables: set[str] | frozenset[str]) -> GuardedSQL:
    """Validate ``sql`` against the snapshot's table allowlist.

    Returns a :class:`GuardedSQL` or raises :class:`GuardError`. Never returns a
    partially-checked result: any failure raises.
    """
    if not sql or not sql.strip():
        raise GuardError("empty", "no SQL provided")

    allowed = {t.lower() for t in allowed_tables}

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as err:
        raise GuardError("parse_error", f"could not parse: {err}") from err

    statements = [s for s in statements if s is not None]
    if len(statements) == 0:
        raise GuardError("empty", "no statement parsed")
    if len(statements) > 1:
        raise GuardError(
            "multiple_statements",
            f"expected 1 statement, got {len(statements)}",
        )

    root = statements[0]

    if isinstance(root, exp.Command):
        # sqlglot's fallback for statements it does not model. Never allowed.
        raise GuardError("disallowed_statement", f"unsupported statement: {root.this}")

    if not isinstance(root, _ALLOWED_ROOTS):
        raise GuardError(
            "disallowed_statement",
            f"root must be a read-only query, got {type(root).__name__}",
        )

    # Table checks run FIRST, because they are the general rule: any source that
    # is not a named, allowlisted table is rejected on structure alone. That
    # covers every table-valued function -- including ones invented after this
    # code was written, which a name blocklist by definition cannot.
    ctes = _cte_names(root)
    referenced: set[str] = set()

    for table in root.find_all(exp.Table):
        # The load-bearing check. A table-valued function source (read_csv,
        # parquet_scan, glob, ...) produces a Table whose `this` is a function
        # node and whose name is the empty string.
        if not isinstance(table.this, exp.Identifier):
            raise GuardError(
                "non_table_source",
                f"source is not a named table: {table.sql(dialect='duckdb')[:80]}",
            )

        name = table.name.lower()
        if not name:
            raise GuardError("non_table_source", "table reference has no name")

        for qualifier in (table.db, table.catalog):
            if qualifier and qualifier.lower() not in _ALLOWED_QUALIFIERS:
                raise GuardError(
                    "disallowed_qualifier",
                    f"qualifier not permitted: {qualifier}",
                )

        if name in ctes:
            continue
        if name not in allowed:
            raise GuardError("unknown_table", f"table not in snapshot: {name}")
        referenced.add(name)

    # Defence in depth. Catches mutating nodes and file/env-reaching functions in
    # positions where no Table node exists -- e.g. `SELECT getenv('AWS_SECRET')`
    # or a blocked function in a projection or WHERE clause.
    for node in root.walk():
        if isinstance(node, exp.Command):
            raise GuardError("disallowed_statement", f"unsupported node: {node.this}")
        if isinstance(node, _ALLOWED_ROOTS):
            continue
        if isinstance(node, exp.DDL | exp.DML):
            raise GuardError(
                "disallowed_statement",
                f"data-modifying node: {type(node).__name__}",
            )
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name and _is_blocked_function(name):
                raise GuardError("blocked_function", f"function not permitted: {name}")

    return GuardedSQL(sql=sql, tables=frozenset(referenced))


def enforce_limit(sql: str, limit: int = 500) -> str:
    """Append a LIMIT if the outermost query has none.

    Applied after :func:`validate`. A query that already sets a smaller limit is
    left alone; one with a larger limit is tightened.
    """
    parsed = sqlglot.parse_one(sql, read="duckdb")
    if not isinstance(parsed, exp.Select):
        return sql
    existing = parsed.args.get("limit")
    if existing is not None:
        try:
            if int(existing.expression.this) <= limit:
                return sql
        except (AttributeError, TypeError, ValueError):
            return sql
    return parsed.limit(limit).sql(dialect="duckdb")
