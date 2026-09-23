"""Do BPD's published figures reproduce from the open data?

    uv run python -m experiments.reproducibility_audit

For every figure in BPD's weekly reports (``claims/candidates/bpd.jsonl``, built
by ``claims.bpd_claims``), count the same thing over the same windows in the open
data, using the open-data definition closest to BPD's own:

* offenses classified by description (``offense_mapping = by_description``);
* citywide includes incidents with no district, as BPD's grand total includes its
  no-district (N/D) row; incidents recorded as "External" are left out;
* districts and areas count incidents recorded in them;
* distinct incidents, occurrence dates, the report's exact window;
* shootings from the city's shootings table, by the measure BPD names.

No model is involved. The audit writes, under
``experiments/results/reproducibility_audit/``:

* ``rows.jsonl`` -- one record per BPD figure with both sides (gitignored).
* ``summary.json`` and ``tables.md`` -- the aggregates the write-up
  (``experiments/REPRODUCIBILITY.md``) cites.

Two derived comparisons isolate known causes. **Rape**: BPD's Part One total
includes rape and the open data has none, so the total is also compared with
rape subtracted. **Revision**: BPD restates each year's figures a year later as
the "prior" column, over a window one day shorter (weeks end on Sundays, which
shift a day a year); adding back the open data's count for the dropped day gives
an estimate of how much BPD revised its own figure.

Weekly figures are cumulative year-to-date, so consecutive weeks are strongly
correlated; the tables report how many reports each number rests on and are
descriptive, not inferential.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from claims.bpd_claims import _district_code
from ingest.manifest import verify_snapshot
from specs.compute import DISTRICTS, _category_predicate

__all__ = ["OPEN_CATEGORY", "window_counts", "audit", "summarize"]

OPEN_CATEGORY = {
    "Homicide": "homicide",
    "Robbery & Attempted": "robbery",
    "Commercial Burglary": "commercial_burglary",
    "Residential Burglary": "residential_burglary",
    "Larceny From MV": "larceny_from_mv",
    "Other Larceny": "other_larceny",
    "Auto Theft": "auto_theft",
    "Totals": "part_one_total",
    # BPD splits aggravated assault; the open data does not. Audited both ways:
    # against the sum, and against the non-domestic half alone.
    "Aggravated Assault (domestic + non-domestic)": "aggravated_assault",
    "Non-Domestic Aggravated Assault": "aggravated_assault",
    "Totals excluding rape": "part_one_total",
    "Totals excluding rape and domestic aggravated assault": "part_one_total",
    # BPD publishes these; the open data has none.
    "Rape & Attempted": None,
    "Domestic Aggravated Assault": None,
}
AGE_BINS = ((0, 45), (45, 120), (120, 240), (240, 400), (400, 800), (800, 1300))
AGE_MEASURES = ("Totals excluding rape", "Other Larceny", "Residential Burglary", "Robbery & Attempted", "Total Shooting Victims")
SHOOTING_MEASURE = {
    "Total Shooting Victims": "victims",
    "Fatal Shootings": "fatal",
    "Non-Fatal Shootings": "non_fatal",
    "Total Incidents": "incidents",
}
_CRIME_KEYS = sorted({v for v in OPEN_CATEGORY.values() if v})
FLAT_BAND = 1.0


def window_counts(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> dict[str | None, dict[str, int]]:
    """Distinct incidents per district (None = no district) for every category."""
    columns = ", ".join(
        f"bool_or({_category_predicate(key, 'by_description')}) AS {key}" for key in _CRIME_KEYS
    )
    sums = ", ".join(f"sum({key}::INTEGER) AS {key}" for key in _CRIME_KEYS)
    rows = conn.execute(
        f"""
        WITH incidents AS (
            SELECT ci.INCIDENT_NUMBER, any_value(ci.DISTRICT) AS district, {columns}
            FROM crime_incidents ci JOIN offense_codes oc USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)
            WHERE ci.OCCURRED_ON_DATE >= ? AND ci.OCCURRED_ON_DATE < ?
            GROUP BY ci.INCIDENT_NUMBER
        )
        SELECT district, {sums} FROM incidents GROUP BY district
        """,
        [start, end + timedelta(days=1)],
    ).fetchall()
    return {row[0]: dict(zip(_CRIME_KEYS, (int(v or 0) for v in row[1:]))) for row in rows}


def shooting_counts(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> dict[str, int]:
    row = conn.execute(
        """SELECT count(*), count(*) FILTER (WHERE shooting_type_v2 = 'Fatal'),
                  count(*) FILTER (WHERE shooting_type_v2 = 'Non-Fatal'), count(DISTINCT incident_num)
           FROM shootings WHERE shooting_date >= ? AND shooting_date < ?""",
        [start, end + timedelta(days=1)],
    ).fetchone()
    return dict(zip(("victims", "fatal", "non_fatal", "incidents"), (int(v) for v in row)))


def _cached(cache: dict, key: tuple, compute):
    """Memoise ``compute()`` under ``key``. (Not ``dict.setdefault``, which would
    evaluate its default -- the query -- on every call.)"""
    if key not in cache:
        cache[key] = compute()
    return cache[key]


def _geo_count(counts: dict[str | None, dict[str, int]], key: str, level: str, unit: str | None) -> int:
    if level == "citywide":
        places = [d for d in counts if d in DISTRICTS or d is None]
    elif level == "area":
        places = [d for d in counts if d in DISTRICTS and d.startswith(unit or "?")]
    else:
        places = [_district_code(unit or "")]
    return sum(counts.get(p, {}).get(key, 0) for p in places)


def _direction(pct: float | None, prior: float, current: float) -> str:
    if pct is None:
        return "up" if current > prior else "down" if current < prior else "flat"
    return "flat" if abs(pct) <= FLAT_BAND else ("up" if pct > 0 else "down")


def _pct(prior: float, current: float) -> float | None:
    return None if prior == 0 else 100.0 * (current - prior) / prior


@dataclass
class Figure:
    post_id: int
    published: str
    name: str
    family: str
    level: str
    unit: str | None
    window: dict[str, str]
    prior: float
    current: float


def _figures(candidates: Path) -> list[Figure]:
    figures: list[Figure] = []
    for line in candidates.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        figures.append(
            Figure(
                post_id=int(c["claim_id"].split("-")[1]),
                published=c["source"]["published"],
                name=c["measure"]["name"],
                family=c["measure"]["family"],
                level=c["geography"]["level"],
                unit=c["geography"]["unit"],
                window=c["window"],
                prior=c["stated"]["prior"],
                current=c["stated"]["current"],
            )
        )
    # Derived figures: aggravated assault (BPD splits it), and Part One without rape.
    by_key: dict[tuple, dict[str, Figure]] = defaultdict(dict)
    for f in figures:
        by_key[(f.post_id, f.level, f.unit)][f.name] = f
    for group in list(by_key.values()):
        dom, non = group.get("Domestic Aggravated Assault"), group.get("Non-Domestic Aggravated Assault")
        if dom and non:
            figures.append(Figure(dom.post_id, dom.published, "Aggravated Assault (domestic + non-domestic)",
                                  "part_one_offense", dom.level, dom.unit, dom.window,
                                  dom.prior + non.prior, dom.current + non.current))
        total, rape = group.get("Totals"), group.get("Rape & Attempted")
        if total:
            rape_p, rape_c = (rape.prior, rape.current) if rape else (0, 0)
            figures.append(Figure(total.post_id, total.published, "Totals excluding rape", "part_one_total",
                                  total.level, total.unit, total.window, total.prior - rape_p, total.current - rape_c))
            dom_p, dom_c = (dom.prior, dom.current) if dom else (0, 0)
            figures.append(Figure(total.post_id, total.published, "Totals excluding rape and domestic aggravated assault",
                                  "part_one_total", total.level, total.unit, total.window,
                                  total.prior - rape_p - dom_p, total.current - rape_c - dom_c))
    return figures


def audit(candidates: Path, db: Path, manifest: Path) -> list[dict[str, Any]]:
    verify_snapshot(manifest, db)
    conn = duckdb.connect(str(db), read_only=True)
    crime_through = conn.execute("SELECT max(OCCURRED_ON_DATE)::DATE - 1 FROM crime_incidents").fetchone()[0]
    shootings_through = conn.execute("SELECT max(shooting_date)::DATE - 1 FROM shootings").fetchone()[0]

    crime_cache: dict[tuple[date, date], dict] = {}
    shooting_cache: dict[tuple[date, date], dict] = {}
    rows = []
    for f in _figures(candidates):
        w = {k: date.fromisoformat(v) for k, v in f.window.items() if k.endswith(("start", "end")) and v}
        periods = {"prior": (w["prior_start"], w["prior_end"]), "current": (w["current_start"], w["current_end"])}
        is_shooting = f.family == "shootings"
        if f.name not in OPEN_CATEGORY and f.name not in SHOOTING_MEASURE:
            continue
        if (w["current_end"] > (shootings_through if is_shooting else crime_through)):
            continue  # the open data does not cover the window yet

        open_counts = {}
        for period, (start, end) in periods.items():
            if is_shooting:
                counts = _cached(shooting_cache, (start, end), lambda s=start, e=end: shooting_counts(conn, s, e))
                open_counts[period] = counts[SHOOTING_MEASURE[f.name]]
            else:
                key = OPEN_CATEGORY[f.name]
                if key is None:
                    open_counts[period] = 0
                    continue
                counts = _cached(crime_cache, (start, end), lambda s=start, e=end: window_counts(conn, s, e))
                open_counts[period] = _geo_count(counts, key, f.level, f.unit)

        bpd_pct, open_pct = _pct(f.prior, f.current), _pct(open_counts["prior"], open_counts["current"])
        rows.append({
            "post_id": f.post_id,
            "published": f.published,
            "window_end": f.window["current_end"],
            "prior_start": f.window["prior_start"],
            "prior_end": f.window["prior_end"],
            "year": w["current_end"].year,
            "year_end": w["current_end"].month == 12 and w["current_end"].day >= 25,
            "measure": f.name,
            "level": f.level,
            "unit": f.unit,
            "bpd_prior": f.prior,
            "bpd_current": f.current,
            "open_prior": open_counts["prior"],
            "open_current": open_counts["current"],
            "ratio_current": open_counts["current"] / f.current if f.current else None,
            "ratio_prior": open_counts["prior"] / f.prior if f.prior else None,
            "bpd_pct": bpd_pct,
            "open_pct": open_pct,
            "bpd_direction": _direction(bpd_pct, f.prior, f.current),
            "open_direction": _direction(open_pct, open_counts["prior"], open_counts["current"]),
        })
    rows_by_period = _revisions(rows, conn, crime_cache)
    return rows + rows_by_period


def _revisions(rows: list[dict[str, Any]], conn: duckdb.DuckDBPyConnection, cache: dict) -> list[dict[str, Any]]:
    """BPD's first-published figure for a period against its restatement a year later.

    Year Y's report for week W shows W's year-to-date as "current"; year Y+1's
    report for the same week shows it again as "prior", over a window that ends
    one or two days earlier. Revision = restated - first-published + the open
    data's count for the days the restated window drops.
    """
    citywide = [r for r in rows if r["level"] == "citywide" and r["measure"] in OPEN_CATEGORY and OPEN_CATEGORY[r["measure"]]]
    first = {(r["measure"], r["window_end"]): r for r in citywide}
    out = []
    for r in citywide:
        prior_end = date.fromisoformat(r["prior_end"])
        for gap in (1, 2):
            original_end = prior_end + timedelta(days=gap)
            earlier = first.get((r["measure"], original_end.isoformat()))
            if not earlier:
                continue
            key = OPEN_CATEGORY[r["measure"]]
            dropped = _cached(
                cache,
                (prior_end + timedelta(days=1), original_end),
                lambda s=prior_end + timedelta(days=1), e=original_end: window_counts(conn, s, e),
            )
            dropped_n = _geo_count(dropped, key, "citywide", None)
            revision = r["bpd_prior"] - earlier["bpd_current"] + dropped_n
            out.append({
                "kind": "revision",
                "measure": r["measure"],
                "period_end": original_end.isoformat(),
                "first_published": earlier["bpd_current"],
                "restated": r["bpd_prior"],
                "days_dropped": gap,
                "open_count_dropped_days": dropped_n,
                "revision": revision,
                "revision_pct": 100.0 * revision / earlier["bpd_current"] if earlier["bpd_current"] else None,
                "months_later": 12,
            })
            break
    return out


# ---------------------------------------------------------------------------
# Aggregation


def _quantiles(values: list[float]) -> dict[str, float] | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    q = statistics.quantiles(values, n=10) if len(values) >= 10 else [values[0]] * 9
    return {"n": len(values), "median": round(statistics.median(values), 3), "p10": round(q[0], 3), "p90": round(q[-1], 3)}


def _agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    agree = sum(1 for r in rows if r["bpd_direction"] == r["open_direction"])
    flips = sum(1 for r in rows if {r["bpd_direction"], r["open_direction"]} == {"up", "down"})
    within5 = sum(
        1 for r in rows
        if r["bpd_pct"] is not None and r["open_pct"] is not None and abs(r["open_pct"] - r["bpd_pct"]) <= 5
    )
    return {
        "rows": n,
        "reports": len({r["post_id"] for r in rows}),
        "direction_agrees": round(agree / n, 3) if n else None,
        "direction_opposite": round(flips / n, 3) if n else None,
        "pct_within_5_points": round(within5 / n, 3) if n else None,
        "level_ratio": _quantiles([r["ratio_current"] for r in rows]),
    }


def _by_age(city: list[dict[str, Any]], snapshot: date) -> dict[str, dict[str, Any]]:
    """The percent-change gap (open minus BPD) by how long before the snapshot the
    report's window ended. If BPD compares an immature current year with a mature
    prior year, the gap is small for recent reports and grows as the open data's
    count for the report's current period matures."""
    out: dict[str, dict[str, Any]] = {}
    for measure in AGE_MEASURES:
        for lo, hi in AGE_BINS:
            sel = [
                r for r in city
                if r["measure"] == measure and r["open_pct"] is not None and r["bpd_pct"] is not None
                and lo <= (snapshot - date.fromisoformat(r["window_end"])).days < hi
            ]
            if sel:
                out.setdefault(measure, {})[f"{lo}-{hi} days"] = {
                    "reports": len(sel),
                    "pct_gap_median": round(statistics.median(r["open_pct"] - r["bpd_pct"] for r in sel), 2),
                    "ratio_current": round(statistics.median(r["ratio_current"] for r in sel if r["ratio_current"]), 3),
                    "ratio_prior": round(statistics.median(r["ratio_prior"] for r in sel if r["ratio_prior"]), 3),
                }
    return out


