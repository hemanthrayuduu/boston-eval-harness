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
- This project has not yet measured how often past records are revised; the weekly refresh will.

## How it can change a claim

Year-to-date comparisons are sensitive to the cutoff date, and the most recent days undercount.
An official figure published on one date will not necessarily reproduce from a snapshot taken
on another.

## What to do

State the snapshot date with every computed figure. End comparison windows at least 7 days
before the snapshot for shootings, and before any incomplete month. Treat small differences from
an official figure as possibly timing, not error.
