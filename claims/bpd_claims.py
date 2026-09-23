"""Turn BPD's published figures into candidate claims, and select the corpus.

    uv run python -m claims.bpd_claims

Each row of a BPD weekly report is an implicit published claim: "robberies in
district B-2 were 90 from January 1 to September 20, 2026, up from 77 in the same
period of 2025." Every such row becomes a *candidate* -- about 30,000 of them,
written to ``claims/candidates/bpd.jsonl`` (gitignored, regenerable).

Most candidates are near-duplicates: the reports are cumulative year-to-date and
weekly, so this week's robbery count is last week's plus a few. Putting them all
in the corpus would inflate it with correlated claims that add no information,
so the corpus (``claims/corpus/bpd.jsonl``, committed) is selected by the rules
in ``RULES`` below. They are relative to the data ("the most recent year-end
report"), so a refresh re-selects consistently, and every selected claim records
the rule that chose it.

What a candidate asserts: a ``ChangeAssertion`` from the prior-year to the
current-year figure. Where BPD printed a percent change, the claim asserts the
printed one -- that is what a reader saw -- and if it contradicts BPD's own
counts the claim says so in ``source_checks_failed``. Where BPD printed none, the
percent is computed from its counts. Rows where both years are zero are not
claims. The no-district (N/D) rows are not claims about a place and are left out.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from claims.schema import Claim, Geography, Measure, Source, Window
from specs.assertions import ChangeAssertion

__all__ = ["RULES", "build_candidates", "select", "paraphrase"]

DISTRICTS = ("A01", "A07", "A15", "B02", "B03", "C06", "C11", "D04", "D14", "E05", "E13", "E18")
AREAS = ("A", "B", "C", "D", "E")

NOUNS = {
    "Homicide": "homicides",
    "Rape & Attempted": "rapes and attempted rapes",
    "Robbery & Attempted": "robberies and attempted robberies",
    "Domestic Aggravated Assault": "domestic aggravated assaults",
    "Non-Domestic Aggravated Assault": "non-domestic aggravated assaults",
    "Commercial Burglary": "commercial burglaries",
    "Residential Burglary": "residential burglaries",
    "Larceny From MV": "larcenies from motor vehicles",
    "Other Larceny": "other larcenies",
    "Auto Theft": "auto thefts",
    "Totals": "Part One crimes",
    "Fatal Shootings": "fatal shooting victims",
    "Non-Fatal Shootings": "non-fatal shooting victims",
    "Total Shooting Victims": "shooting victims (fatal and non-fatal)",
    "Total Incidents": "shooting incidents",
}
SHOOTING_MEASURES = {
    "Fatal Shootings": "fatal_only",
    "Non-Fatal Shootings": "non_fatal_only",
    "Total Shooting Victims": "victims_struck",
    "Total Incidents": "shooting_incidents",
}
NOTES = {
    "Homicide": "BPD counts homicides in the year they were ruled a homicide, which can "
    "include deaths from earlier years (per the report's footnote).",
    "Rape & Attempted": "The open data contains no rape records (LIM-EXCLUDED-OFFENSES).",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _district_code(unit: str) -> str:
    """BPD's report label to the open data's DISTRICT value: "B02" -> "B2"."""
    return re.sub(r"^([A-E])0(\d)$", r"\1\2", unit)


def _day(d: date) -> str:
    return f"{d:%B} {d.day}"


def paraphrase(measure: str, geography: Geography, window: Window, prior: float, current: float, pct: float | None) -> str:
    place = {
        "citywide": "citywide",
        "area": f"in area {geography.unit}",
        "district": f"in district {geography.unit}",
    }[geography.level]
    if current > prior:
        movement = f"up from {prior:,.0f}"
    elif current < prior:
        movement = f"down from {prior:,.0f}"
    else:
        movement = f"unchanged from {prior:,.0f}"
    change = f" ({pct:+.1f}%)" if pct is not None else ""
    return (
        f"BPD's weekly report put {NOUNS[measure]} {place} at {current:,.0f} for "
        f"{_day(window.current_start)} – {_day(window.current_end)}, {window.current_end.year}, "
        f"{movement} in the same period of {window.prior_end.year}{change}."
    )


@dataclass(frozen=True)
class Candidate:
    claim: Claim
    post_id: int
    report_end: date


def _row_label(title: str, subject: str, area: str | None, district: str | None) -> str:
    # Mirrors the detail string claims.bpd_extract.check_figures writes.
    return " / ".join(str(k) for k in (title, subject, area, district) if k)


def _checks_for(row_label: str, subject: str, checks: list[dict[str, Any]]) -> list[str]:
    found = []
    for check in checks:
        applies = check["detail"] == row_label or (
            subject == "Total Shooting Victims" and check["kind"] in ("victims_across_tables", "victims_sum")
        )
        if not applies:
            continue
        if check["kind"] == "victims_across_tables":
            found.append(
                f"victims_across_tables: the victims table gives {check['expected']:g} for the "
                f"{check['detail']} year, the incident table's total gives {check['reported']:g}"
            )
        elif check["kind"] == "victims_sum":
            found.append(
                f"victims_sum: fatal plus non-fatal is {check['expected']:g} for the "
                f"{check['detail']} year, the printed total is {check['reported']:g}"
            )
        else:
            found.append(
                f"{check['kind']}: the report's own counts give {check['expected']:g}, "
                f"it prints {check['reported']:g} ({check['detail']})"
            )
    return found


def build_candidates(sources: str | Path) -> list[Candidate]:
    """Every claimable row of every parsed report. One report per window: where
    BPD posted two reports for the same week, the later post wins."""
    base = Path(sources)
    posts = {p["id"]: p for p in map(json.loads, (base / "posts.jsonl").read_text(encoding="utf-8").splitlines())}
    checks = {o["file"]: o["checks_failed"] for o in json.loads((base / "extraction.json").read_text(encoding="utf-8"))}

    rows: dict[tuple, dict[str, Any]] = {}
    with (base / "figures.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            f = json.loads(line)
            if f["report"] == "shootings":
                if f["subject"] not in SHOOTING_MEASURES:
                    continue
                level, unit = "citywide", None
            elif f["district"] == "Grand Total":
                level, unit = "citywide", None
            elif f["district"] == "Subtotal" and f["area"] in AREAS:
                level, unit = "area", f["area"]
            elif f["district"] in DISTRICTS:
                level, unit = "district", f["district"]
            else:
                continue  # N/D rows: not a place
            key = (f["post_id"], f["file"], f["report"], f["table_title"], f["subject"], level, unit)
            row = rows.setdefault(key, {"figure": f, "periods": {}})
            if f["period"] and f["value"] is not None:
                row["periods"][f["period"]] = f["value"]

    # One report per (report type, window end): the latest post.
    latest_post: dict[tuple[str, str], int] = {}
    for (post_id, _file, report, *_rest), row in rows.items():
        key = (report, row["figure"]["window"]["current_end"])
        if post_id > latest_post.get(key, -1):
            latest_post[key] = post_id

    candidates = []
    for (post_id, file, report, title, subject, level, unit), row in sorted(rows.items(), key=lambda kv: tuple("" if v is None else v for v in kv[0])):
        f, periods = row["figure"], row["periods"]
        if latest_post[(report, f["window"]["current_end"])] != post_id:
            continue
        prior, current = periods.get("prior"), periods.get("current")
        if prior is None or current is None or (prior == 0 and current == 0):
            continue

        w = f["window"]
        window = Window(
            kind="calendar_year" if w["current_start"][5:] == "01-01" and w["current_end"][5:] == "12-31" else "ytd_vs_prior_ytd",
            current_start=date.fromisoformat(w["current_start"]),
            current_end=date.fromisoformat(w["current_end"]),
            prior_start=date.fromisoformat(w["prior_start"]),
            prior_end=date.fromisoformat(w["prior_end"]),
        )
        computed = None if prior == 0 else round(100 * (current - prior) / prior, 1)
        printed = periods.get("pct_change")
        stated_pct = printed if printed is not None else computed
        if stated_pct is not None:
            direction = "flat" if abs(stated_pct) <= 1.0 else ("up" if stated_pct > 0 else "down")
        else:
            direction = "up" if current > prior else "down" if current < prior else "flat"

        post = posts[post_id]
        pdf = next(p for p in post["pdfs"] if p["file"] == file)
        geography = Geography(level=level, unit=unit, code=_district_code(unit) if level == "district" else unit)
        family = "shootings" if report == "shootings" else ("part_one_total" if subject == "Totals" else "part_one_offense")
        measure = Measure(family=family, name=subject, shooting_measure=SHOOTING_MEASURES.get(subject))
        where = unit.lower() if unit else "city"
        failed = _checks_for(_row_label(title, subject, f["area"], f["district"]), subject, checks.get(file, []))
        if printed is not None and computed is not None and abs(printed - computed) > 1.0:
            failed.append(f"printed_pct: the counts give {computed:+.1f}%, the report prints {printed:+g}%")

        claim = Claim(
            claim_id=f"bpd-{post_id}-{report.replace('_', '-')}-{_slug(subject)}-{where}",
            source=Source(
                kind="bpd_weekly_report",
                organization="Boston Police Department",
                url=post["link"],
                published=date.fromisoformat(post["date"][:10]),
                retrieved=date.fromisoformat(post["retrieved_at"][:10]),
                document=pdf["url"],
                document_sha256=pdf["sha256"],
                locator=f"page {f['page'] + 1}, table \"{title}\", row {f['row'] + 1}",
            ),
            paraphrase=paraphrase(subject, geography, window, prior, current, stated_pct),
            measure=measure,
            geography=geography,
            window=window,
            assertion=ChangeAssertion(direction=direction, stated_pct=stated_pct),
            stated={
                "prior": prior,
                "current": current,
                "printed_pct": printed,
                "five_year_avg": periods.get("five_year_avg"),
            },
            source_checks_failed=sorted(set(failed)),
            notes=[NOTES[subject]] if subject in NOTES else [],
            cluster_id=f"bpd:{_slug(subject)}:{level}:{where}",
            selection_rule="candidate",
        )
        candidates.append(Candidate(claim, post_id, window.current_end))
    return candidates


# ---------------------------------------------------------------------------
# Selection


@dataclass(frozen=True)
class Context:
    year_ends: tuple[date, ...]
    """The last report window of each year that has a December report, oldest first."""
    mid_years: tuple[date, ...]
    """Per year, the report window ending nearest June 30 (within two weeks)."""
    latest: date


def _context(candidates: Iterable[Candidate]) -> Context:
    ends = sorted({c.report_end for c in candidates})
    by_year: dict[int, list[date]] = {}
    for end in ends:
        by_year.setdefault(end.year, []).append(end)
    year_ends = tuple(max(e for e in group if e.month == 12) for group in by_year.values() if any(e.month == 12 for e in group))
    mid_years = []
    for year, group in by_year.items():
        target = date(year, 6, 30)
        nearest = min(group, key=lambda e: abs((e - target).days))
        if abs((nearest - target).days) <= 14:
            mid_years.append(nearest)
    return Context(year_ends=tuple(sorted(year_ends)), mid_years=tuple(sorted(mid_years)), latest=ends[-1])


def _is(c: Candidate, level: str, *names: str) -> bool:
    return c.claim.geography.level == level and (not names or c.claim.measure.name in names)


Rule = tuple[str, str, Callable[[Candidate, Context], bool]]

RULES: tuple[Rule, ...] = (
    (
        "year_end_citywide",
        "Every citywide figure in each year's last report: the headline numbers BPD and the "
        "press repeat ('homicides were up from 22 to 30').",
        lambda c, x: c.report_end in x.year_ends and _is(c, "citywide"),
    ),
    (
        "latest_citywide",
        "Every citywide figure in the most recent report: current claims.",
        lambda c, x: c.report_end == x.latest and _is(c, "citywide"),
    ),
    (
        "mid_year_citywide",
        "Part One total, homicides and shooting victims at mid-year: a shorter window over the "
        "same series, where cutoff effects are largest.",
        lambda c, x: c.report_end in x.mid_years and _is(c, "citywide", "Totals", "Homicide", "Total Shooting Victims"),
    ),
    (
        "district_totals",
        "Part One total by district in the most recent year-end report and the latest report: "
        "place claims, where geography and station geocoding matter.",
        lambda c, x: c.report_end in (x.year_ends[-1:] + (x.latest,)) and _is(c, "district", "Totals"),
    ),
    (
        "district_homicide",
        "Homicides by district in the most recent year-end report: small counts, where a "
        "percent change is mostly noise.",
        lambda c, x: c.report_end in x.year_ends[-1:] and _is(c, "district", "Homicide"),
    ),
    (
        "area_totals",
        "Part One total by area in the year-end report before the most recent: a coarser "
        "geography than districts.",
        lambda c, x: c.report_end in x.year_ends[-2:-1] and _is(c, "area", "Totals"),
    ),
)


def select(candidates: list[Candidate]) -> list[Claim]:
    """The corpus: candidates matching a rule, each tagged with the first rule it matched."""
    context = _context(candidates)
    chosen: list[Claim] = []
    for c in candidates:
        for name, _why, matches in RULES:
            if matches(c, context):
                chosen.append(c.claim.model_copy(update={"selection_rule": name}))
                break
    return sorted(chosen, key=lambda claim: claim.claim_id)


def _write(path: Path, claims: Iterable[Claim]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for claim in claims:
            handle.write(claim.model_dump_json() + "\n")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build BPD candidate claims and select the corpus.")
    parser.add_argument("--sources", type=Path, default=Path("claims/sources/bpd"))
    parser.add_argument("--candidates", type=Path, default=Path("claims/candidates/bpd.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("claims/corpus/bpd.jsonl"))
    args = parser.parse_args(argv)

    candidates = build_candidates(args.sources)
    n_candidates = _write(args.candidates, (c.claim for c in candidates))
    corpus = select(candidates)
    _write(args.out, corpus)

    by_rule: dict[str, int] = {}
    for claim in corpus:
        by_rule[claim.selection_rule] = by_rule.get(claim.selection_rule, 0) + 1
    print(f"{n_candidates:,} candidates -> {len(corpus)} selected")
    for name, _why, _ in RULES:
        print(f"  {name:<20} {by_rule.get(name, 0)}")
    flagged = sum(1 for claim in corpus if claim.source_checks_failed)
    print(f"  selected claims whose source contradicts itself: {flagged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
