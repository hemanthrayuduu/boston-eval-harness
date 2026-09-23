"""Extract BPD's published figures from the scraped weekly-report PDFs.

    uv run python -m claims.bpd_extract --sources claims/sources/bpd

Reads ``posts.jsonl`` and the PDFs beside it (see ``claims.bpd_scrape``) and
writes, next to them:

* ``figures.jsonl`` -- one record per numeric cell of every parsed table, with
  full provenance: post, PDF hash, page, table, row, column, the raw text, and
  what the column means (prior year, current year, five-year average, change,
  percent change). Gitignored; regenerated from the PDFs.
* ``extraction.json`` -- per-PDF outcome (parsed, or why not) and every failed
  consistency check. Committed: it is the audit trail for the figures.

Tables are recognised by their header content, not by position or file name:
BPD's file names are inconsistent, some 2023 shooting reports carry a "Part One
Crime" page title, and the layout shifted in 2024. A table whose header has a
``District`` column is Part One crime; one whose first header cell is ``Shooting
Category`` or ``Incident Type`` is a shootings table.

Every PDF ends up in exactly one bucket. PDFs this module does not parse --
firearm-arrest summaries (police activity, not crime), image-only scans, layouts
without a recognised table -- are listed with the reason.

Consistency checks recompute what BPD's tables assert about themselves: district
rows sum to area subtotals, subtotals to the grand total, fatal plus non-fatal to
total victims, change and percent change from the two years, and the two
shootings tables agree on total victims. A failed check is a finding about the
published report, recorded, never corrected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

__all__ = [
    "Window",
    "Figure",
    "Check",
    "PdfOutcome",
    "parse_window",
    "parse_number",
    "figures_from_tables",
    "check_figures",
    "extract_pdf",
    "extract_all",
]

_WINDOW = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*vs\.?\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})"
)
_NUMBER = re.compile(r"^\(?(-?[\d,]*\.?\d+)\)?(%?)$")
_SHOOTING_HEADERS = ("Shooting Category", "Incident Type")


@dataclass(frozen=True)
class Window:
    prior_start: date
    prior_end: date
    current_start: date
    current_end: date

    @property
    def prior_year(self) -> int:
        return self.prior_end.year

    @property
    def current_year(self) -> int:
        return self.current_end.year

    def as_dict(self) -> dict[str, str]:
        return {k: v.isoformat() for k, v in asdict(self).items()}


def parse_window(text: str) -> Window | None:
    """The comparison window, e.g. ``1/1/2025 - 9/20/2025 vs. 1/1/2026 - 9/20/2026``.
    Tolerates the line breaks SSRS puts between the parts."""
    match = _WINDOW.search(re.sub(r"\s+", " ", text))
    if not match:
        return None
    g = [int(x) for x in match.groups()]
    return Window(
        prior_start=date(g[2], g[0], g[1]),
        prior_end=date(g[5], g[3], g[4]),
        current_start=date(g[8], g[6], g[7]),
        current_end=date(g[11], g[9], g[10]),
    )


def parse_number(raw: str | None) -> float | None:
    """``1,153`` -> 1153.0, ``-7%`` -> -7.0, ``''`` -> None."""
    if raw is None:
        return None
    text = raw.strip().replace(" ", "")
    match = _NUMBER.match(text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _clean(cell: str | None) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


@dataclass(frozen=True)
class Figure:
    report: str
    """``part_one`` or ``shootings``."""
    table_title: str
    subject: str
    """Offense (Part One) or row category (shootings), cleaned of footnote marks."""
    subject_raw: str
    area: str | None
    district: str | None
    column: str
    period: str | None
    """prior | current | five_year_avg | change | pct_change | pct_change_vs_avg."""
    raw: str
    value: float | None
    page: int
    table: int
    row: int


def _period(column: str, window: Window | None, group: str) -> str | None:
    label = column.lower()
    if window and column == str(window.prior_year):
        return "prior"
    if window and column == str(window.current_year):
        return "current"
    if "vs 5" in label:
        return "pct_change_vs_avg"
    if label.startswith("%"):
        return "pct_change"
    if label.startswith("#"):
        return "change"
    if "5yr" in label.replace(" ", "") or "5 year" in label or (
        label == "total" and "5 year" in group.lower()
    ):
        return "five_year_avg"
    return None


def _subject(raw: str) -> str:
    return _clean(raw).rstrip("* ").strip()


def _part_one_figures(table: list[list[str | None]], window: Window | None, page: int, index: int) -> Iterator[Figure]:
    header_row = next(i for i, row in enumerate(table) if "District" in [_clean(c) for c in row])
    groups_row = table[header_row - 1] if header_row > 0 else [None] * len(table[header_row])
    columns = [_clean(c) for c in table[header_row]]

    # Offense names span their year columns: None continues the name to the left,
    # '' is a spacer. "% Change" and "5 Year Avg" groups belong to the preceding
    # subject (the Totals block), not to a subject of their own.
    groups: list[str] = []
    current = ""
    for cell in groups_row:
        if cell is None:
            pass
        else:
            current = _clean(cell)
        groups.append(current)

    subjects: list[str] = []
    last_subject = ""
    for group in groups:
        if re.match(r"^(%\s*change|5 year avg)", group, re.IGNORECASE):
            subjects.append(last_subject)
        else:
            subjects.append(group)
            if group:
                last_subject = group

    area: str | None = None
    for r in range(header_row + 1, len(table)):
        row = table[r]
        if row[0]:
            area = _clean(row[0]) or area
        district = _clean(row[1]) if len(row) > 1 else ""
        if district == "Grand Total":
            area = None
        for j in range(2, len(row)):
            subject_raw = subjects[j] if j < len(subjects) else ""
            column = columns[j] or groups[j]
            raw = _clean(row[j])
            if not subject_raw or not column or not raw:
                continue
            yield Figure(
                report="part_one",
                table_title="Part One Crime",
                subject=_subject(subject_raw),
                subject_raw=subject_raw,
                area=area if district != "Grand Total" else None,
                district=district,
                column=column,
                period=_period(column, window, groups[j]),
                raw=raw,
                value=parse_number(raw),
                page=page,
                table=index,
                row=r,
            )


def _shooting_figures(table: list[list[str | None]], window: Window | None, page: int, index: int) -> Iterator[Figure]:
    header_row = next(i for i, row in enumerate(table) if _clean(row[0]) in _SHOOTING_HEADERS)
    columns = [_clean(c) for c in table[header_row]]
    title = _clean(table[header_row - 1][0]) if header_row > 0 else columns[0]
    for r in range(header_row + 1, len(table)):
        row = table[r]
        subject_raw = _clean(row[0])
        if not subject_raw:
            continue
        for j in range(1, len(row)):
            raw = _clean(row[j])
            if not raw or j >= len(columns) or not columns[j]:
                continue
            yield Figure(
                report="shootings",
                table_title=title,
                subject=_subject(subject_raw),
                subject_raw=subject_raw,
                area=None,
                district=None,
                column=columns[j],
                period=_period(columns[j], window, ""),
                raw=raw,
                value=parse_number(raw),
                page=page,
                table=index,
                row=r,
            )


def figures_from_tables(
    pages: list[list[list[list[str | None]]]], window: Window | None
) -> list[Figure]:
    """Every figure from every recognised table. ``pages`` is a list of pages,
    each a list of tables, each a list of rows of cell text."""
    figures: list[Figure] = []
    index = 0
    for page_number, tables in enumerate(pages):
        for table in tables:
            if not table or not table[0]:
                continue
            cells = [[_clean(c) for c in row] for row in table]
            if any("District" in row for row in cells):
                figures.extend(_part_one_figures(table, window, page_number, index))
            elif any(row and row[0] in _SHOOTING_HEADERS for row in cells):
                figures.extend(_shooting_figures(table, window, page_number, index))
            index += 1
    return figures


@dataclass(frozen=True)
class Check:
    kind: str
    detail: str
    expected: float
    reported: float


def _close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance


def check_figures(figures: list[Figure]) -> list[Check]:
    """Recompute what the tables assert about themselves. Returns the failures."""
    failures: list[Check] = []
    by_key: dict[tuple, float] = {}
    for f in figures:
        if f.value is not None:
            by_key[(f.report, f.table_title, f.subject, f.area, f.district, f.period)] = f.value

    # Part One: districts -> subtotal -> grand total, per subject and period.
    part_one = [f for f in figures if f.report == "part_one" and f.value is not None]
    for period in ("prior", "current"):
        for subject in sorted({f.subject for f in part_one}):
            rows = [f for f in part_one if f.subject == subject and f.period == period]
            subtotals = {f.area: f.value for f in rows if f.district == "Subtotal"}
            for area, reported in subtotals.items():
                parts = [f.value for f in rows if f.area == area and f.district not in ("Subtotal", "Grand Total")]
                if parts and not _close(sum(parts), reported, 0.5):
                    failures.append(Check("area_subtotal", f"{subject} {period} area {area}", sum(parts), reported))
            grand = [f.value for f in rows if f.district == "Grand Total"]
            if grand and subtotals and not _close(sum(subtotals.values()), grand[0], 0.5):
                failures.append(Check("grand_total", f"{subject} {period}", sum(subtotals.values()), grand[0]))

    # Change and percent change, wherever a row reports them.
    rows: dict[tuple, dict[str, float]] = {}
    for f in figures:
        if f.value is not None and f.period:
            rows.setdefault((f.report, f.table_title, f.subject, f.area, f.district), {})[f.period] = f.value
    for key, periods in rows.items():
        label = " / ".join(str(k) for k in key[1:] if k)
        if "prior" in periods and "current" in periods:
            delta = periods["current"] - periods["prior"]
            if "change" in periods and not _close(delta, periods["change"], 0.5):
                failures.append(Check("change", label, delta, periods["change"]))
            if "pct_change" in periods and periods["prior"]:
                pct = 100 * delta / periods["prior"]
                if not _close(pct, periods["pct_change"], 1.0):
                    failures.append(Check("pct_change", label, round(pct, 1), periods["pct_change"]))

    # Shootings: fatal + non-fatal = total, and the two tables agree on victims.
    shootings = [f for f in figures if f.report == "shootings" and f.value is not None]
    for period in ("prior", "current"):
        values = {f.subject: f.value for f in shootings if f.period == period}
        if {"Fatal Shootings", "Non-Fatal Shootings", "Total Shooting Victims"} <= values.keys():
            total = values["Fatal Shootings"] + values["Non-Fatal Shootings"]
            if not _close(total, values["Total Shooting Victims"], 0.5):
                failures.append(Check("victims_sum", period, total, values["Total Shooting Victims"]))
        if "Total Shooting Victims" in values and "Total Victims" in values:
            if not _close(values["Total Shooting Victims"], values["Total Victims"], 0.5):
                failures.append(
                    Check("victims_across_tables", period, values["Total Shooting Victims"], values["Total Victims"])
                )
    return failures


@dataclass
class PdfOutcome:
    post_id: int
    post_date: str
    post_link: str
    file: str
    sha256: str
    status: str
    """parsed | firearm_report | no_text | no_recognised_table | error"""
    reports: list[str] = field(default_factory=list)
    window: dict[str, str] | None = None
    figures: int = 0
    checks_failed: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


def extract_pdf(path: Path) -> tuple[str, Window | None, list[Figure], str]:
    """Returns (status, window, figures, note) for one PDF."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
        pages = [page.extract_tables() for page in pdf.pages]

    full_text = "\n".join(texts)
    if not full_text.strip():
        return "no_text", None, [], "no extractable text; an image-only PDF would need OCR"
    window = parse_window(full_text)
    figures = figures_from_tables(pages, window)
    if figures:
        note = None if window else "comparison window not found in the text"
        return "parsed", window, figures, note or ""
    if re.search(r"Firearm (Arrests|Violence Metrics)", full_text):
        return "firearm_report", window, [], "firearm arrests/recoveries: police activity, not parsed"
    return "no_recognised_table", window, [], f"first line: {full_text.strip().splitlines()[0][:80]!r}"


