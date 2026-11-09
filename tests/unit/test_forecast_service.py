"""Unit tests for the forecast service (DB load → fit → persist)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load
from packages.forecasting.service import get_forecast, run_forecast


def _mk_engine_with_history(days: int = 14) -> tuple:
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    stores = pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "West", "timezone": "UTC"},
    ])
    validate_and_load(eng, stores, "store")
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for d in range(days):
        for h in range(9, 18):
            ts = start + timedelta(days=d, hours=h)
            rows.append({
                "store_id": "S001", "ts_utc": ts,
                "labor_minutes": 60 + 10 * (ts.weekday() % 3) + 2 * h,
                "source_system": "t", "schema_version": "1.0.0",
            })
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    return eng, start


def test_run_forecast_writes_run_and_points():
    eng, start = _mk_engine_with_history(14)
    train_from = start
    train_to = start + timedelta(days=14)
    horizon_from = start + timedelta(days=15, hours=9)
    horizon_to = start + timedelta(days=15, hours=18)
    summary = run_forecast(eng, "labor_minutes", train_from, train_to,
                           horizon_from, horizon_to)
    assert summary["model_name"] == "seasonal_baseline_dow_hour_trimmed_mean"
    assert summary["n_points"] > 0
    with eng.connect() as conn:
        n_runs = conn.execute(text("SELECT COUNT(*) FROM forecast_run")).scalar()
        n_pts = conn.execute(
            text("SELECT COUNT(*) FROM forecast WHERE run_id=:r"),
            {"r": summary["run_id"]},
        ).scalar()
    assert n_runs == 1
    assert n_pts == summary["n_points"]


def test_run_forecast_empty_history_reports_zero_points():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    stores = pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "West", "timezone": "UTC"},
    ])
    validate_and_load(eng, stores, "store")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    summary = run_forecast(
        eng, "labor_minutes", start, start + timedelta(days=1),
        start + timedelta(days=1), start + timedelta(days=1, hours=3),
    )
    assert summary["n_points"] == 0
    assert summary["wape"] is None


def test_get_forecast_returns_points_in_window():
    eng, start = _mk_engine_with_history(14)
    train_to = start + timedelta(days=14)
    horizon_from = start + timedelta(days=15, hours=9)
    horizon_to = start + timedelta(days=15, hours=18)
    run_forecast(eng, "labor_minutes", start, train_to, horizon_from, horizon_to)

    pts = get_forecast(eng, "S001", horizon_from, horizon_to)
    assert pts  # non-empty
    assert all(horizon_from <= pd.Timestamp(p["ts_utc"]).to_pydatetime().replace(tzinfo=timezone.utc)
               < horizon_to for p in pts)
    # Each point exposes model metadata
    assert all(p["model_name"] for p in pts)
    assert all(p["run_id"] for p in pts)


def test_get_forecast_scoped_to_store_id():
    eng, start = _mk_engine_with_history(14)
    horizon_from = start + timedelta(days=15, hours=9)
    horizon_to = start + timedelta(days=15, hours=18)
    run_forecast(eng, "labor_minutes", start, start + timedelta(days=14),
                 horizon_from, horizon_to)
    # No history for S999 → zero points returned for it.
    assert get_forecast(eng, "S999", horizon_from, horizon_to) == []