def summarize(records: list[dict[str, Any]], snapshot: date | None = None) -> dict[str, Any]:
    rows = [r for r in records if r.get("kind") != "revision"]
    revisions = [r for r in records if r.get("kind") == "revision"]
    measures = sorted({r["measure"] for r in rows})
    city = [r for r in rows if r["level"] == "citywide"]
    return {
        "overall": {level: _agreement([r for r in rows if r["level"] == level]) for level in ("citywide", "area", "district")},
        "citywide_by_measure": {m: _agreement([r for r in city if r["measure"] == m]) for m in measures},
        "citywide_year_end_by_measure": {
            m: _agreement([r for r in city if r["measure"] == m and r["year_end"]]) for m in measures
        },
        "citywide_totals_ratio_by_year": {
            str(y): _quantiles([r["ratio_current"] for r in city if r["measure"] == "Totals" and r["year"] == y])
            for y in sorted({r["year"] for r in city})
        },
        "district_totals_ratio_by_district": {
            u: _quantiles([r["ratio_current"] for r in rows if r["level"] == "district" and r["measure"] == "Totals" and r["unit"] == u])
            for u in sorted({r["unit"] for r in rows if r["level"] == "district"})
        },
        "district_totals_ex_rape_domestic_ratio_by_district": {
            u: _quantiles([
                r["ratio_current"] for r in rows
                if r["level"] == "district" and r["measure"] == "Totals excluding rape and domestic aggravated assault" and r["unit"] == u
            ])
            for u in sorted({r["unit"] for r in rows if r["level"] == "district"})
        },
        "revision_pct_by_measure": {m: _quantiles([r["revision_pct"] for r in revisions if r["measure"] == m])
                                    for m in sorted({r["measure"] for r in revisions})},
        "pct_gap_by_report_age": _by_age(city, snapshot) if snapshot else {},
        "totals_direction_bpd_vs_open": {
            f"{b}->{o}": n for (b, o), n in sorted(
                defaultdict_count((r["bpd_direction"], r["open_direction"]) for r in city if r["measure"] == "Totals").items()
            )
        },
    }


