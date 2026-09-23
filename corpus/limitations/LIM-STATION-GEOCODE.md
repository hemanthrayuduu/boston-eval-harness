+++
id = "LIM-STATION-GEOCODE"
title = "Reports taken at police stations are located at the station"
applies_to = ["crime_incidents.Lat", "crime_incidents.Long", "crime_incidents.DISTRICT", "districts", "neighborhoods"]
dimensions = ["geography", "missing_geo"]
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/boston-police-stations-bpd-only", "https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"]
+++

# LIM-STATION-GEOCODE: Reports taken at police stations are located at the station

## What it is

Many reports are taken at a police station rather than where the incident happened, and they are
geocoded to the station. Each district's single most common coordinate is its police station.

## Evidence

- In all 12 districts, the most common coordinate lies 19 to 71 meters from that district's
  station, per the city's police-stations dataset. Those 12 points hold 111,203 rows, 12.3% of
  all geocoded rows.
- The share varies by district, from 5.8% of A1's geocoded rows to 18.4% of E5's.
- The most common record type at these points is one reported after the fact: leaving the
  scene with property damage (7 of the 12 districts) or lost property (3 of 12).

## How it can change a claim

Counts for whatever neighborhood or tract contains a station are inflated by reports about
incidents elsewhere. Neighborhood rankings, hotspot claims and area-level trends can be driven
by where people go to report, not where incidents occur. These rows' `DISTRICT` is the station's
district; whether the incident also happened there cannot be told from the data.

## What to do

For neighborhood or tract geography, check how sensitive the result is to excluding rows within
about 100 meters of a station. Say that location means "where the report was located".
District-level counts are less affected, but not immune.
