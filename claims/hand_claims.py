"""Build the hand-sourced corpus file from its readable source.

    uv run python -m claims.hand_claims           # write claims/corpus/hand.jsonl
    uv run python -m claims.hand_claims --check   # fail if it is out of date

Hand-sourced claims are authored in ``claims/hand_sourced.toml``: one table per
source page, one ``[[claim]]`` entry per claim, comments allowed. This module
turns them into validated ``Claim`` records. It refuses a claim naming a source
that is not declared, a field it does not know, a declared source no claim
uses, and duplicate IDs -- the TOML is edited by hand, so typos have to fail
loudly rather than drop a field.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from claims.schema import Claim, Geography, Measure, Source, Window

__all__ = ["TOML_PATH", "OUT_PATH", "HandSourceError", "build", "render"]

TOML_PATH = Path(__file__).with_name("hand_sourced.toml")
OUT_PATH = Path(__file__).parent / "corpus" / "hand.jsonl"

_FIELDS = {
    "id", "source", "speaker", "paraphrase", "measure", "geography", "window",
    "assertion", "stated", "source_checks_failed", "notes", "cluster",
}


class HandSourceError(Exception):
    pass


def _claim(entry: dict[str, Any], sources: dict[str, dict[str, Any]]) -> Claim:
    where = entry.get("id", "<claim without id>")
    unknown = sorted(set(entry) - _FIELDS)
    if unknown:
        raise HandSourceError(f"{where}: unknown fields {unknown}")
    if entry.get("source") not in sources:
        raise HandSourceError(f"{where}: source {entry.get('source')!r} is not declared under [sources]")
    try:
        return Claim(
            claim_id=entry["id"],
            source=Source(**sources[entry["source"]], speaker=entry.get("speaker")),
            paraphrase=entry["paraphrase"],
            measure=Measure(**entry["measure"]),
            geography=Geography(**entry["geography"]),
            window=Window(**entry["window"]),
            assertion=entry["assertion"],
            stated={k: float(v) for k, v in entry.get("stated", {}).items()},
            source_checks_failed=entry.get("source_checks_failed", []),
            notes=entry.get("notes", []),
            cluster_id=f"hand:{entry['cluster']}",
            selection_rule="hand_sourced",
        )
    except (KeyError, TypeError, ValueError) as err:
        raise HandSourceError(f"{where}: {err}") from None


def build(path: str | Path = TOML_PATH) -> list[Claim]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    sources = data.get("sources", {})
    claims = [_claim(entry, sources) for entry in data.get("claim", [])]

    ids = [c.claim_id for c in claims]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise HandSourceError(f"duplicate claim ids: {duplicates}")
    unused = sorted(set(sources) - {entry["source"] for entry in data.get("claim", [])})
    if unused:
        raise HandSourceError(f"sources declared but not used by any claim: {unused}")
    return sorted(claims, key=lambda c: c.claim_id)


def render(claims: list[Claim]) -> str:
    return "".join(claim.model_dump_json() + "\n" for claim in claims)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build claims/corpus/hand.jsonl from hand_sourced.toml.")
    parser.add_argument("--source", type=Path, default=TOML_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--check", action="store_true", help="exit 1 if --out is out of date")
    args = parser.parse_args(argv)

    try:
        text = render(build(args.source))
    except HandSourceError as err:
        print(f"invalid hand-sourced claims: {err}", file=sys.stderr)
        return 1
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date; run python -m claims.hand_claims", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {text.count(chr(10))} claims to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
