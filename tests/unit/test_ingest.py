"""Unit tests for the ingest wrapper (validate → load → quarantine)."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load


def _mk():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    stores = pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "W", "timezone": "UTC"},
    ])
    validate_and_load(eng, stores, "store")
    return eng


def test_unknown_dataset_rejected():
    eng = _mk()
    df = pd.DataFrame([{"foo": "bar"}])
    with pytest.raises(ValueError):
        validate_and_load(eng, df, "bogus")


def test_all_clean_rows_loaded():
    eng = _mk()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": 100, "transactions": 1,
         "units": 1, "currency": "USD", "source_system": "t", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "sales_txn")
    assert result == {"loaded": 1, "quarantined": 0, "reason": None}


def test_mixed_batch_splits_into_loaded_and_quarantined():
    eng = _mk()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": 100, "transactions": 1,
         "units": 1, "currency": "USD", "source_system": "t", "schema_version": "1.0.0"},
        {"store_id": "BAD",  "ts_utc": ts, "net_sales_cents": 100, "transactions": 1,
         "units": 1, "currency": "USD", "source_system": "t", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "sales_txn")
    assert result["loaded"] == 1
    assert result["quarantined"] == 1
    assert result["reason"] == "pandera_failed"


def test_quarantine_row_includes_source_dataset():
    eng = _mk()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "BAD", "ts_utc": ts, "labor_minutes": 60,
         "source_system": "t", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT source_dataset, reason_code FROM quarantine ORDER BY id DESC LIMIT 1"
        )).one()
    assert row.source_dataset == "labor_hours"
    assert row.reason_code == "pandera_failed"


def test_transactions_zero_but_sales_nonzero_quarantined():
    eng = _mk()
    ts = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"store_id": "S001", "ts_utc": ts, "net_sales_cents": 100, "transactions": 0,
         "units": 1, "currency": "USD", "source_system": "t", "schema_version": "1.0.0"},
    ])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    result = validate_and_load(eng, df, "sales_txn")
    # The cross-column check means this entire DataFrame fails validation.
    assert result["loaded"] == 0
    assert result["quarantined"] >= 1
