"""Forecast service: reads history from DB, fits baseline, persists results.

Everything deterministic given the same seed'd data and clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.forecasting.baseline import (
    MODEL_NAME,
    MODEL_VERSION,
    EvalMetrics,
    evaluate,
    fit_predict,
    time_split,
)


FORECAST_KINDS = ("labor_minutes", "net_sales_cents", "visitors")


def _load_hourly(engine: Engine, kind: str, from_utc: datetime, to_utc: datetime) -> pd.DataFrame:
    assert kind in FORECAST_KINDS
    if kind == "labor_minutes":
        sql = text(
            "SELECT store_id, ts_utc, labor_minutes AS val "
            "FROM labor_hours WHERE ts_utc>=:fr AND ts_utc<:to"
        )
    elif kind == "visitors":
        # Footfall forecast: drives the energy (HVAC) check's occupied hours.
        sql = text(
            "SELECT store_id, ts_utc, visitors AS val "
            "FROM traffic_count WHERE ts_utc>=:fr AND ts_utc<:to"
        )
    else:
        sql = text(
            "SELECT store_id, ts_utc, net_sales_cents AS val "
            "FROM sales_txn WHERE ts_utc>=:fr AND ts_utc<:to"
        )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={
            "fr": from_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "to": to_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
        })
    if not df.empty:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


def run_forecast(
    engine: Engine,
    kind: str,
    train_from: datetime,
    train_to: datetime,
    horizon_from: datetime,
    horizon_to: datetime,
) -> dict:
    """Fit on [train_from, train_to), predict hourly over [horizon_from, horizon_to).

    Writes a ForecastRun + per-point Forecast rows. Returns run summary.
    """
    df = _load_hourly(engine, kind, train_from, train_to)
    df = df.rename(columns={"val": kind})

    # Build horizon timestamps at hourly grain
    horizon: list[datetime] = []
    t = horizon_from
    while t < horizon_to:
        horizon.append(t)
        t += timedelta(hours=1)

    points = fit_predict(df, target_col=kind, horizon_hours=horizon)

    # Simple time-split WAPE on in-sample history (last 20% held out)
    if not df.empty:
        cutoff = df["ts_utc"].quantile(0.8)
        train, test = time_split(df, pd.Timestamp(cutoff).to_pydatetime())
        test_points = fit_predict(train, kind, test["ts_utc"].tolist())
        pred_map = {(p.store_id, p.ts_utc): p.predicted for p in test_points}
        preds, actuals = [], []
        for _, row in test.iterrows():
            key = (row["store_id"], row["ts_utc"].to_pydatetime())
            if key in pred_map:
                preds.append(pred_map[key])
                actuals.append(float(row[kind]))
        em = evaluate(preds, actuals)
    else:
        em = EvalMetrics(wape=None, mae=None, rmse=None, n=0)

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO forecast_run (run_id, model_name, model_version, "
                "created_at_utc, wape, mae, rmse, train_window_from, train_window_to, notes) "
                "VALUES (:run_id, :mn, :mv, :cr, :w, :ma, :rm, :tf, :tt, :nt)"
            ),
            {
                "run_id": run_id, "mn": MODEL_NAME, "mv": MODEL_VERSION,
                "cr": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "w": em.wape, "ma": em.mae, "rm": em.rmse,
                "tf": train_from.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "tt": train_to.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "nt": f"kind={kind} n_eval={em.n}",
            },
        )
        if points:
            conn.execute(
                text(
                    "INSERT INTO forecast (run_id, store_id, ts_utc, kind, predicted, lower_80, upper_80) "
                    "VALUES (:run_id, :sid, :ts, :kind, :p, :lo, :hi)"
                ),
                [
                    {
                        "run_id": run_id, "sid": p.store_id,
                        "ts": p.ts_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "kind": kind, "p": p.predicted, "lo": p.lower_80, "hi": p.upper_80,
                    } for p in points
                ],
            )
    return {
        "run_id": run_id,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "wape": em.wape,
        "mae": em.mae,
        "rmse": em.rmse,
        "n_points": len(points),
    }


def get_forecast(engine: Engine, store_id: str, from_utc: datetime, to_utc: datetime,
                 kind: str = "labor_minutes") -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT f.ts_utc, f.predicted, f.lower_80, f.upper_80, f.run_id, "
                "       r.model_name, r.model_version, r.wape "
                "FROM forecast f JOIN forecast_run r ON r.run_id=f.run_id "
                "WHERE f.store_id=:sid AND f.kind=:k AND f.ts_utc>=:fr AND f.ts_utc<:to "
                "ORDER BY f.ts_utc"
            ),
            {
                "sid": store_id, "k": kind,
                "fr": from_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "to": to_utc.strftime("%Y-%m-%d %H:%M:%S.%f"),
            },
        ).mappings().all()
    return [dict(r) for r in rows]
