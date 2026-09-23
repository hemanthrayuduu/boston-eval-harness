"""The offense-code lookup: every (code, description) pair labelled, every label
traceable, and nothing counted one way or the other by default."""

from __future__ import annotations

import csv

import duckdb
import pytest

from ingest.build_db import BuildError
from ingest.offense_codes import LABELS_PATH, build_offense_codes, normalize, read_labels

LABEL_HEADER = ["OFFENSE_CODE", "description", "ucr_part", "is_crime", "rationale", "reviewed"]


def write_labels(path, *rows) -> str:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(LABEL_HEADER)
        writer.writerows(rows)
    return str(path)


@pytest.fixture
def conn():
    """A miniature of the real problem: UCR_PART blank from 2019, a code reused
    with a new meaning, a Part Three crime, and a spacing variant."""
    db = duckdb.connect()
    db.execute(
        "CREATE TABLE crime_incidents (INCIDENT_NUMBER VARCHAR, OFFENSE_CODE INTEGER, "
        "OFFENSE_DESCRIPTION VARCHAR, YEAR INTEGER, UCR_PART VARCHAR)"
    )
    db.executemany(
        "INSERT INTO crime_incidents VALUES (?, ?, ?, ?, ?)",
        [
            ("I1", 613, "LARCENY SHOPLIFTING", 2018, "Part One"),
            ("I2", 613, "LARCENY SHOPLIFTING", 2021, None),  # same pair, part now blank
            ("I3", 613, "LARCENY  SHOPLIFTING ", 2022, None),  # spacing variant
            ("I4", 1831, "DRUGS - SICK ASSIST - OTHER NARCOTIC", 2017, "Part Two"),
            ("I5", 1831, "SICK ASSIST", 2023, None),  # code reused
            ("I6", 3831, "M/V - LEAVING SCENE - PROPERTY DAMAGE", 2018, "Part Three"),
            ("I7", 3115, "INVESTIGATE PERSON", 2016, "Part Three"),
        ],
    )
    db.execute("CREATE TABLE offense_codes_source (CODE INTEGER, NAME VARCHAR)")
    db.executemany(
        "INSERT INTO offense_codes_source VALUES (?, ?)",
        [(613, "LARCENY SHOPLIFTING "), (1831, "SICK ASSIST"), (1831, "DRUGS - SICK ASSIST")],
    )
    return db


def reused_code_label() -> list:
    return [1831, "SICK ASSIST", "Part Two", "false", "code reused; a medical response", "no"]


def lookup(conn) -> dict:
    rows = conn.execute(
        "SELECT OFFENSE_CODE, OFFENSE_DESCRIPTION, ucr_category, ucr_category_source, is_crime, "
        "is_crime_source, reviewed FROM offense_codes"
    ).fetchall()
    return {(r[0], r[1]): r[2:] for r in rows}


class TestLabelling:
    def test_observed_pairs_carry_bpds_part_onto_blank_years(self, conn, tmp_path) -> None:
        build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))
        # 613 is labelled Part One in 2018 and blank in 2021; the pair carries it.
        assert lookup(conn)[(613, "LARCENY SHOPLIFTING")][:2] == ("Part One", "observed")

    def test_reused_code_is_labelled_by_pair_not_by_code(self, conn, tmp_path) -> None:
        """Mapping by code alone would make 2023's sick assist a Part Two drug
        crime. The pair keeps the two meanings apart."""
        build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))
        table = lookup(conn)
        assert table[(1831, "DRUGS - SICK ASSIST - OTHER NARCOTIC")] == (
            "Part Two", "observed", True, "rule", None
        )
        assert table[(1831, "SICK ASSIST")] == ("Part Two", "hand", False, "hand", False)

    def test_part_three_crime_can_be_overridden(self, conn, tmp_path) -> None:
        build_offense_codes(
            conn,
            write_labels(
                tmp_path / "l.csv",
                reused_code_label(),
                [3831, "M/V - LEAVING SCENE - PROPERTY DAMAGE", "", "true", "hit and run", "no"],
            ),
        )
        table = lookup(conn)
        assert table[(3831, "M/V - LEAVING SCENE - PROPERTY DAMAGE")][:4] == (
            "Part Three", "observed", True, "hand"
        )
        # Untouched Part Three stays non-crime by rule.
        assert table[(3115, "INVESTIGATE PERSON")][2:4] == (False, "rule")

    def test_spacing_variants_share_a_label_but_keep_their_raw_text(self, conn, tmp_path) -> None:
        build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))
        table = lookup(conn)
        # Both raw strings are rows, so the join key is exactly what crime_incidents holds.
        assert table[(613, "LARCENY  SHOPLIFTING ")][:2] == ("Part One", "observed")
        joined = conn.execute(
            "SELECT count(*) FROM crime_incidents "
            "JOIN offense_codes USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)"
        ).fetchone()[0]
        assert joined == 7

    def test_published_names_expose_the_code_lists_ambiguity(self, conn, tmp_path) -> None:
        build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))
        names = conn.execute(
            "SELECT published_names FROM offense_codes WHERE OFFENSE_DESCRIPTION = 'SICK ASSIST'"
        ).fetchone()[0]
        assert names == ["DRUGS - SICK ASSIST", "SICK ASSIST"]


