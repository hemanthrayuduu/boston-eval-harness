"""Compute a claim's quantity under one specification, from the snapshot.

This is the real ``specs.curve.Evaluator``: given a claim and a spec, count the
thing the claim is about, over the window the claim names, the way the spec says
to count it, and return the number the claim's assertion judges -- a percent
change, a level, a difference from a threshold, or a rank.

**Windows come from the claim.** Nearly every corpus claim states its dates
("January 1 - September 20, 2026 vs. 2025"), so the window is not a free choice
here; the spec varies only what the claim leaves open -- which records count as
"robbery" (``offense_mapping``), what happens to records with no district
(``missing_geo``), which offense a multi-offense incident counts under
(``multi_offense``), and, for "shootings" claims that do not say, victims versus
incidents (``measure``). ``space_for`` declares that per claim.

**Counts are distinct incidents**, never rows (see ``specs/dimensions.py``).

**Not computable is a result, with a reason.** ``evaluate`` returns ``None`` and
one of three kinds of reason, because they mean different things and the
headline numbers must not blur them:

* ``out_of_scope:`` -- the data cannot address the claim: other cities, arrests,
  offenses the open data omits (rape, the domestic split of aggravated assault).
* ``coverage:`` -- the data does not cover the window: history before the series
  starts, or dates after the published data ends (shootings run a week behind).
* ``engine_gap:`` -- computable in principle, not built yet: neighborhood
  boundaries, gun-recovery channels, population rates.

This module runs trusted SQL on a read-only connection. It does not go through
``env.sandbox``, which exists to contain SQL a model wrote.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import duckdb

from claims.schema import Claim
from specs.space import Spec, SpecSpace

__all__ = [
    "Result",
    "CategoryDef",
    "CATEGORIES",
    "SnapshotEvaluator",
    "space_for",
    "category_for",
]

DISTRICTS = ("A1", "A7", "A15", "B2", "B3", "C6", "C11", "D4", "D14", "E5", "E13", "E18")
MULTI_OFFENSE_ENDS = date(2019, 1, 1)
"""Before 2019 an incident can list several offenses; from 2019, one."""


@dataclass(frozen=True)
class Result:
    value: float | None
    reason: str | None = None
    counts: dict[str, float] = field(default_factory=dict)
    """The counts behind the value, for the curve record."""


# ---------------------------------------------------------------------------
# Offense categories, in the two ways the offense_mapping dimension allows.


@dataclass(frozen=True)
class CategoryDef:
    include: tuple[str, ...]
    """LIKE patterns on the upper-case, whitespace-normalised description."""
    exclude: tuple[str, ...] = ()
    codes: tuple[tuple[int, int], ...] = ()
    """Inclusive offense-code ranges, BPD's UCR-ordered blocks."""
    code_exclude: tuple[int, ...] = ()


_MV = ("%FROM MV%", "%OF MV PARTS%", "%FROM VEH%", "%(B&E) MOTOR VEHICLE%", "%LARCENY FROM MV%")
_MV_CODES = ((614, 615), (624, 624), (634, 634), (641, 641), (650, 650))

