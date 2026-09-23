# Claim corpus changelog

The corpus is every `claims/corpus/*.jsonl` file, loaded and validated by `claims.corpus`.
Bump the version when claims are added, removed, or change meaning. Scores computed against one
version aren't comparable with another without re-scoring, which is free because scoring is pure.

## 0.2.0 — 2026-09-23

Adds 43 hand-sourced claims (`corpus/hand.jsonl`, built from `hand_sourced.toml`) from 11
pages: the City of Boston's 2025 year-end release, WBUR (2), GBH News (2), Boston.com (2), the
Boston Globe, NBC Boston, the Boston Herald via Police1, and CBS Boston. **151 claims in total.**

- **Sourcing.** Every claim was read on its source page. The publication date and key phrases
  were confirmed in the raw HTML, and several claims were corrected to match the page (see
  STATUS.md §4i). Bot-blocked pages were left out even when search results quoted them.
- **What they cover.**
  - Mix: 35 news and 8 official; 30 change, 7 rank, 4 level and 2 comparison assertions.
  - Unverifiable on the open data: 5 cross-city claims, plus claims about arrests, rape, rates
    per capita, and history before 2012.
- **Schema v2.** Optional and new window kinds (`period_vs_period`, `vs_five_year_average`,
  `period`, `unspecified`, `reference_years` for ranks), `cross_city` and `district_group`
  geographies, a `speaker` distinct from the publisher, and more measure families.
  Version-1 records remain valid; `bpd.jsonl` was regenerated as v2 with no other change.
- **`ChangeAssertion.bound`** (`about` / `at_least` / `at_most`), so "down more than 30%" is
  scored as a bound, not a point estimate.

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
