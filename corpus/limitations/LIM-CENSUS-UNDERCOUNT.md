+++
id = "LIM-CENSUS-UNDERCOUNT"
title = "Population denominators disagree, and the city says the census undercounted"
applies_to = ["neighborhoods"]
dimensions = ["denominator", "geography"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/2025-boston-population-estimates-neighborhood-level", "https://data.boston.gov/dataset/historical-boston-population-estimates-1950-2020-tract-level"]
+++

# LIM-CENSUS-UNDERCOUNT: Population denominators disagree, and the city says the census undercounted

## What it is

Per-capita claims need a population denominator, and the candidates disagree. The city's
Planning Department states that the 2020 Decennial Census and later American Community Survey
releases undercounted Boston's population, and it publishes its own corrected estimates.

## Evidence

- The city's historical tract-level dataset uses decennial counts through 2010 but its own
  estimate for 2020, "in light of the 2020 Decennial Census's and subsequent American Community
  Surveys' undercounts".
- The city's 2025 neighborhood estimates cover 24 tract-approximated neighborhoods; the
  `neighborhoods` boundary table has 26.
- Population data is not yet loaded in this snapshot.

## How it can change a claim

A rate computed with a census denominator and one computed with the city's estimate differ,
and the difference is largest where the undercount is largest. Rankings of neighborhoods by rate
can change with the denominator. Neighborhood definitions differ between sources, too.

## What to do

State which population source a rate uses; the `denominator` dimension is this choice. Until
population data is in the snapshot, treat per-capita claims as not computable here.
