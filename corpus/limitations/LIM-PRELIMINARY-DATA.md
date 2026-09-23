+++
id = "LIM-PRELIMINARY-DATA"
title = "The data is live and recent records are incomplete"
applies_to = ["crime_incidents", "shootings", "firearm_recovery", "fire_incidents"]
dimensions = ["window"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system", "https://data.boston.gov/dataset/shootings"]
+++

# LIM-PRELIMINARY-DATA: The data is live and recent records are incomplete

## What it is

The published tables are exports of live systems. Crime reports record initial details,
shootings are released after a 7-day delay, and records can be added or reclassified after the
fact. A figure computed on one date may differ from the same figure computed later, or from a
figure BPD computed from its internal systems.

## Evidence

- Between two pulls about 13 hours apart on 2026-09-23, the current crime file grew from
  299,060 to 299,221 rows, and firearm recovery counts from 3,700 to 3,701 days.
- `shootings` is published with a 7-day rolling delay (per the city), so the most recent week
  is always incomplete.
- BPD revises its own figures upward as they mature. A year after first publication, its restated
  Part One total is a median 4.3% higher (2.4% for robbery, 6.3% for residential burglary).
- So a weekly "vs. last year" comparison sets a preliminary current year against a matured prior
  year. Read later from the open data, BPD's weekly Part One comparisons show 3–4 points more
  increase. BPD reported a decline or no change in 73 of 152 weekly reports where the open data
  now shows an increase (reproducibility audit, `experiments/REPRODUCIBILITY.md`).

## How it can change a claim

Year-to-date comparisons are sensitive to the cutoff date, and the most recent days undercount.
A comparison of a preliminary current period with a matured prior period leans toward showing
decline. An official figure published on one date will not necessarily reproduce from a snapshot
taken on another.

## What to do

State the snapshot date with every computed figure. End comparison windows at least 7 days
before the snapshot for shootings, and before any incomplete month. When checking an older
year-over-year claim, remember the open data has matured since it was made: a few points more
increase is expected from maturation alone. Treat small differences from an official figure as
possibly timing, not error.
