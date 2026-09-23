"""BPD figure extraction, tested on tables shaped like the real SSRS output.

The PDFs themselves are gitignored, so these tests feed the table-level parser
the row lists pdfplumber produces for the two report layouts."""

from __future__ import annotations

from datetime import date

import pytest

from claims.bpd_extract import (
    Window,
    check_figures,
    figures_from_tables,
    parse_number,
    parse_window,
)

WINDOW = Window(date(2025, 1, 1), date(2025, 9, 20), date(2026, 1, 1), date(2026, 9, 20))

# Page 1 of the Part One report: offense names span their year columns.
PART_ONE_PAGE_1 = [
    ["", "", "Homicide **", None, None, "Robbery & Attempted", None, None],
    ["Area", "District", "2025", "2026", "5YR\nAVG", "2025", "2026", "5YR\nAVG"],
    ["A", "A01", "1", "2", "1.8", "98", "84", "109.8"],
    [None, "A07", "0", "1", "0.2", "24", "17", "23"],
    [None, "Subtotal", "1", "3", "2.0", "122", "101", "132.8"],
    ["ND", "N/D", "0", "0", "0", "5", "4", "3.2"],
    [None, "Subtotal", "0", "0", "0", "5", "4", "3.2"],
    ["", "Grand\nTotal", "1", "3", "2.0", "127", "105", "136.0"],
]

# Page 2: a spacer column, then a Totals block whose % Change and 5 Year Avg
# columns belong to Totals rather than being subjects of their own.
PART_ONE_PAGE_2 = [
    ["", None, "Auto Theft", None, None, "", "Totals", None, "%\nChange", "5 Year\nAvg"],
    ["Area", "District", "2025", "2026", "5YR\nAVG", "", "2025", "2026", "", "TOTAL"],
    ["A", "A01", "64", "61", "71", "", "1,655", "1,541", "-7%", "1582"],
    ["ND", "N/D", "13", "7", "6.8", "", "86", "59", "0%", "54"],
    ["", "Grand\nTotal", "77", "68", "77.8", "", "1,741", "1,600", "-8%", "1636"],
]

SHOOTING_VICTIMS = [
    ["Citywide Shooting Victims (Fatal and Non-Fatal)", None, None, None, None, None, None],
    ["Shooting Category", "2025", "2026", "# Change", "% Change", "5 Year Avg. *", "% Change\nvs 5 Yr Avg"],
    ["Fatal Shootings", "14", "14", "0", "0%", "18", "-20%"],
    ["Non-Fatal Shootings", "80", "81", "1", "1%", "103", "-21%"],
    ["Total Shooting Victims", "94", "95", "1", "1%", "120", "-21%"],
]

SHOOTING_INCIDENTS = [
    ["Incident Type", "2025", "2026", "# Change", "% Change", "5 Year Avg. *", "% Change\nvs 5 Yr Avg"],
    ["Single", "71", "57", "-14", "-20%", "80.8", "-29.5%"],
    ["Total Victims", "94", "94", "0", "0%", "120.2", "-22%"],
]


def find(figures, **where):
    matches = [f for f in figures if all(getattr(f, k) == v for k, v in where.items())]
    assert len(matches) == 1, (where, matches)
    return matches[0]


class TestParsing:
    def test_window_survives_ssrs_line_breaks(self) -> None:
        text = "Citywide\n1/1/2025\n - \n9/20/2025\n vs. \n1/1/2026\n - \n9/20/2026\nThis data"
        assert parse_window(text) == WINDOW

    def test_window_absent_is_none(self) -> None:
        assert parse_window("Firearm Arrests January 1 - June 11, 2022 vs. 2023") is None

    @pytest.mark.parametrize(
        ("raw", "value"),
        [("1,153", 1153.0), ("-7%", -7.0), ("24.8", 24.8), ("0", 0.0), ("", None), ("N/D", None), (None, None)],
    )
    def test_numbers(self, raw, value) -> None:
        assert parse_number(raw) == value


