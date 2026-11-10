"""TMS MCP-style adapter: read-only, horizon-capped, typed."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS, TmsMcp


def test_list_tools(engine):
    names = {t["name"] for t in TmsMcp(engine).list_tools()}
    assert names == {"list_inbound_shipments", "get_inbound_shipment", "get_tms_metadata"}


def test_list_inbound_shipments_returns_window_and_records(engine):
    out = TmsMcp(engine).call_tool("list_inbound_shipments", {
        "store_id": "S001", "from_date": "2026-10-01", "horizon_days": 5,
    })
    ids = [s["shipment_id"] for s in out["shipments"]]
    assert ids == ["SHP-000001", "SHP-000002"]
    assert out["window"]["horizon_days"] == 5
    assert out["server"]["name"] == "tms-inbound-shipments"
    assert out["shipments"][0]["eta_utc"].startswith("2026-10-01T07:00:00")


def test_list_inbound_shipments_is_store_scoped(engine):
    out = TmsMcp(engine).call_tool("list_inbound_shipments", {
        "store_id": "S002", "from_date": "2026-10-01", "horizon_days": 2,
    })
    assert [s["shipment_id"] for s in out["shipments"]] == ["SHP-000003"]


def test_horizon_is_capped_at_five_days(engine):
    assert TMS_MAX_HORIZON_DAYS == 5
    with pytest.raises(ValidationError):
        TmsMcp(engine).call_tool("list_inbound_shipments", {
            "store_id": "S001", "from_date": "2026-10-01", "horizon_days": 6,
        })


def test_slipped_truck_is_found_by_planned_or_eta_day(engine):
    # SHP-000002 is planned 10-02 but ETA 10-03: a 1-day window on 10-02 must still see it.
    out = TmsMcp(engine).call_tool("list_inbound_shipments", {
        "store_id": "S001", "from_date": "2026-10-02", "horizon_days": 1,
    })
    assert [s["shipment_id"] for s in out["shipments"]] == ["SHP-000002"]
    assert out["shipments"][0]["delay_minutes"] == 1440


def test_get_inbound_shipment_and_unknown(engine):
    mcp = TmsMcp(engine)
    s = mcp.call_tool("get_inbound_shipment", {"shipment_id": "SHP-000002"})
    assert s["status"] == "delayed" and s["store_id"] == "S001"
    with pytest.raises(ValueError):
        mcp.call_tool("get_inbound_shipment", {"shipment_id": "SHP-999999"})


def test_metadata_declares_read_only_and_horizon(engine):
    md = TmsMcp(engine).call_tool("get_tms_metadata", {})
    assert md["read_only"] is True
    assert md["max_horizon_days"] == 5
    assert md["shipments_available"] == 3


def test_unknown_tool_rejected(engine):
    with pytest.raises(ValueError):
        TmsMcp(engine).call_tool("update_shipment", {"shipment_id": "SHP-000001"})
