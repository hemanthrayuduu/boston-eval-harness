"""Parquet -> DuckDB, with explicit casts and measured coercion loss.

Two things this module refuses to do quietly.

**Silent coercion.** Ingestion keeps everything as strings precisely so that
typing is a decision made here, in the open. Every cast is measured: how many
non-null values went in, how many survived. A cast that nulls 12% of a column is
not a type conversion, it is data loss, and the build stops rather than handing
you a table that looks clean and is not.

**A checksum that promises more than it delivers.** A DuckDB file hash is a hash
of *bytes*, and bytes move for reasons the data did not. Verified: building the
same content to a different filename changes three bytes, because the filename is
embedded in the file. At real size, even rebuilding to the *same* path changes
the file -- the 2026-09-23 snapshot (1.9M rows) hashed differently on two
consecutive builds while its content digest held, presumably because large
tables load in parallel. Small tables do rebuild byte-identically. A DuckDB
storage-format change across versions would move the bytes too.

So the file hash answers "is this the exact file the results were computed
against?" -- identity, which is what the run gate needs. It cannot answer "did
the data change?", because a rename or an engine upgrade would both say yes. The
refresh gate needs that second question, so a content digest is computed from the
data itself: order-independent, path-independent, format-independent.

Run after ``ingest.pull``, using the table definitions in ``ingest.tables``:

    uv run python -m ingest.build_db
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ingest.manifest import Manifest, file_sha256

__all__ = [
    "BuildError",
    "TableSpec",
    "DerivedTable",
    "CastReport",
    "TableReport",
    "BuildReport",
    "build_database",
    "check_coverage",
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
    resource_id: str | tuple[str, ...]
    """One resource, or several with identical columns loaded as one table --
    the crime incidents ship as one file per year."""
    casts: dict[str, str] = field(default_factory=dict)
    """Column -> DuckDB type. Columns not listed stay VARCHAR, as fetched."""
    formats: dict[str, str] = field(default_factory=dict)
    """Column -> strptime format, for timestamps a plain cast cannot read, like
    ``07/08/2012 06:00:00 AM``. Each column here must also be in ``casts``."""
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
        if not self.resource_ids:
            raise BuildError(f"{self.table_name}: needs at least one resource")
        stray = sorted(set(self.formats) - set(self.casts))
        if stray:
            raise BuildError(
                f"{self.table_name}: formats given for columns with no cast: {stray}"
            )

    @property
    def resource_ids(self) -> tuple[str, ...]:
        if isinstance(self.resource_id, str):
            return (self.resource_id,)
        return tuple(self.resource_id)


@dataclass(frozen=True)
class DerivedTable:
    """A table computed from loaded tables rather than loaded from a resource --
    the offense-code lookup, which joins crime_incidents with hand labels.

    ``build`` receives the open build connection after every TableSpec has
    loaded, must create ``table_name``, and raises BuildError to stop the build.
    """

    table_name: str
    build: Callable[[duckdb.DuckDBPyConnection], None]

    def __post_init__(self) -> None:
        if not _SAFE_IDENTIFIER.match(self.table_name):
            raise BuildError(
                f"table name {self.table_name!r} must be a plain lowercase identifier"
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
    resource_ids: tuple[str, ...]
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
            *(
                f"    {t.table_name:<28} {t.row_count:>9,} rows"
                + (
                    "  (derived)"
                    if not t.resource_ids
                    else f"  ({len(t.resource_ids)} resources)"
                    if len(t.resource_ids) > 1
                    else ""
                )
                for t in self.tables
            ),
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


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _source(parquets: Sequence[Path]) -> str:
    files = ", ".join(_literal(str(p)) for p in parquets)
    return f"read_parquet([{files}], union_by_name = true)"


def _cast_expr(spec: TableSpec, column: str) -> str:
    quoted = _quote(column)
    target = spec.casts[column]
    fmt = spec.formats.get(column)
    if fmt is None:
        return f"TRY_CAST({quoted} AS {target})"
    return f"TRY_CAST(try_strptime({quoted}, {_literal(fmt)}) AS {target})"


def _columns(conn: duckdb.DuckDBPyConnection, parquet: Path) -> list[str]:
    return [
        r[0] for r in conn.execute(f"SELECT * FROM {_source([parquet])} LIMIT 0").description
    ]


def _check_same_columns(
    conn: duckdb.DuckDBPyConnection, spec: TableSpec, parquets: Sequence[Path]
) -> list[str]:
    """Columns shared by every resource, or an error naming the differences.

    Unioning by name would happily null-fill a column one year's file lacks, and
    the result would look like a column that is null at source for that year.
    """
    first = _columns(conn, parquets[0])
    for parquet in parquets[1:]:
        other = _columns(conn, parquet)
        if set(other) != set(first):
            raise BuildError(
                f"{spec.table_name}: resources have different columns.\n"
                f"  {parquets[0].stem}: {first}\n"
                f"  {parquet.stem}: {other}\n"
                "Load them as separate tables, or harmonise them explicitly first."
            )
    return first


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
    conn: duckdb.DuckDBPyConnection, spec: TableSpec, parquets: Sequence[Path]
) -> tuple[int, tuple[str, ...]]:
    source = _source(parquets)
    available = _check_same_columns(conn, spec, parquets)

    unknown = set(spec.casts) - set(available)
    if unknown:
        raise BuildError(
            f"{spec.table_name}: cast requested for columns not in the resource: "
            f"{sorted(unknown)}. Available: {available}"
        )

    projection = ", ".join(
        (
            f"{_cast_expr(spec, column)} AS {_quote(column)}"
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
    conn: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    parquets: Sequence[Path],
    row_count: int,
) -> tuple[CastReport, ...]:
    """Measure each cast against the *source*, before typing was applied."""
    reports = []
    source = _source(parquets)

    for column, target in sorted(spec.casts.items()):
        non_null, cast_ok = conn.execute(
            f"SELECT count({_quote(column)}), count({_cast_expr(spec, column)}) FROM {source}"
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


def _load_all(
    conn: duckdb.DuckDBPyConnection,
    specs: Sequence[TableSpec],
    derived: Sequence[DerivedTable],
    raw: Path,
    max_cast_loss: float,
    reports: list[TableReport],
    violations: list[CastReport],
) -> None:
    for spec in specs:
        parquets = [raw / f"{rid}.parquet" for rid in spec.resource_ids]
        missing = [p.stem for p in parquets if not p.exists()]
        if missing:
            raise BuildError(
                f"{spec.table_name}: no Parquet for resource(s) {missing} "
                f"in {raw}. Run ingest.pull first."
            )

        row_count, columns = _load_table(conn, spec, parquets)
        casts = _measure_casts(conn, spec, parquets, row_count)

        allowance = spec.max_cast_loss if spec.max_cast_loss is not None else max_cast_loss
        violations.extend(c for c in casts if c.loss_rate > allowance)

        reports.append(
            TableReport(
                table_name=spec.table_name,
                resource_ids=spec.resource_ids,
                row_count=row_count,
                columns=columns,
                content_digest=_content_digest(conn, spec.table_name),
                casts=casts,
            )
        )

    for table in derived:
        table.build(conn)
        quoted = _quote(table.table_name)
        exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table.table_name],
        ).fetchone()[0]
        if not exists:
            raise BuildError(f"derived table {table.table_name!r}: builder did not create it")
        reports.append(
            TableReport(
                table_name=table.table_name,
                resource_ids=(),
                row_count=conn.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0],
                columns=tuple(
                    r[0] for r in conn.execute(f"SELECT * FROM {quoted} LIMIT 0").description
                ),
                content_digest=_content_digest(conn, table.table_name),
            )
        )


def build_database(
    specs: list[TableSpec],
    raw_dir: str | Path,
    db_path: str | Path,
    *,
    derived: Sequence[DerivedTable] = (),
    max_cast_loss: float = DEFAULT_MAX_CAST_LOSS,
) -> BuildReport:
    """Load Parquet resources into a fresh DuckDB file, then build derived tables.

    Raises :class:`BuildError` if any cast loses more of a column than allowed,
    or if a derived table's builder refuses. A failed build leaves no file.
    """
    raw = Path(raw_dir)
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    # Always a fresh file: appending to an existing database would make the
    # checksum describe a history rather than a snapshot.
    database.unlink(missing_ok=True)

    names = [spec.table_name for spec in specs] + [d.table_name for d in derived]
    if len(set(names)) != len(names):
        raise BuildError(f"duplicate table names: {sorted({n for n in names if names.count(n) > 1})}")

    reports: list[TableReport] = []
    violations: list[CastReport] = []

    conn = duckdb.connect(str(database))
    try:
        _load_all(conn, specs, derived, raw, max_cast_loss, reports, violations)
    except BuildError:
        conn.close()
        database.unlink(missing_ok=True)
        raise
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


def check_coverage(
    manifest: Manifest, specs: Sequence[TableSpec], unused: Mapping[str, str]
) -> None:
    """Every pulled resource is either loaded or deliberately left out.

    A resource that appears in the catalog after the table definitions were
    written -- a new yearly file, a replaced "to present" file -- would otherwise
    be pulled, checksummed into the manifest, and never loaded, and every count
    over that table would quietly come up short.
    """
    entries = manifest.by_resource_id()
    assigned = [rid for spec in specs for rid in spec.resource_ids]

    double = sorted(
        {rid for rid in assigned if assigned.count(rid) > 1} | (set(assigned) & set(unused))
    )
    unassigned = sorted(set(entries) - set(assigned) - set(unused))
    unknown = sorted((set(assigned) | set(unused)) - set(entries))

    problems = []
    if unassigned:
        problems.append(
            "pulled but neither loaded nor listed as unused:\n"
            + "\n".join(
                f"    {rid}  {entries[rid].dataset_id} / {entries[rid].name or '-'} "
                f"({entries[rid].row_count:,} rows, {list(entries[rid].columns)[:5]})"
                for rid in unassigned
            )
        )
    if unknown:
        problems.append(
            "configured but not in this snapshot (removed from the catalog?):\n"
            + "\n".join(f"    {rid}" for rid in unknown)
        )
    if double:
        problems.append(
            "assigned more than once:\n" + "\n".join(f"    {rid}" for rid in double)
        )
    if problems:
        raise BuildError(
            "table definitions do not match the snapshot. Update ingest/tables.py.\n  "
            + "\n  ".join(problems)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the DuckDB snapshot from pulled Parquet and seal the manifest."
    )
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--db", type=Path, default=Path("data/boston.duckdb"))
    args = parser.parse_args(argv)

    from ingest.tables import SNAPSHOT_DERIVED, SNAPSHOT_TABLES, UNUSED_RESOURCES

    manifest = Manifest.read(args.manifest)
    try:
        check_coverage(manifest, SNAPSHOT_TABLES, UNUSED_RESOURCES)
        report = build_database(
            list(SNAPSHOT_TABLES), args.raw, args.db, derived=SNAPSHOT_DERIVED
        )
    except BuildError as err:
        print(f"build failed: {err}", file=sys.stderr)
        return 1

    seal_manifest(manifest, report).write(args.manifest)
    print(report.render())
    print(f"sealed {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