class TestPartOne:
    def figures(self):
        return figures_from_tables([[PART_ONE_PAGE_1], [PART_ONE_PAGE_2]], WINDOW)

    def test_offense_district_and_period_are_attributed(self) -> None:
        f = find(self.figures(), subject="Robbery & Attempted", district="A07", period="current")
        assert (f.area, f.value, f.report) == ("A", 17.0, "part_one")

    def test_footnote_marks_are_cleaned_but_kept_raw(self) -> None:
        f = find(self.figures(), subject="Homicide", district="A01", period="prior")
        assert f.subject_raw == "Homicide **"

    def test_grand_total_has_no_area(self) -> None:
        f = find(self.figures(), subject="Homicide", district="Grand Total", period="current")
        assert f.area is None and f.value == 3.0

    def test_totals_block_percent_and_average_belong_to_totals(self) -> None:
        figures = self.figures()
        pct = find(figures, subject="Totals", district="A01", period="pct_change")
        avg = find(figures, subject="Totals", district="A01", period="five_year_avg")
        assert (pct.value, avg.value, avg.column) == (-7.0, 1582.0, "TOTAL")
        # The spacer column produced nothing.
        assert not [f for f in figures if f.subject == ""]

    def test_five_year_average_column_is_recognised(self) -> None:
        f = find(self.figures(), subject="Auto Theft", district="A01", period="five_year_avg")
        assert f.value == 71.0


class TestShootings:
    def figures(self):
        return figures_from_tables([[SHOOTING_VICTIMS, SHOOTING_INCIDENTS]], WINDOW)

    def test_victims_table(self) -> None:
        f = find(self.figures(), subject="Fatal Shootings", period="current")
        assert (f.report, f.table_title, f.value) == (
            "shootings",
            "Citywide Shooting Victims (Fatal and Non-Fatal)",
            14.0,
        )

    def test_all_change_columns_are_classified(self) -> None:
        periods = {f.period for f in self.figures() if f.subject == "Single"}
        assert periods == {"prior", "current", "change", "pct_change", "five_year_avg", "pct_change_vs_avg"}

    def test_table_without_title_row_uses_its_header(self) -> None:
        f = find(self.figures(), subject="Single", period="current")
        assert f.table_title == "Incident Type"


class TestChecks:
    """Recompute what the published tables assert about themselves. These are
    the errors actually found in BPD's reports."""

    def test_clean_tables_pass(self) -> None:
        figures = figures_from_tables([[PART_ONE_PAGE_1]], WINDOW)
        assert check_figures(figures) == []

    def test_nd_row_printed_as_zero_percent_is_caught(self) -> None:
        # 86 -> 59 is -31%, printed as 0% -- in 151 of BPD's 153 Part One reports.
        failures = check_figures(figures_from_tables([[PART_ONE_PAGE_2]], WINDOW))
        nd = [c for c in failures if c.kind == "pct_change" and "N/D" in c.detail]
        assert len(nd) == 1 and nd[0].expected == pytest.approx(-31.4) and nd[0].reported == 0.0

    def test_shootings_tables_disagreeing_on_victims_is_caught(self) -> None:
        failures = check_figures(figures_from_tables([[SHOOTING_VICTIMS, SHOOTING_INCIDENTS]], WINDOW))
        assert [(c.kind, c.detail, c.expected, c.reported) for c in failures] == [
            ("victims_across_tables", "current", 95.0, 94.0)
        ]

    def test_subtotal_that_does_not_sum_is_caught(self) -> None:
        broken = [list(row) for row in PART_ONE_PAGE_1]
        broken[4] = [None, "Subtotal", "1", "9", "2.0", "122", "101", "132.8"]
        failures = check_figures(figures_from_tables([[broken]], WINDOW))
        kinds = {(c.kind, c.detail) for c in failures}
        assert ("area_subtotal", "Homicide current area A") in kinds

    def test_fatal_plus_non_fatal_must_equal_total(self) -> None:
        broken = [list(row) for row in SHOOTING_VICTIMS]
        broken[4] = ["Total Shooting Victims", "94", "99", "5", "5%", "120", "-18%"]
        failures = check_figures(figures_from_tables([[broken]], WINDOW))
        assert ("victims_sum", "current") in {(c.kind, c.detail) for c in failures}
