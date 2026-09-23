"""The agent's tools against a small snapshot file: what they show, what they
refuse, and that every failure comes back as data."""

from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from env.limitations import load_limitations
from env.tools import Environment


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("snapshot") / "boston.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE crime_incidents (INCIDENT_NUMBER VARCHAR, OFFENSE_CODE INTEGER, OCCURRED_ON_DATE TIMESTAMP, DISTRICT VARCHAR)")
    conn.executemany(
        "INSERT INTO crime_incidents VALUES (?, ?, ?, ?)",
        [(f"I{i}", 301, datetime(2025, 1, 1 + i % 28), "B2" if i % 3 else None) for i in range(120)],
    )
    conn.execute("CREATE TABLE shootings (incident_num VARCHAR, shooting_type_v2 VARCHAR)")
    conn.execute("INSERT INTO shootings VALUES ('S1', 'Fatal')")
    conn.close()
    return path


@pytest.fixture(scope="module")
def env(db):
    return Environment(db, load_limitations(), query_timeout_s=3.0)


class TestLooking:
    def test_list_tables(self, env) -> None:
        result = env.call("list_tables", {})
        assert result.ok
        assert {"table": "crime_incidents", "rows": 120} in result.content

    def test_describe_table(self, env) -> None:
        result = env.call("describe_table", {"table": "crime_incidents"})
        columns = {c["column"]: c for c in result.content["columns"]}
        assert columns["DISTRICT"]["share_null"] == pytest.approx(40 / 120, abs=1e-3)
        assert columns["OFFENSE_CODE"]["examples"] == ["301"]

    def test_unknown_table_is_an_error_not_an_exception(self, env) -> None:
        result = env.call("describe_table", {"table": "users"})
        assert (result.ok, result.error_class) == (False, "unknown_table")


class TestQuery:
    def test_select_returns_rows(self, env) -> None:
        result = env.call("query", {"sql": "SELECT count(DISTINCT INCIDENT_NUMBER) AS n FROM crime_incidents"})
        assert result.ok and result.content["rows"] == [[120]]

    def test_large_results_are_truncated_for_the_model(self, env) -> None:
        result = env.call("query", {"sql": "SELECT INCIDENT_NUMBER FROM crime_incidents"})
        assert result.content["row_count"] == 120
        assert len(result.content["rows"]) == 50 and result.content["truncated"]

    @pytest.mark.parametrize(
        ("sql", "error_class"),
        [
            ("SELECT * FROM read_csv('/etc/passwd')", "guard:"),
            ("DROP TABLE crime_incidents", "guard:"),
            ("SELECT * FROM crime_incidents; SELECT 1", "guard:multiple_statements"),
            ("SELECT * FROM not_a_table", "guard:"),
        ],
    )
    def test_the_guard_refuses_and_says_why(self, env, sql, error_class) -> None:
        result = env.call("query", {"sql": sql})
        assert not result.ok and result.error_class.startswith(error_class)

    def test_a_runaway_query_times_out_without_taking_the_harness_down(self, env) -> None:
        sql = "SELECT count(*) FROM crime_incidents a, crime_incidents b, crime_incidents c, crime_incidents d, crime_incidents e"
        result = env.call("query", {"sql": sql})
        assert (result.ok, result.error_class) == (False, "timeout")
        assert env.call("query", {"sql": "SELECT 1 AS one FROM crime_incidents LIMIT 1"}).ok

    def test_sql_errors_come_back_as_data(self, env) -> None:
        result = env.call("query", {"sql": "SELECT no_such_column FROM crime_incidents"})
        assert not result.ok and result.error_class


class TestLimitationsAndVerdict:
    def test_index_then_document(self, env) -> None:
        index = env.call("read_limitation_doc", {})
        assert any(d["id"] == "LIM-SMALL-N" for d in index.content)
        doc = env.call("read_limitation_doc", {"doc_id": "lim-small-n"})
        assert doc.ok and "## Evidence" in doc.content["text"]

    def test_unknown_limitation(self, env) -> None:
        assert env.call("read_limitation_doc", {"doc_id": "LIM-NOPE"}).error_class == "unknown_limitation"

    def test_valid_verdict_ends_the_episode(self, env) -> None:
        result = env.call("submit_verdict", {"verdict": "Underdetermined", "spec_sensitive": True, "limitation_ids": ["LIM-SMALL-N"]})
        assert result.ok and result.terminal and result.verdict.verdict == "underdetermined"

    @pytest.mark.parametrize(
        ("args", "error_class"),
        [
            ({"verdict": "probably true"}, "bad_verdict"),
            ({"verdict": "supported", "limitation_ids": ["LIM-MADE-UP"]}, "unknown_limitation"),
            ({"verdict": "supported", "confidence": 0.9}, "bad_arguments"),
            ({}, "bad_arguments"),
        ],
    )
    def test_invalid_verdicts_are_refused_so_the_agent_can_retry(self, env, args, error_class) -> None:
        result = env.call("submit_verdict", args)
        assert (result.ok, result.terminal, result.error_class) == (False, False, error_class)

    def test_unknown_tool(self, env) -> None:
        assert env.call("rm_rf", {}).error_class == "unknown_tool"


def test_tool_set_can_be_restricted_but_submit_is_always_on(db) -> None:
    env = Environment(db, load_limitations(), enabled=("query",))
    assert [s.name for s in env.specs()] == ["query", "submit_verdict"]
    assert env.call("list_tables", {}).error_class == "unknown_tool"


def test_specs_carry_json_schemas(env) -> None:
    submit = next(s for s in env.specs() if s.name == "submit_verdict")
    assert submit.parameters["properties"]["verdict"]["type"] == "string"
    assert submit.as_function()["function"]["name"] == "submit_verdict"
