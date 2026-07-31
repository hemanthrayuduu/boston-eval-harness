"""The guard must reject these. Every one is a way out of a naive SELECT-only check.

Several of these are not hypothetical. Verified against DuckDB 1.5.5:
a ``read_only=True`` connection executes ``SELECT * FROM read_csv('secret.csv')``
and returns the file contents, and executes ``COPY (SELECT 1) TO 'out.csv'``
writing to disk. sqlglot reports the read_csv source as a Table with an empty
name, so an allowlist keyed on table names alone lets it through.
"""

from __future__ import annotations

import pytest

from env.guard import GuardError, enforce_limit, validate

SNAPSHOT_TABLES = frozenset(
    {"crime_incidents", "shootings", "offense_codes", "population_acs", "neighborhoods"}
)


MALICIOUS: list[tuple[str, str, str]] = [
    # (id, sql, expected reason code)
    ("read_csv_local_file", "SELECT * FROM read_csv('/etc/passwd')", "non_table_source"),
    ("read_csv_auto", "SELECT * FROM read_csv_auto('secret.csv')", "non_table_source"),
    ("read_parquet", "SELECT * FROM read_parquet('/tmp/x.parquet')", "non_table_source"),
    ("read_json", "SELECT * FROM read_json_auto('/tmp/x.json')", "non_table_source"),
    ("read_text", "SELECT * FROM read_text('/etc/hostname')", "non_table_source"),
    ("parquet_scan", "SELECT * FROM parquet_scan('/tmp/x.parquet')", "non_table_source"),
    ("glob_filesystem", "SELECT * FROM glob('/**')", "non_table_source"),
    ("httpfs_remote", "SELECT * FROM read_csv('https://evil.example/x.csv')", "non_table_source"),
    ("copy_to_file", "COPY (SELECT 1) TO '/tmp/out.csv'", "disallowed_statement"),
    ("attach_db", "ATTACH '/tmp/other.db' AS other", "disallowed_statement"),
    ("install_extension", "INSTALL httpfs", "disallowed_statement"),
    ("load_extension", "LOAD httpfs", "disallowed_statement"),
    ("pragma", "PRAGMA database_list", "disallowed_statement"),
    ("set_config", "SET enable_external_access=true", "disallowed_statement"),
    ("delete_rows", "DELETE FROM crime_incidents", "disallowed_statement"),
    ("insert_rows", "INSERT INTO crime_incidents VALUES (1)", "disallowed_statement"),
    ("update_rows", "UPDATE crime_incidents SET offense_code = 1", "disallowed_statement"),
    ("drop_table", "DROP TABLE crime_incidents", "disallowed_statement"),
    ("create_table", "CREATE TABLE evil AS SELECT 1", "disallowed_statement"),
    ("stacked_statements", "SELECT 1; DROP TABLE crime_incidents", "multiple_statements"),
    ("unknown_table", "SELECT * FROM secrets", "unknown_table"),
    ("duckdb_introspection", "SELECT * FROM duckdb_settings()", "non_table_source"),
    (
        "nested_read_csv_in_subquery",
        "SELECT * FROM crime_incidents WHERE 1 IN (SELECT * FROM read_csv('/etc/passwd'))",
        "non_table_source",
    ),
    (
        "read_csv_hidden_in_cte",
        "WITH leak AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM leak",
        "non_table_source",
    ),
    (
        "join_to_unknown_table",
        "SELECT * FROM crime_incidents c JOIN secrets s ON c.id = s.id",
        "unknown_table",
    ),
]


@pytest.mark.parametrize("case_id,sql,expected_code", MALICIOUS, ids=[c[0] for c in MALICIOUS])
def test_malicious_query_rejected(case_id: str, sql: str, expected_code: str) -> None:
    with pytest.raises(GuardError) as excinfo:
        validate(sql, SNAPSHOT_TABLES)
    assert excinfo.value.code == expected_code, (
        f"{case_id}: rejected, but for the wrong reason "
        f"(got {excinfo.value.code!r}, expected {expected_code!r})"
    )


