"""Parquet -> DuckDB, with explicit casts and measured coercion loss.

Two things this module refuses to do quietly.

**Silent coercion.** Ingestion keeps everything as strings precisely so that
typing is a decision made here, in the open. Every cast is measured: how many
non-null values went in, how many survived. A cast that nulls 12% of a column is
not a type conversion, it is data loss, and the build stops rather than handing
you a table that looks clean and is not.

**A checksum that promises more than it delivers.** A DuckDB file hash is a hash
of *bytes*, and bytes move for reasons the data did not. Verified: rebuilding the
same content to the same path is byte-identical, but building it to a different
filename changes three bytes, because the filename is embedded in the file. A
DuckDB storage-format change across versions would do the same on a larger scale.

So the file hash answers "is this the exact file the results were computed
against?" -- identity, which is what the run gate needs. It cannot answer "did
the data change?", because a rename or an engine upgrade would both say yes. The
refresh gate needs that second question, so a content digest is computed from the
data itself: order-independent, path-independent, format-independent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ingest.manifest import Manifest, file_sha256

__all__ = [
    "BuildError",
    "TableSpec",
    "CastReport",
    "TableReport",
    "BuildReport",
    "build_database",
    "seal_manifest",
]

# Table names become part of the guard's allowlist and appear in generated SQL,
# so they are constrained to plain lowercase identifiers.
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

# Default tolerance for a cast nulling non-null values. Municipal data is messy
# enough that zero is unachievable, and high enough to hide a real mistake, so
# anything past this has to be acknowledged explicitly in the spec.
DEFAULT_MAX_CAST_LOSS = 0.01


class BuildError(Exception):
    pass


@dataclass(frozen=True)
class TableSpec:
    table_name: str
    resource_id: str
    casts: dict[str, str] = field(default_factory=dict)
    """Column -> DuckDB type. Columns not listed stay VARCHAR, as fetched."""
    max_cast_loss: float | None = None
    """Per-table override. Set this deliberately, with a comment saying why the
    loss is acceptable -- that is the whole point of it being explicit."""

    def __post_init__(self) -> None:
        if not _SAFE_IDENTIFIER.match(self.table_name):
            raise BuildError(
                f"table name {self.table_name!r} must be a plain lowercase "
                "identifier: it goes into the SQL guard's allowlist and into "
                "generated SQL"
            )


@dataclass(frozen=True)
class CastReport:
    table: str
    column: str
    target_type: str
    row_count: int
    non_null_before: int
    cast_ok: int

    @property
    def lost(self) -> int:
        """Non-null values the cast turned into nulls."""
        return self.non_null_before - self.cast_ok

    @property
    def loss_rate(self) -> float:
        return self.lost / self.non_null_before if self.non_null_before else 0.0

    def describe(self) -> str:
        return (
            f"{self.table}.{self.column} -> {self.target_type}: "
            f"{self.lost}/{self.non_null_before} non-null values lost "
            f"({self.loss_rate:.2%})"
        )


@dataclass(frozen=True)
class TableReport:
    table_name: str
    resource_id: str
    row_count: int
    columns: tuple[str, ...]
    content_digest: str
    casts: tuple[CastReport, ...] = ()

    @property
    def lossy_casts(self) -> tuple[CastReport, ...]:
        return tuple(c for c in self.casts if c.lost > 0)


@dataclass(frozen=True)
class BuildReport:
    db_path: Path
    tables: tuple[TableReport, ...]
    duckdb_sha256: str
    content_sha256: str

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)

    @property
    def lossy_casts(self) -> tuple[CastReport, ...]:
        return tuple(c for table in self.tables for c in table.lossy_casts)

    def render(self) -> str:
        lines = [
            f"built {self.db_path.name}",
            f"  tables  : {len(self.tables)}",
            f"  rows    : {self.total_rows:,}",
            f"  file    : {self.duckdb_sha256[:16]}  (identity; not reproducible)",
            f"  content : {self.content_sha256[:16]}  (stable across rebuilds)",
        ]
        if self.lossy_casts:
            lines.append("  lossy casts:")
            lines.extend(f"    {c.describe()}" for c in self.lossy_casts)
        return "\n".join(lines)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _content_digest(conn: duckdb.DuckDBPyConnection, table: str) -> str:
    """Order-independent digest of a table's contents.

    Hashing rows and aggregating them in hash order means insertion order,
    parallel scan order and DuckDB's storage layout cannot change the result --
    only the data can.
    """
    quoted = _quote(table)
    row = conn.execute(
        f"SELECT md5(string_agg(rh, '' ORDER BY rh)) FROM "
        f"(SELECT md5(to_json({quoted})::VARCHAR) AS rh FROM {quoted}) s"
    ).fetchone()
    return (row[0] if row and row[0] else "empty")


def _load_table(
    conn: duckdb.DuckDBPyConnection, spec: TableSpec, parquet: Path
) -> tuple[int, tuple[str, ...]]:
    source = f"read_parquet({str(parquet)!r})"
    available = [
        r[0] for r in conn.execute(f"SELECT * FROM {source} LIMIT 0").description
    ]

    unknown = set(spec.casts) - set(available)
    if unknown:
        raise BuildError(
            f"{spec.table_name}: cast requested for columns not in the resource: "
            f"{sorted(unknown)}. Available: {available}"
        )

    projection = ", ".join(
        (
            f"TRY_CAST({_quote(column)} AS {spec.casts[column]}) AS {_quote(column)}"
            if column in spec.casts
            else _quote(column)
        )
        for column in available
    )
    conn.execute(
        f"CREATE OR REPLACE TABLE {_quote(spec.table_name)} AS "
        f"SELECT {projection} FROM {source}"
    )
    count = conn.execute(f"SELECT count(*) FROM {_quote(spec.table_name)}").fetchone()[0]
    return count, tuple(available)


def _measure_casts(
    conn: duckdb.DuckDBPyConnection, spec: TableSpec, parquet: Path, row_count: int
) -> tuple[CastReport, ...]:
    """Measure each cast against the *source*, before typing was applied."""
    reports = []
    source = f"read_parquet({str(parquet)!r})"

    for column, target in sorted(spec.casts.items()):
        quoted = _quote(column)
        non_null, cast_ok = conn.execute(
            f"SELECT count({quoted}), count(TRY_CAST({quoted} AS {target})) FROM {source}"
        ).fetchone()
        reports.append(
            CastReport(
                table=spec.table_name,
                column=column,
                target_type=target,
                row_count=row_count,
                non_null_before=non_null,
                cast_ok=cast_ok,
            )
        )
    return tuple(reports)


def build_database(
    specs: list[TableSpec],
    raw_dir: str | Path,
    db_path: str | Path,
    *,
    max_cast_loss: float = DEFAULT_MAX_CAST_LOSS,
) -> BuildReport:
    """Load Parquet resources into a fresh DuckDB file.

    Raises :class:`BuildError` if any cast loses more of a column than allowed.
    """
    raw = Path(raw_dir)
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    # Always a fresh file: appending to an existing database would make the
    # checksum describe a history rather than a snapshot.
    database.unlink(missing_ok=True)

    names = [spec.table_name for spec in specs]
    if len(set(names)) != len(names):
        raise BuildError(f"duplicate table names: {sorted({n for n in names if names.count(n) > 1})}")

    reports: list[TableReport] = []
    violations: list[CastReport] = []

    conn = duckdb.connect(str(database))
    try:
        for spec in specs:
            parquet = raw / f"{spec.resource_id}.parquet"
            if not parquet.exists():
                raise BuildError(
                    f"{spec.table_name}: no Parquet for resource {spec.resource_id!r} "
                    f"at {parquet}. Run ingest.pull first."
                )

            row_count, columns = _load_table(conn, spec, parquet)
            casts = _measure_casts(conn, spec, parquet, row_count)

            allowance = spec.max_cast_loss if spec.max_cast_loss is not None else max_cast_loss
            violations.extend(c for c in casts if c.loss_rate > allowance)

            reports.append(
                TableReport(
                    table_name=spec.table_name,
                    resource_id=spec.resource_id,
                    row_count=row_count,
                    columns=columns,
                    content_digest=_content_digest(conn, spec.table_name),
                    casts=casts,
                )
            )
    finally:
        conn.close()

    if violations:
        database.unlink(missing_ok=True)
        detail = "\n  ".join(c.describe() for c in violations)
        raise BuildError(
            "casts lost more data than allowed:\n  "
            + detail
            + "\nEither fix the cast, or raise max_cast_loss on that TableSpec with "
            "a comment explaining why the loss is acceptable. Do not let it pass "
            "silently -- a column that is 12% null because of a cast will look "
            "like a column that is 12% null in the source."
        )

    import hashlib

    content = hashlib.sha256(
        "\n".join(
            f"{t.table_name}:{t.row_count}:{t.content_digest}"
            for t in sorted(reports, key=lambda t: t.table_name)
        ).encode("utf-8")
    ).hexdigest()

    return BuildReport(
        db_path=database,
        tables=tuple(reports),
        duckdb_sha256=file_sha256(database),
        content_sha256=content,
    )


def seal_manifest(manifest: Manifest, report: BuildReport) -> Manifest:
    """Record both checksums on the manifest.

    ``duckdb_sha256`` answers "am I running against the exact file these results
    were computed on?" -- what the run gate needs. ``content_sha256`` answers
    "did the data change?" -- what the weekly refresh gate needs, and the only
    one of the two that survives a rebuild.
    """
    manifest.duckdb_sha256 = report.duckdb_sha256
    manifest.content_sha256 = report.content_sha256
    return manifest
