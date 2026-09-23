"""The reproducibility audit on a tiny sealed snapshot and a handful of BPD
figures: geography matching BPD's own totals, the derived comparisons, and the
revision estimate."""

from __future__ import annotations

import json
from datetime import date, datetime

import duckdb
import pytest

from experiments.reproducibility_audit import _geo_count, audit, summarize, window_counts
from ingest.manifest import Manifest, file_sha256


@pytest.fixture
def snapshot(tmp_path):
    db = tmp_path / "boston.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE offense_codes (OFFENSE_CODE INTEGER, OFFENSE_DESCRIPTION VARCHAR, "
        "description_normalized VARCHAR, ucr_category VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO offense_codes VALUES (?, ?, ?, ?)",
        [(301, "ROBBERY", "ROBBERY", "Part One"), (423, "ASSAULT - AGGRAVATED", "ASSAULT - AGGRAVATED", "Part One")],
    )
    conn.execute(
        "CREATE TABLE crime_incidents (INCIDENT_NUMBER VARCHAR, OFFENSE_CODE INTEGER, "
        "OFFENSE_DESCRIPTION VARCHAR, OCCURRED_ON_DATE TIMESTAMP, DISTRICT VARCHAR, SHOOTING VARCHAR)"
    )
    rows = [
        # 2024 Jan 1 - Mar 31: robberies in B2 (one with two rows), one with no district, one External.
        ("R1", 301, "ROBBERY", datetime(2024, 1, 5), "B2"), ("R1", 301, "ROBBERY", datetime(2024, 1, 5), "B2"),
        ("R2", 301, "ROBBERY", datetime(2024, 2, 5), "B2"),
        ("R3", 301, "ROBBERY", datetime(2024, 3, 5), None),
        ("R4", 301, "ROBBERY", datetime(2024, 3, 6), "External"),
        ("R5", 301, "ROBBERY", datetime(2024, 3, 31), "B3"),   # the day a restated window drops
        # 2025: three robberies.
        ("R6", 301, "ROBBERY", datetime(2025, 1, 5), "B2"),
        ("R7", 301, "ROBBERY", datetime(2025, 2, 5), "B3"),
        ("R8", 301, "ROBBERY", datetime(2025, 3, 5), "B3"),
        ("A1", 423, "ASSAULT - AGGRAVATED", datetime(2025, 2, 1), "B2"),
        ("END", 423, "ASSAULT - AGGRAVATED", datetime(2026, 9, 21), "B2"),
    ]
    conn.executemany("INSERT INTO crime_incidents VALUES (?, ?, ?, ?, ?, '0')", rows)
    conn.execute("CREATE TABLE shootings (incident_num VARCHAR, shooting_date TIMESTAMP, district VARCHAR, shooting_type_v2 VARCHAR)")
    conn.execute("INSERT INTO shootings VALUES ('S1', '2026-09-06', 'B2', 'Fatal')")
    conn.close()
    manifest = tmp_path / "manifest.json"
    Manifest(snapshot_date="2026-09-23", base_url="x", duckdb_sha256=file_sha256(db)).write(manifest)
    return db, manifest


def bpd(post_id, name, prior, current, *, level="citywide", unit=None, current_end="2025-03-31", prior_end="2024-03-31"):
    year, prior_year = current_end[:4], prior_end[:4]
    return {
        "claim_id": f"bpd-{post_id}-x",
        "source": {"published": current_end},
        "measure": {"name": name, "family": "part_one_total" if name == "Totals" else "part_one_offense"},
        "geography": {"level": level, "unit": unit},
        "window": {"current_start": f"{year}-01-01", "current_end": current_end,
                   "prior_start": f"{prior_year}-01-01", "prior_end": prior_end, "kind": "ytd_vs_prior_ytd"},
        "stated": {"prior": prior, "current": current},
    }


def write(tmp_path, figures):
    path = tmp_path / "bpd.jsonl"
    path.write_text("\n".join(json.dumps(f) for f in figures) + "\n")
    return path


def test_counts_and_geography(snapshot) -> None:
    db, _ = snapshot
    counts = window_counts(duckdb.connect(str(db), read_only=True), date(2024, 1, 1), date(2024, 3, 31))
    assert counts["B2"]["robbery"] == 2          # R1 counted once
    # Citywide matches BPD's grand total: districts plus no-district, never External.
    assert _geo_count(counts, "robbery", "citywide", None) == 4
    assert _geo_count(counts, "robbery", "district", "B02") == 2
    assert _geo_count(counts, "robbery", "area", "B") == 3


def test_audit_rows_and_derived_comparisons(snapshot, tmp_path) -> None:
    db, manifest = snapshot
    figures = [
        bpd(2, "Robbery & Attempted", 4, 3),
        bpd(2, "Domestic Aggravated Assault", 1, 2),
        bpd(2, "Non-Domestic Aggravated Assault", 2, 1),
        bpd(2, "Rape & Attempted", 5, 4),
        bpd(2, "Totals", 20, 18),
    ]
    rows = {r["measure"]: r for r in audit(write(tmp_path, figures), db, manifest) if r.get("kind") != "revision"}
    robbery = rows["Robbery & Attempted"]
    assert (robbery["open_prior"], robbery["open_current"]) == (4, 3)
    assert robbery["bpd_direction"] == robbery["open_direction"] == "down"
    # The open data's aggravated assaults match BPD's non-domestic half, not the sum.
    assert rows["Aggravated Assault (domestic + non-domestic)"]["bpd_current"] == 3
    assert rows["Non-Domestic Aggravated Assault"]["ratio_current"] == 1.0
    assert rows["Domestic Aggravated Assault"]["open_current"] == 0
    assert rows["Totals excluding rape"]["bpd_current"] == 14


def test_revision_adds_back_the_dropped_day(snapshot, tmp_path) -> None:
    """2025's report shows Jan 1 - Mar 31, 2024 as "current": 4. 2026's report
    restates 2024 as "prior" over Jan 1 - Mar 30 as 4. The open data has one
    robbery on Mar 31, 2024, so BPD revised its own figure up by one."""
    db, manifest = snapshot
    figures = [
        bpd(1, "Robbery & Attempted", 3, 4, current_end="2024-03-31", prior_end="2023-03-31"),
        bpd(3, "Robbery & Attempted", 4, 3, current_end="2025-03-30", prior_end="2024-03-30"),
    ]
    revisions = [r for r in audit(write(tmp_path, figures), db, manifest) if r.get("kind") == "revision"]
    assert len(revisions) == 1
    r = revisions[0]
    assert (r["first_published"], r["restated"], r["days_dropped"], r["open_count_dropped_days"]) == (4, 4, 1, 1)
    assert r["revision"] == 1 and r["revision_pct"] == 25.0


def test_windows_past_the_data_are_skipped(snapshot, tmp_path) -> None:
    db, manifest = snapshot
    late = bpd(9, "Robbery & Attempted", 1, 1, current_end="2026-09-21", prior_end="2025-09-21")
    assert audit(write(tmp_path, [late]), db, manifest) == []


def test_summary_counts_direction_agreement_and_age(snapshot, tmp_path) -> None:
    db, manifest = snapshot
    records = audit(write(tmp_path, [bpd(2, "Robbery & Attempted", 4, 3), bpd(2, "Totals", 20, 22)]), db, manifest)
    summary = summarize(records, date(2026, 9, 23))
    robbery = summary["citywide_by_measure"]["Robbery & Attempted"]
    assert robbery["direction_agrees"] == 1.0 and robbery["reports"] == 1
    assert summary["totals_direction_bpd_vs_open"]
