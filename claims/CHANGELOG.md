# Claim corpus changelog

The corpus is every `claims/corpus/*.jsonl` file, loaded and validated by `claims.corpus`.
Bump the version when claims are added, removed, or change meaning. Scores computed against one
version aren't comparable with another without re-scoring, which is free because scoring is pure.

## 0.1.0 — 2026-09-23

First corpus: 108 claims from BPD's weekly crime-statistics reports (`corpus/bpd.jsonl`),
selected from 29,661 candidates by the rules in `claims/bpd_claims.py`:

| Rule | Claims | What |
|---|---|---|
| `year_end_citywide` | 45 | every citywide figure in each year's last report (2023, 2024, 2025) |
| `latest_citywide` | 15 | every citywide figure in the latest report (window ending 2026-09-20) |
| `mid_year_citywide` | 9 | Part One total, homicides, shooting victims at mid-year (2024, 2025, 2026) |
| `district_totals` | 24 | Part One total by district: 2025 year-end and latest |
| `district_homicide` | 10 | homicides by district, 2025 year-end (districts with 0 in both years excluded) |
| `area_totals` | 5 | Part One total by area, 2024 year-end |

The claims fall into 42 clusters (a cluster is the same measure and place restated across
reports). Four are rape claims that the open data can't check (`LIM-EXCLUDED-OFFENSES`). One
claim's source contradicts itself (the 2026-09-20 shootings tables disagree on total victims).

Hand-sourced claims (news, officials, forums) are not yet in the corpus.
