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


INBOUND_SHIPMENT_STATUSES = ["scheduled", "in_transit", "delayed", "arrived", "cancelled"]


INBOUND_SHIPMENT_SCHEMA = DataFrameSchema(
    {
        "shipment_id": Column(str, Check.str_matches(r"^SHP-\d{6}$"), unique=True),
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$")),
        "dc_id": Column(str, Check.str_matches(r"^DC\d{2}$")),
        "truck_id": Column(str, Check.str_matches(r"^TRK-\d{4}$")),
        "carrier": Column(str),
        "planned_arrival_utc": Column("datetime64[ns, UTC]"),
        "eta_utc": Column("datetime64[ns, UTC]"),
        "status": Column(str, Check.isin(INBOUND_SHIPMENT_STATUSES)),
        # Negative = early. Anything beyond 3 days late is a TMS data error, not a truck.
        "delay_minutes": Column(int, Check.in_range(-240, 60 * 72)),
        "pallets": Column(int, Check.in_range(0, 60)),        # <= 60 pallets per trailer
        "cases": Column(int, Check.ge(0)),
        "unload_minutes_est": Column(int, Check.in_range(0, 60 * 24)),
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
    checks=[
        Check(
            lambda df: (
                ((df["eta_utc"] - df["planned_arrival_utc"]).dt.total_seconds() // 60).astype(int)
                == df["delay_minutes"]
            ),
            error="delay_minutes must equal eta_utc - planned_arrival_utc in minutes",
        ),
        Check(
            lambda df: ~((df["pallets"] == 0) & (df["cases"] > 0)),
            error="cases without pallets is not a valid TMS load",
        ),
    ],
)


MINUTES_PER_DAY = 24 * 60


HVAC_SCHEDULE_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$"), unique=True),
        "bms_id": Column(str, Check.str_matches(r"^BMS-[A-Z0-9]+$")),
        "hvac_on_minute": Column(int, Check.in_range(0, MINUTES_PER_DAY)),
        "hvac_off_minute": Column(int, Check.in_range(0, MINUTES_PER_DAY)),
        "warmup_minutes_p90": Column(int, Check.in_range(0, 360)),   # > 6 h to setpoint is a BMS fault
        "dock_heat_on_minute": Column(int, Check.in_range(0, MINUTES_PER_DAY)),
        "dock_heat_off_minute": Column(int, Check.in_range(0, MINUTES_PER_DAY)),
        "dock_heat_kw": Column(float, Check.in_range(0, 200)),
        "setpoint_f": Column(int, Check.in_range(55, 85)),
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
    checks=[
        Check(lambda df: df["hvac_on_minute"] < df["hvac_off_minute"],
              error="hvac_on_minute must be before hvac_off_minute (same-day schedule)"),
        Check(lambda df: df["dock_heat_on_minute"] <= df["dock_heat_off_minute"],
              error="dock heat window must not be reversed"),
    ],
)


ENERGY_READING_SCHEMA = DataFrameSchema(
    {
        "store_id": Column(str, Check.str_matches(r"^S\d{3}$")),
        "ts_utc": Column("datetime64[ns, UTC]"),
        "hvac_kwh": Column(float, Check.in_range(0, 2000)),
        "total_kwh": Column(float, Check.in_range(0, 5000)),
        "outdoor_temp_f": Column(float, Check.in_range(-60, 130)),
        "source_system": Column(str),
        "schema_version": Column(str),
    },
    strict=True,
    coerce=True,
    checks=[
        Check(lambda df: df["hvac_kwh"] <= df["total_kwh"] + 1e-6,
              error="hvac_kwh cannot exceed total_kwh"),
    ],
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
