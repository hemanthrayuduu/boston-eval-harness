"""The limitations corpus: documented ways the data can mislead, with stable IDs.

Each document in ``corpus/limitations/`` is a markdown file with TOML front
matter between ``+++`` lines. The ID is API: agents cite IDs in their verdicts,
and ``limitation_citation_f1`` scores those citations as exact strings against
the IDs a claim's spec curve shows are load-bearing. So an ID is never renamed --
a superseded document is kept and marked, not moved.

Front matter:

* ``id`` -- ``LIM-`` plus upper-case words joined by hyphens; must equal the
  filename stem.
* ``title`` -- one line.
* ``applies_to`` -- snapshot tables or ``table.column``s the limitation bears
  on. May be empty for data the snapshot does not contain (that absence is the
  point of the document).
* ``dimensions`` -- spec dimensions whose options the limitation argues for or
  against. This is what links a curve's driver to the IDs a verdict should cite.
* ``evidence_snapshot`` -- the snapshot date the Evidence numbers were measured on.
* ``sources`` -- URLs for anything not measured from the snapshot.

The body must contain the four sections in ``REQUIRED_SECTIONS``, in order.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CORPUS_DIR",
    "REQUIRED_SECTIONS",
    "LimitationDoc",
    "LimitationError",
    "load_limitations",
    "parse_limitation",
]

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus" / "limitations"

REQUIRED_SECTIONS = (
    "## What it is",
    "## Evidence",
    "## How it can change a claim",
    "## What to do",
)

_ID = re.compile(r"^LIM-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_REFERENCE = re.compile(r"\bLIM-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_FRONT_MATTER = re.compile(r"\A\+\+\+\n(.*?)\n\+\+\+\n(.*)\Z", re.DOTALL)


class LimitationError(Exception):
    pass


@dataclass(frozen=True)
class LimitationDoc:
    id: str
    title: str
    applies_to: tuple[str, ...]
    dimensions: tuple[str, ...]
    evidence_snapshot: str
    sources: tuple[str, ...]
    body: str

    @property
    def tables(self) -> frozenset[str]:
        return frozenset(ref.split(".", 1)[0] for ref in self.applies_to)


def parse_limitation(text: str, *, source: str = "<string>") -> LimitationDoc:
    """Parse one document. Checks shape only; cross-references are checked by
    :func:`load_limitations`."""
    match = _FRONT_MATTER.match(text)
    if not match:
        raise LimitationError(f"{source}: must start with TOML front matter between +++ lines")

    try:
        meta = tomllib.loads(match.group(1))
    except tomllib.TOMLDecodeError as err:
        raise LimitationError(f"{source}: bad front matter: {err}") from None

    expected = {"id", "title", "applies_to", "dimensions", "evidence_snapshot", "sources"}
    if set(meta) != expected:
        raise LimitationError(
            f"{source}: front matter keys must be {sorted(expected)}; "
            f"missing {sorted(expected - set(meta))}, unexpected {sorted(set(meta) - expected)}"
        )

    doc_id = meta["id"]
    if not isinstance(doc_id, str) or not _ID.match(doc_id):
        raise LimitationError(f"{source}: id {doc_id!r} must look like LIM-UPPER-CASE-WORDS")
    for key in ("applies_to", "dimensions", "sources"):
        if not isinstance(meta[key], list) or not all(isinstance(v, str) for v in meta[key]):
            raise LimitationError(f"{source}: {key} must be a list of strings")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(meta["evidence_snapshot"])):
        raise LimitationError(f"{source}: evidence_snapshot must be a YYYY-MM-DD date string")

    body = match.group(2)
    positions = [body.find(f"\n{heading}\n") for heading in REQUIRED_SECTIONS]
    missing = [h for h, pos in zip(REQUIRED_SECTIONS, positions) if pos < 0]
    if missing:
        raise LimitationError(f"{source}: missing sections {missing}")
    if positions != sorted(positions):
        raise LimitationError(f"{source}: sections must appear in the order {list(REQUIRED_SECTIONS)}")

    return LimitationDoc(
        id=doc_id,
        title=meta["title"],
        applies_to=tuple(meta["applies_to"]),
        dimensions=tuple(meta["dimensions"]),
        evidence_snapshot=str(meta["evidence_snapshot"]),
        sources=tuple(meta["sources"]),
        body=body,
    )


def load_limitations(
    directory: str | Path = CORPUS_DIR,
    *,
    tables: frozenset[str] | None = None,
    dimensions: frozenset[str] | None = None,
) -> dict[str, LimitationDoc]:
    """Load and validate every document in ``directory``.

    ``tables`` and ``dimensions`` default to the snapshot's table names and the
    spec engine's dimension keys, so a document cannot point at a table that does
    not exist or a dimension that was renamed.
    """
    if tables is None:
        from ingest.tables import SNAPSHOT_DERIVED, SNAPSHOT_TABLES

        tables = frozenset(t.table_name for t in (*SNAPSHOT_TABLES, *SNAPSHOT_DERIVED))
    if dimensions is None:
        from specs.dimensions import DIMENSIONS

        dimensions = frozenset(DIMENSIONS)

    docs: dict[str, LimitationDoc] = {}
    for path in sorted(Path(directory).glob("*.md")):
        if path.name == "README.md":
            continue
        doc = parse_limitation(path.read_text(encoding="utf-8"), source=str(path))
        if doc.id != path.stem:
            raise LimitationError(f"{path}: id {doc.id!r} does not match the filename")
        unknown_tables = sorted(doc.tables - tables)
        if unknown_tables:
            raise LimitationError(f"{path}: applies_to names tables not in the snapshot: {unknown_tables}")
        unknown_dims = sorted(set(doc.dimensions) - dimensions)
        if unknown_dims:
            raise LimitationError(f"{path}: unknown spec dimensions {unknown_dims}")
        docs[doc.id] = doc

    # Cross-references in prose ("see LIM-X") are part of the API too: a renamed
    # or missing target would leave an agent following a dead link.
    for doc in docs.values():
        dangling = sorted(set(_REFERENCE.findall(doc.body)) - set(docs) - {doc.id})
        if dangling:
            raise LimitationError(f"{doc.id}: refers to unknown limitation(s) {dangling}")
    return docs
