"""Check the snapshot database against its sealed manifest.

    uv run python -m ingest.verify_snapshot

Exit 0 if the database on disk is the exact file the manifest was sealed with,
1 otherwise. Anything that computes results from the snapshot runs this first:
an answer key computed against a different file is not the answer key.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ingest.manifest import SnapshotMismatch, verify_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--db", type=Path, default=Path("data/boston.duckdb"))
    args = parser.parse_args(argv)

    try:
        manifest = verify_snapshot(args.manifest, args.db)
    except (SnapshotMismatch, FileNotFoundError) as err:
        print(f"snapshot check failed: {err}", file=sys.stderr)
        return 1

    print(
        f"ok  {args.db}  snapshot {manifest.snapshot_date}  "
        f"file {manifest.duckdb_sha256[:16]}  content {manifest.content_sha256[:16]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