CATEGORIES: dict[str, CategoryDef] = {
    "homicide": CategoryDef(include=("MURDER%", "%CRIMINAL HOMICIDE%"), codes=((100, 111),)),
    "robbery": CategoryDef(include=("%ROBBERY%",), codes=((300, 399),)),
    "aggravated_assault": CategoryDef(
        include=("%AGGRAVATED%", "%D/W%", "A&B HANDS, FEET%"), codes=((400, 499),)
    ),
    "residential_burglary": CategoryDef(
        include=("BURGLARY - RESIDENTIAL%", "B&E RESIDENCE%"), exclude=("%NO PROP TAKEN%",), codes=((510, 529),)
    ),
    "commercial_burglary": CategoryDef(
        include=("BURGLARY - COMMERICAL%", "BURGLARY - COMMERCIAL%", "B&E NON-RESIDENCE%"),
        exclude=("%NO PROP TAKEN%",),
        codes=((530, 549),),
    ),
    "other_burglary": CategoryDef(
        include=("BURGLARY - OTHER%", "MIGRATED REPORT - BURGLARY%"), codes=((500, 509), (550, 569))
    ),
    "larceny_from_mv": CategoryDef(include=_MV, exclude=("%NO PROPERTY STOLEN%",), codes=_MV_CODES),
    "other_larceny": CategoryDef(
        include=("LARCENY%", "MIGRATED REPORT - OTHER LARCENY%"),
        exclude=_MV,
        codes=((600, 669),),
        code_exclude=(614, 615, 624, 634, 641, 650),
    ),
    "auto_theft": CategoryDef(include=("AUTO THEFT%", "MIGRATED REPORT - AUTO THEFT%"), exclude=("%RECOVERED%",), codes=((700, 734),)),
}
_VIOLENT = ("homicide", "robbery", "aggravated_assault")
_PROPERTY = (
    "residential_burglary", "commercial_burglary", "other_burglary",
    "larceny_from_mv", "other_larceny", "auto_theft",
)

NOT_IN_DATA = {
    "rape": "the open data contains no rape records (LIM-EXCLUDED-OFFENSES)",
    "domestic_aggravated_assault": "the open data does not separate domestic from non-domestic aggravated assault",
    "non_domestic_aggravated_assault": "the open data does not separate domestic from non-domestic aggravated assault",
}

_NAMES = {
    "homicide": "homicide",
    "rape & attempted": "rape",
    "rape": "rape",
    "robbery & attempted": "robbery",
    "robbery": "robbery",
    "aggravated assault": "aggravated_assault",
    "domestic aggravated assault": "domestic_aggravated_assault",
    "non-domestic aggravated assault": "non_domestic_aggravated_assault",
    "commercial burglary": "commercial_burglary",
    "residential burglary": "residential_burglary",
    "larceny from mv": "larceny_from_mv",
    "other larceny": "other_larceny",
    "auto theft": "auto_theft",
}


def category_for(claim: Claim) -> str | None:
    """The category key a crime claim is about, or None if it is not a crime claim."""
    family = claim.measure.family
    if family == "part_one_total":
        return "part_one_total"
    if family == "part_one_violent":
        return "part_one_violent"
    if family == "part_one_property":
        return "part_one_property"
    if family == "part_one_offense":
        return _NAMES.get(claim.measure.name.lower())
    return None


def _like_any(column: str, patterns: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"{column} LIKE '{p}'" for p in patterns) + ")" if patterns else "FALSE"


def _category_predicate(category: str, mapping: str) -> str:
    """SQL predicate over (oc.description_normalized, ci.OFFENSE_CODE, oc.ucr_category)."""
    if category == "part_one_total":
        if mapping == "by_description":
            return "oc.ucr_category = 'Part One'"
        return _union_predicate(tuple(CATEGORIES), mapping)
    if category == "part_one_violent":
        return _union_predicate(_VIOLENT, mapping)
    if category == "part_one_property":
        return _union_predicate(_PROPERTY, mapping)

    cat = CATEGORIES[category]
    if mapping == "by_description":
        text = "oc.description_normalized"
        predicate = _like_any(text, cat.include)
        if cat.exclude:
            predicate += f" AND NOT {_like_any(text, cat.exclude)}"
        return f"({predicate})"
    ranges = " OR ".join(f"ci.OFFENSE_CODE BETWEEN {lo} AND {hi}" for lo, hi in cat.codes)
    predicate = f"({ranges})"
    if cat.code_exclude:
        predicate += f" AND ci.OFFENSE_CODE NOT IN ({', '.join(map(str, cat.code_exclude))})"
    return f"({predicate})"


def _union_predicate(categories: tuple[str, ...], mapping: str) -> str:
    return "(" + " OR ".join(_category_predicate(c, mapping) for c in categories) + ")"


# ---------------------------------------------------------------------------
# Spec spaces


def _earliest_date(claim: Claim) -> date | None:
    w = claim.window
    if w.kind == "vs_five_year_average" and w.current_start:
        return _shift_years(w.current_start, -5)
    if w.reference_years:
        return date(w.reference_years[0], 1, 1)
    return w.prior_start or w.current_start


