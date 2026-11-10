"""Deterministic inbound labor-plan check (TMS trucks joined to the labor plan)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from infrastructure.db.ingest import validate_and_load
from packages.inbound_planning.service import (
    CHECK_NAME,
    UNDER_COVERAGE_SHARE,
    labor_plan_check,
    list_inbound_shipments,
)


def _add_labor(engine, store_id: str, day: datetime, minutes_per_hour: int = 60) -> None:
    rows = [{"store_id": store_id, "ts_utc": day.replace(hour=h), "labor_minutes": minutes_per_hour,
             "source_system": "test", "schema_version": "1.0.0"} for h in range(9, 12)]
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(engine, df, "labor_hours")


def _add_truck(engine, shipment_id: str, store_id: str, planned: datetime, unload: int,
               pallets: int = 8, delay: int = 0, status: str = "scheduled") -> None:
    df = pd.DataFrame([{
        "shipment_id": shipment_id, "store_id": store_id, "dc_id": "DC01", "truck_id": "TRK-5555",
        "carrier": "Test Carrier", "planned_arrival_utc": planned,
        "eta_utc": planned + timedelta(minutes=delay), "status": status, "delay_minutes": delay,
        "pallets": pallets, "cases": pallets * 40, "unload_minutes_est": unload,
        "source_system": "test_tms", "schema_version": "1.0.0"}])
    for c in ("planned_arrival_utc", "eta_utc"):
        df[c] = pd.to_datetime(df[c], utc=True)
    validate_and_load(engine, df, "inbound_shipment")


def test_day_one_is_balanced_on_scheduled_labor(engine, seed_window):
    start, _ = seed_window
    out = labor_plan_check(engine, "S001", start, horizon_days=1)
    d = out["days"][0]
    assert d["date"] == "2026-10-01"
    assert d["status"] == "balanced"
    assert d["trucks_due"] == 1
    assert d["planned_labor_minutes"] == 270          # 60 + 120 + 90 scheduled minutes
    assert d["receiving_window_labor_minutes"] == 270 # all three hours fall in 09–12
    assert d["receiving_share"] == round(75 / 270, 4)
    assert out["summary"]["labor_source"] == "scheduled"
    assert out["status"] == "ok"


def test_no_labor_plan_means_insufficient_evidence_not_a_guess(engine, seed_window):
    start, _ = seed_window
    out = labor_plan_check(engine, "S001", start, horizon_days=3)
    day2 = out["days"][1]
    # A truck slipped off 10-02, but there is no labor plan for 10-02 → say so.
    assert day2["status"] == "insufficient_evidence"
    assert day2["trucks_slipped"] == 1
    assert day2["planned_labor_minutes"] is None


def test_slipped_truck_flags_idle_risk_when_labor_is_planned(engine, seed_window):
    start, _ = seed_window
    _add_labor(engine, "S001", start + timedelta(days=1))
    out = labor_plan_check(engine, "S001", start, horizon_days=3)
    day2, day3 = out["days"][1], out["days"][2]
    assert day2["status"] == "idle_risk"
    assert day2["idle_minutes_at_risk"] == 250
    assert day2["shipments"][0]["shipment_id"] == "SHP-000002"
    # The truck lands on day 3, where no labor is planned → insufficient evidence there.
    assert day3["trucks_due"] == 1 and day3["status"] == "insufficient_evidence"
    assert out["summary"]["by_status"]["idle_risk"] == 1
    assert out["summary"]["idle_minutes_at_risk"] == 250


def test_cancelled_truck_also_counts_as_idle_risk(engine, seed_window):
    start, _ = seed_window
    day4 = start + timedelta(days=3)
    _add_labor(engine, "S001", day4)
    _add_truck(engine, "SHP-000010", "S001", day4.replace(hour=7), unload=200, status="cancelled")
    out = labor_plan_check(engine, "S001", day4, horizon_days=1)
    assert out["days"][0]["status"] == "idle_risk"
    assert out["days"][0]["trucks_due"] == 0
    assert out["days"][0]["idle_minutes_at_risk"] == 200


def test_heavy_inbound_day_flags_under_coverage(engine, seed_window):
    start, _ = seed_window
    day5 = start + timedelta(days=4)
    _add_labor(engine, "S001", day5)                    # 180 receiving-window minutes
    _add_truck(engine, "SHP-000011", "S001", day5.replace(hour=7), unload=120, pallets=5)
    out = labor_plan_check(engine, "S001", day5, horizon_days=1)
    d = out["days"][0]
    assert d["receiving_share"] == round(120 / 180, 4)
    assert d["receiving_share"] > UNDER_COVERAGE_SHARE
    assert d["status"] == "under_coverage"


def test_no_truck_day_is_no_inbound(engine, seed_window):
    start, _ = seed_window
    day6 = start + timedelta(days=5)
    _add_labor(engine, "S001", day6)
    out = labor_plan_check(engine, "S001", day6, horizon_days=1)
    assert out["days"][0]["status"] == "no_inbound"


def test_forecast_is_preferred_over_schedule(engine, seed_window):
    start, _ = seed_window
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO forecast_run (run_id, model_name, model_version, created_at_utc, "
            "train_window_from, train_window_to) VALUES ('FR1','m','1.0.0','2026-09-30 00:00:00',"
            "'2026-09-01 00:00:00','2026-09-30 00:00:00')"))
        conn.execute(text(
            "INSERT INTO forecast (run_id, store_id, ts_utc, kind, predicted) VALUES "
            "('FR1','S001','2026-10-01 09:36:00.000000','labor_minutes',100.0),"
            "('FR1','S001','2026-10-01 15:36:00.000000','labor_minutes',100.0)"))
    out = labor_plan_check(engine, "S001", start, horizon_days=1)
    d = out["days"][0]
    assert out["summary"]["labor_source"] == "forecast"
    assert d["planned_labor_minutes"] == 200
    assert d["receiving_window_labor_minutes"] == 100   # only the 09:36 point is in 09–12
    assert d["receiving_share"] == 0.75                 # 75 / 100 → heavy
    assert d["status"] == "under_coverage"


def test_horizon_bounds_enforced(engine, seed_window):
    start, _ = seed_window
    with pytest.raises(ValueError):
        labor_plan_check(engine, "S001", start, horizon_days=6)
    with pytest.raises(ValueError):
        labor_plan_check(engine, "S001", start, horizon_days=0)


def test_lineage_block_is_complete(engine, seed_window):
    start, _ = seed_window
    out = labor_plan_check(engine, "S001", start, horizon_days=2)
    lin = out["lineage"]
    assert lin["check_name"] == CHECK_NAME
    assert lin["check_version"] == "1.0.0"
    assert set(lin["source_tables"]) == {"inbound_shipment", "forecast", "labor_hours"}
    assert lin["row_counts"]["inbound_shipment"] == 2
    assert lin["thresholds"]["under_coverage_share"] == UNDER_COVERAGE_SHARE
    assert lin["tms_server"]["name"] == "tms-inbound-shipments"
    assert lin["synthetic_data"] is False   # test_tms source is not labelled synthetic
    assert any("never names" in a for a in lin["assumptions"])


def test_list_inbound_shipments_clamps_to_horizon(engine, seed_window):
    start, _ = seed_window
    rows = list_inbound_shipments(engine, "S001", start, start + timedelta(days=30))
    # 30-day ask is clamped to the 5-day TMS horizon; both seeded S001 trucks are inside it.
    assert [r["shipment_id"] for r in rows] == ["SHP-000001", "SHP-000002"]
    with pytest.raises(ValueError):
        list_inbound_shipments(engine, "S001", start, start)
