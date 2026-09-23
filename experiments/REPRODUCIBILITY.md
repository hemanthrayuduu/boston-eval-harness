# Do the Boston Police Department's published crime figures reproduce from the city's open data?

*Reproducibility audit, snapshot of 2026-09-23. No model is involved.*

The Boston Police Department (BPD) publishes weekly crime statistics as PDF reports: Part One
crime by offense and district, and shooting victims, each year to date against the same period
last year. The city separately publishes incident-level crime and shootings data on Analyze
Boston. This audit asks whether the first can be rebuilt from the second.

It compares every figure in **305 weekly reports** (153 Part One, 152 shootings, June 2023 to
September 2026) against the open data counted over the report's exact windows, using the
open-data definition closest to BPD's own (see *Method*).

## Findings

**1. Shootings reproduce almost exactly.** Total shooting victims come within 1% of BPD's
figures (median open/BPD ratio 0.993), and the direction of the year-over-year change agrees in
99% of reports. Fatal victims match exactly (median 1.000), non-fatal ones nearly so (0.983). The
shootings data is curated separately by the Boston Regional Intelligence Center, and it shows.

**2. The open data omits domestic aggravated assaults.** BPD reports aggravated assault in two
rows, domestic and non-domestic. The open data does not split them, and its count is only 62% of
BPD's combined total. Against BPD's *non-domestic* row alone, it matches: median ratio **1.008**
(p10–p90: 0.97–1.06) across 152 reports. The records missing from the open data are the
domestic ones. That is consistent with the city's statement that records falling under MGL
ch. 41 §98F are excluded, and with the absence of domestic-violence descriptions from 2021 on
(see `corpus/limitations/LIM-EXCLUDED-OFFENSES.md`).

**3. With rape, these two exclusions explain most of the gap in Part One totals.** The open data
contains no rape records, which BPD's totals include. The open data's Part One total is 0.906 of
BPD's. It rises to 0.914 once BPD's rape row is removed, and to **0.972** once its domestic
aggravated assault row is removed as well. The same adjustment pulls the twelve districts
together, from 0.78–0.98 of BPD's figures to 0.93–1.03. The districts that looked worst gained
the most: B-3 went from 0.779 to 0.946, and B-2 from 0.836 to 0.930.

**4. BPD revises its own figures upward by about 4% within a year.** Each report restates the
previous year's figures as its "prior" column. Comparing a figure as first published with its
restatement a year later, adjusted for the one day by which the windows differ (see *Method*),
shows the Part One total revised up by a median of **4.3%** (p10–p90: 2.2%–8.7%). By offense,
the median revision runs from 2.4% for robbery to 6.3% for residential burglary. First-published
year-to-date figures are preliminary, as the reports themselves say.

**5. That makes BPD's weekly year-over-year comparisons read about 3–4 points too favorable.**
Each weekly report compares the current year, just published and still preliminary, with a prior
year that has had twelve months to fill in. Read later from the open data, the same comparison
shows more increase:

| When the report's window ended (before the snapshot) | Reports | Part One (excluding rape) % change: open minus BPD |
|---|---|---|
| 0–45 days | 6 | +0.7 points |
| 45–120 days | 10 | +1.8 |
| 120–240 days | 15 | +3.8 |
| 240–400 days | 22 | +2.1 |
| 400–800 days | 53 | +3.7 |
| 800–1,300 days | 46 | +7.1 |

The gap is near zero for the most recent reports, where the open data is as preliminary as BPD
was, and it grows with age. That growth comes entirely from the *current* column: the open data's
count for a report's current period rises from 0.896 to 0.930 of BPD's figure as the months pass.
Its count for the *prior* period holds steady at about 0.886, because that year was already
mature when BPD published. Other larceny and residential burglary show the same pattern.
Robbery's is noisier. Shootings don't show it at all (gap about 0), consistent with finding 1.

The consequence for the direction of change is large. **For the citywide Part One total, BPD
reported a decline or no change in 73 of 152 weekly reports where the open data now shows an
increase** (39 "down → up", 34 "flat → up"). The two agree on direction in 38% of reports. None of
this requires anyone to have done anything wrong. It is what comparing a preliminary figure with
a revised one produces. But a reader of the weekly posts sees a systematically rosier trend than
the final data supports.

The oldest reports, from 2023, show an extra few points of gap. Their prior column, 2022, sits
in a separately published yearly file whose counts are relatively low against BPD's restated
figures. That is possibly a file frozen before 2022 had fully matured; this audit cannot tell.

**6. Some gaps remain unexplained.** Residential burglary reproduces at 0.857 of BPD's figures
and robbery at 0.902, for reasons this audit does not identify: neither the exclusions above nor
the offense mapping accounts for them. Homicide varies widely (median 0.917, p10–p90 0.77–1.40).
That fits BPD's footnoted practice of counting homicides in the year they are ruled, which can
include deaths from earlier years, against the open data's occurrence dates.

## What this means for checking published claims

- **Totals and trends that include rape or domestic aggravated assault cannot be reproduced from
  the open data**, only approximated after removing those offenses from the official figure.
- **A weekly year-over-year claim should be checked against a window that has matured.**
  Checking a recent claim against the open data compares like with like (both preliminary);
  checking an old claim against today's open data measures the maturation as much as the claim.
- **Shootings claims are the most checkable.** Offense-level and district-level claims come
  next, after the adjustments above. Part One totals are the least checkable.

These findings feed the benchmark directly. They are why the spec curves (`specs/`) label many
official change claims `contradicted`, and why the benchmark's limitations corpus documents the
exclusions and the preliminary-data bias.

## Method

- **BPD figures:** every row of every parseable weekly report, extracted with provenance by
  `claims/bpd_extract.py` (`claims/sources/bpd/extraction.json`). One report per week: where BPD
  posted twice, the later post. Figures are turned into records by `claims/bpd_claims.py`.
- **Open-data counts:** distinct incidents, by occurrence date, over each report's exact windows.
  Offenses are classified by description (`specs.compute`, `offense_mapping = by_description`).
  - Citywide counts include incidents with no district, as BPD's grand total includes its
    no-district row, and exclude incidents recorded as "External".
  - District and area counts use the incident's recorded district.
  - Shootings come from the city's shootings table, by the measure BPD names.
- **Coverage:** windows ending after the open data is complete (crime through 2026-09-20,
  shootings through 2026-09-05) are skipped.
- **Revision estimate:** a figure first published as "current" for weeks ending on date E is
  restated a year later as "prior" over a window ending one or two days earlier, because weekly
  reports end on Sundays. Revision = restated − first-published + the open data's count for the
  dropped days.
- **Caveats:**
  - Weekly figures are cumulative year to date, so consecutive reports are strongly correlated;
    the numbers above are descriptive, and the report counts show what each rests on.
  - The open data is itself a snapshot and will keep maturing.
  - Direction uses a ±1% flat band.

Reproduce:

```bash
python -m claims.bpd_scrape && python -m claims.bpd_extract && python -m claims.bpd_claims
python -m experiments.reproducibility_audit    # writes experiments/results/reproducibility_audit/
```

Every number above is in `experiments/results/reproducibility_audit/tables.md` and
`summary.json`, generated from the snapshot with content hash `60ce0517…` (see
`data/manifest.json`).
