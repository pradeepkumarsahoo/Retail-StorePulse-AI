"""Run the labor-minute workforce forecast for the next N days of horizon.

Trains on the first 80% of loaded history, predicts hourly for the remaining
20% so there are forecasts to compare against (and seed the anomaly detector).

Usage:
    python -m scripts.run_forecast --kind labor_minutes --horizon-days 14
    python -m scripts.run_forecast --kind visitors --horizon-days 14   # feeds the energy check
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from infrastructure.db.engine import get_engine
from packages.forecasting.service import run_forecast


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kind", choices=["labor_minutes", "net_sales_cents", "visitors"],
                   default="labor_minutes")
    p.add_argument("--horizon-days", type=int, default=14)
    args = p.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MIN(ts_utc) AS mn, MAX(ts_utc) AS mx FROM sales_txn")).one()
    if not row.mn or not row.mx:
        raise SystemExit("No sales data loaded. Run `python -m scripts.load_db` first.")
    mn = datetime.fromisoformat(str(row.mn))
    mx = datetime.fromisoformat(str(row.mx))
    if mn.tzinfo is None:
        mn = mn.replace(tzinfo=timezone.utc)
    if mx.tzinfo is None:
        mx = mx.replace(tzinfo=timezone.utc)

    span = mx - mn
    train_to = mn + timedelta(seconds=int(span.total_seconds() * 0.8))
    horizon_from = train_to
    horizon_to = horizon_from + timedelta(days=args.horizon_days)

    summary = run_forecast(
        engine=engine,
        kind=args.kind,
        train_from=mn,
        train_to=train_to,
        horizon_from=horizon_from,
        horizon_to=horizon_to,
    )
    print(
        f"forecast run_id={summary['run_id']} model={summary['model_name']} v{summary['model_version']} "
        f"wape={summary['wape']} mae={summary['mae']} rmse={summary['rmse']} n={summary['n_points']}"
    )


if __name__ == "__main__":
    main()
