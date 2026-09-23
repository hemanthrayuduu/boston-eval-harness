"""The offense-code lookup: which incidents are Part One, and which are crimes.

The spec engine's ``offense_set`` dimension needs two facts per incident -- its
UCR part (for ``part_one``) and whether it is a crime at all (for
``exclude_non_crime``). ``crime_incidents`` cannot answer either on its own for
most of its rows:

* **UCR_PART is blank on every row from 2019 on**, when BPD changed records
  systems. Before that it is populated and, per (code, description), unambiguous.
* **Codes were reused with new meanings across that change.** Code 1831 was
  "DRUGS - SICK ASSIST - OTHER NARCOTIC" (Part Two) and is now "SICK ASSIST", on
  33k rows; 530 went from commercial burglary to B&E of a motor vehicle; 3305
  from demonstrations to drunkenness. Carrying a UCR part across by code alone
  would count medical assists as drug crimes.
* **Part Three is not the same as non-crime.** It is mostly service calls, but
  it also holds hit-and-runs (3830, 3831) and witness intimidation (3170).

So the lookup is keyed on the exact ``(OFFENSE_CODE, OFFENSE_DESCRIPTION)`` pair
as it appears in ``crime_incidents`` -- agents join with
``USING (OFFENSE_CODE, OFFENSE_DESCRIPTION)`` -- and every label says where it
came from:

* ``ucr_part`` is BPD's own label where the pair was observed with one
  (``ucr_part_source = 'observed'``), otherwise a hand label (``'hand'``). BPD's
  labels are kept even where they differ from the FBI's -- ARSON is "Other"
  here, not Part One -- because ``part_one`` is defined as what BPD reports.
* ``is_crime`` follows a rule (Part One and Two are crimes, Part Three is not)
  unless a hand label overrides it. "Other" has no default and must be labelled.

Hand labels live in ``ingest/offense_code_labels.csv``, one row per pair, keyed
by the whitespace-normalised description so that spacing variants share a label.
Every row carries a rationale and a ``reviewed`` flag. The build refuses to
finish while any pair lacks a label, while a hand label contradicts BPD's own,
or while a hand label matches no pair -- so a new code appearing in a refresh
stops the build instead of being silently counted one way or the other.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ingest.build_db import BuildError

__all__ = ["LABELS_PATH", "HandLabel", "read_labels", "build_offense_codes", "normalize"]

LABELS_PATH = Path(__file__).with_name("offense_code_labels.csv")

UCR_PARTS = ("Part One", "Part Two", "Part Three", "Other")
_CRIME_BY_PART = {"Part One": True, "Part Two": True, "Part Three": False}
_LABEL_COLUMNS = ("OFFENSE_CODE", "description", "ucr_part", "is_crime", "rationale", "reviewed")


def normalize(description: str) -> str:
    """Upper-case, trimmed, internal whitespace collapsed. Spelling is left alone:
    'NEGLIGIENT' and 'NEGLIGENT' stay distinct and are labelled separately."""
    return re.sub(r"\s+", " ", description.strip()).upper()


@dataclass(frozen=True)
class HandLabel:
    code: int
    description: str
    ucr_part: str | None
    is_crime: bool | None
    rationale: str
    reviewed: bool

    @property
    def key(self) -> tuple[int, str]:
        return (self.code, self.description)


def read_labels(path: str | Path = LABELS_PATH) -> dict[tuple[int, str], HandLabel]:
    """Parse and validate the hand-label file. Raises BuildError on any bad row."""
    labels: dict[tuple[int, str], HandLabel] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _LABEL_COLUMNS:
            raise BuildError(
                f"{path}: expected columns {list(_LABEL_COLUMNS)}, got {reader.fieldnames}"
            )
        for line, row in enumerate(reader, start=2):
            where = f"{path}:{line}"
            try:
                code = int(row["OFFENSE_CODE"])
            except ValueError:
                raise BuildError(f"{where}: OFFENSE_CODE {row['OFFENSE_CODE']!r} is not an integer") from None

            ucr_part = row["ucr_part"].strip() or None
            if ucr_part is not None and ucr_part not in UCR_PARTS:
                raise BuildError(f"{where}: ucr_part {ucr_part!r} is not one of {list(UCR_PARTS)}")

            is_crime_text = row["is_crime"].strip().lower()
            if is_crime_text not in ("", "true", "false"):
                raise BuildError(f"{where}: is_crime must be true, false, or blank")
            is_crime = None if not is_crime_text else is_crime_text == "true"

            if ucr_part is None and is_crime is None:
                raise BuildError(f"{where}: a label must set ucr_part, is_crime, or both")
            if not row["rationale"].strip():
                raise BuildError(f"{where}: every hand label needs a rationale")
            if row["reviewed"].strip() not in ("yes", "no"):
                raise BuildError(f"{where}: reviewed must be yes or no")

            label = HandLabel(
                code=code,
                description=normalize(row["description"]),
                ucr_part=ucr_part,
                is_crime=is_crime,
                rationale=row["rationale"].strip(),
                reviewed=row["reviewed"].strip() == "yes",
            )
            if label.key in labels:
                raise BuildError(f"{where}: duplicate label for {label.key}")
            labels[label.key] = label
    return labels


def build_offense_codes(
    conn: duckdb.DuckDBPyConnection, labels_path: str | Path = LABELS_PATH
) -> None:
    """Create ``offense_codes`` from ``crime_incidents``, ``offense_codes_source``
    and the hand labels. Raises BuildError rather than leave any pair unlabelled."""
    labels = read_labels(labels_path)

    pairs = conn.execute(
        """
        SELECT OFFENSE_CODE, OFFENSE_DESCRIPTION, min(YEAR), max(YEAR), count(*),
               coalesce(list(DISTINCT UCR_PART) FILTER (WHERE UCR_PART IS NOT NULL), [])
        FROM crime_incidents
        WHERE OFFENSE_CODE IS NOT NULL AND OFFENSE_DESCRIPTION IS NOT NULL
        GROUP BY ALL
        """
    ).fetchall()
    published = dict(
        conn.execute(
            "SELECT CODE, list(DISTINCT trim(NAME) ORDER BY trim(NAME)) "
            "FROM offense_codes_source WHERE CODE IS NOT NULL GROUP BY CODE"
        ).fetchall()
    )

    # BPD's own part, per normalised pair: spacing variants of one description
    # are one offense, so their observed parts are pooled.
    observed: dict[tuple[int, str], set[str]] = {}
    rows_by_key: dict[tuple[int, str], int] = {}
    for code, raw, _first, _last, n, parts in pairs:
        key = (code, normalize(raw))
        observed.setdefault(key, set()).update(parts)
        rows_by_key[key] = rows_by_key.get(key, 0) + n

    problems: list[str] = []

    ambiguous = {k: v for k, v in observed.items() if len(v) > 1}
    problems += [f"observed with more than one UCR part {sorted(v)}: {k}" for k, v in sorted(ambiguous.items())]

    stale = sorted(set(labels) - set(observed))
    problems += [f"hand label matches no (code, description) in crime_incidents: {k}" for k in stale]

    for key, label in sorted(labels.items()):
        seen = observed.get(key, set())
        if label.ucr_part and len(seen) == 1 and label.ucr_part not in seen:
            problems.append(
                f"hand label says {label.ucr_part!r} but BPD labels {key} {next(iter(seen))!r}; "
                "set only is_crime for pairs BPD already labels"
            )

    records = []
    for code, raw, first_year, last_year, n, _parts in sorted(pairs, key=lambda p: (p[0], p[1])):
        key = (code, normalize(raw))
        label = labels.get(key)
        seen = observed[key]

        if len(seen) == 1:
            ucr_part, ucr_source = next(iter(seen)), "observed"
        elif label and label.ucr_part:
            ucr_part, ucr_source = label.ucr_part, "hand"
        else:
            ucr_part, ucr_source = None, None

        if label and label.is_crime is not None:
            is_crime, crime_source = label.is_crime, "hand"
        elif ucr_part in _CRIME_BY_PART:
            is_crime, crime_source = _CRIME_BY_PART[ucr_part], "rule"
        else:
            is_crime, crime_source = None, None

        if ucr_part is None or is_crime is None:
            missing = "ucr_part" if ucr_part is None else "is_crime"
            problems.append(
                f"no {missing} for {key} ({rows_by_key[key]:,} rows, "
                f"{first_year}-{last_year}); add a row to {Path(labels_path).name}"
            )
            continue

        records.append(
            (
                code,
                raw,
                key[1],
                first_year,
                last_year,
                n,
                ucr_part,
                ucr_source,
                is_crime,
                crime_source,
                label.rationale if label else None,
                label.reviewed if label else None,
                published.get(code, []),
            )
        )

    if problems:
        raise BuildError(
            "offense_codes: labels do not cover the snapshot:\n  " + "\n  ".join(problems)
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE offense_codes (
            OFFENSE_CODE INTEGER,
            OFFENSE_DESCRIPTION VARCHAR,
            description_normalized VARCHAR,
            first_year INTEGER,
            last_year INTEGER,
            n_rows BIGINT,
            ucr_part VARCHAR,
            ucr_part_source VARCHAR,
            is_crime BOOLEAN,
            is_crime_source VARCHAR,
            rationale VARCHAR,
            reviewed BOOLEAN,
            published_names VARCHAR[]
        )
        """
    )
    conn.executemany(
        "INSERT INTO offense_codes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records
    )