def space_for(claim: Claim) -> SpecSpace:
    """The defensible specifications for one claim.

    Dimensions the claim settles are pinned (recorded, not dropped); dimensions it
    leaves open vary. Claims the engine cannot compute get a pinned space so their
    curve exists and records why nothing computed.
    """
    family = claim.measure.family
    geography = claim.geography.level
    dims: dict[str, str | tuple[str, ...]] = {}

    category = category_for(claim)
    if category is not None and category not in NOT_IN_DATA:
        dims["offense_mapping"] = ("by_description", "by_code_range")
        dims["missing_geo"] = ("drop", "proportional_allocation")
        earliest = _earliest_date(claim)
        touches_multi = earliest is not None and earliest < MULTI_OFFENSE_ENDS
        dims["multi_offense"] = ("any_offense", "most_serious_offense") if touches_multi else "any_offense"
    elif family == "shootings":
        stated = claim.measure.shooting_measure
        dims["measure"] = stated if stated else ("shooting_incidents", "victims_struck")
    elif family == "gunfire":
        dims["measure"] = "gunfire_reports"
        dims["missing_geo"] = ("drop", "proportional_allocation")

    if geography in ("district", "area", "district_group"):
        dims["geography"] = "bpd_district"
    dims["denominator"] = "none"
    return SpecSpace.build(**dims)


# ---------------------------------------------------------------------------
# Evaluation


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def _pct_change(prior: float, current: float) -> float:
    if prior == 0:
        return 0.0 if current == 0 else math.inf
    return 100.0 * (current - prior) / prior


