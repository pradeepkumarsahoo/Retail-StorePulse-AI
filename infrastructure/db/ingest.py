"""Batch ingest: validate with Pandera, quarantine failures, load the rest."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import pandera as pa
from sqlalchemy.engine import Engine

from packages.validation.schemas import (
    ENERGY_READING_SCHEMA,
    HVAC_SCHEDULE_SCHEMA,
    INBOUND_SHIPMENT_SCHEMA,
    LABOR_HOURS_SCHEMA,
    SALES_TXN_SCHEMA,
    STORE_SCHEMA,
    TRAFFIC_COUNT_SCHEMA,
)


SCHEMA_MAP = {
    "store": (STORE_SCHEMA, "store"),
    "sales_txn": (SALES_TXN_SCHEMA, "sales_txn"),
    "labor_hours": (LABOR_HOURS_SCHEMA, "labor_hours"),
    "traffic_count": (TRAFFIC_COUNT_SCHEMA, "traffic_count"),
    # TMS feed: validated like every other source, quarantined on failure.
    "inbound_shipment": (INBOUND_SHIPMENT_SCHEMA, "inbound_shipment"),
    # BMS feed (energy): HVAC timetable + hourly meter readings.
    "hvac_schedule": (HVAC_SCHEDULE_SCHEMA, "hvac_schedule"),
    "energy_reading": (ENERGY_READING_SCHEMA, "energy_reading"),
}


def _quarantine_rows(engine: Engine, dataset: str, rows: Iterable[dict], reason: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "ingested_at_utc": now,
            "source_dataset": dataset,
            "reason_code": reason,
            "raw_row_json": json.dumps(row, default=str),
        }
        for row in rows
    ]
    if not payload:
        return 0
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO quarantine (ingested_at_utc, source_dataset, reason_code, raw_row_json) "
            "VALUES (:ingested_at_utc, :source_dataset, :reason_code, :raw_row_json)",
            payload,
        )
    return len(payload)


def validate_and_load(
    engine: Engine, df: pd.DataFrame, dataset: str, table_name: str | None = None
) -> dict:
    """Validate df against the registered schema; load clean rows; quarantine bad ones.

    Returns a dict summary: {"loaded": int, "quarantined": int, "reason": str | None}.
    """
    if dataset not in SCHEMA_MAP:
        raise ValueError(f"Unknown dataset: {dataset}")
    schema, default_table = SCHEMA_MAP[dataset]
    target_table = table_name or default_table

    clean: pd.DataFrame
    bad_rows: list[dict] = []
    reason: str | None = None

    try:
        clean = schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as err:
        failure_idx = err.failure_cases["index"].dropna().astype(int).unique().tolist()
        bad_rows = df.loc[df.index.isin(failure_idx)].to_dict(orient="records")
        clean = df.drop(index=failure_idx, errors="ignore")
        reason = "pandera_failed"
        # Re-validate the cleaned slice to guarantee the loader receives a clean df.
        clean = schema.validate(clean, lazy=True)

    if not clean.empty:
        clean.to_sql(target_table, con=engine, if_exists="append", index=False)

    quarantined = _quarantine_rows(engine, dataset, bad_rows, reason or "none")
    return {"loaded": int(len(clean)), "quarantined": int(quarantined), "reason": reason}
