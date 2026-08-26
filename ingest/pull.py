"""Pull selected datasets to Parquet and seal a manifest.

Run this where the network allows it:

    uv run python -m ingest.pull --datasets ingest/catalog.txt --out data/

Failures are reported, never swallowed. A resource that cannot be parsed is
listed at the end with its format, because the alternative -- skipping it
quietly -- is how a catalog ends up smaller than the roadmap claims while every
log line still says success.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ingest.ckan_client import DEFAULT_BASE_URL, CkanClient, CkanError, FetchedResource
from ingest.http import HttpError, RetryingTransport, RequestsTransport, Transport
from ingest.manifest import Manifest, ResourceEntry

__all__ = ["PullReport", "pull"]


@dataclass
class PullReport:
    manifest: Manifest
    fetched: list[FetchedResource]
    failures: list[tuple[str, str]]
    """(resource identifier, reason) -- printed, never hidden."""

    def render(self) -> str:
        lines = [
            f"snapshot {self.manifest.snapshot_date}",
            f"  resources : {len(self.fetched)} fetched, {len(self.failures)} failed",
            f"  rows      : {self.manifest.total_rows:,}",
            f"  by path   : {self.manifest.source_counts}",
        ]
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

    for dataset_id in dataset_ids:
        try:
            resources = client.list_resources(dataset_id)
        except (CkanError, HttpError) as err:
            failures.append((dataset_id, f"could not list resources: {err}"))
            continue

        for ref in resources:
            label = f"{dataset_id}/{ref.name or ref.resource_id}"
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

    manifest = Manifest(
        snapshot_date=datetime.now(UTC).date().isoformat(),
        base_url=base_url,
        resources=[ResourceEntry.from_fetched(r) for r in fetched],
        # Sealed later by build_db, once there is a database to hash.
        duckdb_sha256=None,
    )
    manifest.write(Path(out_dir) / "manifest.json")
    return PullReport(manifest=manifest, fetched=fetched, failures=failures)


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