class SnapshotEvaluator:
    """Evaluates corpus claims against an open snapshot connection.

    Use :meth:`open` to verify the snapshot against its manifest first; the
    constructor takes a connection so tests can pass an in-memory database.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, claims: Mapping[str, Claim]) -> None:
        self.conn = conn
        self.claims = claims
        self.crime_through = self._complete_through("SELECT max(OCCURRED_ON_DATE) FROM crime_incidents")
        self.crime_from = self._first("SELECT min(OCCURRED_ON_DATE) FROM crime_incidents")
        self.shootings_through = self._complete_through("SELECT max(shooting_date) FROM shootings")
        self.shootings_from = self._first("SELECT min(shooting_date) FROM shootings")
        self._count = lru_cache(maxsize=None)(self._count_uncached)

    @classmethod
    def open(cls, db_path: str | Path, manifest_path: str | Path, claims: Mapping[str, Claim]) -> SnapshotEvaluator:
        from ingest.manifest import verify_snapshot

        verify_snapshot(manifest_path, db_path)
        return cls(duckdb.connect(str(db_path), read_only=True), claims)

    def _first(self, sql: str) -> date:
        value = self.conn.execute(sql).fetchone()[0]
        return value.date() if hasattr(value, "date") else value

    def _complete_through(self, sql: str) -> date:
        # The last day with any record may be partial; the day before is complete.
        return self._first(sql) - timedelta(days=1)

    # -- Evaluator protocol ------------------------------------------------

    def __call__(self, claim_id: str, spec: Spec) -> float | None:
        return self.evaluate(claim_id, spec).value

    def evaluate(self, claim_id: str, spec: Spec) -> Result:
        claim = self.claims[claim_id]
        blocked = self._blocked(claim)
        if blocked:
            return Result(None, blocked)
        return self._value(claim, spec)

    # -- what cannot be computed, and why ----------------------------------

    def _blocked(self, claim: Claim) -> str | None:
        family, name = claim.measure.family, claim.measure.name.lower()
        if claim.geography.level == "cross_city":
            return "out_of_scope:cross_city"
        if family == "arrests":
            return "out_of_scope:not_in_data:arrests"
        if "per 100,000" in name or "per capita" in name:
            return "engine_gap:population_denominator"
        category = category_for(claim)
        if category in NOT_IN_DATA:
            return f"out_of_scope:not_in_data:{category}"
        if family in ("part_one_offense",) and category is None:
            return f"engine_gap:unmapped_offense:{claim.measure.name}"
        if family == "other":
            return "out_of_scope:not_in_data:unspecified_measure"
        if family == "guns_recovered":
            return "engine_gap:gun_recovery_channels"
        if claim.geography.level == "neighborhood":
            return "engine_gap:neighborhood_geography"
        if claim.window.kind == "unspecified":
            return "out_of_scope:no_window"
        return None

    def _covered(self, start: date, end: date, source: str) -> str | None:
        first, through = (
            (self.shootings_from, self.shootings_through) if source == "shootings" else (self.crime_from, self.crime_through)
        )
        if start < first:
            return f"coverage:before_data_start:{source} starts {first.isoformat()}"
        if end > through:
            return f"coverage:after_data_end:{source} complete through {through.isoformat()}"
        return None

    # -- counting ------------------------------------------------------------

    def _geo(self, claim: Claim) -> tuple[str, tuple[str, ...]]:
        level = claim.geography.level
        if level == "citywide":
            return "citywide", ()
        if level == "district":
            return "subset", (claim.geography.code or "",)
        if level == "area":
            return "subset", tuple(d for d in DISTRICTS if d.startswith(claim.geography.code or "?"))
        if level == "district_group":
            return "subset", tuple((claim.geography.code or "").split("+"))
        raise ValueError(f"unsupported geography {level}")

    def _count_uncached(
        self, source: str, what: str, mapping: str, multi: str, geo: tuple[str, tuple[str, ...]],
        missing_geo: str, start: date, end: date,
    ) -> float:
        params = [start, end + timedelta(days=1)]
        if source == "shootings":
            measure = {
                "victims_struck": "count(*)",
                "fatal_only": "count(*) FILTER (WHERE shooting_type_v2 = 'Fatal')",
                "non_fatal_only": "count(*) FILTER (WHERE shooting_type_v2 = 'Non-Fatal')",
                "shooting_incidents": "count(DISTINCT incident_num)",
            }[what]
            scope, codes = geo
            where = "" if scope == "citywide" else f"AND district IN ({', '.join('?' * len(codes))})"
            if scope != "citywide":
                params += list(codes)
            return float(self.conn.execute(
                f"SELECT {measure} FROM shootings WHERE shooting_date >= ? AND shooting_date < ? {where}", params
            ).fetchone()[0])

        if what == "gunfire":
            match = "ci.SHOOTING IN ('1', 'Y')"
        else:
            match = _category_predicate(what, mapping)

        if multi == "most_serious_offense":
            incidents = f"""
                SELECT INCIDENT_NUMBER, any_value(DISTRICT) AS district,
                       arg_min(matched, severity) AS matched
                FROM (
                    SELECT ci.INCIDENT_NUMBER, ci.DISTRICT, {match} AS matched,
                           (CASE oc.ucr_category WHEN 'Part One' THEN 1 WHEN 'Part Two' THEN 2
                                 WHEN 'Other' THEN 3 ELSE 4 END) * 100000 + ci.OFFENSE_CODE AS severity
                    FROM crime_incidents ci JOIN offense_codes oc USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)
                    WHERE ci.OCCURRED_ON_DATE >= ? AND ci.OCCURRED_ON_DATE < ?
                ) GROUP BY INCIDENT_NUMBER"""
        else:
            incidents = f"""
                SELECT ci.INCIDENT_NUMBER, any_value(ci.DISTRICT) AS district, bool_or({match}) AS matched
                FROM crime_incidents ci JOIN offense_codes oc USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)
                WHERE ci.OCCURRED_ON_DATE >= ? AND ci.OCCURRED_ON_DATE < ?
                GROUP BY ci.INCIDENT_NUMBER"""

        districts = ", ".join(f"'{d}'" for d in DISTRICTS)
        scope, codes = geo
        target = f"district IN ({', '.join(repr(c) for c in codes)})" if scope == "subset" else f"district IN ({districts})"
        row = self.conn.execute(
            f"""SELECT count(*) FILTER (WHERE matched AND {target}),
                       count(*) FILTER (WHERE matched AND district IS NULL),
                       count(*) FILTER (WHERE matched AND district IN ({districts}))
                FROM ({incidents})""",
            params,
        ).fetchone()
        in_target, unlocated, located = (float(x) for x in row)
        if missing_geo == "drop":
            return in_target
        # Proportional allocation: spread incidents with no district across the
        # located ones in proportion. Citywide, that is simply counting them.
        # "External" incidents are located -- outside the districts -- and are
        # never allocated.
        if scope == "citywide":
            return in_target + unlocated
        return in_target + (unlocated * in_target / located if located else 0.0)

    def _series(self, claim: Claim, spec: Spec) -> tuple[str, str, str, str, str]:
        family = claim.measure.family
        if family == "shootings":
            return "shootings", spec["measure"], "", "any_offense", "drop"
        if family == "gunfire":
            return "crime", "gunfire", "", "any_offense", spec.get("missing_geo", "drop")
        return (
            "crime",
            category_for(claim) or "",
            spec["offense_mapping"],
            spec.get("multi_offense", "any_offense"),
            spec.get("missing_geo", "drop"),
        )

    def _count_window(self, claim: Claim, spec: Spec, start: date, end: date) -> tuple[float | None, str | None]:
        source, what, mapping, multi, missing_geo = self._series(claim, spec)
        uncovered = self._covered(start, end, source)
        if uncovered:
            return None, uncovered
        return self._count(source, what, mapping, multi, self._geo(claim), missing_geo, start, end), None

    def _value(self, claim: Claim, spec: Spec) -> Result:
        w, assertion = claim.window, claim.assertion
        if w.kind in ("ytd_vs_prior_ytd", "calendar_year", "period_vs_period"):
            prior, why = self._count_window(claim, spec, w.prior_start, w.prior_end)
            if prior is None:
                return Result(None, why)
            current, why = self._count_window(claim, spec, w.current_start, w.current_end)
            if current is None:
                return Result(None, why)
            return Result(_pct_change(prior, current), counts={"prior": prior, "current": current})

        if w.kind == "vs_five_year_average":
            history = []
            for back in range(1, 6):
                n, why = self._count_window(
                    claim, spec, _shift_years(w.current_start, -back), _shift_years(w.current_end, -back)
                )
                if n is None:
                    return Result(None, why)
                history.append(n)
            current, why = self._count_window(claim, spec, w.current_start, w.current_end)
            if current is None:
                return Result(None, why)
            average = sum(history) / len(history)
            return Result(_pct_change(average, current), counts={"five_year_avg": average, "current": current})

        # A single period: a level, a difference from a threshold, or a rank.
        if assertion.kind == "rank":
            first, last = w.reference_years or (w.current_end.year, w.current_end.year)
            counts = {}
            for year in range(first, last + 1):
                shift = year - w.current_end.year
                n, why = self._count_window(claim, spec, _shift_years(w.current_start, shift), _shift_years(w.current_end, shift))
                if n is None:
                    return Result(None, why)
                counts[str(year)] = n
            current = counts[str(w.current_end.year)]
            rank = 1 + sum(1 for n in counts.values() if n < current)
            return Result(float(rank), counts=counts)

        if assertion.kind == "comparison" and claim.stated.get("per_year_max"):
            yearly = {}
            for year in range(w.current_start.year, w.current_end.year + 1):
                start = max(w.current_start, date(year, 1, 1))
                end = min(w.current_end, date(year, 12, 31))
                n, why = self._count_window(claim, spec, start, end)
                if n is None:
                    return Result(None, why)
                yearly[str(year)] = n
            return Result(max(yearly.values()) - claim.stated["reference"], counts=yearly)

        level, why = self._count_window(claim, spec, w.current_start, w.current_end)
        if level is None:
            return Result(None, why)
        if assertion.kind == "comparison":
            reference = claim.stated.get("reference")
            if reference is None:
                return Result(None, "engine_gap:comparison_without_reference")
            return Result(level - reference, counts={"level": level, "reference": reference})
        return Result(level, counts={"level": level})
