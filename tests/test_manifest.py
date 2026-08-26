"""Snapshot sealing and drift detection."""

from __future__ import annotations

import pytest

from ingest.manifest import (
    MANIFEST_VERSION,
    Manifest,
    ResourceEntry,
    SnapshotMismatch,
    file_sha256,
    verify_snapshot,
)


def entry(
    resource_id: str = "r1",
    *,
    rows: int = 100,
    sha: str = "a" * 64,
    columns: tuple[str, ...] = ("nbhd", "n"),
    source: str = "datastore",
) -> ResourceEntry:
    return ResourceEntry(
        dataset_id="crime-incidents",
        resource_id=resource_id,
        name="Crime Incidents",
        format="csv",
        source=source,
        row_count=rows,
        columns=columns,
        content_sha256=sha,
        fetched_at="2026-07-28T00:00:00+00:00",
    )


def manifest(*entries: ResourceEntry, db_sha: str | None = None) -> Manifest:
    return Manifest(
        snapshot_date="2026-07-28",
        base_url="https://data.boston.gov",
        resources=list(entries),
        duckdb_sha256=db_sha,
    )


class TestRoundTrip:
    def test_json_round_trip(self) -> None:
        original = manifest(entry("r1"), entry("r2"), db_sha="b" * 64)
        assert Manifest.from_json(original.to_json()) == original

    def test_serialisation_is_stable(self) -> None:
        """Order must not depend on fetch order, or every re-pull looks like a diff."""
        a = manifest(entry("r1"), entry("r2"))
        b = manifest(entry("r2"), entry("r1"))
        assert a.to_json() == b.to_json()

    def test_wrong_manifest_version_refused(self) -> None:
        text = manifest(entry()).to_json().replace(
            f'"manifest_version": {MANIFEST_VERSION}', '"manifest_version": 99'
        )
        with pytest.raises(SnapshotMismatch, match="manifest version"):
            Manifest.from_json(text)

    def test_source_counts_are_reported(self) -> None:
        """How much of the catalog is not in the datastore -- a number the roadmap
        treats as a planning risk and this turns into a measurement."""
        counts = manifest(
            entry("r1", source="datastore"),
            entry("r2", source="direct_download"),
            entry("r3", source="direct_download"),
        ).source_counts
        assert counts == {"datastore": 1, "direct_download": 2}

    def test_total_rows(self) -> None:
        assert manifest(entry("r1", rows=10), entry("r2", rows=5)).total_rows == 15


class TestVerification:
    def test_matching_checksum_passes(self, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        database.write_bytes(b"pretend database")
        manifest(entry(), db_sha=file_sha256(database)).verify(database)

    def test_mutated_database_is_refused(self, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        database.write_bytes(b"pretend database")
        sealed = manifest(entry(), db_sha=file_sha256(database))

        database.write_bytes(b"pretend database, but different")
        with pytest.raises(SnapshotMismatch, match="checksum mismatch"):
            sealed.verify(database)

    def test_unsealed_manifest_is_refused(self, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        database.write_bytes(b"x")
        with pytest.raises(SnapshotMismatch, match="never sealed"):
            manifest(entry(), db_sha=None).verify(database)

    def test_missing_database_is_refused(self, tmp_path) -> None:
        with pytest.raises(SnapshotMismatch, match="not found"):
            manifest(entry(), db_sha="c" * 64).verify(tmp_path / "absent.duckdb")

    def test_verify_snapshot_helper(self, tmp_path) -> None:
        database = tmp_path / "boston.duckdb"
        database.write_bytes(b"db")
        path = tmp_path / "manifest.json"
        manifest(entry(), db_sha=file_sha256(database)).write(path)

        assert verify_snapshot(path, database).snapshot_date == "2026-07-28"

        database.write_bytes(b"tampered")
        with pytest.raises(SnapshotMismatch):
            verify_snapshot(path, database)


class TestDrift:
    def test_no_change(self) -> None:
        before = manifest(entry("r1"))
        assert before.diff(manifest(entry("r1"))).is_empty

    def test_added_and_removed(self) -> None:
        diff = manifest(entry("r1"), entry("r2")).diff(manifest(entry("r2"), entry("r3")))
        assert diff.added == {"r3"}
        assert diff.removed == {"r1"}

    def test_content_change_is_detected_with_row_delta(self) -> None:
        diff = manifest(entry("r1", rows=100, sha="a" * 64)).diff(
            manifest(entry("r1", rows=142, sha="b" * 64))
        )
        assert diff.changed.keys() == {"r1"}
        assert diff.row_deltas == {"r1": 42}

    def test_appended_rows_are_not_breaking(self) -> None:
        """Crime data appends daily. Treating that as a regression would make the
        weekly refresh gate fire every week and get switched off."""
        diff = manifest(entry("r1", rows=100, sha="a" * 64)).diff(
            manifest(entry("r1", rows=142, sha="b" * 64))
        )
        assert not diff.is_breaking

    @pytest.mark.parametrize(
        "before,after,reason",
        [
            (entry("r1", rows=100, sha="a" * 64), entry("r1", rows=40, sha="b" * 64), "rows lost"),
            (
                entry("r1", sha="a" * 64, columns=("nbhd", "n")),
                entry("r1", sha="b" * 64, columns=("neighborhood", "n")),
                "column renamed",
            ),
        ],
    )
    def test_breaking_changes_are_flagged(self, before, after, reason) -> None:
        """Queries written against the old snapshot may now compute something
        different. The gate should stop and ask rather than promote."""
        assert manifest(before).diff(manifest(after)).is_breaking, reason

    def test_removed_resource_is_breaking(self) -> None:
        assert manifest(entry("r1"), entry("r2")).diff(manifest(entry("r1"))).is_breaking

    def test_schema_change_is_reported_separately(self) -> None:
        diff = manifest(entry("r1", sha="a" * 64, columns=("a", "b"))).diff(
            manifest(entry("r1", sha="b" * 64, columns=("a", "b", "c")))
        )
        assert diff.schema_changed == {"r1"}

    def test_summary_is_human_readable(self) -> None:
        assert manifest(entry("r1")).diff(manifest(entry("r1"))).summary() == "no changes"
        summary = manifest(entry("r1")).diff(manifest(entry("r2"))).summary()
        assert "1 added" in summary and "1 removed" in summary
