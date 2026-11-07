"""Pandera schema rules must fail loud, not coerce silently."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load


def _mk_engine():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    # Minimal store so FKs resolve
    stores = pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "West", "timezone": "America/Los_Angeles"},
    ])
    validate_and_load(eng, stores, "store")
    return eng


def test_negative_sales_quarantined():
    eng = _mk_engine()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": -100, "transactions": 1,
         "units": 1, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": 500, "transactions": 1,
         "units": 1, "currency": "USD", "source_system": "test", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "sales_txn")
    assert result["loaded"] == 1
    assert result["quarantined"] == 1
    with eng.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM quarantine")).scalar()
    assert n == 1


def test_bad_store_id_quarantined():
    eng = _mk_engine()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "BOGUS", "ts_utc": ts, "labor_minutes": 60,
         "source_system": "test", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "labor_hours")
    assert result["loaded"] == 0
    assert result["quarantined"] == 1


def test_wrong_currency_quarantined():
    eng = _mk_engine()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": 1000, "transactions": 2,
         "units": 2, "currency": "EUR", "source_system": "test", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "sales_txn")
    assert result["loaded"] == 0
    assert result["quarantined"] == 1
