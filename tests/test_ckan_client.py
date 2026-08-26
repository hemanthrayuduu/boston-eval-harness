"""CKAN ingestion, tested offline against a fake transport.

The fallback path gets the most attention, because it is the one that decides
whether the catalog ends up the size the roadmap claims: a client that assumes
datastore_search works will 404 on resources that were never pushed to the
datastore, and lose them without saying so.
"""

from __future__ import annotations

import json
import random

import pytest

from ingest.ckan_client import (
    CkanClient,
    CkanError,
    ResourceRef,
    UnsupportedFormat,
)
from ingest.http import (
    HttpError,
    HttpResponse,
    RetryingTransport,
    RetryPolicy,
)


class FakeTransport:
    """Serves canned responses and records what was asked for."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None) -> HttpResponse:
        self.calls.append((url, params))
        for pattern, response in self.routes.items():
            if pattern in url:
                return self._materialise(response, params)
        raise HttpError(f"HTTP 404 for {url}", status=404)

    @staticmethod
    def _materialise(response: object, params: dict | None) -> HttpResponse:
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(params or {})
        if isinstance(response, bytes):
            return HttpResponse(200, response, {})
        body = json.dumps(response).encode("utf-8")
        return HttpResponse(200, body, {})


def ckan_ok(result: object) -> dict:
    return {"success": True, "result": result}


class TestRetry:
    def test_retries_then_succeeds(self) -> None:
        attempts = {"n": 0}

        class Flaky:
            def get(self, url, params=None):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise HttpError("HTTP 503", status=503)
                return HttpResponse(200, b'{"ok": true}', {})

        slept: list[float] = []
        transport = RetryingTransport(
            Flaky(), RetryPolicy(max_attempts=4, base_delay_s=1.0),
            sleep=slept.append, rng=random.Random(0),
        )
        assert transport.get("http://x").status == 200
        assert attempts["n"] == 3
        assert len(slept) == 2

    def test_backoff_grows(self) -> None:
        class AlwaysDown:
            def get(self, url, params=None):
                raise HttpError("HTTP 500", status=500)

        slept: list[float] = []
        transport = RetryingTransport(
            AlwaysDown(), RetryPolicy(max_attempts=4, base_delay_s=1.0, jitter=0.0),
            sleep=slept.append, rng=random.Random(0),
        )
        with pytest.raises(HttpError):
            transport.get("http://x")
        assert slept == [1.0, 2.0, 4.0]

    def test_delay_is_capped(self) -> None:
        policy = RetryPolicy(base_delay_s=1.0, max_delay_s=5.0, jitter=0.0)
        assert policy.delay_for(10, random.Random(0)) == 5.0

    def test_fatal_errors_are_not_retried(self) -> None:
        """A 404 is a routing signal, not a failure. Retrying it four times per
        missing resource wastes most of an afternoon across a large catalog."""
        attempts = {"n": 0}

        class NotFound:
            def get(self, url, params=None):
                attempts["n"] += 1
                raise HttpError("HTTP 404", status=404)

        transport = RetryingTransport(NotFound(), RetryPolicy(max_attempts=4), sleep=lambda _: None)
        with pytest.raises(HttpError):
            transport.get("http://x")
        assert attempts["n"] == 1

    def test_transport_failures_are_retried(self) -> None:
        # No status: DNS, reset, timeout. Assumed transient.
        assert HttpError("boom", status=None).retryable

    @pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False), (404, False)])
    def test_retryability_by_status(self, status: int, retryable: bool) -> None:
        assert HttpError("x", status=status).retryable is retryable


class TestResourceRef:
    def test_missing_datastore_active_means_false(self) -> None:
        """CKAN reports this as absent, null, or false interchangeably."""
        for payload in ({}, {"datastore_active": None}, {"datastore_active": False}):
            assert ResourceRef.from_api(payload).datastore_active is False

    def test_format_is_normalised(self) -> None:
        ref = ResourceRef.from_api({"format": "  CSV  "})
        assert ref.format == "csv"


class TestDatastorePath:
    def test_pagination_collects_all_pages(self) -> None:
        def datastore(params):
            offset = int(params.get("offset", 0))
            limit = int(params.get("limit", 10))
            rows = [{"_id": i, "n": str(i)} for i in range(offset, min(offset + limit, 25))]
            return ckan_ok({"total": 25, "records": rows})

        client = CkanClient(FakeTransport({"datastore_search": datastore}))
        records = list(client.iter_datastore_records("res1", page_size=10))
        assert len(records) == 25

    def test_stops_on_short_page_without_trusting_total(self) -> None:
        """CKAN's `total` is a snapshot from the first request. A resource being
        refreshed mid-pull would otherwise loop or truncate."""
        def datastore(params):
            offset = int(params.get("offset", 0))
            rows = [] if offset else [{"_id": 1, "n": "1"}]
            return ckan_ok({"total": 9999, "records": rows})

        client = CkanClient(FakeTransport({"datastore_search": datastore}))
        assert len(list(client.iter_datastore_records("res1", page_size=10))) == 1

    def test_internal_id_column_is_dropped(self) -> None:
        client = CkanClient(
            FakeTransport(
                {"datastore_search": ckan_ok({"records": [{"_id": 1, "nbhd": "Roxbury"}]})}
            )
        )
        fetched = client.fetch_resource(
            ResourceRef("r1", "crime", "csv", "http://x/f.csv", datastore_active=True)
        )
        assert fetched.columns == ("nbhd",)
        assert fetched.source == "datastore"

    def test_ckan_failure_payload_raises(self) -> None:
        client = CkanClient(FakeTransport({"package_show": {"success": False, "error": "nope"}}))
        with pytest.raises(CkanError, match="nope"):
            client.package_show("x")


class TestFallbackPath:
    def test_non_datastore_resource_is_downloaded(self) -> None:
        csv = b"nbhd,n\nRoxbury,3\nDorchester,438\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        fetched = client.fetch_resource(
            ResourceRef("r1", "crime", "csv", "http://x/f.csv", datastore_active=False)
        )
        assert fetched.source == "direct_download"
        assert fetched.row_count == 2
        assert fetched.columns == ("nbhd", "n")

    def test_stale_datastore_active_flag_falls_back(self) -> None:
        """CKAN says the datastore is active and it 404s. The resource is still
        downloadable, and dropping it here is how a catalog silently shrinks."""
        csv = b"nbhd,n\nRoxbury,3\n"
        transport = FakeTransport({"/f.csv": csv})  # no datastore_search route -> 404
        fetched = CkanClient(transport).fetch_resource(
            ResourceRef("r1", "crime", "csv", "http://x/f.csv", datastore_active=True)
        )
        assert fetched.source == "direct_download"
        assert fetched.row_count == 1

    def test_server_error_on_datastore_is_not_silently_swallowed(self) -> None:
        """A 500 means the datastore is broken, not that this resource lives
        elsewhere. Falling back would hide an outage as a format quirk."""
        transport = FakeTransport(
            {"datastore_search": HttpError("HTTP 500", status=500), "/f.csv": b"a\n1\n"}
        )
        with pytest.raises(HttpError):
            CkanClient(transport).fetch_resource(
                ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=True)
            )

    def test_unsupported_format_names_itself(self) -> None:
        client = CkanClient(FakeTransport({"/f.xlsx": b"PK\x03\x04"}))
        with pytest.raises(UnsupportedFormat, match="openpyxl"):
            client.fetch_resource(
                ResourceRef("r1", "assess", "xlsx", "http://x/f.xlsx", datastore_active=False)
            )

    def test_resource_without_url_is_an_error_not_an_empty_frame(self) -> None:
        client = CkanClient(FakeTransport({}))
        with pytest.raises(CkanError, match="no URL"):
            client.fetch_resource(ResourceRef("r1", "x", "csv", "", datastore_active=False))

    def test_geojson_properties_become_rows(self) -> None:
        geojson = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"name": "Roxbury", "id": "1"}},
                    {"type": "Feature", "properties": {"name": "Fenway", "id": "2"}},
                ],
            }
        ).encode()
        client = CkanClient(FakeTransport({"/n.geojson": geojson}))
        fetched = client.fetch_resource(
            ResourceRef("r1", "nbhds", "geojson", "http://x/n.geojson", datastore_active=False)
        )
        assert fetched.row_count == 2
        assert set(fetched.columns) == {"name", "id"}


class TestParsingPreservesMess:
    def test_values_are_not_type_coerced(self) -> None:
        """Inference turns '02134' into 2134 and a blank into 0. Typing is a
        decision for build_db, where it is explicit and reviewable."""
        csv = b"zip,count,rate\n02134,007,1.50\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        frame = client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        ).frame
        assert frame.row(0) == ("02134", "007", "1.50")

    def test_original_column_names_are_preserved(self) -> None:
        csv = b"OFFENSE_CODE_GROUP,Lat, Long \n1,2,3\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        frame = client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        ).frame
        assert frame.columns == ["OFFENSE_CODE_GROUP", "Lat", " Long "]

    def test_non_utf8_bytes_do_not_kill_the_pull(self) -> None:
        # Real municipal exports contain latin-1 bytes in street names.
        csv = "nbhd,note\nRoxbury,caf\xe9\n".encode("latin-1")
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        fetched = client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        )
        assert fetched.row_count == 1

    def test_empty_cell_is_null_and_sentinel_strings_are_not(self) -> None:
        """Both halves matter. Flattening nulls to empty strings invents a null
        rate of zero; treating "NA" as null invents data that was never there."""
        csv = b"nbhd,note\nRoxbury,\nFenway,NA\nDorchester,N/A\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        frame = client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        ).frame
        assert frame["note"].to_list() == [None, "NA", "N/A"]
        assert frame["note"].null_count() == 1

    def test_ragged_datastore_records_do_not_break_the_pull(self) -> None:
        """CKAN omits keys whose value is null, and different pages can carry
        different key sets. Inferring a schema from the first row would fail on
        the rest."""
        records = [
            {"_id": 1, "nbhd": "Roxbury"},
            {"_id": 2, "nbhd": "Fenway", "district": "D4"},
            {"_id": 3, "district": "B2"},
        ]
        client = CkanClient(FakeTransport({"datastore_search": ckan_ok({"records": records})}))
        frame = client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=True)
        ).frame
        assert frame.columns == ["nbhd", "district"]
        assert frame["district"].to_list() == [None, "D4", "B2"]
        assert frame["nbhd"].to_list() == ["Roxbury", "Fenway", None]

    def test_ragged_rows_are_tolerated(self) -> None:
        csv = b"a,b,c\n1,2,3\n4,5\n6,7,8,9\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        assert client.fetch_resource(
            ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        ).row_count == 3


class TestContentHash:
    def test_identical_content_hashes_identically(self) -> None:
        csv = b"a,b\n1,2\n"
        client = CkanClient(FakeTransport({"/f.csv": csv}))
        ref = ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        assert (
            client.fetch_resource(ref).content_sha256
            == client.fetch_resource(ref).content_sha256
        )

    def test_changed_content_changes_the_hash(self) -> None:
        ref = ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        first = CkanClient(FakeTransport({"/f.csv": b"a,b\n1,2\n"})).fetch_resource(ref)
        second = CkanClient(FakeTransport({"/f.csv": b"a,b\n1,3\n"})).fetch_resource(ref)
        assert first.content_sha256 != second.content_sha256

    def test_hash_is_comparable_across_fetch_paths(self) -> None:
        """The same data pulled two ways must hash the same, or the refresh gate
        reports drift every time a resource changes route."""
        ref_direct = ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=False)
        ref_store = ResourceRef("r1", "x", "csv", "http://x/f.csv", datastore_active=True)

        direct = CkanClient(FakeTransport({"/f.csv": b"nbhd,n\nRoxbury,3\n"})).fetch_resource(
            ref_direct
        )
        store = CkanClient(
            FakeTransport(
                {"datastore_search": ckan_ok({"records": [{"_id": 1, "nbhd": "Roxbury", "n": "3"}]})}
            )
        ).fetch_resource(ref_store)

        assert direct.source != store.source
        assert direct.content_sha256 == store.content_sha256
