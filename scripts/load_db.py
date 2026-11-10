"""Load synthetic CSVs into the database via Pandera-validated ingest.

Datasets: store, sales_txn, labor_hours, traffic_count, inbound_shipment (TMS feed).
Also seeds metric_catalog (all v1 metrics) and labor_rate (from MCP server).

Usage:
    python -m scripts.load_db --in data/synthetic
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load
from packages.analytics.kpi_service import METRICS
from services.labor_rate_mcp.server import seed_rates


DATASETS = ["store", "sales_txn", "labor_hours", "traffic_count", "inbound_shipment"]
TIMESTAMP_COLUMNS = ("ts_utc", "planned_arrival_utc", "eta_utc")


def _seed_metric_catalog(engine) -> int:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM metric_catalog"))
        conn.execute(
            text(
                "INSERT INTO metric_catalog "
                "(metric_name, metric_version, unit, formula, source_tables, owner, description) "
                "VALUES (:n, :v, :u, :f, :st, :o, :d)"
            ),
            [
                {
                    "n": name, "v": m["version"], "u": m["unit"], "f": m["formula"],
                    "st": ",".join(m["source_tables"]), "o": m["owner"], "d": m["description"],
                } for name, m in METRICS.items()
            ],
        )
    return len(METRICS)


def _reset_tables(engine) -> None:
    with engine.begin() as conn:
        # Child tables first.
        for table in ["approval_event", "recommendation", "tool_call", "ai_run",
                      "anomaly", "forecast", "forecast_run", "audit_event",
                      "quarantine", "sales_txn", "labor_hours", "traffic_count",
                      "inbound_shipment", "labor_rate", "metric_catalog", "store"]:
            conn.execute(text(f"DELETE FROM {table}"))


def main() -> None:
    p = argparse.ArgumentParser(description="StorePulse-AI DB loader")
    p.add_argument("--in", dest="input_dir", default="data/synthetic")
    args = p.parse_args()

    engine = get_engine()
    init_schema(engine)
    _reset_tables(engine)

    input_dir = Path(args.input_dir)
    for dataset in DATASETS:
        path = input_dir / f"{dataset}.csv"
        if not path.exists():
            raise SystemExit(f"Missing synthetic file: {path}")
        df = pd.read_csv(path)
        for col in TIMESTAMP_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True)
        result = validate_and_load(engine, df, dataset)
        print(
            f"{dataset:14s}  loaded={result['loaded']:>7,}  "
            f"quarantined={result['quarantined']:>5}  reason={result['reason']}"
        )

    seeded = _seed_metric_catalog(engine)
    print(f"metric_catalog seeded with {seeded} metrics")
    n_rates = seed_rates(engine)
    print(f"labor_rate seeded with {n_rates} synthetic reference rates")


if __name__ == "__main__":
    main()
