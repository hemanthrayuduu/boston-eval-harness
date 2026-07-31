"""The sandbox must contain what the guard would have caught, independently.

Two layers, deliberately not sharing assumptions: if a guard rule is wrong or a
new DuckDB function slips past it, ``enable_external_access=false`` and process
isolation still hold the line.
"""

from __future__ import annotations

import duckdb
import pytest

from env.sandbox import execute


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("snapshot") / "boston.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE crime_incidents AS "
        "SELECT * FROM (VALUES ('Roxbury', 3), ('Dorchester', 438)) AS t(nbhd, n)"
    )
    conn.close()
    return str(path)


@pytest.fixture(scope="module")
def secret(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("secrets") / "secret.csv"
    path.write_text("user,token\nadmin,hunter2\n")
    return str(path)


def test_valid_query_returns_rows(db: str) -> None:
    result = execute(db, "SELECT nbhd, n FROM crime_incidents ORDER BY n")
    assert result.ok
    assert result.row_count == 2
    assert result.columns == ["nbhd", "n"]
    assert result.rows[0][0] == "Roxbury"
    assert result.latency_ms > 0


def test_local_file_read_is_blocked_at_the_engine(db: str, secret: str) -> None:
    """The vulnerability this whole module exists for.

    On a plain ``read_only=True`` connection this returns ('admin', 'hunter2').
    """
    result = execute(db, f"SELECT * FROM read_csv('{secret}')")
    assert not result.ok
    assert "hunter2" not in (result.error or "")


def test_filesystem_write_is_blocked(db: str, tmp_path) -> None:
    target = tmp_path / "exfil.csv"
    result = execute(db, f"COPY (SELECT 1) TO '{target}'")
    assert not result.ok
    assert not target.exists()


def test_timeout_kills_runaway_query(db: str) -> None:
    # DuckDB has no statement_timeout, so this is the only mechanism there is.
    runaway = (
        "SELECT count(*) FROM range(100000000) a, range(100000000) b, range(100000000) c"
    )
    result = execute(db, runaway, timeout_s=2.0)
    assert not result.ok
    assert result.timed_out
    assert result.latency_ms < 10_000


def test_syntax_error_is_classified_not_raised(db: str) -> None:
    result = execute(db, "SELECT FROM WHERE")
    assert not result.ok
    assert result.error_class in {"syntax_error", "sql_error"}


def test_missing_table_is_classified(db: str) -> None:
    result = execute(db, "SELECT * FROM does_not_exist")
    assert not result.ok
    assert result.error_class in {"catalog_error", "sql_error"}


def test_worker_failure_does_not_kill_the_harness(db: str) -> None:
    """Whatever happens in the subprocess, execute() returns a value."""
    for sql in ["SELECT 1/0", "SELECT * FROM range(1e18)", "SELECT abcdef"]:
        result = execute(db, sql, timeout_s=3.0)
        assert isinstance(result.ok, bool)
        if not result.ok:
            assert result.error_class


def test_snapshot_is_not_mutated(db: str) -> None:
    before = duckdb.connect(db, read_only=True).execute(
        "SELECT count(*) FROM crime_incidents"
    ).fetchone()[0]
    execute(db, "INSERT INTO crime_incidents VALUES ('Fenway', 1)")
    execute(db, "DROP TABLE crime_incidents")
    after = duckdb.connect(db, read_only=True).execute(
        "SELECT count(*) FROM crime_incidents"
    ).fetchone()[0]
    assert before == after == 2
