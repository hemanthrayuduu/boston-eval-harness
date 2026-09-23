+++
id = "LIM-SHOOTINGS-VS-VICTIMS"
title = 'What "shootings" means: victims, incidents, fatalities or gunfire'
applies_to = ["shootings", "crime_incidents.SHOOTING"]
dimensions = ["measure"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/shootings", "https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-SHOOTINGS-VS-VICTIMS: What "shootings" means: victims, incidents, fatalities or gunfire

## What it is

The `shootings` table and the `SHOOTING` flag in `crime_incidents` measure different things, and
published claims often conflate them.

## Evidence

- `shootings` has one row per victim struck, fatal or non-fatal. It excludes self-inflicted and
  justifiable shootings, and runs 7 days behind (per the city). In 2024 it has 127 victims in
  102 incidents, 20 of them fatal; in 2025, 120 victims in 102 incidents, 19 fatal.
- The `SHOOTING` flag in `crime_incidents` marks gunfire more broadly. From 2019 it is carried
  by records such as INVESTIGATE PROPERTY (2,180 rows), BALLISTICS EVIDENCE/FOUND (929) and
  VANDALISM (458), not only assaults. It flags 485 incidents in 2024.
- The flag's meaning changed at 2019: 170 flagged incidents in 2018, 810 in 2019.
- 312 of the 2,258 `shootings` rows have no matching `INCIDENT_NUMBER` in `crime_incidents`.
- Homicides are not fatal shootings: they include non-firearm deaths.

## How it can change a claim

Depending on which measure is used, "shootings" in the same year differ several-fold. A claim
that uses victims and a check that uses incidents (or the flag) will disagree without either
being wrong.

## What to do

Identify which measure a claim uses: victims struck, shooting incidents, fatal only, or gunfire
reports. The `measure` dimension is this choice. Use `shootings` for victims and incidents with
a victim. Don't use the crime-table flag across 2019.
