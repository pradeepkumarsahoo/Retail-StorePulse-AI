"""MCP-style BMS adapter: read-only, typed, capped meter history."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.bms_mcp.server import BMS_MAX_READING_DAYS, BmsMcp, minutes_to_hhmm


def test_list_tools(engine):
    names = {t["name"] for t in BmsMcp(engine).list_tools()}
    assert names == {"get_hvac_schedule", "list_energy_readings", "get_bms_metadata"}


def test_get_hvac_schedule_formats_times(engine):
    out = BmsMcp(engine).call_tool("get_hvac_schedule", {"store_id": "S001"})
    assert (out["hvac_on"], out["hvac_off"]) == ("05:00", "22:00")
    assert (out["dock_heat_on"], out["dock_heat_off"]) == ("06:30", "09:00")
    assert out["warmup_minutes_p90"] == 75
    assert out["server"] == {"name": "bms-hvac", "version": "1.0.0"}
    assert out["synthetic"] is False


def test_unknown_store_schedule_raises(engine):
    with pytest.raises(ValueError):
        BmsMcp(engine).call_tool("get_hvac_schedule", {"store_id": "S999"})


def test_list_energy_readings_returns_the_day(engine):
    out = BmsMcp(engine).call_tool("list_energy_readings",
                                   {"store_id": "S001", "from_date": "2026-10-01", "days": 1})
    assert len(out["readings"]) == 24
    assert out["readings"][5]["hvac_kwh"] == 30.0
    assert out["window"]["days"] == 1


def test_reading_history_is_capped(engine):
    with pytest.raises(ValidationError):
        BmsMcp(engine).call_tool("list_energy_readings",
                                 {"store_id": "S001", "from_date": "2026-10-01",
                                  "days": BMS_MAX_READING_DAYS + 1})


def test_metadata_declares_read_only(engine):
    meta = BmsMcp(engine).call_tool("get_bms_metadata", {})
    assert meta["read_only"] is True
    assert meta["schedules_available"] == 2
    assert meta["readings_available"] == 24
    assert meta["max_reading_days"] == BMS_MAX_READING_DAYS


def test_unknown_tool_rejected(engine):
    with pytest.raises(ValueError):
        BmsMcp(engine).call_tool("set_hvac_schedule", {"store_id": "S001"})


def test_minutes_to_hhmm():
    assert minutes_to_hhmm(0) == "00:00"
    assert minutes_to_hhmm(450) == "07:30"
    assert minutes_to_hhmm(1320) == "22:00"
