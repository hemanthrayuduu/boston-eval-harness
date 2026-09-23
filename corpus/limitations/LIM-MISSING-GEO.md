+++
id = "LIM-MISSING-GEO"
title = "About 5% of incidents have no location, unevenly by type and year"
applies_to = ["crime_incidents.Lat", "crime_incidents.Long", "crime_incidents.DISTRICT"]
dimensions = ["missing_geo", "geography"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-MISSING-GEO: About 5% of incidents have no location, unevenly by type and year

## What it is

Some incident records have no coordinates, and some have no usable district. The missing
records are not a random sample.

## Evidence

- 48,811 rows (5.1%) have no latitude or longitude. There are no placeholder coordinates such as
  0 or -1; unusable locations are NULL.
- The share varies by year, from 2.5% (2020) to 7.2% (2016, 2017).
- It varies by offense: 20.1% of pedestrian-injury crashes, 17.6% of operating-without-license
  records and 14.5% of class B drug possession records have no coordinates.
- `DISTRICT` is NULL on 4,848 rows and "External" on 944.

## How it can change a claim

Dropping ungeocoded rows undercounts traffic and enforcement categories more than others.
Year-to-year changes in the geocoding rate move area-level counts even when nothing else
changed.

## What to do

State how ungeocoded records were handled; the `missing_geo` dimension is this choice. Report
the city-wide total alongside area totals, so the unallocated share is visible.