def defaultdict_count(items) -> dict:
    counts: dict = defaultdict(int)
    for item in items:
        counts[item] += 1
    return counts


def _table(title: str, block: dict[str, dict[str, Any]], key_name: str) -> str:
    lines = [f"### {title}", "", f"| {key_name} | reports | direction agrees | opposite | within 5 pts | open/BPD level (median, p10–p90) |", "|---|---|---|---|---|---|"]
    for key, a in block.items():
        if not a or not a.get("rows"):
            continue
        lr = a["level_ratio"]
        level = f"{lr['median']:.3f} ({lr['p10']:.2f}–{lr['p90']:.2f})" if lr else "—"
        lines.append(
            f"| {key} | {a['reports']} | {a['direction_agrees']:.0%} | {a['direction_opposite']:.0%} | "
            f"{a['pct_within_5_points']:.0%} | {level} |"
        )
    return "\n".join(lines) + "\n"


def _quantile_table(title: str, block: dict[str, dict[str, float] | None], key_name: str, value: str) -> str:
    lines = [f"### {title}", "", f"| {key_name} | n | {value} median | p10 | p90 |", "|---|---|---|---|---|"]
    for key, q in block.items():
        if q:
            lines.append(f"| {key} | {q['n']} | {q['median']:.3f} | {q['p10']:.3f} | {q['p90']:.3f} |")
    return "\n".join(lines) + "\n"


