"""Pandera contract for the TMS inbound_shipment feed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from infrastructure.db.engine import get_engine, init_schema
from infrastructure.db.ingest import validate_and_load


def _engine():
    eng = get_engine("sqlite:///:memory:")
    init_schema(eng)
    validate_and_load(eng, pd.DataFrame([
        {"store_id": "S001", "name": "T", "region": "W", "timezone": "UTC"}]), "store")
    return eng


def _row(**over) -> dict:
    planned = datetime(2026, 10, 1, 7, tzinfo=timezone.utc)
    base = {"shipment_id": "SHP-000001", "store_id": "S001", "dc_id": "DC01", "truck_id": "TRK-1000",
            "carrier": "C", "planned_arrival_utc": planned, "eta_utc": planned + timedelta(minutes=45),
            "status": "delayed", "delay_minutes": 45, "pallets": 8, "cases": 320,
            "unload_minutes_est": 200, "source_system": "tms", "schema_version": "1.0.0"}
    base.update(over)
    return base


def _df(*rows) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    for c in ("planned_arrival_utc", "eta_utc"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def test_valid_shipment_loads():
    eng = _engine()
    assert validate_and_load(eng, _df(_row()), "inbound_shipment") == {
        "loaded": 1, "quarantined": 0, "reason": None}


def test_delay_must_match_eta_minus_planned():
    eng = _engine()
    res = validate_and_load(eng, _df(_row(delay_minutes=10)), "inbound_shipment")
    assert res["loaded"] == 0 and res["quarantined"] >= 1
    with eng.connect() as conn:
        row = conn.execute(text("SELECT source_dataset, reason_code FROM quarantine")).one()
    assert row.source_dataset == "inbound_shipment" and row.reason_code == "pandera_failed"


def test_unknown_status_quarantined():
    eng = _engine()
    res = validate_and_load(eng, _df(_row(status="teleported")), "inbound_shipment")
    assert res["loaded"] == 0 and res["quarantined"] >= 1


def test_cases_without_pallets_quarantined():
    eng = _engine()
    res = validate_and_load(eng, _df(_row(pallets=0, cases=50)), "inbound_shipment")
    assert res["loaded"] == 0


def test_bad_ids_quarantined_and_good_row_kept():
    eng = _engine()
    good = _row()
    bad = _row(shipment_id="SHIP-1", truck_id="T1")
    res = validate_and_load(eng, _df(good, bad), "inbound_shipment")
    assert res["loaded"] == 1 and res["quarantined"] == 1
