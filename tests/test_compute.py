"""The snapshot evaluator, on a miniature snapshot built to exercise each choice
it makes: distinct incidents, offense mapping around a reused code, missing
districts, multi-offense incidents before 2019, five-year averages, ranks,
thresholds, shootings measures, coverage, and the reasons a claim cannot be
computed."""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import pytest

from claims.schema import Claim, Geography, Measure, Source, Window
from specs.compute import SnapshotEvaluator, space_for
from specs.curve import compute_curve
from specs.labels import Label, derive_label
from specs.space import Spec


@pytest.fixture
def conn():
    db = duckdb.connect()
    db.execute(
        "CREATE TABLE offense_codes (OFFENSE_CODE INTEGER, OFFENSE_DESCRIPTION VARCHAR, "
        "description_normalized VARCHAR, ucr_category VARCHAR)"
    )
    db.executemany(
        "INSERT INTO offense_codes VALUES (?, ?, ?, ?)",
        [
            (301, "ROBBERY", "ROBBERY", "Part One"),
            (423, "ASSAULT - AGGRAVATED", "ASSAULT - AGGRAVATED", "Part One"),
            (530, "BREAKING AND ENTERING (B&E) MOTOR VEHICLE", "BREAKING AND ENTERING (B&E) MOTOR VEHICLE", "Part One"),
            (614, "LARCENY THEFT FROM MV - NON-ACCESSORY", "LARCENY THEFT FROM MV - NON-ACCESSORY", "Part One"),
            (111, "MURDER, NON-NEGLIGENT MANSLAUGHTER", "MURDER, NON-NEGLIGENT MANSLAUGHTER", "Part One"),
            (3115, "INVESTIGATE PERSON", "INVESTIGATE PERSON", "Part Three"),
        ],
    )
    db.execute(
        "CREATE TABLE crime_incidents (INCIDENT_NUMBER VARCHAR, OFFENSE_CODE INTEGER, "
        "OFFENSE_DESCRIPTION VARCHAR, OCCURRED_ON_DATE TIMESTAMP, DISTRICT VARCHAR, SHOOTING VARCHAR)"
    )
    rows = []
    # Robbery: 2024 Jan-Jun has 4 incidents (one with two rows), 2025 has 5.
    rows += [("R24-1", 301, "ROBBERY", datetime(2024, 2, 1), "B2", "0")] * 2
    rows += [(f"R24-{i}", 301, "ROBBERY", datetime(2024, 3, i), "B2", "0") for i in (2, 3)]
    rows += [("R24-4", 301, "ROBBERY", datetime(2024, 4, 1), None, "0")]           # no district
    rows += [(f"R25-{i}", 301, "ROBBERY", datetime(2025, 3, i), "B2", "0") for i in range(1, 4)]
    rows += [("R25-4", 301, "ROBBERY", datetime(2025, 4, 1), "C11", "0")]
    rows += [("R25-5", 301, "ROBBERY", datetime(2025, 5, 1), "External", "0")]     # outside the districts
    # Larceny from MV in 2025: one plain, one under the reused code 530.
    rows += [("L25-1", 614, "LARCENY THEFT FROM MV - NON-ACCESSORY", datetime(2025, 2, 1), "D4", "0")]
    rows += [("L25-2", 530, "BREAKING AND ENTERING (B&E) MOTOR VEHICLE", datetime(2025, 2, 2), "D4", "0")]
    rows += [("L24-1", 614, "LARCENY THEFT FROM MV - NON-ACCESSORY", datetime(2024, 2, 1), "D4", "0")]
    # Before 2019 an incident can list two offenses: robbery and aggravated assault.
    rows += [("M17", 301, "ROBBERY", datetime(2017, 5, 1), "A1", None),
             ("M17", 423, "ASSAULT - AGGRAVATED", datetime(2017, 5, 1), "A1", None)]
    rows += [("A18", 423, "ASSAULT - AGGRAVATED", datetime(2018, 5, 1), "A1", None)]
    # Homicides, one a year 2016-2020 (for averages and ranks), three in 2021.
    rows += [(f"H{y}", 111, "MURDER, NON-NEGLIGENT MANSLAUGHTER", datetime(y, 6, 1), "B3", "1") for y in range(2016, 2021)]
    rows += [(f"H21-{i}", 111, "MURDER, NON-NEGLIGENT MANSLAUGHTER", datetime(2021, 6, i), "B3", "1") for i in (1, 2, 3)]
    # Gunfire with no victim.
    rows += [("G25", 3115, "INVESTIGATE PERSON", datetime(2025, 3, 5), "B3", "1")]
    # Coverage: the crime series starts in 2015 and runs to 2026-09-21.
    rows += [("START", 3115, "INVESTIGATE PERSON", datetime(2015, 6, 15), "A1", None)]
    rows += [("END", 3115, "INVESTIGATE PERSON", datetime(2026, 9, 21), "A1", "0")]
    db.executemany("INSERT INTO crime_incidents VALUES (?, ?, ?, ?, ?, ?)", rows)

    db.execute("CREATE TABLE shootings (incident_num VARCHAR, shooting_date TIMESTAMP, district VARCHAR, shooting_type_v2 VARCHAR)")
    db.executemany(
        "INSERT INTO shootings VALUES (?, ?, ?, ?)",
        [
            ("S1", datetime(2024, 3, 1), "B2", "Fatal"),
            ("S2", datetime(2024, 4, 1), "B2", "Non-Fatal"),
            ("S3", datetime(2025, 3, 1), "B2", "Non-Fatal"),
            ("S3", datetime(2025, 3, 1), "B2", "Non-Fatal"),  # one incident, two victims
            ("S3", datetime(2025, 3, 1), "B2", "Fatal"),      # ...and a third
            ("S0", datetime(2015, 1, 1), "B2", "Fatal"),
            ("SZ", datetime(2026, 9, 6), "B2", "Fatal"),
        ],
    )
    return db