BENIGN: list[tuple[str, str, set[str]]] = [
    ("simple_select", "SELECT * FROM crime_incidents", {"crime_incidents"}),
    (
        "join_two_tables",
        "SELECT n.name FROM crime_incidents c JOIN neighborhoods n ON c.nbhd = n.id",
        {"crime_incidents", "neighborhoods"},
    ),
    (
        "cte_over_allowed_table",
        "WITH c AS (SELECT * FROM crime_incidents) SELECT count(*) FROM c",
        {"crime_incidents"},
    ),
    (
        "aggregate_with_group_by",
        "SELECT nbhd, count(*) FROM crime_incidents GROUP BY nbhd ORDER BY 2 DESC",
        {"crime_incidents"},
    ),
    (
        "rate_with_population_join",
        "SELECT c.nbhd, count(*) * 1000.0 / p.pop_total AS rate "
        "FROM crime_incidents c JOIN population_acs p ON c.nbhd = p.nbhd "
        "GROUP BY c.nbhd, p.pop_total",
        {"crime_incidents", "population_acs"},
    ),
    (
        "union_of_allowed_tables",
        "SELECT nbhd FROM crime_incidents UNION SELECT nbhd FROM shootings",
        {"crime_incidents", "shootings"},
    ),
    ("scalar_function_ok", "SELECT upper(nbhd) FROM crime_incidents", {"crime_incidents"}),
]


@pytest.mark.parametrize("case_id,sql,tables", BENIGN, ids=[c[0] for c in BENIGN])
def test_benign_query_allowed(case_id: str, sql: str, tables: set[str]) -> None:
    result = validate(sql, SNAPSHOT_TABLES)
    assert result.tables == frozenset(tables)


def test_unknown_table_function_rejected_without_being_blocklisted() -> None:
    """The property that actually matters.

    A name blocklist can only reject functions someone thought of. The structural
    rule -- every source must be a named, allowlisted table -- rejects table-valued
    functions that do not exist yet.
    """
    with pytest.raises(GuardError) as excinfo:
        validate("SELECT * FROM some_future_scan_fn('/etc/passwd')", SNAPSHOT_TABLES)
    assert excinfo.value.code == "non_table_source"


def test_blocked_function_caught_outside_from_clause() -> None:
    """Defence in depth: no Table node exists here, so the blocklist is the catch."""
    with pytest.raises(GuardError) as excinfo:
        validate("SELECT getenv('AWS_SECRET_ACCESS_KEY')", SNAPSHOT_TABLES)
    assert excinfo.value.code == "blocked_function"


def test_cte_name_not_reported_as_snapshot_table() -> None:
    """Retrieval metrics score against real tables; a CTE alias is not one."""
    result = validate(
        "WITH c AS (SELECT * FROM crime_incidents) SELECT * FROM c", SNAPSHOT_TABLES
    )
    assert result.tables == frozenset({"crime_incidents"})
    assert "c" not in result.tables


def test_empty_sql_rejected() -> None:
    with pytest.raises(GuardError) as excinfo:
        validate("   ", SNAPSHOT_TABLES)
    assert excinfo.value.code == "empty"


def test_unparseable_sql_rejected() -> None:
    with pytest.raises(GuardError) as excinfo:
        validate("SELECT FROM WHERE ((", SNAPSHOT_TABLES)
    assert excinfo.value.code == "parse_error"


class TestEnforceLimit:
    def test_adds_limit_when_absent(self) -> None:
        assert "LIMIT 500" in enforce_limit("SELECT * FROM crime_incidents").upper()

    def test_leaves_smaller_limit_alone(self) -> None:
        sql = "SELECT * FROM crime_incidents LIMIT 10"
        assert enforce_limit(sql, 500) == sql

    def test_tightens_larger_limit(self) -> None:
        out = enforce_limit("SELECT * FROM crime_incidents LIMIT 100000", 500)
        assert "LIMIT 500" in out.upper()
