"""Pandera dataframe schemas applied at ingest boundary.

Rules fail loud; invalid rows are routed to the `quarantine` table with a
reason code, never silently discarded.
"""
from __future__ import annotations

import pandera as pa
from pandera import Column, DataFrameSchema, Check


SALES_TXN_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$")),
        "ts_utc": Column("datetime64[ns, UTC]"),
        "net_sales_cents": Column(int, Check.ge(0)),
        "transactions": Column(int, Check.ge(0)),
        "units": Column(int, Check.ge(0)),
        "currency": Column(str, Check.isin(["USD"])),
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
    checks=[
        Check(
            lambda df: ((df["transactions"] == 0) == (df["net_sales_cents"] == 0)),
            error="transactions and net_sales_cents must agree on zero",
        ),
    ],
)


LABOR_HOURS_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$")),
        "ts_utc": Column("datetime64[ns, UTC]"),
        "labor_minutes": Column(int, Check.in_range(0, 60 * 500)),  # <=500 associates/hr sanity
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
)


TRAFFIC_COUNT_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$")),
        "ts_utc": Column("datetime64[ns, UTC]"),
        "visitors": Column(int, Check.ge(0)),
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
)


STORE_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$"), unique=True),
        "name": Column(str),
        "region": Column(str),
        "timezone": Column(str),
    },
    strict=True,
    coerce=True,
)