def test_lookup_columns_do_not_shadow_crime_columns(conn, tmp_path) -> None:
    """A joined query filtering on the lookup's category must not silently read
    crime_incidents.UCR_PART, which is blank from 2019. Found by hand-verifying a
    district claim that came back as zero."""
    build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))
    n = conn.execute(
        "SELECT count(*) FROM crime_incidents JOIN offense_codes "
        "USING (OFFENSE_CODE, OFFENSE_DESCRIPTION) WHERE ucr_category = 'Part One'"
    ).fetchone()[0]
    assert n == 3  # all three 613 rows, including the two from years with UCR_PART blank


class TestRefusals:
    """Each of these would otherwise count incidents one way or the other by
    default, invisibly."""

    def test_unlabelled_pair_stops_the_build_with_context(self, conn, tmp_path) -> None:
        with pytest.raises(BuildError, match="no ucr_part") as excinfo:
            build_offense_codes(conn, write_labels(tmp_path / "l.csv"))
        message = str(excinfo.value)
        assert "(1831, 'SICK ASSIST')" in message
        assert "2023-2023" in message

    def test_other_without_is_crime_stops_the_build(self, conn, tmp_path) -> None:
        conn.execute("INSERT INTO crime_incidents VALUES ('I8', 900, 'ARSON', 2017, 'Other')")
        with pytest.raises(BuildError, match="no is_crime.*ARSON"):
            build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))

    def test_hand_label_contradicting_bpd_is_refused(self, conn, tmp_path) -> None:
        with pytest.raises(BuildError, match="BPD labels"):
            build_offense_codes(
                conn,
                write_labels(
                    tmp_path / "l.csv",
                    reused_code_label(),
                    [613, "LARCENY SHOPLIFTING", "Part Two", "", "disagree", "no"],
                ),
            )

    def test_stale_label_is_refused(self, conn, tmp_path) -> None:
        with pytest.raises(BuildError, match="matches no"):
            build_offense_codes(
                conn,
                write_labels(
                    tmp_path / "l.csv",
                    reused_code_label(),
                    [9999, "GONE", "Other", "false", "old code", "no"],
                ),
            )

    def test_pair_with_two_observed_parts_is_refused(self, conn, tmp_path) -> None:
        conn.execute(
            "INSERT INTO crime_incidents VALUES ('I9', 613, 'LARCENY SHOPLIFTING', 2017, 'Part Two')"
        )
        with pytest.raises(BuildError, match="more than one UCR part"):
            build_offense_codes(conn, write_labels(tmp_path / "l.csv", reused_code_label()))

    def test_failed_build_creates_no_table(self, conn, tmp_path) -> None:
        with pytest.raises(BuildError):
            build_offense_codes(conn, write_labels(tmp_path / "l.csv"))
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "offense_codes" not in tables


class TestLabelFile:
    @pytest.mark.parametrize(
        ("row", "error"),
        [
            (["x", "A", "Other", "false", "why", "no"], "not an integer"),
            ([1, "A", "Part Four", "", "why", "no"], "ucr_part"),
            ([1, "A", "", "maybe", "why", "no"], "is_crime"),
            ([1, "A", "", "", "why", "no"], "must set"),
            ([1, "A", "Other", "", "", "no"], "rationale"),
            ([1, "A", "Other", "", "why", "maybe"], "reviewed"),
        ],
    )
    def test_bad_rows_are_refused(self, tmp_path, row, error) -> None:
        with pytest.raises(BuildError, match=error):
            read_labels(write_labels(tmp_path / "l.csv", row))

    def test_duplicate_after_normalisation_is_refused(self, tmp_path) -> None:
        with pytest.raises(BuildError, match="duplicate"):
            read_labels(
                write_labels(
                    tmp_path / "l.csv",
                    [1, "SICK ASSIST", "Other", "", "a", "no"],
                    [1, " sick  assist", "Other", "", "b", "no"],
                )
            )

    def test_wrong_header_is_refused(self, tmp_path) -> None:
        path = tmp_path / "l.csv"
        path.write_text("code,desc\n1,A\n")
        with pytest.raises(BuildError, match="expected columns"):
            read_labels(path)

    def test_committed_label_file_is_valid(self) -> None:
        """The real file parses, and every label explains itself."""
        labels = read_labels(LABELS_PATH)
        assert len(labels) > 50
        assert all(len(label.rationale) > 20 for label in labels.values())


def test_normalize_collapses_spacing_but_not_spelling() -> None:
    assert normalize("  larceny   shoplifting ") == "LARCENY SHOPLIFTING"
    assert normalize("NEGLIGIENT") != normalize("NEGLIGENT")
