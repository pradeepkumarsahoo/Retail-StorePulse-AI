"""Energy Analyst agent (forecast-driven HVAC).

Reads the store's HVAC timetable from the building-management system (BMS)
and joins it to store activity through the deterministic
``energy.hvac_schedule_check`` tool: the visitor forecast, the energy meter,
and the TMS truck schedule (for dock heating). It explains *when* the building
is heated or cooled while nobody is in it, prices that waste, and hands the
evidence to the Operations Advisor.

Boundaries:
- Reads the BMS only through the typed tool gateway (never the vendor API).
- Never writes a setpoint or a schedule; facilities apply an approved change.
- Comfort at opening and food-safety limits are hard constraints, not trade-offs.
- About the building only. Never names, ranks, or monitors an individual employee.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from packages.energy_planning.service import ENERGY_MAX_HORIZON_DAYS
from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "energy_analyst"
PROMPT_VERSION = "1.0.0"


def run(ctx: Context, run_id: str, store_id: str, as_of_utc: datetime,
        horizon_days: int = ENERGY_MAX_HORIZON_DAYS, start_seq: int = 0) -> dict:
    seq = start_seq + 1
    schedule = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "bms.hvac_schedule.get",
                                 {"store_id": store_id})
    seq += 1
    check = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "energy.hvac_schedule_check", {
        "store_id": store_id, "as_of_utc": as_of_utc, "horizon_days": horizon_days,
    })
    seq += 1
    footfall = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.visitors", {
        "store_id": store_id, "from_utc": as_of_utc - timedelta(days=7), "to_utc": as_of_utc,
    })

    findings = []
    for d in check["days"]:
        if d["status"] not in ("waste", "comfort_risk"):
            continue
        parts = []
        for f in d["findings"]:
            if f["kind"] == "early_start":
                parts.append(f"HVAC starts {f['minutes']} min before it needs to ({f['window']}, ~{f['kwh']:.0f} kWh)")
            elif f["kind"] == "late_stop":
                parts.append(f"runs {f['minutes']} min after closing tasks end ({f['window']}, ~{f['kwh']:.0f} kWh)")
            elif f["kind"] == "dock_heat_no_truck":
                parts.append(f"dock heated {f['window']} with no truck due (~{f['kwh']:.0f} kWh)")
            elif f["kind"] == "comfort_risk":
                parts.append(f"starts {f['minutes']} min too late to reach setpoint before opening")
        findings.append({
            "date": d["date"],
            "kind": d["status"],
            "avoidable_kwh": d["avoidable_kwh"],
            "avoidable_usd": d["avoidable_usd"],
            "recommended_on": d["recommended_on"],
            "recommended_off": d["recommended_off"],
            "meter_source": d["meter_source"],
            "occupancy_source": d["occupancy_source"],
            "details": d["findings"],
            "rationale": f"{d['date']}: " + "; ".join(parts) + ".",
        })

    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "store_id": store_id,
        "as_of_utc": as_of_utc.isoformat(),
        "horizon_days": horizon_days,
        "bms": {"server": schedule["server"],
                "schedule": {k: schedule[k] for k in ("hvac_on", "hvac_off", "warmup_minutes_p90",
                                                      "dock_heat_on", "dock_heat_off", "dock_heat_kw",
                                                      "setpoint_f")}},
        "check": check,
        "footfall_7d": {"value": footfall["value"], "unit": footfall["unit"]},
        "findings": findings,
        "summary": check["summary"],
        "last_seq": seq,
    }
