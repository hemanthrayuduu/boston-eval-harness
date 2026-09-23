+++
id = "LIM-UCR-PART-DEFINITION"
title = "BPD's Part One is not the FBI's Part I, and Part Three is not non-crime"
applies_to = ["crime_incidents.UCR_PART", "offense_codes"]
dimensions = ["offense_set"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-UCR-PART-DEFINITION: BPD's Part One is not the FBI's Part I, and Part Three is not non-crime

## What it is

`UCR_PART` (and `offense_codes.ucr_category`, which follows it) uses BPD's own labels. They differ
from the FBI's Uniform Crime Reporting definitions. "Part Three" is a BPD category that the FBI
scheme does not have.

## Evidence

- ARSON (code 900) is labelled "Other", though arson is FBI Part I.
- Negligent manslaughter (121, 123) and breaking and entering with no property taken (527, 547)
  are also "Other".
- Rape, which is FBI Part I, does not appear in the data at all (see LIM-EXCLUDED-OFFENSES).
- Part Three is mostly service events, but it also holds crimes: leaving the scene of a crash
  (3830, 3831; 57,179 rows) and witness intimidation (3170; 498 rows).

## How it can change a claim

A "Part One" total computed from the open data will not reconcile with FBI figures, or with
official Part I totals that include rape and arson. Treating Part Three as "not crime" drops
tens of thousands of hit-and-runs.

## What to do

Say "Part One as labelled by BPD" when using `ucr_category`. For "is it a crime" questions, use
`offense_codes.is_crime`, which overrides the Part rule for specific codes, each with a rationale.
Do not compare open-data Part One against FBI or other-city Part I figures.