SOURCE = Source(kind="news", organization="Test", url="https://example.org", published=date(2026, 9, 1), retrieved=date(2026, 9, 23))
H1 = dict(kind="ytd_vs_prior_ytd", current_start=date(2025, 1, 1), current_end=date(2025, 6, 30),
          prior_start=date(2024, 1, 1), prior_end=date(2024, 6, 30))


def claim(claim_id="c", *, family="part_one_offense", name="Robbery", level="citywide", code=None,
          window=None, assertion=None, stated=None, shooting_measure=None, unit=None) -> Claim:
    return Claim(
        claim_id=claim_id,
        source=SOURCE,
        paraphrase="A test claim about Boston crime counts.",
        measure=Measure(family=family, name=name, shooting_measure=shooting_measure),
        geography=Geography(level=level, code=code, unit=unit),
        window=Window(**(window or H1)),
        assertion=assertion or {"kind": "change", "direction": "up"},
        stated=stated or {},
        cluster_id="test",
        selection_rule="test",
    )


def spec(**choices) -> Spec:
    return Spec(choices=tuple(sorted(choices.items())))


def evaluate(conn, c: Claim, **choices):
    return SnapshotEvaluator(conn, {c.claim_id: c}).evaluate(c.claim_id, spec(**choices))


CRIME = dict(offense_mapping="by_description", missing_geo="drop", multi_offense="any_offense")


class TestCounting:
    def test_change_counts_distinct_incidents(self, conn) -> None:
        # 2024: R24-1 (two rows), R24-2, R24-3 in B2 -> 3 located; 2025: 4 located.
        result = evaluate(conn, claim(), **CRIME)
        assert result.counts == {"prior": 3.0, "current": 4.0}
        assert result.value == pytest.approx(33.333, abs=1e-3)

    def test_citywide_missing_geo(self, conn) -> None:
        """Drop counts only incidents in a district; allocation also counts the
        ones with no district, but never the External one."""
        dropped = evaluate(conn, claim(), **CRIME).counts
        allocated = evaluate(conn, claim(), **{**CRIME, "missing_geo": "proportional_allocation"}).counts
        assert dropped == {"prior": 3.0, "current": 4.0}
        assert allocated == {"prior": 4.0, "current": 4.0}

    def test_district_proportional_allocation(self, conn) -> None:
        # 2024: 3 in B2, 1 unlocated, 3 located citywide -> B2 gets the whole extra one.
        c = claim(level="district", code="B2")
        assert evaluate(conn, c, **CRIME).counts["prior"] == 3.0
        assert evaluate(conn, c, **{**CRIME, "missing_geo": "proportional_allocation"}).counts["prior"] == 4.0

    def test_reused_code_follows_the_mapping(self, conn) -> None:
        """530 is a vehicle break-in by its description and a burglary by its code."""
        c = claim(name="Larceny From MV")
        by_text = evaluate(conn, c, **CRIME).counts["current"]
        by_code = evaluate(conn, c, **{**CRIME, "offense_mapping": "by_code_range"}).counts["current"]
        assert (by_text, by_code) == (2.0, 1.0)

    def test_multi_offense_incident_counts_once_under_its_most_serious_offense(self, conn) -> None:
        window = dict(kind="period", current_start=date(2017, 1, 1), current_end=date(2018, 12, 31))
        c = claim(name="Aggravated assault", window=window, assertion={"kind": "level", "stated_value": 2})
        assert evaluate(conn, c, **CRIME).value == 2.0          # M17 and A18
        assert evaluate(conn, c, **{**CRIME, "multi_offense": "most_serious_offense"}).value == 1.0  # M17 counts as robbery

    def test_gunfire_uses_the_shooting_flag(self, conn) -> None:
        c = claim(family="gunfire", name="Gunfire", assertion={"kind": "level", "stated_value": 1},
                  window=dict(kind="period", current_start=date(2025, 1, 1), current_end=date(2025, 12, 31)))
        assert evaluate(conn, c, measure="gunfire_reports", missing_geo="drop").value == 1.0


