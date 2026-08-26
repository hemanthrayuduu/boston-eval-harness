"""Analyze Boston (CKAN) client.

The important part is the fallback. CKAN exposes a queryable ``datastore_search``
API, but only for resources that have been pushed into the datastore, and on
Analyze Boston a meaningful share of resources have not been -- they exist only as
a downloadable CSV/XLSX/GeoJSON on the resource URL. A client that assumes the
datastore works will 404 on those and quietly lose datasets, which is how a
catalog silently ends up smaller than the roadmap claims.

Everything is parsed to strings. Type inference on messy municipal data silently
coerces -- ``"02134"`` becomes ``2134``, a blank becomes ``0``, a date in three
formats becomes null in two of them. Typing is a decision that belongs in
``build_db`` with explicit casts, where it is visible and reviewable. The mess is
the test.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from ingest.http import HttpError, Transport

__all__ = [
    "CkanError",
    "UnsupportedFormat",
    "ResourceRef",
    "FetchedResource",
    "CkanClient",
    "DEFAULT_BASE_URL",
]

DEFAULT_BASE_URL = "https://data.boston.gov"
DATASTORE_PAGE_SIZE = 10_000

# Formats we can parse without extra dependencies. XLSX needs openpyxl and is
# reported as unsupported rather than silently skipped.
_CSV_FORMATS = frozenset({"csv", "text/csv", "tsv"})
_JSON_FORMATS = frozenset({"json", "geojson", "application/json"})


class CkanError(Exception):
    pass


class UnsupportedFormat(CkanError):
    pass


@dataclass(frozen=True)
class ResourceRef:
    resource_id: str
    name: str
    format: str
    url: str
    datastore_active: bool
    dataset_id: str = ""

    @classmethod
    def from_api(cls, payload: dict[str, Any], dataset_id: str = "") -> ResourceRef:
        return cls(
            resource_id=payload.get("id", ""),
            name=payload.get("name", "") or "",
            format=(payload.get("format") or "").strip().lower(),
            url=payload.get("url", "") or "",
            # CKAN reports this inconsistently -- absent, null, or false all mean
            # "not in the datastore".
            datastore_active=bool(payload.get("datastore_active")),
            dataset_id=dataset_id,
        )


@dataclass(frozen=True)
class FetchedResource:
    ref: ResourceRef
    frame: pl.DataFrame
    content_sha256: str
    """Hash of the normalised content, not the raw bytes.

    Comparable across both fetch paths, and stable when a re-fetch returns the
    same data through a different route -- which is what the weekly data-refresh
    gate needs in order to distinguish real drift from transport noise."""
    source: str
    """``datastore`` or ``direct_download`` -- which path produced this."""
    fetched_at: str
    row_count: int = 0
    columns: tuple[str, ...] = field(default_factory=tuple)


def _content_hash(frame: pl.DataFrame) -> str:
    buffer = io.BytesIO()
    frame.write_csv(buffer)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _frame_from_records(records: list[dict[str, Any]]) -> pl.DataFrame:
    """Build an all-string frame, dropping CKAN's internal row id.

    The schema is stated rather than inferred. Records are ragged in practice --
    CKAN omits keys whose value is null, and different pages can carry different
    key sets -- so inference on the first row produces a schema the later rows
    violate. Nulls stay null: an absent value is not the empty string, and
    conflating them invents a null rate of zero.
    """
    if not records:
        return pl.DataFrame()

    columns: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            if key != "_id" and key not in seen:
                seen.add(key)
                columns.append(key)

    cleaned = [
        {
            column: (None if record.get(column) is None else str(record.get(column)))
            for column in columns
        }
        for record in records
    ]
    return pl.DataFrame(cleaned, schema={column: pl.Utf8 for column in columns})


def _parse_csv(data: bytes) -> pl.DataFrame:
    """Parse CSV as strings, tolerating the encoding real municipal files use."""
    for encoding in ("utf8", "utf8-lossy"):
        try:
            return pl.read_csv(
                io.BytesIO(data),
                infer_schema_length=0,
                encoding=encoding,
                truncate_ragged_lines=True,
                # Polars' default: an empty field is null, and a literal "NA" or
                # "N/A" stays the string it is. Both halves matter -- inventing
                # nulls from sentinel strings, or flattening nulls to empty
                # strings, would each corrupt the null rates the column cards
                # report to the retriever.
            )
        except Exception:  # noqa: PERF203 - the fallback is the point
            continue
    raise CkanError("could not parse CSV under any supported encoding")


def _parse_json(data: bytes) -> pl.DataFrame:
    payload = json.loads(data.decode("utf-8", errors="replace"))

    # GeoJSON: the rows are the feature properties.
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        records = [feature.get("properties", {}) for feature in payload.get("features", [])]
    elif isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        records = payload["records"]
    else:
        raise UnsupportedFormat("JSON payload is not a record collection")

    return _frame_from_records(records)


class CkanClient:
    def __init__(self, transport: Transport, base_url: str = DEFAULT_BASE_URL) -> None:
        self.transport = transport
        self.base_url = base_url.rstrip("/")

    def _action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.transport.get(f"{self.base_url}/api/3/action/{action}", params)
        payload = response.json()
        if not payload.get("success", False):
            raise CkanError(f"{action} failed: {payload.get('error', 'unknown error')}")
        return payload["result"]

    def package_show(self, dataset_id: str) -> dict[str, Any]:
        return self._action("package_show", {"id": dataset_id})

    def list_resources(self, dataset_id: str) -> list[ResourceRef]:
        package = self.package_show(dataset_id)
        return [
            ResourceRef.from_api(resource, dataset_id)
            for resource in package.get("resources", [])
        ]

    def iter_datastore_records(
        self, resource_id: str, page_size: int = DATASTORE_PAGE_SIZE
    ) -> Iterator[dict[str, Any]]:
        """Page through ``datastore_search``.

        Stops on a short page rather than trusting ``total``: CKAN's total is a
        snapshot taken at the first request, and a resource being refreshed
        mid-pull will otherwise loop or truncate.
        """
        offset = 0
        while True:
            result = self._action(
                "datastore_search",
                {"resource_id": resource_id, "limit": page_size, "offset": offset},
            )
            records = result.get("records", [])
            if not records:
                return

            yield from records

            if len(records) < page_size:
                return
            offset += len(records)

    def fetch_resource(self, ref: ResourceRef) -> FetchedResource:
        """Fetch a resource by whichever path is available.

        Tries the datastore when CKAN says it is active, and falls back to
        downloading the file if that turns out to be a lie -- ``datastore_active``
        can be stale, and a 404 there is a routing signal, not a failure.
        """
        if ref.datastore_active:
            try:
                return self._fetch_via_datastore(ref)
            except (CkanError, HttpError) as err:
                status = getattr(err, "status", None)
                if status is not None and status not in (404, 409):
                    raise
                if not ref.url:
                    raise
        return self._fetch_via_download(ref)

    def _fetch_via_datastore(self, ref: ResourceRef) -> FetchedResource:
        records = list(self.iter_datastore_records(ref.resource_id))
        frame = _frame_from_records(records)
        return self._finalize(ref, frame, source="datastore")

    def _fetch_via_download(self, ref: ResourceRef) -> FetchedResource:
        if not ref.url:
            raise CkanError(f"resource {ref.resource_id!r} has no URL to download")

        fmt = ref.format
        if fmt not in _CSV_FORMATS | _JSON_FORMATS:
            raise UnsupportedFormat(
                f"resource {ref.name!r} is {fmt or 'an unknown format'}, which needs "
                "handling this client does not have (XLSX needs openpyxl; shapefiles "
                "need a GIS reader). Convert it or add a parser -- do not drop it "
                "silently, or the catalog quietly shrinks."
            )

        data = self.transport.get(ref.url).body
        frame = _parse_csv(data) if fmt in _CSV_FORMATS else _parse_json(data)
        return self._finalize(ref, frame, source="direct_download")

    @staticmethod
    def _finalize(ref: ResourceRef, frame: pl.DataFrame, source: str) -> FetchedResource:
        return FetchedResource(
            ref=ref,
            frame=frame,
            content_sha256=_content_hash(frame),
            source=source,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            row_count=frame.height,
            columns=tuple(frame.columns),
        )
