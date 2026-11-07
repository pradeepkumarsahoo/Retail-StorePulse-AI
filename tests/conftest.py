"""Shared pytest fixtures: in-memory SQLite engine seeded with a tiny dataset."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load


@pytest.fixture
def seed_window() -> tuple[datetime, datetime]:
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # 3 hourly rows
    return start, end


@pytest.fixture
def engine(seed_window) -> Engine:
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    start, end = seed_window

    stores = pd.DataFrame(
        [{"store_id": "S001", "name": "Test Store", "region": "West",
          "timezone": "America/Los_Angeles"}]
    )
    validate_and_load(eng, stores, "store")

    hours = [start + timedelta(hours=i) for i in range(3)]
    sales = pd.DataFrame([
        {"store_id": "S001", "ts_utc": hours[0], "net_sales_cents": 20000, "transactions": 5,
         "units": 7, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[1], "net_sales_cents": 35000, "transactions": 8,
         "units": 12, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[2], "net_sales_cents": 15000, "transactions": 4,
         "units": 5, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
    ])
    sales["ts_utc"] = pd.to_datetime(sales["ts_utc"], utc=True)
    validate_and_load(eng, sales, "sales_txn")

    # 60 + 120 + 90 = 270 minutes = 4.5 hours. Sales total $700 → SPLH = 155.5556
    labor = pd.DataFrame([
        {"store_id": "S001", "ts_utc": hours[0], "labor_minutes": 60,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[1], "labor_minutes": 120,
         "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": hours[2], "labor_minutes": 90,
         "source_system": "test", "schema_version": "1.0.0"},
    ])
    labor["ts_utc"] = pd.to_datetime(labor["ts_utc"], utc=True)
    validate_and_load(eng, labor, "labor_hours")

    return eng


@pytest.fixture
def api_client(engine, monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api import main as api_main

    # Point the FastAPI singleton at the in-memory engine.
    monkeypatch.setattr(api_main, "_engine", engine)
    return TestClient(api_main.app)