class TestWindowsAndAssertions:
    def test_five_year_average(self, conn) -> None:
        window = dict(kind="vs_five_year_average", current_start=date(2021, 1, 1), current_end=date(2021, 12, 31))
        c = claim(name="Homicide", window=window)
        result = evaluate(conn, c, **CRIME)
        assert result.counts == {"five_year_avg": 1.0, "current": 3.0}
        assert result.value == pytest.approx(200.0)

    def test_rank_among_reference_years(self, conn) -> None:
        window = dict(kind="period", current_start=date(2021, 1, 1), current_end=date(2021, 12, 31), reference_years=(2016, 2021))
        c = claim(name="Homicide", window=window, assertion={"kind": "rank", "stated_rank": 1})
        # 2021 has 3, every other year 1: it ranks 6th lowest of six.
        assert evaluate(conn, c, **CRIME).value == 6.0

    def test_rank_reaching_before_the_data_is_a_coverage_gap(self, conn) -> None:
        window = dict(kind="period", current_start=date(2021, 1, 1), current_end=date(2021, 12, 31), reference_years=(1957, 2021))
        c = claim(name="Homicide", window=window, assertion={"kind": "rank", "stated_rank": 1})
        result = evaluate(conn, c, **CRIME)
        assert result.value is None and result.reason.startswith("coverage:before_data_start")

    def test_comparison_against_a_stated_threshold(self, conn) -> None:
        window = dict(kind="period", current_start=date(2025, 1, 1), current_end=date(2025, 12, 31))
        c = claim(family="shootings", name="Non-fatal", shooting_measure="non_fatal_only", window=window,
                  assertion={"kind": "comparison", "direction": "greater"}, stated={"reference": 1})
        assert evaluate(conn, c, measure="non_fatal_only").value == 1.0   # 2 survivors minus 1

    def test_per_year_maximum_against_a_threshold(self, conn) -> None:
        window = dict(kind="period", current_start=date(2024, 1, 1), current_end=date(2025, 12, 31))
        c = claim(family="shootings", name="Victims", shooting_measure="victims_struck", window=window,
                  assertion={"kind": "comparison", "direction": "less"}, stated={"reference": 5, "per_year_max": 1})
        result = evaluate(conn, c, measure="victims_struck")
        assert result.counts == {"2024": 2.0, "2025": 3.0}
        assert result.value == -2.0

    def test_change_from_zero_is_infinite_not_missing(self, conn) -> None:
        window = dict(kind="ytd_vs_prior_ytd", current_start=date(2021, 1, 1), current_end=date(2021, 12, 31),
                      prior_start=date(2015, 7, 1), prior_end=date(2015, 12, 31))
        c = claim(name="Homicide", window={**window, "kind": "period_vs_period"})
        assert evaluate(conn, c, **CRIME).value == float("inf")


class TestShootings:
    WINDOW = dict(kind="period", current_start=date(2025, 1, 1), current_end=date(2025, 12, 31))

    @pytest.mark.parametrize(
        ("measure", "expected"),
        [("victims_struck", 3.0), ("shooting_incidents", 1.0), ("fatal_only", 1.0), ("non_fatal_only", 2.0)],
    )
    def test_measures(self, conn, measure, expected) -> None:
        c = claim(family="shootings", name="Shootings", window=self.WINDOW, assertion={"kind": "level", "stated_value": 1})
        assert evaluate(conn, c, measure=measure).value == expected

    def test_window_past_the_published_data_is_not_computable(self, conn) -> None:
        """Shootings run a week behind; a claim through a later date cannot be checked yet."""
        window = dict(kind="ytd_vs_prior_ytd", current_start=date(2026, 1, 1), current_end=date(2026, 9, 20),
                      prior_start=date(2025, 1, 1), prior_end=date(2025, 9, 20))
        c = claim(family="shootings", name="Victims", shooting_measure="victims_struck", window=window)
        result = evaluate(conn, c, measure="victims_struck")
        assert result.value is None
        assert result.reason == "coverage:after_data_end:shootings complete through 2026-09-05"