def _age_table(block: dict[str, dict[str, Any]]) -> str:
    lines = [
        "### Percent-change gap (open minus BPD) by how long before the snapshot the window ended",
        "",
        "| measure | window ended | reports | gap (pts, median) | open/BPD current | open/BPD prior |",
        "|---|---|---|---|---|---|",
    ]
    for measure, bins in block.items():
        for age, a in bins.items():
            lines.append(
                f"| {measure} | {age} | {a['reports']} | {a['pct_gap_median']:+.1f} | {a['ratio_current']:.3f} | {a['ratio_prior']:.3f} |"
            )
    return "\n".join(lines) + "\n"


def render(summary: dict[str, Any]) -> str:
    crosstab = summary.get("totals_direction_bpd_vs_open", {})
    return "\n".join([
        "# Reproducibility audit: generated tables",
        "",
        "Generated by `python -m experiments.reproducibility_audit`. Interpretation is in "
        "`experiments/REPRODUCIBILITY.md`. Weekly figures are cumulative, so rows within a series "
        "are correlated; 'reports' counts the distinct weekly reports behind each line.",
        "",
        _table("By geography level", summary["overall"], "level"),
        _table("Citywide, by measure (every weekly report)", summary["citywide_by_measure"], "measure"),
        _table("Citywide, by measure (year-end reports only)", summary["citywide_year_end_by_measure"], "measure"),
        _quantile_table("Citywide Part One total, open/BPD level by year", summary["citywide_totals_ratio_by_year"], "year", "ratio"),
        _quantile_table("Part One total by district, open/BPD level", summary["district_totals_ratio_by_district"], "district", "ratio"),
        _quantile_table(
            "Part One total by district, excluding BPD's rape and domestic aggravated assault rows, open/BPD level",
            summary["district_totals_ex_rape_domestic_ratio_by_district"], "district", "ratio",
        ),
        _quantile_table("BPD's own revisions a year later, % of first-published figure", summary["revision_pct_by_measure"], "measure", "revision %"),
        _age_table(summary.get("pct_gap_by_report_age", {})),
        "### Citywide Part One total: BPD's direction vs the open data's",
        "",
        "| BPD -> open | reports |",
        "|---|---|",
        *[f"| {k} | {n} |" for k, n in crosstab.items()],
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit BPD's published figures against the open data.")
    parser.add_argument("--candidates", type=Path, default=Path("claims/candidates/bpd.jsonl"))
    parser.add_argument("--db", type=Path, default=Path("data/boston.duckdb"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--out", type=Path, default=Path("experiments/results/reproducibility_audit"))
    args = parser.parse_args(argv)

    if not args.candidates.exists():
        print(f"{args.candidates} not found; run python -m claims.bpd_claims first", file=sys.stderr)
        return 1
    records = audit(args.candidates, args.db, args.manifest)
    from ingest.manifest import Manifest

    snapshot = date.fromisoformat(Manifest.read(args.manifest).snapshot_date)
    summary = summarize(records, snapshot)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "tables.md").write_text(render(summary), encoding="utf-8")
    print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
