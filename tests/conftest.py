"""Shared pytest fixtures: in-memory SQLite engine seeded with a small, deterministic dataset."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy.engine import Engine

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load
from services.labor_rate_mcp.server import seed_rates


ADMIN_TOKEN = "demo-admin"
WEST_TOKEN = "demo-regional-west"
S001_TOKEN = "demo-store-s001"
REVIEWER_TOKEN = "demo-reviewer"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
def seed_window() -> tuple[datetime, datetime]:
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    return start, end


def _seed_dataset(engine: Engine, hours: list[datetime]) -> None:
    stores = pd.DataFrame(
        [
            {"store_id": "S001", "name": "Market St", "region": "West",
             "timezone": "America/Los_Angeles"},
            {"store_id": "S002", "name": "Loop Center", "region": "Midwest",
             "timezone": "America/Chicago"},
        ]
    )
    validate_and_load(engine, stores, "store")

    sales = pd.DataFrame([
        {"store_id": "S001", "ts_utc": hours[0], "net_sales_cents": 20000, "transactions": 5,
         "units": 7, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[1], "net_sales_cents": 35000, "transactions": 8,
         "units": 12, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[2], "net_sales_cents": 15000, "transactions": 4,
         "units": 5, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S002", "ts_utc": hours[0], "net_sales_cents": 50000, "transactions": 10,
         "units": 15, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
    ])
    sales["ts_utc"] = pd.to_datetime(sales["ts_utc"], utc=True)
    validate_and_load(engine, sales, "sales_txn")

    labor = pd.DataFrame([
        {"store_id": "S001", "ts_utc": hours[0], "labor_minutes": 60,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[1], "labor_minutes": 120,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[2], "labor_minutes": 90,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S002", "ts_utc": hours[0], "labor_minutes": 120,
         "source_system": "test", "schema_version": "1.0.0"},
    ])
    labor["ts_utc"] = pd.to_datetime(labor["ts_utc"], utc=True)
    validate_and_load(engine, labor, "labor_hours")

    traffic = pd.DataFrame([
        {"store_id": "S001", "ts_utc": hours[0], "visitors": 40,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[1], "visitors": 60,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[2], "visitors": 30,
         "source_system": "test", "schema_version": "1.0.0"},
    ])
    traffic["ts_utc"] = pd.to_datetime(traffic["ts_utc"], utc=True)
    validate_and_load(engine, traffic, "traffic_count")

    # TMS mirror: one on-time truck for S001 on day 1 (balanced against the
    # 270 scheduled minutes), one S001 truck planned day 2 that rolls to day 3
    # (the idle-labor case), and one S002 truck (scope tests).
    day1 = hours[0].replace(hour=7)
    shipments = pd.DataFrame([
        {"shipment_id": "SHP-000001", "store_id": "S001", "dc_id": "DC01", "truck_id": "TRK-1001",
         "carrier": "NorthStar Freight", "planned_arrival_utc": day1, "eta_utc": day1,
         "status": "scheduled", "delay_minutes": 0, "pallets": 3, "cases": 120,
         "unload_minutes_est": 75, "source_system": "test_tms", "schema_version": "1.0.0"},
        {"shipment_id": "SHP-000002", "store_id": "S001", "dc_id": "DC01", "truck_id": "TRK-1002",
         "carrier": "NorthStar Freight", "planned_arrival_utc": day1 + timedelta(days=1),
         "eta_utc": day1 + timedelta(days=2), "status": "delayed", "delay_minutes": 1440,
         "pallets": 10, "cases": 420, "unload_minutes_est": 250,
         "source_system": "test_tms", "schema_version": "1.0.0"},
        {"shipment_id": "SHP-000003", "store_id": "S002", "dc_id": "DC02", "truck_id": "TRK-2001",
         "carrier": "Lakeside Logistics", "planned_arrival_utc": day1, "eta_utc": day1,
         "status": "scheduled", "delay_minutes": 0, "pallets": 6, "cases": 250,
         "unload_minutes_est": 150, "source_system": "test_tms", "schema_version": "1.0.0"},
    ])
    for col in ("planned_arrival_utc", "eta_utc"):
        shipments[col] = pd.to_datetime(shipments[col], utc=True)
    validate_and_load(engine, shipments, "inbound_shipment")

    # BMS mirror. S001 runs 05:00–22:00 (75-min warm-up, dock heated 06:30–09:00);
    # S002 runs a schedule that already matches a 09:00–21:00 store.
    hvac = pd.DataFrame([
        {"store_id": "S001", "bms_id": "BMS-S001", "hvac_on_minute": 300, "hvac_off_minute": 1320,
         "warmup_minutes_p90": 75, "dock_heat_on_minute": 390, "dock_heat_off_minute": 540,
         "dock_heat_kw": 8.0, "setpoint_f": 72, "source_system": "test_bms", "schema_version": "1.0.0"},
        {"store_id": "S002", "bms_id": "BMS-S002", "hvac_on_minute": 450, "hvac_off_minute": 1290,
         "warmup_minutes_p90": 60, "dock_heat_on_minute": 0, "dock_heat_off_minute": 0,
         "dock_heat_kw": 0.0, "setpoint_f": 70, "source_system": "test_bms", "schema_version": "1.0.0"},
    ])
    validate_and_load(engine, hvac, "hvac_schedule")
    # Day-1 meter for S001: flat 30 kWh/h while the BMS runs (05–22), 0.3 standby otherwise.
    day0 = hours[0].replace(hour=0)
    readings = pd.DataFrame([
        {"store_id": "S001", "ts_utc": day0 + timedelta(hours=h),
         "hvac_kwh": 30.0 if 5 <= h < 22 else 0.3,
         "total_kwh": (30.0 if 5 <= h < 22 else 0.3) + 10.0,
         "outdoor_temp_f": 40.0, "source_system": "test_bms", "schema_version": "1.0.0"}
        for h in range(24)
    ])
    readings["ts_utc"] = pd.to_datetime(readings["ts_utc"], utc=True)
    validate_and_load(engine, readings, "energy_reading")

    seed_rates(engine)


@pytest.fixture
def engine(seed_window) -> Engine:
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    start, _ = seed_window
    hours = [start + timedelta(hours=i) for i in range(3)]
    _seed_dataset(eng, hours)
    return eng


@pytest.fixture
def api_client(engine, monkeypatch):
    from fastapi.testclient import TestClient

    # Reset metrics counters per test to keep assertions clean.
    from packages.observability import metrics as _m
    _m.reset()

    from apps.api import dependencies
    from apps.api import main as api_main

    dependencies.set_engine(engine)
    return TestClient(api_main.app, headers=ADMIN_HEADERS)
