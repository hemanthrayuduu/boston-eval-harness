+++
id = "LIM-OFFENSE-CODE-REUSE"
title = "Some offense codes mean different things before and after 2019"
applies_to = ["crime_incidents.OFFENSE_CODE", "crime_incidents.OFFENSE_DESCRIPTION", "offense_codes"]
dimensions = ["offense_set"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-OFFENSE-CODE-REUSE: Some offense codes mean different things before and after 2019

## What it is

When BPD changed records systems, some offense codes were kept but given new meanings. A code
number alone does not identify an offense across 2019.

## Evidence

- Code 1831 was "DRUGS - SICK ASSIST - OTHER NARCOTIC" (labelled Part Two) before 2019 and is
  "SICK ASSIST" from 2019, on 33,545 rows. Code 1832 is similar (5,446 rows).
- Code 530 was "B&E NON-RESIDENCE NIGHT - FORCE" (a burglary) and is now "BREAKING AND ENTERING
  (B&E) MOTOR VEHICLE". Code 3305 was "DEMONSTRATIONS/RIOT" and is now "DRUNKENNESS".
- 19.5% of 2019+ rows carry a code under a description not seen with that code before 2019. Most
  are rewordings of the same offense; some, like those above, are new meanings.

## How it can change a claim

Carrying a pre-2019 category or UCR part across by code number would count medical assists as
drug offenses and vehicle break-ins as commercial burglaries. Category trends across 2019 can
then be off by multiples, not percentages.

## What to do

Classify offenses by the (`OFFENSE_CODE`, `OFFENSE_DESCRIPTION`) pair: join `offense_codes` with
`USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)`. Its `ucr_part_source` and `is_crime_source` columns
show which labels are BPD's own and which were assigned by hand, with a `rationale` for each.
