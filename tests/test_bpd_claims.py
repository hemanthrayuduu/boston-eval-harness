"""BPD figures -> candidate claims -> selected corpus. Built on a miniature
sources directory in the exact shapes bpd_scrape and bpd_extract write."""

from __future__ import annotations

import json
from datetime import date

import pytest

from claims.bpd_claims import build_candidates, select
from claims.corpus import CorpusError, load_corpus
from claims.schema import Claim


def window(end: str, prior_end: str | None = None) -> dict:
    year = int(end[:4])
    prior_end = prior_end or f"{year - 1}{end[4:]}"
    return {
        "current_start": f"{year}-01-01",
        "current_end": end,
        "prior_start": f"{year - 1}-01-01",
        "prior_end": prior_end,
    }


def figure(post_id: int, end: str, subject: str, district, area, period: str, value, *, report="part_one", title="Part One Crime") -> dict:
    return {
        "post_id": post_id,
        "post_date": f"{end}T12:00:00",
        "file": f"pdf/{end}_{post_id}_1.pdf",
        "pdf_sha256": "ab" * 32,
        "window": window(end),
        "report": report,
        "table_title": title,
        "subject": subject,
        "subject_raw": subject,
        "area": area,
        "district": district,
        "column": period,
        "period": period,
        "raw": str(value),
        "value": value,
        "page": 1,
        "table": 1,
        "row": 3,
    }


def row(post_id, end, subject, district, area, prior, current, **extra):
    out = [figure(post_id, end, subject, district, area, "prior", prior, **extra),
           figure(post_id, end, subject, district, area, "current", current, **extra)]
    return out


@pytest.fixture
def sources(tmp_path):
    figures = [
        *row(10, "2025-12-28", "Robbery & Attempted", "B02", "B", 77, 90),
        *row(10, "2025-12-28", "Homicide", "A07", "A", 0, 0),          # both zero: not a claim
        *row(10, "2025-12-28", "Homicide", "A15", "A", 0, 2),          # from zero: no percent
        *row(10, "2025-12-28", "Totals", "N/D", "ND", 86, 59),          # no district: not a place
        *row(10, "2025-12-28", "Totals", "Grand Total", None, 1741, 1600),
        figure(10, "2025-12-28", "Totals", "Grand Total", None, "pct_change", -43.0),  # misprinted
        *row(11, "2026-09-20", "Totals", "Grand Total", None, 900, 910),
        *row(12, "2026-09-20", "Totals", "Grand Total", None, 900, 950),  # later post, same week
        *row(12, "2026-09-20", "Total Shooting Victims", None, None, 94, 95,
             report="shootings", title="Citywide Shooting Victims (Fatal and Non-Fatal)"),
        figure(12, "2026-09-20", "Total Shooting Victims", None, None, "pct_change", 1.0,
               report="shootings", title="Citywide Shooting Victims (Fatal and Non-Fatal)"),
        *row(12, "2026-09-20", "Single", None, None, 71, 57,
             report="shootings", title="Incident Type"),               # not a claim subject
        *row(13, "2023-12-31", "Auto Theft", "Grand Total", None, 1000, 900),
    ]
    (tmp_path / "figures.jsonl").write_text("\n".join(json.dumps(f) for f in figures) + "\n")
    posts = []
    for pid, end in ((10, "2025-12-28"), (11, "2026-09-20"), (12, "2026-09-20"), (13, "2023-12-31")):
        posts.append({
            "id": pid, "date": f"{end}T12:00:00", "link": f"https://police.boston.gov/post-{pid}/",
            "retrieved_at": "2026-09-23T03:00:00+00:00",
            "pdfs": [{"url": f"https://police.boston.gov/{pid}.pdf", "file": f"pdf/{end}_{pid}_1.pdf", "sha256": "ab" * 32}],
        })
    (tmp_path / "posts.jsonl").write_text("\n".join(json.dumps(p) for p in posts) + "\n")
    extraction = [{"file": "pdf/2026-09-20_12_1.pdf", "checks_failed": [
        {"kind": "victims_across_tables", "detail": "current", "expected": 95.0, "reported": 94.0}]}]
    (tmp_path / "extraction.json").write_text(json.dumps(extraction))
    return tmp_path


def by_id(candidates) -> dict[str, Claim]:
    return {c.claim.claim_id: c.claim for c in candidates}


