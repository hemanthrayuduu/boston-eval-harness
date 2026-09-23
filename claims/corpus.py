"""The claim corpus: every ``claims/corpus/*.jsonl`` file, validated together.

One file per source so generators never overwrite each other: ``bpd.jsonl`` is
written by ``claims.bpd_claims``, hand-sourced claims live in their own file.
Loading checks every record against ``claims.schema.Claim`` and refuses duplicate
claim IDs across files -- labels, splits and scores all key on the ID.
"""

from __future__ import annotations

from pathlib import Path

from claims.schema import Claim

__all__ = ["CORPUS_DIR", "CorpusError", "load_corpus"]

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


class CorpusError(Exception):
    pass


def load_corpus(directory: str | Path = CORPUS_DIR) -> dict[str, Claim]:
    claims: dict[str, Claim] = {}
    where: dict[str, str] = {}
    for path in sorted(Path(directory).glob("*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                claim = Claim.model_validate_json(line)
            except ValueError as err:
                raise CorpusError(f"{path}:{number}: {err}") from None
            if claim.claim_id in claims:
                raise CorpusError(
                    f"{path}:{number}: duplicate claim_id {claim.claim_id!r} (first in {where[claim.claim_id]})"
                )
            claims[claim.claim_id] = claim
            where[claim.claim_id] = f"{path.name}:{number}"
    return claims