def extract_all(sources: str | Path) -> tuple[list[PdfOutcome], list[dict[str, Any]]]:
    """Extract every downloaded PDF listed in ``sources/posts.jsonl``."""
    base = Path(sources)
    outcomes: list[PdfOutcome] = []
    records: list[dict[str, Any]] = []
    for line in (base / "posts.jsonl").read_text(encoding="utf-8").splitlines():
        post = json.loads(line)
        for ref in post["pdfs"]:
            if not ref.get("file"):
                continue
            outcome = PdfOutcome(
                post_id=post["id"],
                post_date=post["date"],
                post_link=post["link"],
                file=ref["file"],
                sha256=ref["sha256"],
                status="error",
            )
            try:
                status, window, figures, note = extract_pdf(base / ref["file"])
            except Exception as err:  # a malformed PDF must not stop the other 400
                outcome.note = f"{type(err).__name__}: {err}"
                outcomes.append(outcome)
                continue
            outcome.status, outcome.note = status, note or None
            outcome.window = window.as_dict() if window else None
            outcome.reports = sorted({f.report for f in figures})
            outcome.figures = len(figures)
            outcome.checks_failed = [asdict(c) for c in check_figures(figures)]
            outcomes.append(outcome)
            for f in figures:
                records.append(
                    {
                        "post_id": post["id"],
                        "post_date": post["date"],
                        "file": ref["file"],
                        "pdf_sha256": ref["sha256"],
                        "window": outcome.window,
                        **asdict(f),
                    }
                )
    return outcomes, records


