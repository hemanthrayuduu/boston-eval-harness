"""Hand-sourced claims: the committed corpus file matches its TOML source, and
hand edits that would silently drop information fail instead."""

from __future__ import annotations

import pytest

from claims.corpus import load_corpus
from claims.hand_claims import OUT_PATH, TOML_PATH, HandSourceError, build, main, render

SOURCE = """
[sources.example]
kind = "news"
organization = "Example News"
url = "https://example.org/story"
published = 2025-12-15
retrieved = 2026-09-23
"""

CLAIM = """
[[claim]]
id = "hand-example-homicides"
source = "example"
paraphrase = "Example News reported 31 homicides in Boston so far in 2025."
measure = { family = "part_one_offense", name = "Homicide" }
geography = { level = "citywide" }
window = { kind = "period", current_start = 2025-01-01, current_end = 2025-12-15 }
assertion = { kind = "level", stated_value = 31 }
cluster = "homicide:citywide:2025"
"""


def write(tmp_path, text: str):
    path = tmp_path / "hand.toml"
    path.write_text(text)
    return path


class TestCommitted:
    def test_committed_file_matches_the_toml(self) -> None:
        assert OUT_PATH.read_text(encoding="utf-8") == render(build(TOML_PATH))
        assert main(["--check"]) == 0

    def test_every_claim_is_dated_and_attributed(self) -> None:
        for claim in build(TOML_PATH):
            assert claim.claim_id.startswith("hand-")
            assert claim.source.published <= claim.source.retrieved
            assert claim.source.url.startswith("https://")
            assert claim.selection_rule == "hand_sourced"

    def test_full_corpus_reaches_the_phase_one_target(self) -> None:
        corpus = load_corpus()
        assert len(corpus) >= 150
        kinds = {c.source.kind for c in corpus.values()}
        assert {"bpd_weekly_report", "news", "official_statement"} <= kinds


class TestBuild:
    def test_well_formed_claim_builds(self, tmp_path) -> None:
        [claim] = build(write(tmp_path, SOURCE + CLAIM))
        assert claim.cluster_id == "hand:homicide:citywide:2025"
        assert claim.assertion.kind == "level"

    def test_undeclared_source_fails(self, tmp_path) -> None:
        with pytest.raises(HandSourceError, match="not declared"):
            build(write(tmp_path, SOURCE + CLAIM.replace('source = "example"', 'source = "exmaple"')))

    def test_unknown_field_fails_rather_than_being_dropped(self, tmp_path) -> None:
        with pytest.raises(HandSourceError, match="unknown fields"):
            build(write(tmp_path, SOURCE + CLAIM + 'note = "typo for notes"\n'))

    def test_unused_source_fails(self, tmp_path) -> None:
        extra = SOURCE.replace("[sources.example]", "[sources.orphan]")
        with pytest.raises(HandSourceError, match="not used"):
            build(write(tmp_path, SOURCE + extra + CLAIM))

    def test_duplicate_ids_fail(self, tmp_path) -> None:
        with pytest.raises(HandSourceError, match="duplicate"):
            build(write(tmp_path, SOURCE + CLAIM + CLAIM))

    def test_window_that_contradicts_its_kind_fails(self, tmp_path) -> None:
        broken = CLAIM.replace('kind = "period", ', 'kind = "calendar_year", ')
        with pytest.raises(HandSourceError, match="needs current and prior"):
            build(write(tmp_path, SOURCE + broken))

    def test_check_mode_catches_a_stale_file(self, tmp_path) -> None:
        source = write(tmp_path, SOURCE + CLAIM)
        out = tmp_path / "hand.jsonl"
        out.write_text("stale\n")
        assert main(["--source", str(source), "--out", str(out), "--check"]) == 1
        assert main(["--source", str(source), "--out", str(out)]) == 0
        assert main(["--source", str(source), "--out", str(out), "--check"]) == 0
