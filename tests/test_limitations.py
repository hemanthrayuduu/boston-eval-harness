"""The limitations corpus: IDs are API, so the loader refuses anything that would
let an agent cite an ID that does not exist or a document point at nothing."""

from __future__ import annotations

import pytest

from env.limitations import (
    REQUIRED_SECTIONS,
    LimitationError,
    load_limitations,
    parse_limitation,
)
from specs.dimensions import DIMENSIONS

BODY = "\n# X\n\n" + "\n\n".join(f"{h}\n\ntext" for h in REQUIRED_SECTIONS) + "\n"


def document(
    doc_id: str = "LIM-EXAMPLE",
    applies_to: str = '["crime_incidents"]',
    dimensions: str = '["window"]',
    snapshot: str = '"2026-09-23"',
    extra: str = "",
    body: str = BODY,
) -> str:
    return (
        f'+++\nid = "{doc_id}"\ntitle = "An example"\napplies_to = {applies_to}\n'
        f"dimensions = {dimensions}\nevidence_snapshot = {snapshot}\nsources = []\n{extra}+++\n{body}"
    )


def load(tmp_path, files: dict[str, str]):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return load_limitations(
        tmp_path, tables=frozenset({"crime_incidents"}), dimensions=frozenset({"window"})
    )


class TestCommittedCorpus:
    def test_loads_against_the_real_snapshot_tables_and_dimensions(self) -> None:
        docs = load_limitations()
        assert len(docs) >= 10

    def test_every_spec_dimension_is_covered_by_some_limitation(self) -> None:
        """limitation_citation_f1 derives the IDs a verdict should cite from the
        dimension that drives a claim's curve. A dimension no document covers
        would make that set empty for exactly the claims it decides."""
        covered = {dim for doc in load_limitations().values() for dim in doc.dimensions}
        assert covered == set(DIMENSIONS)

    def test_sources_are_urls(self) -> None:
        for doc in load_limitations().values():
            assert all(src.startswith("https://") for src in doc.sources), doc.id


class TestParsing:
    def test_well_formed_document_parses(self) -> None:
        doc = parse_limitation(document(applies_to='["crime_incidents.Lat", "districts"]'))
        assert doc.id == "LIM-EXAMPLE"
        assert doc.tables == frozenset({"crime_incidents", "districts"})

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            ("# no front matter\n", "front matter"),
            (document(doc_id="lim-lower"), "LIM-UPPER"),
            (document(doc_id="LIM-"), "LIM-UPPER"),
            (document(extra='owner = "me"\n'), "unexpected"),
            (document(dimensions='"window"'), "list of strings"),
            (document(snapshot='"last week"'), "YYYY-MM-DD"),
            (document(body="\n## What it is\n\ntext\n"), "missing sections"),
        ],
    )
    def test_malformed_documents_are_refused(self, text: str, error: str) -> None:
        with pytest.raises(LimitationError, match=error):
            parse_limitation(text)

    def test_sections_out_of_order_are_refused(self) -> None:
        swapped = "\n# X\n\n" + "\n\n".join(
            f"{h}\n\ntext" for h in (REQUIRED_SECTIONS[1], REQUIRED_SECTIONS[0], *REQUIRED_SECTIONS[2:])
        ) + "\n"
        with pytest.raises(LimitationError, match="order"):
            parse_limitation(document(body=swapped))


class TestCorpusRules:
    def test_id_must_match_filename(self, tmp_path) -> None:
        with pytest.raises(LimitationError, match="does not match the filename"):
            load(tmp_path, {"LIM-OTHER.md": document()})

    def test_unknown_table_is_refused(self, tmp_path) -> None:
        with pytest.raises(LimitationError, match="not in the snapshot"):
            load(tmp_path, {"LIM-EXAMPLE.md": document(applies_to='["crime_incidnets"]')})

    def test_unknown_dimension_is_refused(self, tmp_path) -> None:
        with pytest.raises(LimitationError, match="unknown spec dimensions"):
            load(tmp_path, {"LIM-EXAMPLE.md": document(dimensions='["windows"]')})

    def test_empty_applies_to_is_allowed_for_absent_data(self, tmp_path) -> None:
        docs = load(tmp_path, {"LIM-EXAMPLE.md": document(applies_to="[]", dimensions="[]")})
        assert docs["LIM-EXAMPLE"].tables == frozenset()

    def test_dangling_cross_reference_is_refused(self, tmp_path) -> None:
        body = BODY.replace("text", "see LIM-GONE", 1)
        with pytest.raises(LimitationError, match="LIM-GONE"):
            load(tmp_path, {"LIM-EXAMPLE.md": document(body=body)})

    def test_cross_reference_to_a_sibling_resolves(self, tmp_path) -> None:
        body = BODY.replace("text", "see LIM-SIBLING", 1)
        docs = load(
            tmp_path,
            {"LIM-EXAMPLE.md": document(body=body), "LIM-SIBLING.md": document(doc_id="LIM-SIBLING")},
        )
        assert set(docs) == {"LIM-EXAMPLE", "LIM-SIBLING"}

    def test_readme_is_not_a_document(self, tmp_path) -> None:
        docs = load(tmp_path, {"README.md": "# index\n", "LIM-EXAMPLE.md": document()})
        assert set(docs) == {"LIM-EXAMPLE"}
