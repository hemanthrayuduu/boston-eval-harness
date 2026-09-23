+++
id = "LIM-REPORTS-VS-INCIDENCE"
title = "Incident records count police reports, not crime"
applies_to = ["crime_incidents", "offense_codes"]
dimensions = ["offense_set"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-REPORTS-VS-INCIDENCE: Incident records count police reports, not crime

## What it is

`crime_incidents` documents "the initial details surrounding an incident to which BPD officers
respond" (the city's description). It records what was reported to or observed by police and how
it was classified at the time, not how much crime occurred. Reporting varies by offense,
neighborhood and year; proactive enforcement (drugs, weapons, traffic) creates records in
proportion to police activity; and many records are not crimes at all.

## Evidence

- Only about half of incident records are crimes, and the share is falling: 54.8% of incidents
  in 2016, 52.0% in 2019, 45.5% in 2022, 45.2% in 2025 (by `offense_codes.is_crime`).
- The most common record types are service events: INVESTIGATE PERSON (76,385 rows), TOWED
  MOTOR VEHICLE (38,245), SICK/INJURED/MEDICAL - PERSON (36,537).

## How it can change a claim

A claim that "crime" rose or fell, computed over all incidents, is dominated by non-crime records
and can move for reasons unrelated to crime. Enforcement-driven categories track police activity
as much as offending. A falling count can mean fewer offenses, fewer reports, or different
classification.

## What to do

Filter through `offense_codes` (`is_crime`, or `ucr_category` for Part One); the `offense_set`
dimension is exactly this choice. Say "reported incidents", not "crimes committed". Treat
victimization or risk language as unsupported by this table alone.
