from __future__ import annotations

import pytest

from services.labor_rate_mcp.server import LaborRateMcp


def test_list_tools(engine):
    mcp = LaborRateMcp(engine)
    tools = mcp.list_tools()
    names = {t["name"] for t in tools}
    assert names == {"get_labor_rate", "list_labor_rate_regions", "get_labor_rate_metadata"}


def test_get_labor_rate_returns_versioned_record(engine):
    mcp = LaborRateMcp(engine)
    r = mcp.call_tool("get_labor_rate", {
        "region": "West", "role_category": "associate", "effective_date": "2026-10-01",
    })
    assert r["region"] == "West"
    assert r["rate_per_hour_usd"] > 0
    assert r["source"] == "synthetic_reference"
    assert r["version"] == "1.0.0"
    assert r["server"]["name"] == "labor-rate-reference"
    assert r["synthetic"] is True


def test_get_labor_rate_rejects_unknown_region(engine):
    mcp = LaborRateMcp(engine)
    with pytest.raises(ValueError):
        mcp.call_tool("get_labor_rate", {
            "region": "Atlantis", "role_category": "associate", "effective_date": "2026-10-01",
        })


def test_get_labor_rate_rejects_malformed_date(engine):
    from pydantic import ValidationError
    mcp = LaborRateMcp(engine)
    with pytest.raises(ValidationError):
        mcp.call_tool("get_labor_rate", {
            "region": "West", "role_category": "associate", "effective_date": "10/01/2026",
        })


def test_unknown_tool_raises(engine):
    mcp = LaborRateMcp(engine)
    with pytest.raises(ValueError):
        mcp.call_tool("set_labor_rate", {})


def test_metadata_includes_server_version(engine):
    mcp = LaborRateMcp(engine)
    md = mcp.call_tool("get_labor_rate_metadata", {})
    assert md["version"] == "1.0.0"
    assert md["synthetic"] is True
