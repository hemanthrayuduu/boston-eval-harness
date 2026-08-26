"""Building the snapshot: explicit casts, measured loss, and two checksums that
answer two different questions."""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from ingest.build_db import BuildError, TableSpec, build_database, seal_manifest
from ingest.manifest import Manifest


@pytest.fixture
def raw(tmp_path):
    """A raw dir of all-string Parquet, as ingest.pull writes it."""
    directory = tmp_path / "raw"
    directory.mkdir()

    pl.DataFrame(
        {
            "OFFENSE_CODE": ["613", "3115", "801"],
            "NEIGHBORHOOD": ["Roxbury", "Fenway", None],
            "OCCURRED_ON_DATE": ["2024-01-05", "2024-02-11", "2024-03-02"],
        }
    ).write_parquet(directory / "res-crime.parquet")

    pl.DataFrame(
        {"nbhd": ["Roxbury", "Fenway"], "pop_total": ["52000", "40000"]}
    ).write_parquet(directory / "res-pop.parquet")

    # A column where typing genuinely loses data: 2 of 5 non-null values are junk.
    pl.DataFrame(
        {"code": ["1", "2", "unknown", "n/a", "5", None]}
    ).write_parquet(directory / "res-messy.parquet")

    return directory


def crime_spec(**overrides) -> TableSpec:
    base = {
        "table_name": "crime_incidents",
        "resource_id": "res-crime",
        "casts": {"OFFENSE_CODE": "INTEGER", "OCCURRED_ON_DATE": "DATE"},
    }
    base.update(overrides)
    return TableSpec(**base)


class TestTableNaming:
    @pytest.mark.parametrize("name", ["Crime", "crime-incidents", "1crime", "crime;drop", ""])
    def test_unsafe_names_refused(self, name: str) -> None:
        # These names end up in the guard's allowlist and in generated SQL.
        with pytest.raises(BuildError, match="plain lowercase identifier"):
            TableSpec(table_name=name, resource_id="r")

    def test_duplicate_table_names_refused(self, raw, tmp_path) -> None:
        with pytest.raises(BuildError, match="duplicate table names"):
            build_database(
                [crime_spec(casts={}), crime_spec(casts={}, resource_id="res-pop")],
                raw,
                tmp_path / "b.duckdb",
            )


class TestBuild:
    def test_loads_tables_with_casts_applied(self, raw, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        report = build_database(
            [crime_spec(), TableSpec("population_acs", "res-pop", {"pop_total": "INTEGER"})],
            raw,
            database,
        )

        assert report.total_rows == 5
        conn = duckdb.connect(str(database), read_only=True)
        types = dict(
            conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'crime_incidents'"
            ).fetchall()
        )
        assert types["OFFENSE_CODE"] == "INTEGER"
        assert types["OCCURRED_ON_DATE"] == "DATE"
        # Uncast columns stay as fetched.
        assert types["NEIGHBORHOOD"] == "VARCHAR"

    def test_original_column_names_are_preserved(self, raw, tmp_path) -> None:
        build_database([crime_spec()], raw, tmp_path / "b.duckdb")
        conn = duckdb.connect(str(tmp_path / "b.duckdb"), read_only=True)
        columns = [r[0] for r in conn.execute("SELECT * FROM crime_incidents LIMIT 0").description]
        assert columns == ["OFFENSE_CODE", "NEIGHBORHOOD", "OCCURRED_ON_DATE"]

    def test_nulls_survive_the_build(self, raw, tmp_path) -> None:
        build_database([crime_spec()], raw, tmp_path / "b.duckdb")
        conn = duckdb.connect(str(tmp_path / "b.duckdb"), read_only=True)
        assert conn.execute(
            "SELECT count(*) - count(NEIGHBORHOOD) FROM crime_incidents"
        ).fetchone()[0] == 1

    def test_missing_parquet_names_the_fix(self, raw, tmp_path) -> None:
        with pytest.raises(BuildError, match="Run ingest.pull first"):
            build_database(
                [TableSpec("shootings", "res-absent")], raw, tmp_path / "b.duckdb"
            )

    def test_cast_on_unknown_column_refused(self, raw, tmp_path) -> None:
        with pytest.raises(BuildError, match="not in the resource"):
            build_database(
                [crime_spec(casts={"NO_SUCH_COLUMN": "INTEGER"})], raw, tmp_path / "b.duckdb"
            )

    def test_rebuild_replaces_rather_than_appends(self, raw, tmp_path) -> None:
        """Appending would make the checksum describe a history, not a snapshot."""
        database = tmp_path / "b.duckdb"
        build_database([crime_spec()], raw, database)
        second = build_database([crime_spec()], raw, database)
        assert second.total_rows == 3


