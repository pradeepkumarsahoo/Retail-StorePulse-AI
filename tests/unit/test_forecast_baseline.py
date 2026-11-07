from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from packages.forecasting.baseline import evaluate, fit_predict, time_split


def _mk_history(days: int = 14) -> pd.DataFrame:
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for d in range(days):
        for h in range(9, 21):
            ts = start + timedelta(days=d, hours=h)
            # Simple weekly seasonality
            val = 60 + 20 * ((ts.weekday() % 3) + 1) + 5 * h
            rows.append({"store_id": "S001", "ts_utc": ts, "labor_minutes": val})
    return pd.DataFrame(rows)


def test_fit_predict_reproduces_dow_hour_level():
    history = _mk_history(14)
    # Predict for a Monday 10:00 that is clearly in-dow-pattern
    target = datetime(2026, 1, 19, 10, 0, tzinfo=timezone.utc)  # Monday
    points = fit_predict(history, "labor_minutes", [target])
    assert len(points) == 1
    assert points[0].predicted > 0
    # 80% interval must contain the predicted value
    assert points[0].lower_80 <= points[0].predicted <= points[0].upper_80


def test_evaluate_handles_empty_input():
    em = evaluate([], [])
    assert em.wape is None and em.mae is None and em.rmse is None and em.n == 0


def test_evaluate_wape_mae_rmse():
    em = evaluate([10, 20, 30], [12, 18, 33])
    assert em.n == 3
    # |10-12|+|20-18|+|30-33| = 2+2+3 = 7. denom=12+18+33=63. wape = 7/63
    assert abs(em.wape - 7/63) < 1e-6
    assert abs(em.mae - 7/3) < 1e-6


def test_time_split_never_random():
    history = _mk_history(10)
    cutoff = datetime(2026, 1, 6, tzinfo=timezone.utc)
    train, test = time_split(history, cutoff)
    assert len(train) > 0 and len(test) > 0
    assert train["ts_utc"].max() < pd.Timestamp(cutoff).tz_convert("UTC")
    assert test["ts_utc"].min() >= pd.Timestamp(cutoff).tz_convert("UTC")
