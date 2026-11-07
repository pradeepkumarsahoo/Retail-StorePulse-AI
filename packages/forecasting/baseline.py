"""Seasonal baseline forecaster: trimmed mean per (day-of-week, hour-of-day).

Deterministic, tiny, auditable. The point is to give downstream capabilities
(anomaly detection, agent evidence, coverage-gap view) a credible baseline
before any ML lands. A gradient-boosted candidate is reserved for M3+.

The forecast is on labor demand measured in minutes per hour; the same shape
works for sales when a different target column is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import pandas as pd


MODEL_NAME = "seasonal_baseline_dow_hour_trimmed_mean"
MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class ForecastPoint:
    store_id: str
    ts_utc: datetime
    predicted: float
    lower_80: float
    upper_80: float


@dataclass(frozen=True)
class EvalMetrics:
    wape: float | None
    mae: float | None
    rmse: float | None
    n: int


def _dow_hour(ts: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame({"dow": ts.dt.dayofweek, "hour": ts.dt.hour})
    return out


def fit_predict(
    history: pd.DataFrame,
    target_col: str,
    horizon_hours: Iterable[datetime],
    trim_pct: float = 0.1,
) -> list[ForecastPoint]:
    """Fit per-store (dow, hour) means (trimmed) on history, predict for horizon.

    history: columns ['store_id','ts_utc', target_col]. ts_utc is tz-aware UTC.
    """
    if history.empty:
        return []
    df = history.copy()
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["dow"] = df["ts_utc"].dt.dayofweek
    df["hour"] = df["ts_utc"].dt.hour

    # Trimmed mean per (store, dow, hour)
    def tmean(x: pd.Series) -> float:
        if len(x) < 3:
            return float(x.mean())
        lo = x.quantile(trim_pct)
        hi = x.quantile(1 - trim_pct)
        trimmed = x[(x >= lo) & (x <= hi)]
        return float(trimmed.mean()) if len(trimmed) else float(x.mean())

    grouped = df.groupby(["store_id", "dow", "hour"])[target_col]
    means = grouped.agg(tmean)
    # Residual standard deviation per (store, dow, hour) for prediction intervals
    stds = grouped.std(ddof=0).fillna(0.0)

    out: list[ForecastPoint] = []
    horizon_list = list(horizon_hours)
    # Iterate each store seen in history
    for store_id in df["store_id"].unique():
        for ts in horizon_list:
            ts_utc = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            key = (store_id, ts_utc.weekday(), ts_utc.hour)
            if key not in means.index:
                continue
            mu = float(means.loc[key])
            sigma = float(stds.loc[key]) if key in stds.index else 0.0
            out.append(ForecastPoint(
                store_id=store_id,
                ts_utc=ts_utc,
                predicted=round(mu, 4),
                # 80% interval: ~ ±1.282σ under near-normal residuals
                lower_80=round(max(mu - 1.282 * sigma, 0.0), 4),
                upper_80=round(mu + 1.282 * sigma, 4),
            ))
    return out


def evaluate(predicted: list[float], actual: list[float]) -> EvalMetrics:
    """WAPE / MAE / RMSE. WAPE guards against zero-actual division by requiring
    non-trivial actual sum.
    """
    if not predicted or not actual or len(predicted) != len(actual):
        return EvalMetrics(wape=None, mae=None, rmse=None, n=0)
    p = np.asarray(predicted, dtype=float)
    a = np.asarray(actual, dtype=float)
    err = p - a
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err * err)))
    denom = float(np.sum(np.abs(a)))
    wape = float(np.sum(np.abs(err)) / denom) if denom > 0 else None
    return EvalMetrics(wape=wape, mae=mae, rmse=rmse, n=len(predicted))


def time_split(df: pd.DataFrame, split_at: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-based train/test split. Never random-split time series."""
    ts = pd.to_datetime(df["ts_utc"], utc=True)
    at = pd.Timestamp(split_at).tz_convert("UTC") if pd.Timestamp(split_at).tzinfo else pd.Timestamp(split_at).tz_localize("UTC")
    return df.loc[ts < at].copy(), df.loc[ts >= at].copy()
