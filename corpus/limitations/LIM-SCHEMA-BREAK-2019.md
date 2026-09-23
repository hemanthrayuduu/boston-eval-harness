+++
id = "LIM-SCHEMA-BREAK-2019"
title = "The crime data changes instrument at the start of 2019"
applies_to = ["crime_incidents", "crime_incidents.UCR_PART", "crime_incidents.OFFENSE_CODE_GROUP", "crime_incidents.SHOOTING"]
dimensions = ["offense_set", "multi_offense", "window", "measure"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-SCHEMA-BREAK-2019: The crime data changes instrument at the start of 2019

## What it is

BPD moved its records to the Mark43 system (launched September 2019, per the city), and the
published data changes at the start of 2019. Several columns that look continuous are not.

## Evidence

- `UCR_PART` and `OFFENSE_CODE_GROUP` are blank on 100% of rows from 2019 onward.
- Grain: through 2018 there is one row per offense, from 2019 one row per incident. 2018 has
  98,888 rows for 86,734 incidents; 2019 has 87,184 rows for 87,184 incidents. From 2018 to 2019,
  rows fall 11.8% while incidents rise 0.5%.
- `SHOOTING` is `Y`/NULL through 2018 and `1`/`0` from 2019, and flagged incidents jump from 170
  (2018) to 810 (2019) as its meaning widens (see LIM-SHOOTINGS-VS-VICTIMS).
- Descriptions are reworded (for example "ROBBERY - STREET" becomes "ROBBERY"), and some codes
  change meaning (see LIM-OFFENSE-CODE-REUSE).
- The city notes that the 2019 data originally posted combined exports from two systems.

## How it can change a claim

Any comparison spanning 2018 to 2019 that counts rows, reads `UCR_PART` or `OFFENSE_CODE_GROUP`,
uses the `SHOOTING` flag, or matches description strings compares two different instruments. The
break can manufacture a trend, or hide one.

## What to do

Count `DISTINCT INCIDENT_NUMBER`, never rows. Take UCR part from `offense_codes`, not from
`UCR_PART`. Don't use the `SHOOTING` flag across 2019. For single-category claims spanning 2019,
the `multi_offense` dimension matters: before 2019 an incident can list several offenses, after
2019 only one.
