"""Anomaly detection combining deterministic thresholds + statistical z-scores.

Each flagged anomaly carries baseline, observed, deviation, z-score, and the
exact evidence used to flag it — never a bare "something looks off."
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


DETECTOR_NAME = "robust_zscore_v1"
DETECTOR_VERSION = "1.0.0"

# Thresholds (business-overridable in later milestones)
SALES_ZSCORE_HIGH = 3.0
SALES_ZSCORE_MED = 2.0
LABOR_COVERAGE_GAP_PCT = 0.25   # labor supply more than 25% under/over forecast
MIN_HISTORY_HOURS = 24          # don't flag before we have a baseline


def _severity(zscore: float) -> str:
    az = abs(zscore)
    if az >= SALES_ZSCORE_HIGH:
        return "high"
    if az >= SALES_ZSCORE_MED:
        return "medium"
    return "low"


@dataclass(frozen=True)
class AnomalyFinding:
    anomaly_id: str
    store_id: str
    ts_utc: datetime
    kind: str
    severity: str
    observed: float
    baseline: float
    deviation: float
    z_score: float
    evidence: dict


def detect_sales_anomalies(engine: Engine, window_from: datetime, window_to: datetime) -> list[AnomalyFinding]:
    """Flag hours where observed net sales deviate robustly from the (dow, hour) baseline."""
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT store_id, ts_utc, net_sales_cents FROM sales_txn "
                "WHERE ts_utc>=:fr AND ts_utc<:to"
            ),
            conn,
            params={
                "fr": window_from.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "to": window_to.strftime("%Y-%m-%d %H:%M:%S.%f"),
            },
        )
    if df.empty:
        return []
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["dow"] = df["ts_utc"].dt.dayofweek
    df["hour"] = df["ts_utc"].dt.hour
    df["sales_usd"] = df["net_sales_cents"] / 100.0

    findings: list[AnomalyFinding] = []
    for (store_id, dow, hour), group in df.groupby(["store_id", "dow", "hour"]):
        if len(group) < max(3, MIN_HISTORY_HOURS // 24):
            continue
        median = float(group["sales_usd"].median())
        mad = float((group["sales_usd"] - median).abs().median())
        # Robust z: 1.4826 scales MAD to sigma for near-normal data.
        sigma = 1.4826 * mad if mad > 0 else float(group["sales_usd"].std(ddof=0) or 0.0)
        if sigma <= 0:
            continue
        for _, row in group.iterrows():
            obs = float(row["sales_usd"])
            z = (obs - median) / sigma
            if abs(z) >= SALES_ZSCORE_MED:
                dev = obs - median
                findings.append(AnomalyFinding(
                    anomaly_id=str(uuid.uuid4()),
                    store_id=store_id,
                    ts_utc=row["ts_utc"].to_pydatetime(),
                    kind="net_sales_hourly",
                    severity=_severity(z),
                    observed=round(obs, 2),
                    baseline=round(median, 2),
                    deviation=round(dev, 2),
                    z_score=round(z, 3),
                    evidence={
                        "formula": "robust_zscore = (obs - median)/(1.4826 * MAD)",
                        "dow": int(dow),
                        "hour": int(hour),
                        "sample_size": int(len(group)),
                        "sigma_estimate": round(sigma, 4),
                        "detector": DETECTOR_NAME,
                        "detector_version": DETECTOR_VERSION,
                    },
                ))
    return findings


def detect_labor_coverage_gaps(
    engine: Engine, window_from: datetime, window_to: datetime,
) -> list[AnomalyFinding]:
    """Flag hours where scheduled labor diverges materially from the latest forecast."""
    with engine.connect() as conn:
        actual = pd.read_sql(
            text(
                "SELECT store_id, ts_utc, labor_minutes FROM labor_hours "
                "WHERE ts_utc>=:fr AND ts_utc<:to"
            ),
            conn,
            params={
                "fr": window_from.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "to": window_to.strftime("%Y-%m-%d %H:%M:%S.%f"),
            },
        )
        forecasts = pd.read_sql(
            text(
                "SELECT store_id, ts_utc, predicted FROM forecast "
                "WHERE kind='labor_minutes' AND ts_utc>=:fr AND ts_utc<:to"
            ),
            conn,
            params={
                "fr": window_from.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "to": window_to.strftime("%Y-%m-%d %H:%M:%S.%f"),
            },
        )
    if actual.empty or forecasts.empty:
        return []
    actual["ts_utc"] = pd.to_datetime(actual["ts_utc"], utc=True)
    forecasts["ts_utc"] = pd.to_datetime(forecasts["ts_utc"], utc=True)
    merged = actual.merge(forecasts, on=["store_id", "ts_utc"], how="inner")
    findings: list[AnomalyFinding] = []
    for _, row in merged.iterrows():
        obs = float(row["labor_minutes"])
        pred = float(row["predicted"])
        if pred <= 0:
            continue
        gap = (obs - pred) / pred
        if abs(gap) >= LABOR_COVERAGE_GAP_PCT:
            findings.append(AnomalyFinding(
                anomaly_id=str(uuid.uuid4()),
                store_id=row["store_id"],
                ts_utc=row["ts_utc"].to_pydatetime(),
                kind="labor_coverage_gap",
                severity="high" if abs(gap) >= 0.5 else "medium",
                observed=round(obs, 2),
                baseline=round(pred, 2),
                deviation=round(obs - pred, 2),
                z_score=round(gap, 3),
                evidence={
                    "formula": "(labor_minutes - predicted) / predicted",
                    "threshold": LABOR_COVERAGE_GAP_PCT,
                    "detector": DETECTOR_NAME,
                    "detector_version": DETECTOR_VERSION,
                },
            ))
    return findings


def persist_findings(engine: Engine, findings: list[AnomalyFinding]) -> int:
    if not findings:
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO anomaly (anomaly_id, store_id, ts_utc, kind, severity, observed, "
                "baseline, deviation, z_score, detector_name, detector_version, evidence_json, "
                "status, created_at_utc) VALUES "
                "(:aid, :sid, :ts, :kind, :sev, :obs, :bl, :dev, :z, :dn, :dv, :ej, 'open', :now)"
            ),
            [
                {
                    "aid": f.anomaly_id, "sid": f.store_id,
                    "ts": f.ts_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "kind": f.kind, "sev": f.severity, "obs": f.observed, "bl": f.baseline,
                    "dev": f.deviation, "z": f.z_score,
                    "dn": DETECTOR_NAME, "dv": DETECTOR_VERSION,
                    "ej": json.dumps(f.evidence), "now": now,
                } for f in findings
            ],
        )
    return len(findings)


def list_anomalies(
    engine: Engine, store_id: str | None = None, status: str | None = "open", limit: int = 200,
) -> list[dict]:
    sql = (
        "SELECT anomaly_id, store_id, ts_utc, kind, severity, observed, baseline, "
        "deviation, z_score, detector_name, detector_version, evidence_json, status, created_at_utc "
        "FROM anomaly WHERE 1=1"
    )
    params: dict = {}
    if store_id:
        sql += " AND store_id = :sid"
        params["sid"] = store_id
    if status:
        sql += " AND status = :st"
        params["st"] = status
    sql += " ORDER BY ts_utc DESC LIMIT :lim"
    params["lim"] = limit
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json"))
        out.append(d)
    return out


def get_anomaly(engine: Engine, anomaly_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT anomaly_id, store_id, ts_utc, kind, severity, observed, baseline, "
                "deviation, z_score, detector_name, detector_version, evidence_json, status, created_at_utc "
                "FROM anomaly WHERE anomaly_id=:aid"
            ),
            {"aid": anomaly_id},
        ).mappings().one_or_none()
    if not row:
        return None
    d = dict(row)
    d["evidence"] = json.loads(d.pop("evidence_json"))
    return d
