"""Pull selected datasets to Parquet and seal a manifest.

Run this where the network allows it:

    uv run python -m ingest.pull --datasets ingest/catalog.txt --out data/

Failures are reported, never swallowed. A resource that cannot be parsed is
listed at the end with its format, because the alternative -- skipping it
quietly -- is how a catalog ends up smaller than the roadmap claims while every
log line still says success.

Some resources are deliberately not fetched: Analyze Boston publishes each
boundary dataset six ways (CSV, GeoJSON, KML, shapefile, an ArcGIS endpoint, a
hub page), and a PDF rendering beside some tables. KML and shapefile duplicate
the GeoJSON's geometry; the rest duplicate the CSV. Those are listed as skipped,
not failed -- otherwise every pull exits non-zero and a real failure hides among
the expected ones. The safety net is per dataset: one that yields nothing at all
is a failure whatever the reason.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ingest.ckan_client import DEFAULT_BASE_URL, CkanClient, CkanError, FetchedResource
from ingest.http import HttpError, RetryingTransport, RequestsTransport, Transport
from ingest.manifest import Manifest, ResourceEntry

__all__ = ["PullReport", "pull", "ALTERNATE_FORMATS"]

# Renderings of data the same dataset also ships in a parseable format. Verified
# against the live catalog on 2026-09-23: every dataset carrying one of these
# also has a CSV or GeoJSON with the same fields. For the boundary datasets the
# geometry survives only via the GeoJSON -- the CSV's shape_wkt is empty at source.
ALTERNATE_FORMATS = frozenset(
    {"html", "arcgis geoservices rest api", "kml", "kmz", "shp", "pdf"}
)


@dataclass
class PullReport:
    manifest: Manifest
    fetched: list[FetchedResource]
    failures: list[tuple[str, str]]
    """(resource identifier, reason) -- printed, never hidden."""
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(resource identifier, format) -- alternate renderings, listed but not fetched."""

    def render(self) -> str:
        lines = [
            f"snapshot {self.manifest.snapshot_date}",
            f"  resources : {len(self.fetched)} fetched, {len(self.failures)} failed, "
            f"{len(self.skipped)} skipped",
            f"  rows      : {self.manifest.total_rows:,}",
            f"  by path   : {self.manifest.source_counts}",
        ]
        if self.skipped:
            lines.append("  skipped (alternate renderings):")
            lines.extend(f"    {name}: {fmt}" for name, fmt in self.skipped)
        if self.failures:
            lines.append("  failures:")
            lines.extend(f"    {name}: {reason}" for name, reason in self.failures)
        return "\n".join(lines)


def pull(
    dataset_ids: list[str],
    out_dir: str | Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    transport: Transport | None = None,
) -> PullReport:
    """Fetch every resource of every dataset, writing Parquet plus a manifest."""
    client = CkanClient(transport or RetryingTransport(RequestsTransport()), base_url)

    raw_dir = Path(out_dir) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    fetched: list[FetchedResource] = []
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    for dataset_id in dataset_ids:
        try:
            resources = client.list_resources(dataset_id)
        except (CkanError, HttpError) as err:
            failures.append((dataset_id, f"could not list resources: {err}"))
            continue

        fetched_before = len(fetched)
        for ref in resources:
            label = f"{dataset_id}/{ref.name or ref.resource_id}"
            if ref.format in ALTERNATE_FORMATS:
                skipped.append((label, ref.format))
                continue
            try:
                resource = client.fetch_resource(ref)
            except (CkanError, HttpError) as err:
                failures.append((label, str(err)))
                continue

            if resource.frame.is_empty():
                failures.append((label, "fetched but empty"))
                continue

            resource.frame.write_parquet(raw_dir / f"{ref.resource_id}.parquet")
            fetched.append(resource)

        if len(fetched) == fetched_before:
            failures.append((dataset_id, "no resource could be fetched"))

    manifest = Manifest(
        snapshot_date=datetime.now(UTC).date().isoformat(),
        base_url=base_url,
        resources=[ResourceEntry.from_fetched(r) for r in fetched],
        # Sealed later by build_db, once there is a database to hash.
        duckdb_sha256=None,
    )
    manifest.write(Path(out_dir) / "manifest.json")
    return PullReport(
        manifest=manifest, fetched=fetched, failures=failures, skipped=skipped
    )


def _read_dataset_ids(path: Path) -> list[str]:
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            ids.append(entry)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        type=Path,
        required=True,
        help="file of CKAN dataset IDs, one per line; # starts a comment",
    )
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)

    report = pull(
        _read_dataset_ids(args.datasets), args.out, base_url=args.base_url
    )
    print(report.render())

    # Non-zero on any failure: a partial snapshot should not look like a success
    # to whatever runs this next.
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
