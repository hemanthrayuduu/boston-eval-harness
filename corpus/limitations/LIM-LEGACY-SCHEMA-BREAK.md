+++
id = "LIM-LEGACY-SCHEMA-BREAK"
title = "Before mid-2015 the data comes from a different system"
applies_to = ["crime_incidents_legacy", "crime_incidents"]
dimensions = ["window", "offense_set"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-july-2012-august-2015-source-legacy-system", "https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-LEGACY-SCHEMA-BREAK: Before mid-2015 the data comes from a different system

## What it is

Crime reports from July 2012 to August 2015 are in `crime_incidents_legacy`, from an older
records system with its own schema and offense vocabulary. The current system's data begins in
June 2015. The two overlap for about two months.

## Evidence

- `crime_incidents_legacy` has 268,056 rows, dated 2012-07-08 to 2015-08-10. Its columns differ:
  `COMPNOS` rather than `INCIDENT_NUMBER`, text crime codes like `05RB` in `MAIN_CRIMECODE`
  rather than integer offense codes, and state-plane `X`/`Y` coordinates with latitude/longitude
  inside a `Location` string.
- `crime_incidents` begins 2015-06-15. Of the 200 legacy incidents dated on or after that day,
  182 also appear in `crime_incidents`.

## How it can change a claim

A long-run claim ("lowest since 2012") straddles two instruments whose categories do not map one
to one. Combining the two tables double-counts the overlap. A series that starts in 2015 from
`crime_incidents` alone begins mid-year.

## What to do

Prefer windows that start in 2016 or later, when `crime_incidents` covers full years. If a claim
needs earlier years, say the categories are only approximately comparable, and exclude legacy
records after 2015-06-14 to avoid double counting.
