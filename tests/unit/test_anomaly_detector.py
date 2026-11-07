from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load
from packages.anomaly_detection.detector import (
    detect_sales_anomalies,
    list_anomalies,
    persist_findings,
)


def _mk():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    stores = pd.DataFrame([{"store_id":"S001","name":"T","region":"W","timezone":"America/Los_Angeles"}])
    validate_and_load(eng, stores, "store")
    return eng


def test_flags_outlier_hour():
    eng = _mk()
    # 4 weeks of Mondays at 10:00 with $100 sales, then one Monday with $1000.
    rows = []
    start = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # Monday
    base_cents = 100_00
    for w in range(4):
        ts = start + timedelta(days=7*w)
        rows.append({"store_id":"S001","ts_utc":ts,"net_sales_cents":base_cents,
                     "transactions":5,"units":5,"currency":"USD",
                     "source_system":"t","schema_version":"1.0.0"})
    outlier_ts = start + timedelta(days=28)
    rows.append({"store_id":"S001","ts_utc":outlier_ts,"net_sales_cents":1000_00,
                 "transactions":30,"units":35,"currency":"USD",
                 "source_system":"t","schema_version":"1.0.0"})
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(eng, df, "sales_txn")

    findings = detect_sales_anomalies(eng, start, outlier_ts + timedelta(hours=1))
    assert any(abs(f.z_score) >= 2.0 for f in findings)
    n = persist_findings(eng, findings)
    assert n == len(findings)
    rows = list_anomalies(eng)
    assert any(r["severity"] in ("medium", "high") for r in rows)


def test_no_history_no_flags():
    eng = _mk()
    findings = detect_sales_anomalies(eng, datetime(2026,1,1,tzinfo=timezone.utc),
                                     datetime(2026,1,2,tzinfo=timezone.utc))
    assert findings == []