class TestNotComputable:
    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            (dict(level="cross_city"), "out_of_scope:cross_city"),
            (dict(name="Rape & Attempted"), "out_of_scope:not_in_data:rape"),
            (dict(name="Domestic Aggravated Assault"), "out_of_scope:not_in_data:domestic_aggravated_assault"),
            (dict(family="arrests", name="Retail theft arrests"), "out_of_scope:not_in_data:arrests"),
            (dict(level="neighborhood", unit="Downtown"), "engine_gap:neighborhood_geography"),
            (dict(name="Homicide rate per 100,000"), "engine_gap:population_denominator"),
        ],
    )
    def test_reasons(self, conn, overrides, reason) -> None:
        result = evaluate(conn, claim(**overrides), denominator="none")
        assert (result.value, result.reason) == (None, reason)


class TestSpaces:
    def test_recent_crime_claim_varies_mapping_and_missing_geo(self) -> None:
        space = space_for(claim())
        assert space.varying_dimensions == ("offense_mapping", "missing_geo")
        assert space.size == 4

    def test_window_before_2019_also_varies_multi_offense(self) -> None:
        window = dict(kind="vs_five_year_average", current_start=date(2021, 1, 1), current_end=date(2021, 12, 31))
        assert "multi_offense" in space_for(claim(window=window)).varying_dimensions

    def test_shootings_measure_is_pinned_when_stated_and_varies_when_not(self) -> None:
        stated = space_for(claim(family="shootings", name="Victims", shooting_measure="victims_struck"))
        vague = space_for(claim(family="shootings", name="Shootings"))
        assert stated.size == 1
        assert vague.varying_dimensions == ("measure",)


def test_ambiguous_shootings_claim_is_underdetermined_by_measure(conn) -> None:
    """The conflation this benchmark is built on: the same "shootings" claim holds
    as incidents and fails as victims."""
    window = dict(kind="period", current_start=date(2025, 1, 1), current_end=date(2025, 12, 31))
    c = claim(family="shootings", name="Shootings", window=window,
              assertion={"kind": "level", "stated_value": 1, "abs_tol": 0})
    evaluator = SnapshotEvaluator(conn, {c.claim_id: c})
    decision = derive_label(compute_curve(c.claim_id, c.assertion, space_for(c), evaluator))
    assert decision.derived == Label.UNDERDETERMINED
    assert decision.dominant_driver == "measure"


def test_open_refuses_an_unverified_snapshot(tmp_path) -> None:
    from ingest.manifest import Manifest, SnapshotMismatch

    db = tmp_path / "boston.duckdb"
    duckdb.connect(str(db)).close()
    manifest = tmp_path / "manifest.json"
    Manifest(snapshot_date="2026-09-23", base_url="x", duckdb_sha256="0" * 64).write(manifest)
    with pytest.raises(SnapshotMismatch):
        SnapshotEvaluator.open(db, manifest, {})


def test_summary_keeps_engine_gaps_apart_from_unverifiable_claims() -> None:
    """Only out-of-scope claims are a finding about the claims; engine gaps are a
    to-do list and must not inflate the unverifiable share."""
    from specs.run import summarize

    def record(label, reasons):
        return {"claim_id": label + str(len(reasons)), "source_kind": "news", "label": label,
                "dominant_driver": "measure" if label == "underdetermined" else None,
                "not_computable": reasons}

    summary = summarize(
        [
            record("unverifiable", {"out_of_scope:cross_city": 1}),
            record("unverifiable", {"engine_gap:neighborhood_geography": 1}),
            record("unverifiable", {"coverage:after_data_end:shootings": 2}),
            record("underdetermined", {}),
            record("supported", {}),
        ],
        snapshot="x",
    )
    assert summary["unverifiable_by_why"] == {"coverage": 1, "engine_gap": 1, "out_of_scope": 1}
    assert summary["computable_claims"] == 2
    assert summary["underdetermined_share_of_computable"] == 0.5
