"""Load synthetic CSVs into the database via Pandera-validated ingest.

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
from packages.analytics.kpi_service import (
    SPLH_FORMULA,
    SPLH_METRIC_NAME,
    SPLH_METRIC_VERSION,
)


DATASETS = ["store", "sales_txn", "labor_hours", "traffic_count"]


def _seed_metric_catalog(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM metric_catalog WHERE metric_name = :n AND metric_version = :v"
            ),
            {"n": SPLH_METRIC_NAME, "v": SPLH_METRIC_VERSION},
        )
        conn.execute(
            text(
                "INSERT INTO metric_catalog "
                "(metric_name, metric_version, unit, formula, source_tables, owner, description) "
                "VALUES (:n, :v, :u, :f, :st, :o, :d)"
            ),
            {
                "n": SPLH_METRIC_NAME,
                "v": SPLH_METRIC_VERSION,
                "u": "USD per labor hour",
                "f": SPLH_FORMULA,
                "st": "sales_txn,labor_hours",
                "o": "analytics-platform",
                "d": "Net sales divided by labor hours over the requested window for a single store.",
            },
        )


def _reset_tables(engine) -> None:
    """Idempotent reload — delete existing rows in dependency order."""
    with engine.begin() as conn:
        for table in ["quarantine", "sales_txn", "labor_hours", "traffic_count", "store"]:
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
        if "ts_utc" in df.columns:
            df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        result = validate_and_load(engine, df, dataset)
        print(
            f"{dataset:14s}  loaded={result['loaded']:>7,}  "
            f"quarantined={result['quarantined']:>5}  reason={result['reason']}"
        )

    _seed_metric_catalog(engine)
    print("metric_catalog seeded for sales_per_labor_hour v1.0.0")


if __name__ == "__main__":
    main()
