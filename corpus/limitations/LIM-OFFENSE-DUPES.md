+++
id = "LIM-OFFENSE-DUPES"
title = "One offense can have several descriptions, and the code list repeats codes"
applies_to = ["crime_incidents.OFFENSE_DESCRIPTION", "offense_codes_source", "offense_codes"]
dimensions = ["offense_set", "offense_mapping"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-OFFENSE-DUPES: One offense can have several descriptions, and the code list repeats codes

## What it is

The same offense appears under slightly different description strings (misspellings, spacing,
rewording), and the published code list has repeated codes with conflicting names.

## Evidence

- `crime_incidents` has 255 offense codes with 304 description strings; 51 codes have more than
  one. Two of the extra strings differ only in spacing; others are misspellings ("MURDER,
  NON-NEGLIGIENT MANSLAUGHTER" alongside "NEGLIGENT"; "ANNOYING AND ACCOSTIN") or rewordings.
- `offense_codes_source` has 576 rows for 425 distinct codes, some with conflicting names (301 is
  both "ROBBERY - STREET" and "ROBBERY - FIREARM - BANK"). 30 codes that appear in the data are
  not in the list.
- Joining `crime_incidents` to `offense_codes_source` on code alone turns 956,125 rows into
  1,668,107.

## How it can change a claim

Filtering on one exact description string silently drops the variants' rows, so a count can be
low with no error. Joining the published code list on code multiplies rows and inflates every
total built on the join.

## What to do

Filter on `OFFENSE_CODE`, or join `offense_codes` on (`OFFENSE_CODE`, `OFFENSE_DESCRIPTION`),
which has exactly one row per pair and a `description_normalized` column. Never join
`offense_codes_source` on code alone for counting.
