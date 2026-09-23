"""The claim record: one published, dated, attributable statement about Boston
crime, in a form the spec engine can evaluate.

A claim carries its provenance (who said it, where, when it was published and
retrieved, and the exact cell of the exact document), a short paraphrase rather
than a quote, the quantity and window it is about, and a ``specs.assertions``
assertion that decides whether a computed number vindicates it. It carries no
label: labels are derived later from spec curves, or hand-assigned for the two
classes that need it, and live beside the corpus rather than in it.

``cluster_id`` groups claims that are instances of one series -- the same
measure in the same place, restated week after week in cumulative year-to-date
reports. They are correlated, and the bootstrap resamples clusters, not claims.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from specs.assertions import Assertion

__all__ = ["Source", "Measure", "Geography", "Window", "Claim", "SCHEMA_VERSION"]

# 2: optional windows and new window kinds (period comparisons, five-year
# averages, single periods, unspecified), cross-city geography, speakers,
# more measure families. Version-1 records are valid version-2 records.
SCHEMA_VERSION = 2


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Base):
    kind: Literal["bpd_weekly_report", "news", "official_statement", "forum", "other"]
    organization: str
    """Who published it. Attribute to organizations, not individuals, where possible."""
    speaker: str | None = None
    """Who made the claim, when not the publisher -- the organization and role,
    e.g. "Boston Police Department (Commissioner)" quoted by a newspaper."""
    url: str
    published: date
    retrieved: date
    document: str | None = None
    """The file the figure came from, when it is not the page at ``url``."""
    document_sha256: str | None = None
    locator: str | None = None
    """Where in the document: page, table, row."""


class Measure(_Base):
    family: Literal[
        "part_one_offense",
        "part_one_total",
        "part_one_violent",
        "part_one_property",
        "shootings",
        "gunfire",
        "guns_recovered",
        "arrests",
        "other",
    ]
    name: str
    """The publisher's own label, e.g. BPD's "Robbery & Attempted"."""
    shooting_measure: Literal["victims_struck", "fatal_only", "non_fatal_only", "shooting_incidents"] | None = None
    """For shootings: which of the quantities in LIM-SHOOTINGS-VS-VICTIMS the
    publisher counted."""


class Geography(_Base):
    level: Literal["citywide", "area", "district", "district_group", "neighborhood", "cross_city"]
    """``cross_city`` claims compare Boston with other cities; the snapshot has
    no other cities (LIM-NO-CROSS-CITY)."""
    unit: str | None = None
    """The publisher's label, e.g. BPD's "B02"."""
    code: str | None = None
    """The same unit as it appears in the open data, e.g. "B2"."""


class Window(_Base):
    kind: Literal[
        "ytd_vs_prior_ytd",
        "calendar_year",
        "period_vs_period",
        "vs_five_year_average",
        "period",
        "unspecified",
    ]
    """``ytd_vs_prior_ytd``: the same dates a year apart. ``calendar_year``: full
    years. ``period_vs_period``: any other two periods -- including a partial year
    against a full one, which published claims do. ``vs_five_year_average``: the
    current period against the mean of the same period over the five years
    before. ``period``: one period, no comparison (counts, ranks).
    ``unspecified``: the claim names no period."""
    current_start: date | None = None
    current_end: date | None = None
    prior_start: date | None = None
    prior_end: date | None = None
    reference_years: tuple[int, int] | None = None
    """For rank claims: the years ranked among ("lowest since 1957" -> 1957-2024)."""

    @model_validator(mode="after")
    def _dates_match_kind(self) -> Window:
        current = self.current_start is not None and self.current_end is not None
        prior = self.prior_start is not None and self.prior_end is not None
        if self.kind in ("ytd_vs_prior_ytd", "calendar_year", "period_vs_period") and not (current and prior):
            raise ValueError(f"a {self.kind} window needs current and prior start and end dates")
        if self.kind in ("vs_five_year_average", "period") and not current:
            raise ValueError(f"a {self.kind} window needs current start and end dates")
        if self.kind in ("vs_five_year_average", "period", "unspecified") and prior:
            raise ValueError(f"a {self.kind} window has no prior period")
        if self.kind == "unspecified" and (self.current_start or self.current_end):
            raise ValueError("an unspecified window has no dates")
        return self


class Claim(_Base):
    schema_version: int = SCHEMA_VERSION
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]*$")
    source: Source
    paraphrase: str = Field(min_length=20)
    measure: Measure
    geography: Geography
    window: Window
    assertion: Assertion
    stated: dict[str, float | None] = Field(default_factory=dict)
    """The numbers as published, e.g. prior, current, printed_pct, five_year_avg."""
    source_checks_failed: list[str] = Field(default_factory=list)
    """Where the published document contradicts itself about these numbers."""
    notes: list[str] = Field(default_factory=list)
    cluster_id: str
    selection_rule: str
    """Which documented rule put this claim in the corpus."""
