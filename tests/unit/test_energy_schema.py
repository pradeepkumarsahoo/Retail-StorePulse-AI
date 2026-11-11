"""BMS data contracts: bad schedules and meter rows are quarantined, never loaded."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from infrastructure.db.ingest import validate_and_load
from packages.validation.schemas import ENERGY_READING_SCHEMA, HVAC_SCHEDULE_SCHEMA
from scripts import generate_data
from scripts.generate_data import generate


def _reading(**kw) -> dict:
    row = {"store_id": "S002", "ts_utc": datetime(2026, 10, 1, 6, tzinfo=timezone.utc),
           "hvac_kwh": 12.0, "total_kwh": 20.0, "outdoor_temp_f": 50.0,
           "source_system": "test_bms", "schema_version": "1.0.0"}
    row.update(kw)
    return row


def _load_readings(engine, rows):
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return validate_and_load(engine, df, "energy_reading")


def test_valid_reading_loads(engine):
    out = _load_readings(engine, [_reading()])
    assert out == {"loaded": 1, "quarantined": 0, "reason": None}


def test_hvac_above_total_is_quarantined(engine):
    out = _load_readings(engine, [_reading(hvac_kwh=25.0, total_kwh=20.0),
                                  _reading(ts_utc=datetime(2026, 10, 1, 7, tzinfo=timezone.utc))])
    assert out["loaded"] == 1 and out["quarantined"] == 1
    with engine.connect() as conn:
        reason = conn.execute(text(
            "SELECT reason_code FROM quarantine WHERE source_dataset='energy_reading'")).scalar()
    assert reason == "pandera_failed"


def test_implausible_temperature_and_negative_kwh_quarantined(engine):
    out = _load_readings(engine, [_reading(outdoor_temp_f=180.0),
                                  _reading(ts_utc=datetime(2026, 10, 1, 8, tzinfo=timezone.utc),
                                           hvac_kwh=-1.0)])
    assert out["loaded"] == 0 and out["quarantined"] == 2


def test_reversed_hvac_schedule_quarantined(engine):
    df = pd.DataFrame([{"store_id": "S003", "bms_id": "BMS-S003", "hvac_on_minute": 1320,
                        "hvac_off_minute": 300, "warmup_minutes_p90": 60,
                        "dock_heat_on_minute": 0, "dock_heat_off_minute": 0, "dock_heat_kw": 0.0,
                        "setpoint_f": 70, "source_system": "test_bms", "schema_version": "1.0.0"}])
    out = validate_and_load(engine, df, "hvac_schedule")
    assert out["loaded"] == 0 and out["quarantined"] == 1


def test_generated_bms_feed_validates_and_is_deterministic():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = generate(seed=42, start_date=start, days=3)
    b = generate(seed=42, start_date=start, days=3)
    HVAC_SCHEDULE_SCHEMA.validate(a["hvac_schedule"], lazy=True)
    ENERGY_READING_SCHEMA.validate(a["energy_reading"], lazy=True)
    assert len(a["energy_reading"]) == 3 * 24 * 5        # every hour, every store
    pd.testing.assert_frame_equal(a["energy_reading"], b["energy_reading"])
    # The BMS runs its timetable before the 09:00 open — the waste the check finds —
    # and sits on standby in hours the timetable is off.
    six = a["energy_reading"][a["energy_reading"]["ts_utc"].dt.hour == 6]
    for sid, (on, *_rest) in generate_data.HVAC_SCHEDULES.items():
        kwh = six[six["store_id"] == sid]["hvac_kwh"]
        assert ((kwh > 1.0) if on < 7 * 60 else (kwh < 1.0)).all(), sid


def test_bms_feed_does_not_perturb_existing_tables(monkeypatch):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with_bms = generate(seed=42, start_date=start, days=3)
    monkeypatch.setattr(generate_data, "_generate_energy_readings", lambda rng, s, d: [])
    without_bms = generate(seed=42, start_date=start, days=3)
    for name in ["sales_txn", "labor_hours", "traffic_count", "inbound_shipment"]:
        pd.testing.assert_frame_equal(with_bms[name], without_bms[name])
