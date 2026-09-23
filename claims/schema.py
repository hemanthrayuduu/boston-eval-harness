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

from pydantic import BaseModel, ConfigDict, Field

from specs.assertions import Assertion

__all__ = ["Source", "Measure", "Geography", "Window", "Claim", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Base):
    kind: Literal["bpd_weekly_report", "news", "official_statement", "forum", "other"]
    organization: str
    """Attribute to the organization, not the individual, where possible."""
    url: str
    published: date
    retrieved: date
    document: str | None = None
    """The file the figure came from, when it is not the page at ``url``."""
    document_sha256: str | None = None
    locator: str | None = None
    """Where in the document: page, table, row."""


class Measure(_Base):
    family: Literal["part_one_offense", "part_one_total", "shootings", "other"]
    name: str
    """The publisher's own label, e.g. BPD's "Robbery & Attempted"."""
    shooting_measure: Literal["victims_struck", "fatal_only", "non_fatal_only", "shooting_incidents"] | None = None
    """For shootings: which of the quantities in LIM-SHOOTINGS-VS-VICTIMS the
    publisher counted."""


class Geography(_Base):
    level: Literal["citywide", "area", "district", "neighborhood"]
    unit: str | None = None
    """The publisher's label, e.g. BPD's "B02"."""
    code: str | None = None
    """The same unit as it appears in the open data, e.g. "B2"."""


class Window(_Base):
    kind: Literal["ytd_vs_prior_ytd", "calendar_year"]
    current_start: date
    current_end: date
    prior_start: date
    prior_end: date


class Claim(_Base):
    schema_version: int = SCHEMA_VERSION
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]*$")
    source: Source
    paraphrase: str = Field(min_length=20)
    measure: Measure
    geography: Geography
    window: Window
    assertion: Assertion
    stated: dict[str, float | None]
    """The numbers as published: prior, current, printed_pct, five_year_avg."""
    source_checks_failed: list[str] = Field(default_factory=list)
    """Where the published document contradicts itself about these numbers."""
    notes: list[str] = Field(default_factory=list)
    cluster_id: str
    selection_rule: str
    """Which documented rule put this claim in the corpus."""
