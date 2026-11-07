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
