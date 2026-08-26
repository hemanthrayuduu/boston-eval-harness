"""Snapshot manifest: what was pulled, when, and whether it still matches.

Two jobs. First, refuse to run against a database that is not the one the
results were computed on -- an eval whose answer key silently drifted is worse
than no eval. Second, make data drift *visible*: `diff` is what the weekly
refresh gate uses to distinguish "the model got worse" from "the world changed",
which is the distinction the whole regression story rests on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ingest.ckan_client import FetchedResource

__all__ = [
    "ResourceEntry",
    "Manifest",
    "ManifestDiff",
    "SnapshotMismatch",
    "file_sha256",
    "verify_snapshot",
]

MANIFEST_VERSION = 2


class SnapshotMismatch(Exception):
    """The database on disk is not the one the manifest describes."""


def file_sha256(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ResourceEntry:
    dataset_id: str
    resource_id: str
    name: str
    format: str
    source: str
    row_count: int
    columns: tuple[str, ...]
    content_sha256: str
    fetched_at: str

    @classmethod
    def from_fetched(cls, fetched: FetchedResource) -> ResourceEntry:
        return cls(
            dataset_id=fetched.ref.dataset_id,
            resource_id=fetched.ref.resource_id,
            name=fetched.ref.name,
            format=fetched.ref.format,
            source=fetched.source,
            row_count=fetched.row_count,
            columns=tuple(fetched.columns),
            content_sha256=fetched.content_sha256,
            fetched_at=fetched.fetched_at,
        )


@dataclass
class Manifest:
    snapshot_date: str
    base_url: str
    resources: list[ResourceEntry] = field(default_factory=list)
    duckdb_sha256: str | None = None
    """Hash of the database file. Proves *identity* -- that a run used the exact
    file the results were computed on. It is a hash of bytes, so it also moves
    when the data has not: DuckDB embeds the filename in the file, and a storage
    format change across versions rewrites it wholesale."""
    content_sha256: str | None = None
    """Hash of the data itself, order-independent and stable across rebuilds.
    This is the one the weekly refresh gate compares, because it answers "did the
    world change?" rather than "was this file rebuilt?"."""
    manifest_version: int = MANIFEST_VERSION

    def by_resource_id(self) -> dict[str, ResourceEntry]:
        return {entry.resource_id: entry for entry in self.resources}

    @property
    def total_rows(self) -> int:
        return sum(entry.row_count for entry in self.resources)

    @property
    def source_counts(self) -> dict[str, int]:
        """How many resources came from each path.

        Worth recording: the share needing `direct_download` is the concrete
        answer to "how much of the catalog is not in the datastore", which the
        roadmap treats as a planning risk rather than a measured number.
        """
        counts: dict[str, int] = {}
        for entry in self.resources:
            counts[entry.source] = counts.get(entry.source, 0) + 1
        return dict(sorted(counts.items()))

    def to_json(self) -> str:
        payload = {
            "manifest_version": self.manifest_version,
            "snapshot_date": self.snapshot_date,
            "base_url": self.base_url,
            "duckdb_sha256": self.duckdb_sha256,
            "content_sha256": self.content_sha256,
            "resources": [
                {**asdict(entry), "columns": list(entry.columns)}
                for entry in sorted(self.resources, key=lambda e: e.resource_id)
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        payload = json.loads(text)
        version = payload.get("manifest_version")
        if version != MANIFEST_VERSION:
            raise SnapshotMismatch(
                f"manifest version {version} but this code writes {MANIFEST_VERSION}; "
                "rebuild the snapshot rather than reading it under the wrong schema"
            )
        return cls(
            snapshot_date=payload["snapshot_date"],
            base_url=payload["base_url"],
            duckdb_sha256=payload.get("duckdb_sha256"),
            content_sha256=payload.get("content_sha256"),
            resources=[
                ResourceEntry(**{**entry, "columns": tuple(entry["columns"])})
                for entry in payload.get("resources", [])
            ],
        )

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def verify(self, db_path: str | Path) -> None:
        """Raise unless the database matches the manifest. Called before every run."""
        if self.duckdb_sha256 is None:
            raise SnapshotMismatch(
                "manifest records no database checksum; the snapshot was never sealed"
            )

        database = Path(db_path)
        if not database.exists():
            raise SnapshotMismatch(f"database not found: {database}")

        actual = file_sha256(database)
        if actual != self.duckdb_sha256:
            raise SnapshotMismatch(
                f"database checksum mismatch for {database}\n"
                f"  manifest: {self.duckdb_sha256}\n"
                f"  on disk:  {actual}\n"
                "Results computed against this file would not be reproducible. "
                "Rebuild the snapshot, or check out the manifest that matches it."
            )

    def diff(self, other: Manifest) -> ManifestDiff:
        """What changed between two snapshots."""
        mine, theirs = self.by_resource_id(), other.by_resource_id()

        changed = {
            resource_id: (mine[resource_id], theirs[resource_id])
            for resource_id in mine.keys() & theirs.keys()
            if mine[resource_id].content_sha256 != theirs[resource_id].content_sha256
        }
        schema_changed = {
            resource_id
            for resource_id, (before, after) in changed.items()
            if before.columns != after.columns
        }
        return ManifestDiff(
            added=frozenset(theirs.keys() - mine.keys()),
            removed=frozenset(mine.keys() - theirs.keys()),
            changed=changed,
            schema_changed=frozenset(schema_changed),
        )


@dataclass(frozen=True)
class ManifestDiff:
    added: frozenset[str]
    removed: frozenset[str]
    changed: dict[str, tuple[ResourceEntry, ResourceEntry]]
    schema_changed: frozenset[str]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def row_deltas(self) -> dict[str, int]:
        return {
            resource_id: after.row_count - before.row_count
            for resource_id, (before, after) in sorted(self.changed.items())
        }

    @property
    def is_breaking(self) -> bool:
        """Changes that can invalidate ground truth rather than merely extend it.

        A resource gaining rows is normal -- crime data appends daily. A resource
        losing rows, changing columns, or vanishing means queries written against
        the old snapshot may now compute something different, so the refresh gate
        should stop and ask rather than promote.
        """
        return bool(
            self.removed
            or self.schema_changed
            or any(delta < 0 for delta in self.row_deltas.values())
        )

    def summary(self) -> str:
        if self.is_empty:
            return "no changes"
        parts = [
            f"{len(self.added)} added",
            f"{len(self.removed)} removed",
            f"{len(self.changed)} changed",
        ]
        if self.schema_changed:
            parts.append(f"{len(self.schema_changed)} with schema changes")
        return ", ".join(parts)


def verify_snapshot(manifest_path: str | Path, db_path: str | Path) -> Manifest:
    """Load a manifest and verify the database against it. Fails loudly."""
    manifest = Manifest.read(manifest_path)
    manifest.verify(db_path)
    return manifest
