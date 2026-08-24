"""The analytic choices a competent analyst could defensibly make.

This module is the project's most contestable artifact, so it is written to be
argued with. Every option carries a ``justification``, and the constructor
refuses an option without one -- "defensible" has to mean something more than
"I put it in the list".

The honest failure mode here is a straw-man option: include a choice nobody would
actually make, and every claim comes out ``underdetermined``. Phase 7's hand-audit
of derived labels exists to catch that. If the curve disagrees with your judgment,
the *space* is wrong, not the label.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Option", "Dimension", "DIMENSIONS", "get_dimension"]


@dataclass(frozen=True)
class Option:
    """One defensible choice within a dimension."""

    key: str
    justification: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("option key is required")
        if not self.justification or len(self.justification) < 20:
            raise ValueError(
                f"option {self.key!r} needs a real justification; "
                "an undefended option makes every curve meaningless"
            )


@dataclass(frozen=True)
class Dimension:
    """A family of mutually exclusive analytic choices."""

    key: str
    question: str
    options: tuple[Option, ...]

    def __post_init__(self) -> None:
        if len(self.options) < 2:
            raise ValueError(f"dimension {self.key!r} needs at least two options")
        keys = [o.key for o in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError(f"dimension {self.key!r} has duplicate option keys")

    @property
    def option_keys(self) -> tuple[str, ...]:
        return tuple(o.key for o in self.options)

    def option(self, key: str) -> Option:
        for candidate in self.options:
            if candidate.key == key:
                return candidate
        raise KeyError(f"{self.key!r} has no option {key!r}; have {self.option_keys}")


MEASURE = Dimension(
    key="measure",
    question="Which quantity does 'shootings' refer to?",
    options=(
        Option(
            "shooting_incidents",
            "Distinct incidents where a firearm was discharged. The natural reading of "
            "'shootings' but not what the city's shootings dataset actually counts.",
        ),
        Option(
            "victims_struck",
            "People struck by gunfire, fatal and non-fatal. What the Analyze Boston "
            "shootings dataset records; one incident can produce several victims.",
        ),
        Option(
            "fatal_only",
            "Fatal shootings only. Used when a claim is about lethal violence, and the "
            "measure least sensitive to reporting propensity.",
        ),
        Option(
            "gunfire_reports",
            "Reports of gunfire including those with no confirmed victim. Broadest "
            "measure; heavily dependent on reporting and detection.",
        ),
    ),
)

WINDOW = Dimension(
    key="window",
    question="Over what period is the comparison made?",
    options=(
        Option(
            "ytd_vs_ytd",
            "Year-to-date against the same dates last year. What BPD's weekly reports "
            "use; comparable seasonally but sensitive to the cutoff date.",
        ),
        Option(
            "trailing_12mo",
            "Trailing twelve months against the prior twelve. Removes seasonality and "
            "cutoff sensitivity, but lags recent turning points.",
        ),
        Option(
            "calendar_year",
            "Complete calendar years only. The most stable comparison, unavailable for "
            "the current year.",
        ),
        Option(
            "trailing_3mo",
            "Trailing quarter. Most responsive to recent change and the most volatile; "
            "defensible when a claim is explicitly about a recent period.",
        ),
    ),
)

GEOGRAPHY = Dimension(
    key="geography",
    question="What spatial unit is 'a neighborhood' here?",
    options=(
        Option(
            "bpd_district",
            "BPD reporting districts. The unit the department publishes and polices by; "
            "does not align with how residents name neighborhoods.",
        ),
        Option(
            "neighborhood_boundary",
            "City neighborhood boundary files. Matches lay usage and the way claims are "
            "phrased, but has no operational meaning in the source data.",
        ),
        Option(
            "tract_rollup",
            "Census tracts rolled up to neighborhoods. The only unit with clean ACS "
            "denominators; boundaries are approximate at the edges.",
        ),
    ),
)

DENOMINATOR = Dimension(
    key="denominator",
    question="Rate per what, if anything?",
    options=(
        Option(
            "none",
            "Raw counts. Correct when the claim is explicitly about totals; misleading "
            "when framed as risk, since it tracks population size.",
        ),
        Option(
            "acs_5yr",
            "ACS five-year residential population. The standard denominator; smoothed "
            "over five years, so it lags rapid neighborhood change.",
        ),
        Option(
            "decennial",
            "Decennial census population. Exact enumeration at one moment, increasingly "
            "stale as the decade progresses.",
        ),
        Option(
            "daytime_population",
            "Residential plus commuter population. Arguably the right exposure base for "
            "downtown and commercial districts, where residents are a small minority.",
        ),
    ),
)

OFFENSE_SET = Dimension(
    key="offense_set",
    question="Which incident types count as crime?",
    options=(
        Option(
            "part_one",
            "FBI Part One offenses. The standard for cross-jurisdiction comparison and "
            "what BPD reports; excludes much of what residents experience as crime.",
        ),
        Option(
            "all_incidents",
            "Every incident record. Maximally inclusive and includes non-crime types "
            "such as medical assists and property found.",
        ),
        Option(
            "exclude_non_crime",
            "All incidents minus offense codes that are not crimes. The most faithful to "
            "lay meaning; requires a judgment call on each borderline code.",
        ),
    ),
)

MISSING_GEO = Dimension(
    key="missing_geo",
    question="What happens to records with no usable location?",
    options=(
        Option(
            "drop",
            "Exclude ungeocoded records. Standard practice; biases against areas with "
            "systematically poorer geocoding.",
        ),
        Option(
            "proportional_allocation",
            "Distribute ungeocoded records across areas in proportion to geocoded ones. "
            "Preserves totals; assumes missingness is unrelated to location.",
        ),
    ),
)


DIMENSIONS: dict[str, Dimension] = {
    dim.key: dim
    for dim in (MEASURE, WINDOW, GEOGRAPHY, DENOMINATOR, OFFENSE_SET, MISSING_GEO)
}


def get_dimension(key: str) -> Dimension:
    try:
        return DIMENSIONS[key]
    except KeyError:
        raise KeyError(f"unknown dimension {key!r}; have {sorted(DIMENSIONS)}") from None