def summarize(outcomes: list[PdfOutcome]) -> str:
    by_status: dict[str, int] = {}
    for o in outcomes:
        by_status[o.status] = by_status.get(o.status, 0) + 1
    by_report: dict[str, int] = {}
    for o in outcomes:
        for r in o.reports:
            by_report[r] = by_report.get(r, 0) + 1
    failed = [o for o in outcomes if o.checks_failed]
    lines = [
        f"{len(outcomes)} PDFs: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())),
        "  parsed reports: " + ", ".join(f"{k} {v}" for k, v in sorted(by_report.items())),
        f"  figures: {sum(o.figures for o in outcomes):,}",
        f"  PDFs with failed consistency checks: {len(failed)} "
        f"({sum(len(o.checks_failed) for o in failed)} checks)",
    ]
    no_window = [o for o in outcomes if o.status == "parsed" and not o.window]
    if no_window:
        lines.append(f"  parsed without a comparison window: {len(no_window)}")
    for o in outcomes:
        if o.status not in ("parsed", "firearm_report"):
            lines.append(f"    {o.status}: {o.file} ({o.post_date[:10]}) {o.note or ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract figures from scraped BPD report PDFs.")
    parser.add_argument("--sources", type=Path, default=Path("claims/sources/bpd"))
    args = parser.parse_args(argv)

    outcomes, records = extract_all(args.sources)
    with (args.sources / "figures.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    (args.sources / "extraction.json").write_text(
        json.dumps([asdict(o) for o in outcomes], indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(summarize(outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