class TestCandidates:
    def test_district_row_becomes_a_change_claim(self, sources) -> None:
        claim = by_id(build_candidates(sources))["bpd-10-part-one-robbery-attempted-b02"]
        assert claim.geography.model_dump() == {"level": "district", "unit": "B02", "code": "B2"}
        assert claim.assertion.direction == "up"
        assert claim.assertion.stated_pct == pytest.approx(16.9)
        assert claim.stated["prior"] == 77 and claim.stated["current"] == 90
        assert claim.cluster_id == "bpd:robbery-attempted:district:b02"
        assert claim.source.document_sha256 == "ab" * 32
        assert claim.paraphrase.startswith(
            "BPD's weekly report put robberies and attempted robberies in district B02 at 90 "
            "for January 1 – December 28, 2025, up from 77 in the same period of 2024"
        )

    def test_rows_that_are_not_claims_are_left_out(self, sources) -> None:
        ids = set(by_id(build_candidates(sources)))
        assert "bpd-10-part-one-homicide-a07" not in ids       # zero to zero
        assert not any(i.endswith("-n-d") or "-nd" in i for i in ids)  # N/D
        assert not any("single" in i for i in ids)              # shootings breakdown rows

    def test_change_from_zero_has_direction_but_no_percent(self, sources) -> None:
        claim = by_id(build_candidates(sources))["bpd-10-part-one-homicide-a15"]
        assert (claim.assertion.direction, claim.assertion.stated_pct) == ("up", None)
        assert claim.notes  # the ruling-date caveat travels with homicide claims

    def test_printed_percent_is_asserted_and_a_contradiction_is_flagged(self, sources) -> None:
        claim = by_id(build_candidates(sources))["bpd-10-part-one-totals-city"]
        assert claim.assertion.stated_pct == -43.0
        assert any(check.startswith("printed_pct") for check in claim.source_checks_failed)

    def test_later_post_wins_a_duplicated_week(self, sources) -> None:
        ids = set(by_id(build_candidates(sources)))
        assert "bpd-12-part-one-totals-city" in ids
        assert "bpd-11-part-one-totals-city" not in ids

    def test_shooting_table_disagreement_is_attached_in_words(self, sources) -> None:
        claim = by_id(build_candidates(sources))["bpd-12-shootings-total-shooting-victims-city"]
        assert claim.measure.shooting_measure == "victims_struck"
        # BPD printed 1%, inside the flat band; the counts alone would give +1.1%, "up".
        assert claim.assertion.direction == "flat"
        assert claim.source_checks_failed == [
            "victims_across_tables: the victims table gives 95 for the current year, "
            "the incident table's total gives 94"
        ]

    def test_full_calendar_year_window_is_named_as_such(self, sources) -> None:
        candidates = by_id(build_candidates(sources))
        assert candidates["bpd-13-part-one-auto-theft-city"].window.kind == "calendar_year"
        assert candidates["bpd-10-part-one-totals-city"].window.kind == "ytd_vs_prior_ytd"


class TestSelection:
    def test_rules_select_and_tag(self, sources) -> None:
        corpus = {c.claim_id: c for c in select(build_candidates(sources))}
        # 2023-12-31 and 2025-12-28 are year-ends; 2026-09-20 is the latest.
        assert corpus["bpd-13-part-one-auto-theft-city"].selection_rule == "year_end_citywide"
        assert corpus["bpd-12-part-one-totals-city"].selection_rule == "latest_citywide"
        assert corpus["bpd-10-part-one-homicide-a15"].selection_rule == "district_homicide"
        # A district robbery row matches no rule.
        assert "bpd-10-part-one-robbery-attempted-b02" not in corpus

    def test_selection_is_deterministic(self, sources) -> None:
        first = [c.model_dump_json() for c in select(build_candidates(sources))]
        second = [c.model_dump_json() for c in select(build_candidates(sources))]
        assert first == second


class TestCorpus:
    def test_committed_corpus_is_valid(self) -> None:
        corpus = load_corpus()
        assert len(corpus) >= 100
        assert all(c.source.published <= c.source.retrieved for c in corpus.values())
        assert all(c.selection_rule != "candidate" for c in corpus.values())

    def test_duplicate_ids_across_files_are_refused(self, tmp_path, sources) -> None:
        line = select(build_candidates(sources))[0].model_dump_json()
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / "a.jsonl").write_text(line + "\n")
        (tmp_path / "corpus" / "b.jsonl").write_text(line + "\n")
        with pytest.raises(CorpusError, match="duplicate claim_id"):
            load_corpus(tmp_path / "corpus")

    def test_invalid_record_names_file_and_line(self, tmp_path) -> None:
        (tmp_path / "bad.jsonl").write_text('{"claim_id": "x"}\n')
        with pytest.raises(CorpusError, match=r"bad.jsonl:1"):
            load_corpus(tmp_path)
