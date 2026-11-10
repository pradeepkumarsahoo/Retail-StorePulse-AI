"""Inbound Logistics Planner agent.

Takes the Transport Management System (TMS) 5-day truck outlook for a store
and joins it to the labor plan through the deterministic
``inbound.labor_plan_check`` tool. It explains *where* receiving workload and
labor disagree — a slipped truck leaving planned hours idle, or a heavy
inbound day the plan cannot absorb — and hands that evidence to the
Operations Advisor.

Boundaries:
- Reads the TMS only through the typed tool gateway (never the vendor API).
- Never changes a schedule, a route, or a shipment.
- Estimates workload. Never names, ranks, or scores an individual employee.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS


AGENT_NAME = "inbound_logistics"
PROMPT_VERSION = "1.0.0"


def run(ctx: Context, run_id: str, store_id: str, as_of_utc: datetime,
        horizon_days: int = TMS_MAX_HORIZON_DAYS, start_seq: int = 0) -> dict:
    seq = start_seq + 1
    feed = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "tms.shipments.list", {
        "store_id": store_id, "from_date": as_of_utc.date().isoformat(),
        "horizon_days": horizon_days,
    })
    seq += 1
    plan = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "inbound.labor_plan_check", {
        "store_id": store_id, "as_of_utc": as_of_utc, "horizon_days": horizon_days,
    })
    seq += 1
    forecast = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "forecast.labor", {
        "store_id": store_id, "from_utc": as_of_utc,
        "to_utc": as_of_utc + timedelta(days=horizon_days), "kind": "labor_minutes",
    })

    at_risk = [d for d in plan["days"] if d["status"] in ("idle_risk", "under_coverage")]
    findings = []
    for d in at_risk:
        if d["status"] == "idle_risk":
            trucks = [s for s in d["shipments"]
                      if s["status"] == "cancelled" or not s["eta_utc"].startswith(d["date"])]
            findings.append({
                "date": d["date"],
                "kind": "idle_risk",
                "idle_minutes_at_risk": d["idle_minutes_at_risk"],
                "trucks": [{"shipment_id": s["shipment_id"], "truck_id": s["truck_id"],
                            "status": s["status"], "delay_minutes": s["delay_minutes"],
                            "eta_utc": s["eta_utc"]} for s in trucks],
                "rationale": (
                    f"{len(trucks)} truck(s) planned for {d['date']} slipped or were cancelled in the TMS; "
                    f"~{d['idle_minutes_at_risk']} receiving minutes planned for them have no inbound work."
                ),
            })
        else:
            findings.append({
                "date": d["date"],
                "kind": "under_coverage",
                "unload_minutes_est": d["unload_minutes_est"],
                "planned_labor_minutes": d["planned_labor_minutes"],
                "receiving_window_labor_minutes": d["receiving_window_labor_minutes"],
                "receiving_share": d["receiving_share"],
                "trucks": [{"shipment_id": s["shipment_id"], "truck_id": s["truck_id"],
                            "pallets": s["pallets"], "eta_utc": s["eta_utc"]} for s in d["shipments"]],
                "rationale": (
                    f"{d['trucks_due']} truck(s) due {d['date']} need ~{d['unload_minutes_est']} receiving "
                    f"minutes, {d['receiving_share']:.0%} of the {d['receiving_window_labor_minutes']:.0f} labor "
                    "minutes planned for the morning receiving window — floor coverage would thin out during unload."
                ),
            })

    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "store_id": store_id,
        "as_of_utc": as_of_utc.isoformat(),
        "horizon_days": horizon_days,
        "tms": {
            "server": feed["server"],
            "trucks_in_window": len(feed["shipments"]),
            "shipments": feed["shipments"],
        },
        "plan_check": plan,
        "forecast_points": forecast["points"][:24 * horizon_days],
        "findings": findings,
        "summary": plan["summary"],
        "last_seq": seq,
    }
