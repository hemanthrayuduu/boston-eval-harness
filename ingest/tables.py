"""The tables in the snapshot, and the pulled resources deliberately left out.

``ingest.build_db`` refuses to run unless every resource in the manifest appears
exactly once, here or in ``UNUSED_RESOURCES``. A new file in the catalog -- next
year's crime incidents, a replaced "to present" file -- then stops the build
instead of being pulled, checksummed, and silently never loaded.

Resource IDs are CKAN's, and are what ``data/manifest.json`` records. Every cast
below was measured lossless against the 2026-09-23 snapshot.

Timestamps load as ``TIMESTAMP`` (no time zone), in Boston local time. The crime
"2023 to Present" file and the shootings file stamp times with ``+00``, but that
is a mislabel, not UTC: the hour-of-day distribution of the ``+00`` rows matches
the older unstamped rows hour for hour (trough at 4-5am, peak at 4-5pm), where
real UTC would shift it by 4-5 hours; and 997 of 1,946 shootings that match a
crime incident carry the identical timestamp, with 5 off by 4-5 hours. The cast
drops the suffix without shifting, which is correct for local times.
"""

from __future__ import annotations

from ingest.build_db import TableSpec

__all__ = ["SNAPSHOT_TABLES", "UNUSED_RESOURCES"]

SNAPSHOT_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "crime_incidents",
        (
        "792031bf-b9bb-467c-b118-fe795befdf00",  # 2015
        "b6c4e2c3-7b1e-4f4a-b019-bef8c6a0e882",  # 2016
        "64ad0053-842c-459b-9833-ff53d568f2e3",  # 2017
        "e86f8e38-a23c-4c1a-8455-c8f94210a8f1",  # 2018
        "34e0ae6b-8c94-4998-ae9e-1b51551fe9ba",  # 2019
        "be047094-85fe-4104-a480-4fa3d03f9623",  # 2020
        "f4495ee9-c42c-4019-82c1-d067f07e45d2",  # 2021
        "313e56df-6d77-49d2-9c49-ee411f10cf58",  # 2022
        "b973d8cb-eeb2-4e7e-99da-c92938efc9c0",  # 2023 to Present
        ),
        casts={
            # Zero-padded in some years' files ("00613") and not others; the
            # integer is the join key to offense_codes_source.
            "OFFENSE_CODE": "INTEGER",
            "OCCURRED_ON_DATE": "TIMESTAMP",
            "YEAR": "INTEGER",
            "MONTH": "INTEGER",
            "HOUR": "INTEGER",
            # 0 and -1 are placeholder coordinates, not casting failures; they
            # survive as numbers and are the spec engine's missing_geo problem.
            "Lat": "DOUBLE",
            "Long": "DOUBLE",
        },
    ),
    TableSpec(
        "crime_incidents_legacy",  # July 2012 - August 2015, a different system
        "ba5ed0e2-e901-438c-b2e0-4acfc3c452b9",
        casts={
            "FROMDATE": "TIMESTAMP",
            "Year": "INTEGER",
            "Month": "INTEGER",
            # State-plane coordinates, not lat/long; Location holds "(lat, long)".
            "X": "DOUBLE",
            "Y": "DOUBLE",
        },
        formats={"FROMDATE": "%m/%d/%Y %I:%M:%S %p"},
    ),
    TableSpec(
        "offense_codes_source",  # rmsoffensecodes.xlsx, as published
        "3aeccf51-a231-4555-ba21-74572b4c33d6",
        casts={"CODE": "INTEGER"},
    ),
    TableSpec(
        "shootings",
        "73c7e069-701f-4910-986d-b950f46c91a1",
        casts={"shooting_date": "TIMESTAMP", "multi_victim": "BOOLEAN"},
    ),
    TableSpec(
        "firearm_recovery",
        "a3d2260f-8a41-4e95-9134-d14711b0f954",
        casts={
            "collection_date": "DATE",
            "crime_guns_recovered": "INTEGER",
            "guns_recovered_safeguard": "INTEGER",
            "buyback_guns_recovered": "INTEGER",
        },
    ),
    TableSpec(
        "fire_incidents",  # 2014 onward
        "91a38b1f-8439-46df-ba47-a30c48845e06",
        casts={
            "alarm_date": "DATE",
            "alarm_time": "TIME",
            "estimated_property_loss": "DOUBLE",
            "estimated_content_loss": "DOUBLE",
        },
    ),
    TableSpec(
        "fire_incidents_legacy",  # 2012 and 2013, in an older column layout
        (
            "64d6aa98-a3aa-4080-a316-b6d493082091",  # 2012
            "76771c63-2d95-4095-bf3d-5f22844350d8",  # 2013
        ),
        casts={
            "Alarm Date": "DATE",
            "Alarm Time": "TIME",
            "Estimated Property Loss": "DOUBLE",
            "Estimated Content Loss": "DOUBLE",
        },
    ),
    TableSpec("fire_incident_types", "29a6ef10-2e09-4e67-a4d3-9c2865c8b7ee"),
    TableSpec("fire_property_uses", "e2819183-5029-42da-b9ab-2e90e044e6e3"),
    # The GeoJSON resources, not the CSVs: the CSVs' shape_wkt is empty at
    # source, so _geometry here is the only boundary geometry there is.
    TableSpec(
        "districts",
        "beead2e5-9d5a-4c74-be5c-65d44f9000f5",
        casts={"Shape_Area": "DOUBLE", "Shape_Length": "DOUBLE"},
    ),
    TableSpec(
        "neighborhoods",
        "e5849875-a6f6-4c9c-9d8a-5048b0fbd03e",
        casts={"acres": "DOUBLE", "sqmiles": "DOUBLE", "Shape_Area": "DOUBLE"},
    ),
)

