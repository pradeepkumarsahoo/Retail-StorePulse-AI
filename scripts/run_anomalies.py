"""Run anomaly detection across the last N days and persist findings.

Usage:
    python -m scripts.run_anomalies --days 30
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from infrastructure.db.engine import get_engine
from packages.anomaly_detection.detector import (
    detect_labor_coverage_gaps,
    detect_sales_anomalies,
    persist_findings,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MAX(ts_utc) AS mx FROM sales_txn")).one()
    if not row.mx:
        raise SystemExit("No sales data loaded. Run `python -m scripts.load_db` first.")
    mx = datetime.fromisoformat(str(row.mx))
    if mx.tzinfo is None:
        mx = mx.replace(tzinfo=timezone.utc)
    window_to = mx + timedelta(hours=1)
    window_from = window_to - timedelta(days=args.days)

    sales = detect_sales_anomalies(engine, window_from, window_to)
    labor = detect_labor_coverage_gaps(engine, window_from, window_to)
    n_sales = persist_findings(engine, sales)
    n_labor = persist_findings(engine, labor)
    print(f"persisted anomalies: sales={n_sales} labor_coverage={n_labor}")


if __name__ == "__main__":
    main()
