"""The pull orchestrator. Failures must be visible, and partial snapshots must
not look like successes."""

from __future__ import annotations

import json

from ingest.manifest import Manifest
from ingest.pull import pull
from tests.test_ckan_client import FakeTransport, ckan_ok


def package(*resources: dict) -> dict:
    return ckan_ok({"resources": list(resources)})


def resource(rid: str, name: str, fmt: str = "csv", datastore: bool = False) -> dict:
    return {
        "id": rid,
        "name": name,
        "format": fmt,
        "url": f"http://x/{rid}.{fmt}",
        "datastore_active": datastore,
    }


def test_pull_writes_parquet_and_manifest(tmp_path) -> None:
    transport = FakeTransport(
        {
            "package_show": package(resource("r1", "Crime Incidents")),
            "/r1.csv": b"nbhd,n\nRoxbury,3\nDorchester,438\n",
        }
    )
    report = pull(["crime-incidents"], tmp_path, transport=transport)

    assert (tmp_path / "raw" / "r1.parquet").exists()
    assert report.failures == []
    assert report.manifest.total_rows == 2

    written = Manifest.read(tmp_path / "manifest.json")
    assert written.resources[0].resource_id == "r1"
    assert written.resources[0].source == "direct_download"
    # Sealed only once build_db produces a database to hash.
    assert written.duckdb_sha256 is None


def test_manifest_records_which_path_each_resource_took(tmp_path) -> None:
    transport = FakeTransport(
        {
            "package_show": package(
                resource("r1", "In datastore", datastore=True),
                resource("r2", "Not in datastore"),
            ),
            "datastore_search": ckan_ok({"records": [{"_id": 1, "a": "1"}]}),
            "/r2.csv": b"a\n2\n",
        }
    )
    report = pull(["ds"], tmp_path, transport=transport)
    assert report.manifest.source_counts == {"datastore": 1, "direct_download": 1}


def test_unparseable_resource_is_reported_not_swallowed(tmp_path) -> None:
    """The failure mode this whole report exists to prevent: a catalog quietly
    smaller than claimed, with every log line saying success."""
    transport = FakeTransport(
        {
            "package_show": package(
                resource("r1", "Good"), resource("r2", "Spreadsheet", fmt="xlsx")
            ),
            "/r1.csv": b"a\n1\n",
            "/r2.xlsx": b"PK\x03\x04",
        }
    )
    report = pull(["ds"], tmp_path, transport=transport)

    assert len(report.fetched) == 1
    assert len(report.failures) == 1
    assert "Spreadsheet" in report.failures[0][0]
    assert "openpyxl" in report.failures[0][1]
    assert "failures:" in report.render()


def test_unreachable_dataset_is_reported(tmp_path) -> None:
    report = pull(["missing-dataset"], tmp_path, transport=FakeTransport({}))
    assert len(report.failures) == 1
    assert "could not list resources" in report.failures[0][1]


def test_empty_resource_is_a_failure_not_a_zero_row_table(tmp_path) -> None:
    transport = FakeTransport(
        {"package_show": package(resource("r1", "Empty")), "/r1.csv": b"a,b\n"}
    )
    report = pull(["ds"], tmp_path, transport=transport)
    assert report.fetched == []
    assert "empty" in report.failures[0][1]


def test_manifest_is_valid_json_with_stable_ordering(tmp_path) -> None:
    transport = FakeTransport(
        {
            "package_show": package(resource("r2", "B"), resource("r1", "A")),
            "/r1.csv": b"a\n1\n",
            "/r2.csv": b"a\n2\n",
        }
    )
    pull(["ds"], tmp_path, transport=transport)
    payload = json.loads((tmp_path / "manifest.json").read_text())
    assert [r["resource_id"] for r in payload["resources"]] == ["r1", "r2"]