_DICTIONARY = (
    "data dictionary: source material for corpus/limitations, not data to query"
)
_NO_GEOMETRY = "same fields as the GeoJSON resource, but shape_wkt is empty at source"
_FIO_DEFERRED = (
    "FIO deferred: RMS-era and Mark43-era files differ in columns, and contact and "
    "person records are split across paired files; needs its own harmonisation"
)

UNUSED_RESOURCES: dict[str, str] = {
    "9c30453a-fefa-4fe0-b51a-5fc09b0f4655": _DICTIONARY,  # crime incident field explanation
    "b3b7d7f6-2582-45b1-a342-cc14eaa7d14a": _DICTIONARY,  # legacy crime field explanation
    "15e2652a-99df-4d40-8eae-da7d1dd226f3": _DICTIONARY,  # shootings data dictionary
    "0239b5e8-643f-4a8a-9f9d-4ea51d3847e7": _NO_GEOMETRY,  # police districts CSV
    "d45a6d03-2616-4449-9687-0c864ec9f9e4": _NO_GEOMETRY,  # neighborhoods CSV
    "03f33240-47c1-46f2-87ae-bcdabec092ad": _FIO_DEFERRED,  # 1,709 rows, 22 cols
    "060526ca-ab4e-4da5-997c-1a4460bde5fd": _FIO_DEFERRED,  # 4,860 rows, 21 cols
    "1e5f1bc5-a0b4-4dce-ae1c-7c01ab3364f6": _DICTIONARY,  # 44 rows, 2 cols
    "2d29a168-534b-47c4-977a-b8f4aaf2ea8c": _FIO_DEFERRED,  # 2,963 rows, 14 cols
    "34453828-67ca-45f1-a31d-526b11ca49f4": _FIO_DEFERRED,  # 11,989 rows, 11 cols
    "35cfa498-cb10-43da-b8b2-948a66e48f26": _FIO_DEFERRED,  # 6,628 rows, 25 cols
    "35f3fb8f-4a01-4242-9758-f664e7ead125": _FIO_DEFERRED,  # 9,048 rows, 25 cols
    "3db83582-83a8-4dc8-99a4-23aaa343b437": _FIO_DEFERRED,  # 10,224 rows, 14 cols
    "570eb634-64cf-4e88-8ce8-327422b104fa": _DICTIONARY,  # 13 rows, 2 cols
    "64dd32d9-26f9-4275-9265-97fa3de7e22b": _FIO_DEFERRED,  # 5,717 rows, 22 cols
    "8119eb8d-6ee8-412d-a45d-53c367a98cea": _FIO_DEFERRED,  # 9,203 rows, 25 cols
    "83cccd9f-2c2a-4d51-a65f-0142827f44aa": _FIO_DEFERRED,  # 8,832 rows, 14 cols
    "a16e0f1b-a536-4f64-a712-86dfe6bce9c6": _FIO_DEFERRED,  # 7,787 rows, 14 cols
    "a2b8d492-0f60-45bb-80de-226d2fd67c50": _FIO_DEFERRED,  # 5,868 rows, 21 cols
    "aa46b3ad-1526-4551-9f0f-6dbdfbb429c0": _FIO_DEFERRED,  # 14,093 rows, 11 cols
    "ab4879bb-7f91-4e2c-9b2f-cb82c06faec2": _FIO_DEFERRED,  # 10,199 rows, 14 cols
    "b102d3a4-8b44-443e-bc09-00c44974c3b1": _FIO_DEFERRED,  # 11,481 rows, 11 cols
    "b211de86-fa2b-4a5e-9378-8113125011ca": _FIO_DEFERRED,  # 9,408 rows, 14 cols
    "c696738d-2625-4337-8c50-123c2a85fbad": _FIO_DEFERRED,  # 152,230 rows, 44 cols
    "c72b9288-2658-4e6a-9686-ffdcacb585e7": _FIO_DEFERRED,  # 8,809 rows, 25 cols
    "d137c5da-681f-4a81-bf81-2c430cb65adb": _FIO_DEFERRED,  # 5,903 rows, 21 cols
    "db5fd41f-58fd-42b8-8d14-a59a539e10a3": _FIO_DEFERRED,  # 10,704 rows, 14 cols
    "e350b43e-1c0c-4356-a6ba-1aa599fb8890": _DICTIONARY,  # 24 rows, 2 cols
    "ebb9c51c-6e9a-40a4-94d0-895de9bf47ad": _FIO_DEFERRED,  # 14,994 rows, 11 cols
    "ed2e15c9-ac7b-4d05-890d-a5cdc86c42d8": _FIO_DEFERRED,  # 5,278 rows, 22 cols
    "ee4f1175-54b6-4d06-bceb-26d349118e25": _FIO_DEFERRED,  # 8,708 rows, 25 cols
    "f18a0632-46ea-4032-9749-f5b50cf7b865": _FIO_DEFERRED,  # 15,017 rows, 11 cols
    "f2d08479-878b-4cb9-8ddc-2c8173155123": _FIO_DEFERRED,  # 4,582 rows, 22 cols
}
