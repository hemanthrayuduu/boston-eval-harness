+++
id = "LIM-EXCLUDED-OFFENSES"
title = "Sexual offenses are absent from the published crime data"
applies_to = ["crime_incidents", "offense_codes_source", "crime_incidents_legacy"]
dimensions = ["offense_set"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system", "https://data.boston.gov/dataset/crime-incident-reports-july-2012-august-2015-source-legacy-system"]
+++

# LIM-EXCLUDED-OFFENSES: Sexual offenses are absent from the published crime data

## What it is

The city states that the crime data is complete "with the exclusion of data that falls under MGL
ch.41 s.98F". The data is consistent with sexual offenses being among the excluded records.

## Evidence

- Zero rows in `crime_incidents` have a description mentioning rape, sexual assault or indecent
  assault, in any year from 2015 on.
- The published code list (`offense_codes_source`) includes 26 codes naming rape (the first is
  211), so the codes exist; the records do not.
- The legacy system (2012 to 2015) is nearly as empty: 2 of its 268,056 rows are "Rape and
  Attempted". Its 814 other sex-related rows are sex-offender registrations, which are
  administrative records, not offenses.
- Descriptions mentioning domestic violence or restraining orders: 477 to 533 a year in
  2016–2018, none in 2019, 230 in 2020, none in 2021–2025.

## How it can change a claim

Any claim about sexual assault or rape trends cannot be checked against this data. Totals that
officially include them (violent crime, Part I) cannot be reproduced from it. Domestic-violence
trends are not computable either.

## What to do

Label claims about excluded offenses `unverifiable`, and say why. When comparing an open-data
total with a published one, note that the published figure may include offenses the open data
omits.
