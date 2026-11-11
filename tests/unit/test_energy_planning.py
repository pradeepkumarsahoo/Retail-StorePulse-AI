"""Deterministic energy HVAC-schedule check (BMS timetable joined to store activity).

Fixture (tests/conftest.py): S001 BMS runs 05:00–22:00, 75-min warm-up, dock heated
06:30–09:00 at 8 kW; meter = 30 kWh/h while running on 2026-10-01; traffic 09–12;
one on-time truck on 10-01 and one truck planned 10-02 that rolls to 10-03.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from infrastructure.db.ingest import validate_and_load
from packages.energy_planning.service import (
    CHECK_NAME,
    ENERGY_MAX_HORIZON_DAYS,
    TARIFF_USD_PER_KWH,
    WASTE_THRESHOLD_KWH,
    _kwh_between,
    hvac_schedule_check,
)


def _add_traffic(engine, store_id: str, day: datetime, hours=range(9, 21), visitors: int = 20) -> None:
    df = pd.DataFrame([{"store_id": store_id, "ts_utc": day.replace(hour=h, minute=0), "visitors": visitors,
                        "source_system": "test", "schema_version": "1.0.0"} for h in hours])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(engine, df, "traffic_count")


def _add_meter(engine, store_id: str, day: datetime, on_h: int, off_h: int, kwh: float = 30.0) -> None:
    day0 = day.replace(hour=0, minute=0)
    df = pd.DataFrame([{"store_id": store_id, "ts_utc": day0 + timedelta(hours=h),
                        "hvac_kwh": kwh if on_h <= h < off_h else 0.3,
                        "total_kwh": (kwh if on_h <= h < off_h else 0.3) + 5.0,
                        "outdoor_temp_f": 45.0, "source_system": "test_bms", "schema_version": "1.0.0"}
                       for h in range(24)])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(engine, df, "energy_reading")


def test_kwh_between_prorates_partial_hours():
    hourly = {5: 30.0, 6: 60.0}
    assert _kwh_between(hourly, 330, 390) == pytest.approx(15.0 + 30.0)   # 05:30–06:30
    assert _kwh_between(hourly, 400, 400) == 0.0


def test_day_one_flags_early_start_and_late_stop(engine, seed_window):
    start, _ = seed_window
    out = hvac_schedule_check(engine, "S001", start, horizon_days=1)
    d = out["days"][0]
    assert d["status"] == "waste"
    assert (d["first_occupied"], d["last_occupied"]) == ("09:00", "12:00")
    # 09:00 − 15 min comfort lead − 75 min warm-up = 07:30; close 12:00 + 30 min grace.
    assert (d["recommended_on"], d["recommended_off"]) == ("07:30", "12:30")
    assert d["early_start_minutes"] == 150 and d["late_stop_minutes"] == 570
    assert d["early_start_kwh"] == 75.0         # 2.5 h × 30 kW holding load
    assert d["late_stop_kwh"] == 285.0          # 12:30–22:00 metered
    assert d["dock_heat_kwh"] == 0.0            # a truck is due on 10-01
    assert d["dock_heat_needed"] is True
    assert d["avoidable_kwh"] == 360.0
    assert d["avoidable_usd"] == round(360.0 * TARIFF_USD_PER_KWH, 2)
    assert d["meter_source"] == "meter" and d["occupancy_source"] == "traffic_actual"
    assert {f["kind"] for f in d["findings"]} == {"early_start", "late_stop"}
    assert out["status"] == "ok"


def test_no_occupancy_evidence_is_insufficient_not_a_guess(engine, seed_window):
    start, _ = seed_window
    out = hvac_schedule_check(engine, "S001", start, horizon_days=2)
    day2 = out["days"][1]
    assert day2["status"] == "insufficient_evidence"
    assert "visitor" in day2["reason"]
    assert day2["avoidable_kwh"] == 0.0


def test_slipped_truck_flags_dock_heat_and_baseline_meter(engine, seed_window):
    start, _ = seed_window
    day2 = start + timedelta(days=1)
    _add_traffic(engine, "S001", day2, hours=range(9, 21))
    out = hvac_schedule_check(engine, "S001", day2, horizon_days=1)
    d = out["days"][0]
    # No readings for 10-02 → the trailing baseline (10-01) prices the hours.
    assert d["meter_source"] == "meter_baseline"
    assert d["trucks_slipped"] == 1 and d["trucks_due"] == 0
    dock = next(f for f in d["findings"] if f["kind"] == "dock_heat_no_truck")
    assert dock["kwh"] == 20.0                  # 8 kW × 2.5 h
    assert "TRK-1002" in dock["evidence"]
    assert d["late_stop_minutes"] == 30         # 21:00 + 30 → 21:30; BMS off 22:00


def test_matching_schedule_is_aligned(engine, seed_window):
    start, _ = seed_window
    _add_traffic(engine, "S002", start, hours=range(9, 21))
    _add_meter(engine, "S002", start, on_h=7, off_h=22, kwh=12.0)
    out = hvac_schedule_check(engine, "S002", start, horizon_days=1)
    d = out["days"][0]
    # 09:00 − 15 − 60 = 07:45 recommended; BMS starts 07:30 → 15 min early ≈ 3 kWh < threshold.
    assert d["early_start_minutes"] == 15
    assert d["avoidable_kwh"] < WASTE_THRESHOLD_KWH
    assert d["status"] == "aligned"
    assert d["dock_heat_needed"] is None        # no dock heater configured


def test_late_start_is_a_comfort_risk(engine, seed_window):
    start, _ = seed_window
    with engine.begin() as conn:
        conn.execute(text("UPDATE hvac_schedule SET hvac_on_minute=480, hvac_off_minute=1260 "
                          "WHERE store_id='S002'"))
    _add_traffic(engine, "S002", start, hours=range(9, 21))
    _add_meter(engine, "S002", start, on_h=8, off_h=21, kwh=12.0)
    d = hvac_schedule_check(engine, "S002", start, horizon_days=1)["days"][0]
    assert d["status"] == "comfort_risk"
    assert d["comfort_risk_minutes"] == 15      # needs 07:45, starts 08:00
    assert d["avoidable_kwh"] == 0.0


def test_visitor_forecast_is_preferred_over_traffic(engine, seed_window):
    start, _ = seed_window
    fmt = "%Y-%m-%d %H:%M:%S.%f"
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO forecast_run (run_id, model_name, model_version, created_at_utc, "
            "train_window_from, train_window_to) VALUES ('fr-v', 'm', '1', :n, :n, :n)"),
            {"n": start.strftime(fmt)})
        for h in (10, 11, 12, 13):
            conn.execute(text(
                "INSERT INTO forecast (run_id, store_id, ts_utc, kind, predicted) "
                "VALUES ('fr-v', 'S001', :ts, 'visitors', 25.0)"),
                {"ts": start.replace(hour=h).strftime(fmt)})
    d = hvac_schedule_check(engine, "S001", start, horizon_days=1)["days"][0]
    assert d["occupancy_source"] == "forecast"
    assert (d["first_occupied"], d["last_occupied"]) == ("10:00", "14:00")


def test_missing_bms_schedule_is_insufficient(engine, seed_window):
    start, _ = seed_window
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM hvac_schedule WHERE store_id='S001'"))
    out = hvac_schedule_check(engine, "S001", start, horizon_days=1)
    assert out["status"] == "insufficient_evidence"
    assert out["days"][0]["reason"] == "no BMS schedule on record"
    assert out["summary"]["current_schedule"] is None


def test_horizon_bounds_enforced(engine, seed_window):
    start, _ = seed_window
    with pytest.raises(ValueError):
        hvac_schedule_check(engine, "S001", start, horizon_days=0)
    with pytest.raises(ValueError):
        hvac_schedule_check(engine, "S001", start, horizon_days=ENERGY_MAX_HORIZON_DAYS + 1)


def test_summary_annualizes_schedule_waste_only(engine, seed_window):
    start, _ = seed_window
    s = hvac_schedule_check(engine, "S001", start, horizon_days=1)["summary"]
    assert s["schedule_kwh_per_day"] == 360.0
    assert s["annualized_usd"] == round(360.0 * 365 * TARIFF_USD_PER_KWH, 0)
    assert s["annualized_mwh"] == round(360.0 * 365 / 1000, 1)
    assert s["recommended_schedule"] == {"hvac_on": "07:30", "hvac_off": "12:30",
                                         "skip_dock_heat_without_truck": False}
    assert s["current_schedule"]["hvac_on"] == "05:00"


def test_lineage_block_is_complete(engine, seed_window):
    start, _ = seed_window
    lin = hvac_schedule_check(engine, "S001", start, horizon_days=1)["lineage"]
    assert lin["check_name"] == CHECK_NAME
    assert lin["tariff_usd_per_kwh"] == TARIFF_USD_PER_KWH
    assert set(lin["source_tables"]) >= {"hvac_schedule", "energy_reading", "forecast",
                                         "traffic_count", "inbound_shipment"}
    assert lin["bms_server"]["name"] == "bms-hvac"
    assert lin["tms_server"]["name"] == "tms-inbound-shipments"
    assert lin["row_counts"]["energy_reading"] == 24
    assert any("never written" in a for a in lin["assumptions"])
    assert any("individual employee" in a for a in lin["assumptions"])
    assert lin["synthetic_data"] is False        # test_bms source is not labeled synthetic
