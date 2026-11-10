"""Workforce Intelligence Analyst agent.

Reads the authorized workforce demand forecast and approved labor-rate ref
via the MCP adapter, and checks the labor plan against the TMS 5-day inbound
truck outlook (``inbound.labor_plan_check``). Explains coverage gaps, idle
receiving hours, and cost assumptions. Never changes schedules or employee
records.
"""
from __future__ import annotations

from datetime import datetime

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS


AGENT_NAME = "workforce"
PROMPT_VERSION = "1.1.0"


def run(ctx: Context, run_id: str, store_id: str, from_utc: datetime, to_utc: datetime,
        region: str, start_seq: int = 0, inbound_horizon_days: int = TMS_MAX_HORIZON_DAYS) -> dict:
    seq = start_seq + 1
    forecast = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "forecast.labor", {
        "store_id": store_id, "from_utc": from_utc, "to_utc": to_utc, "kind": "labor_minutes",
    })
    seq += 1
    splh = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.splh", {
        "store_id": store_id, "from_utc": from_utc, "to_utc": to_utc,
    })
    seq += 1
    labor_cost_pct = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.labor_cost_pct", {
        "store_id": store_id, "from_utc": from_utc, "to_utc": to_utc,
    })
    seq += 1
    rate = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "mcp.labor_rate.get", {
        "region": region, "role_category": "associate",
        "effective_date": from_utc.strftime("%Y-%m-%d"),
    })
    # Labor planning must see the trucks: look 5 days ahead from the end of the window.
    seq += 1
    inbound = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "inbound.labor_plan_check", {
        "store_id": store_id, "as_of_utc": to_utc, "horizon_days": inbound_horizon_days,
    })
    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "store_id": store_id,
        "forecast_points": forecast["points"][:24],   # cap for response size
        "splh": splh,
        "labor_cost_pct": labor_cost_pct,
        "labor_rate": rate,
        "inbound_plan": {
            "window": inbound["window"],
            "summary": inbound["summary"],
            "days": [{k: d[k] for k in ("date", "status", "trucks_due", "trucks_slipped",
                                         "unload_minutes_est", "idle_minutes_at_risk",
                                         "planned_labor_minutes", "receiving_window_labor_minutes",
                                         "receiving_share")}
                     for d in inbound["days"]],
            "lineage": inbound["lineage"],
        },
        "last_seq": seq,
    }