class TestCastLoss:
    def test_clean_casts_report_no_loss(self, raw, tmp_path) -> None:
        report = build_database([crime_spec()], raw, tmp_path / "b.duckdb")
        assert report.lossy_casts == ()

    def test_lossy_cast_blocks_the_build(self, raw, tmp_path) -> None:
        """A cast that nulls 40% of a column is data loss, not type conversion.

        Letting it pass would produce a column that is 40% null for reasons
        invisible in the data -- indistinguishable from a column that is 40% null
        at source.
        """
        with pytest.raises(BuildError, match="lost more data than allowed") as excinfo:
            build_database(
                [TableSpec("messy", "res-messy", {"code": "INTEGER"})],
                raw,
                tmp_path / "b.duckdb",
            )
        assert "2/5 non-null values lost (40.00%)" in str(excinfo.value)

    def test_failed_build_leaves_no_database_behind(self, raw, tmp_path) -> None:
        database = tmp_path / "b.duckdb"
        with pytest.raises(BuildError):
            build_database(
                [TableSpec("messy", "res-messy", {"code": "INTEGER"})], raw, database
            )
        assert not database.exists()

    def test_loss_can_be_accepted_deliberately(self, raw, tmp_path) -> None:
        # Explicit, per-table, and visible in the report -- which is the point.
        report = build_database(
            [TableSpec("messy", "res-messy", {"code": "INTEGER"}, max_cast_loss=0.5)],
            raw,
            tmp_path / "b.duckdb",
        )
        assert len(report.lossy_casts) == 1
        assert report.lossy_casts[0].loss_rate == pytest.approx(0.4)
        assert "lossy casts:" in report.render()

    def test_loss_is_measured_against_non_nulls_not_rows(self, raw, tmp_path) -> None:
        """A pre-existing null was never lost by the cast, so counting it as loss
        would blame typing for missing data that was already missing."""
        report = build_database(
            [TableSpec("messy", "res-messy", {"code": "INTEGER"}, max_cast_loss=1.0)],
            raw,
            tmp_path / "b.duckdb",
        )
        cast = report.lossy_casts[0]
        assert cast.row_count == 6
        assert cast.non_null_before == 5
        assert cast.lost == 2


class TestChecksums:
    def test_rebuilding_the_same_path_is_byte_identical(self, raw, tmp_path) -> None:
        """DuckDB output is deterministic for identical content at a given path."""
        database = tmp_path / "boston.duckdb"
        first = build_database([crime_spec()], raw, database)
        second = build_database([crime_spec()], raw, database)
        assert first.duckdb_sha256 == second.duckdb_sha256

    def test_file_hash_moves_when_only_the_filename_changes(self, raw, tmp_path) -> None:
        """Why the file hash cannot answer "did the data change?".

        DuckDB embeds the filename in the file -- identical data written to
        a.duckdb and b.duckdb differs by three bytes. A storage-format change
        across engine versions does the same on a larger scale.
        """
        first = build_database([crime_spec()], raw, tmp_path / "a.duckdb")
        second = build_database([crime_spec()], raw, tmp_path / "b.duckdb")
        assert first.duckdb_sha256 != second.duckdb_sha256
        # ...and this is exactly what the content digest is immune to.
        assert first.content_sha256 == second.content_sha256

    def test_content_hash_ignores_row_order(self, tmp_path) -> None:
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        rows = {"nbhd": ["Roxbury", "Fenway", "Dorchester"]}
        pl.DataFrame(rows).write_parquet(raw_dir / "a.parquet")
        pl.DataFrame({"nbhd": list(reversed(rows["nbhd"]))}).write_parquet(
            raw_dir / "b.parquet"
        )

        first = build_database([TableSpec("t", "a")], raw_dir, tmp_path / "1.duckdb")
        second = build_database([TableSpec("t", "b")], raw_dir, tmp_path / "2.duckdb")
        assert first.content_sha256 == second.content_sha256

    def test_content_hash_changes_when_data_changes(self, tmp_path) -> None:
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        pl.DataFrame({"n": ["3"]}).write_parquet(raw_dir / "a.parquet")
        pl.DataFrame({"n": ["4"]}).write_parquet(raw_dir / "b.parquet")

        first = build_database([TableSpec("t", "a")], raw_dir, tmp_path / "1.duckdb")
        second = build_database([TableSpec("t", "b")], raw_dir, tmp_path / "2.duckdb")
        assert first.content_sha256 != second.content_sha256

    def test_content_hash_changes_when_casts_change(self, raw, tmp_path) -> None:
        untyped = build_database([crime_spec(casts={})], raw, tmp_path / "1.duckdb")
        typed = build_database([crime_spec()], raw, tmp_path / "2.duckdb")
        assert untyped.content_sha256 != typed.content_sha256


class TestSealing:
    def test_sealing_records_both_checksums(self, raw, tmp_path) -> None:
        report = build_database([crime_spec()], raw, tmp_path / "boston.duckdb")
        manifest = seal_manifest(
            Manifest(snapshot_date="2026-07-28", base_url="https://data.boston.gov"),
            report,
        )
        assert manifest.duckdb_sha256 == report.duckdb_sha256
        assert manifest.content_sha256 == report.content_sha256

    def test_sealed_manifest_verifies_then_catches_tampering(self, raw, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        report = build_database([crime_spec()], raw, database)
        manifest = seal_manifest(
            Manifest(snapshot_date="2026-07-28", base_url="https://data.boston.gov"),
            report,
        )
        manifest.verify(database)

        # An honest rebuild of identical data at the same path still verifies,
        # since DuckDB's output is deterministic there.
        build_database([crime_spec()], raw, database)
        manifest.verify(database)

        # A modified file must not.
        from ingest.manifest import SnapshotMismatch

        database.write_bytes(database.read_bytes() + b"tampered")
        with pytest.raises(SnapshotMismatch, match="checksum mismatch"):
            manifest.verify(database)

    def test_sealed_manifest_survives_json_round_trip(self, raw, tmp_path) -> None:
        report = build_database([crime_spec()], raw, tmp_path / "boston.duckdb")
        manifest = seal_manifest(
            Manifest(snapshot_date="2026-07-28", base_url="https://data.boston.gov"),
            report,
        )
        restored = Manifest.from_json(manifest.to_json())
        assert restored.content_sha256 == report.content_sha256
        assert restored.duckdb_sha256 == report.duckdb_sha256
