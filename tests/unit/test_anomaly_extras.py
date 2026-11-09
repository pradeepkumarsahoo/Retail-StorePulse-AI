"""Unit tests for anomaly detector paths not covered by test_anomaly_detector.

Specifically: labor coverage gap detection, list filters, and get_anomaly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load
from packages.anomaly_detection.detector import (
    AnomalyFinding,
    detect_labor_coverage_gaps,
    get_anomaly,
    list_anomalies,
    persist_findings,
)


def _mk():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    stores = pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "West", "timezone": "UTC"},
        {"store_id": "S002", "name": "U", "region": "Midwest", "timezone": "UTC"},
    ])
    validate_and_load(eng, stores, "store")
    return eng


def _write_forecast(eng, run_id: str, store_id: str, ts: datetime, predicted: float) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO forecast_run (run_id, model_name, model_version, "
                "created_at_utc, train_window_from, train_window_to) VALUES "
                "(:r, 'm', '1', '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'2026-01-01 01:00:00')"
            ),
            {"r": run_id},
        )
        conn.execute(
            text(
                "INSERT INTO forecast (run_id, store_id, ts_utc, kind, predicted, "
                "lower_80, upper_80) VALUES (:r, :sid, :ts, 'labor_minutes', :p, 0, :p)"
            ),
            {"r": run_id, "sid": store_id, "ts": ts.strftime("%Y-%m-%d %H:%M:%S.%f"),
             "p": predicted},
        )


def test_labor_coverage_gap_flags_large_overstaff():
    eng = _mk()
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    # Actual labor = 180 min; predicted = 60 min → +200% gap.
    df = pd.DataFrame([{
        "store_id": "S001", "ts_utc": ts, "labor_minutes": 180,
        "source_system": "t", "schema_version": "1.0.0",
    }])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    _write_forecast(eng, "RUN1", "S001", ts, predicted=60.0)

    findings = detect_labor_coverage_gaps(
        eng, ts, ts + timedelta(hours=1)
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "labor_coverage_gap"
    assert f.severity == "high"  # |gap| >= 0.5
    assert f.baseline == 60.0
    assert f.observed == 180.0


def test_labor_coverage_gap_ignores_small_gap():
    eng = _mk()
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    df = pd.DataFrame([{
        "store_id": "S001", "ts_utc": ts, "labor_minutes": 65,
        "source_system": "t", "schema_version": "1.0.0",
    }])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    _write_forecast(eng, "R", "S001", ts, predicted=60.0)
    # 5/60 ≈ 8% gap, below 25% threshold.
    assert detect_labor_coverage_gaps(eng, ts, ts + timedelta(hours=1)) == []


def test_labor_coverage_gap_ignores_zero_predicted():
    eng = _mk()
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    df = pd.DataFrame([{
        "store_id": "S001", "ts_utc": ts, "labor_minutes": 90,
        "source_system": "t", "schema_version": "1.0.0",
    }])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    _write_forecast(eng, "R", "S001", ts, predicted=0.0)
    assert detect_labor_coverage_gaps(eng, ts, ts + timedelta(hours=1)) == []


def test_labor_coverage_gap_empty_without_forecast():
    eng = _mk()
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    df = pd.DataFrame([{
        "store_id": "S001", "ts_utc": ts, "labor_minutes": 180,
        "source_system": "t", "schema_version": "1.0.0",
    }])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "labor_hours")
    assert detect_labor_coverage_gaps(eng, ts, ts + timedelta(hours=1)) == []


def _mk_finding(store_id: str = "S001", severity: str = "medium") -> AnomalyFinding:
    return AnomalyFinding(
        anomaly_id=str(uuid.uuid4()),
        store_id=store_id,
        ts_utc=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        kind="net_sales_hourly",
        severity=severity,
        observed=200.0, baseline=100.0, deviation=100.0, z_score=2.5,
        evidence={"detector": "robust_zscore_v1"},
    )


def test_persist_findings_zero_on_empty():
    eng = _mk()
    assert persist_findings(eng, []) == 0


def test_list_anomalies_filters_by_store_and_status():
    eng = _mk()
    persist_findings(eng, [_mk_finding("S001"), _mk_finding("S002")])
    # Status default is "open".
    by_store = list_anomalies(eng, store_id="S001")
    assert len(by_store) == 1 and by_store[0]["store_id"] == "S001"
    # A different status returns nothing.
    assert list_anomalies(eng, status="closed") == []


def test_list_anomalies_decodes_evidence_json():
    eng = _mk()
    persist_findings(eng, [_mk_finding("S001")])
    rows = list_anomalies(eng)
    assert rows[0]["evidence"]["detector"] == "robust_zscore_v1"
    assert "evidence_json" not in rows[0]


def test_get_anomaly_returns_decoded_row():
    eng = _mk()
    f = _mk_finding("S001")
    persist_findings(eng, [f])
    row = get_anomaly(eng, f.anomaly_id)
    assert row is not None
    assert row["anomaly_id"] == f.anomaly_id
    assert row["evidence"]["detector"] == "robust_zscore_v1"


def test_get_anomaly_missing_returns_none():
    eng = _mk()
    assert get_anomaly(eng, "nope") is None
