+++
id = "LIM-FIO-POLICE-ACTIVITY"
title = "Field interrogation records measure police activity, not crime"
applies_to = []
dimensions = []
evidence_snapshot = "2026-09-23"
sources = ["https://data.boston.gov/dataset/boston-police-department-fio"]
+++

# LIM-FIO-POLICE-ACTIVITY: Field interrogation records measure police activity, not crime

## What it is

FIO (field interrogation and observation) records document encounters between BPD officers and
individuals. They measure police activity and practice, not crime. They are not loaded in this
snapshot: the files come from three records systems with different columns and need
harmonising first.

## Evidence

- The city publishes FIO from three systems (OLD RMS, NEW RMS, MARK43), each with a contact
  table (`FieldContact`) and a person table (`FieldContact_Name`). The 2026-09-23 pull has 28 FIO
  resources with 11 to 44 columns.
- The city warns that "it is not methodologically correct to join the two datasets for the
  purpose of generating aggregate statistics on columns from the FieldContact table", because
  contacts can involve several people.
- FIO records live in a database that can change over time (per the city).

## How it can change a claim

FIO counts rise and fall with enforcement priorities. Using them as a proxy for crime or safety
misreads them. Claims about FIO trends cannot be checked against this snapshot.

## What to do

Label claims that depend on FIO data `unverifiable` against this snapshot. Where FIO is cited as
evidence about crime, say that it measures police activity.
