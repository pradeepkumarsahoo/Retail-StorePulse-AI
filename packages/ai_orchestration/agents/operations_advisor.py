"""Operations Advisor agent.

Combines validated findings and emits a prioritized, safe, store-level
recommendation. Policy-bound:
  * Never recommends reducing an individual's hours.
  * Never produces a definitive causal claim.
  * Always includes direction of impact, risk, confidence, assumptions.
  * Every recommendation routes to a human reviewer (status = pending_review).
  * Inbound (TMS) evidence is evaluated first: a slipped truck or a heavy
    inbound day is a concrete, dated planning fact, so it outranks the
    softer sales-trend heuristics.
  * Energy (BMS) evidence is only considered in the energy workflow. The
    recommended HVAC schedule must pass a comfort guard: the store reaches
    setpoint before opening on every checked day. The advisor never writes
    to the building system; facilities apply an approved change.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "operations_advisor"
PROMPT_VERSION = "1.2.0"


FORBIDDEN_TERMS = ("cut ", "reduce hours", "fire ", "terminate ", "layoff", "lay-off",
                   "lower pay", "cut pay")


def _policy_check(title: str, rationale: str) -> list[str]:
    hits = []
    text = f"{title.lower()} :: {rationale.lower()}"
    for term in FORBIDDEN_TERMS:
        if term in text:
            hits.append(term.strip())
    return hits


def _inbound_findings(workforce: dict, inbound: dict | None) -> list[dict]:
    """Collect idle-risk / under-coverage days from the Inbound Logistics Planner
    output (preferred) or from the workforce agent's embedded plan check."""
    if inbound and inbound.get("findings"):
        return list(inbound["findings"])
    plan = workforce.get("inbound_plan") or {}
    out = []
    for d in plan.get("days", []):
        if d.get("status") == "idle_risk":
            out.append({"date": d["date"], "kind": "idle_risk",
                        "idle_minutes_at_risk": d.get("idle_minutes_at_risk", 0), "trucks": []})
        elif d.get("status") == "under_coverage":
            out.append({"date": d["date"], "kind": "under_coverage",
                        "unload_minutes_est": d.get("unload_minutes_est", 0),
                        "planned_labor_minutes": d.get("planned_labor_minutes"),
                        "receiving_share": d.get("receiving_share"), "trucks": []})
    return out


def _hhmm(v: str) -> int:
    h, m = v.split(":")
    return int(h) * 60 + int(m)


def _comfort_guard(energy: dict) -> dict:
    """Recommended start + measured warm-up must reach setpoint before comfort-ready on every day."""
    check = energy.get("check") or {}
    rec = (check.get("summary") or {}).get("recommended_schedule") or {}
    cur = (check.get("summary") or {}).get("current_schedule") or {}
    if not rec or not cur:
        return {"passed": False, "reason": "no recommended schedule"}
    warm = int(cur.get("warmup_minutes_p90") or 0)
    lead = int(((check.get("lineage") or {}).get("thresholds") or {}).get("comfort_lead_minutes", 15))
    ready_at = _hhmm(rec["hvac_on"]) + warm
    for d in check.get("days", []):
        if d.get("first_occupied") and ready_at > _hhmm(d["first_occupied"]) - lead:
            return {"passed": False, "reason": f"setpoint not reached before opening on {d['date']}"}
    return {"passed": True, "setpoint_ready_at": f"{ready_at // 60:02d}:{ready_at % 60:02d}"}


def _energy_recommendation(energy: dict) -> tuple[str, str, str, str, str] | None:
    """(title, rationale, direction, confidence, risk) for the energy workflow, or None."""
    check = energy.get("check") or {}
    s = check.get("summary") or {}
    days = [d for d in check.get("days", []) if d.get("status") in ("waste", "comfort_risk")]
    if not days:
        return None
    cur, rec = s.get("current_schedule") or {}, s.get("recommended_schedule") or {}
    guard = _comfort_guard(energy)
    if any(d["status"] == "waste" for d in days) and guard["passed"]:
        dock_days = [d["date"] for d in days if d.get("dock_heat_kwh")]
        dock_txt = (f" Skip dock heating on days the TMS shows no truck ({', '.join(dock_days)})."
                    if dock_days else "")
        title = "Align the store's HVAC schedule with store activity"
        rationale = (
            f"The BMS runs HVAC {cur.get('hvac_on')}-{cur.get('hvac_off')}, but the visitor forecast and "
            f"meter show the store is empty outside opening hours. Starting at {rec.get('hvac_on')} still "
            f"reaches setpoint by {guard['setpoint_ready_at']} (measured warm-up {cur.get('warmup_minutes_p90')} min), "
            f"and stopping at {rec.get('hvac_off')} covers closing tasks.{dock_txt} "
            f"About {s.get('avoidable_kwh', 0):.0f} kWh (${s.get('avoidable_usd', 0):.2f}) over the next "
            f"{s.get('days_checked')} days, ~${s.get('annualized_usd', 0):,.0f} a year from the schedule alone, "
            "with comfort at opening unchanged. Facilities apply the change in the BMS after review."
        )
        meter_ok = all(d.get("meter_source") == "meter" for d in days)
        return (title, rationale, "down", "high" if meter_ok else "medium",
                "Unusually hot or cold mornings can need a longer warm-up; facilities should keep an "
                "earlier start on those days and re-check the BMS warm-up figure after one week.")
    comfort = [d for d in days if d["status"] == "comfort_risk"]
    if comfort:
        worst = max(comfort, key=lambda d: d.get("comfort_risk_minutes", 0))
        return (
            "Start HVAC earlier so the store is comfortable at opening",
            (f"The BMS starts HVAC at {cur.get('hvac_on')}, but the store needs "
             f"{cur.get('warmup_minutes_p90')} min to reach setpoint before the first visitors at "
             f"{worst.get('first_occupied')}. Recommend starting at {worst.get('recommended_on')} on the "
             f"affected days ({', '.join(d['date'] for d in comfort)})."),
            "up", "medium", "Slightly higher energy use; comfort and food-safety limits take priority.",
        )
    return None


def run(ctx: Context, run_id: str, store_perf: dict, workforce: dict,
        investigations: list[dict] | None = None, start_seq: int = 0,
        inbound: dict | None = None, energy: dict | None = None) -> dict:
    """Synthesize a recommendation. Draft only — human must review."""
    store_id = store_perf["store_id"]
    kpis = store_perf.get("kpis", {})
    splh = kpis.get("splh", {}).get("value")
    sales_change = store_perf.get("derived", {}).get("sales_change_pct")
    coverage_gap = None
    fp = workforce.get("forecast_points", [])
    if fp:
        coverage_gap = fp[0]  # first predicted point — compare downstream
    inbound_findings = _inbound_findings(workforce, inbound)
    idle_days = [f for f in inbound_findings if f["kind"] == "idle_risk"]
    heavy_days = [f for f in inbound_findings if f["kind"] == "under_coverage"]

    # Simple, transparent decision table — not a model.
    title: str
    rationale: str
    direction: str
    confidence: str
    risk: str

    energy_rec = _energy_recommendation(energy) if energy else None

    if energy_rec:
        title, rationale, direction, confidence, risk = energy_rec
    elif idle_days:
        # TMS says the truck will not land when the labor plan expects it.
        dates = ", ".join(f["date"] for f in idle_days)
        idle_minutes = sum(int(f.get("idle_minutes_at_risk") or 0) for f in idle_days)
        trucks = ", ".join(
            f"{t['truck_id']} ({t['status']}, +{t['delay_minutes']} min)"
            for f in idle_days for t in f.get("trucks", [])
        ) or "see TMS evidence"
        title = "Re-sequence receiving work around slipped inbound trucks"
        rationale = (
            f"TMS shows inbound truck(s) planned for {dates} slipping to a later day or cancelled "
            f"[{trucks}]. About {idle_minutes} receiving minutes planned for them would have no "
            "inbound work. Recommend a manager-led review that moves planned receiving tasks to "
            "the new ETA and pre-assigns productive alternatives (resets, training, back-of-house, "
            "customer recovery) for the affected shift — keep total planned hours unchanged."
        )
        direction = "up"
        confidence = "medium" if all(f.get("trucks") for f in idle_days) else "low"
        risk = "ETA may move again; re-check the TMS feed the evening before and on the morning of arrival."
    elif heavy_days:
        dates = ", ".join(f["date"] for f in heavy_days)
        share = max(float(f.get("receiving_share") or 0) for f in heavy_days)
        title = "Plan additional receiving coverage for heavy inbound days"
        rationale = (
            f"TMS shows a heavy inbound load on {dates}: estimated unload work is {share:.0%} of the "
            "labor minutes currently planned for the morning receiving window. Recommend a manager-led "
            "review of the shift plan so receiving can be absorbed without thinning floor coverage "
            "during open hours."
        )
        direction = "up"
        confidence = "medium"
        risk = "Pallet counts are TMS estimates; actual unload time varies by load mix."
    elif sales_change is not None and sales_change < -0.10 and splh and splh < 20:
        title = "Review staffing mix against predicted demand for next week"
        rationale = (
            f"Net sales fell {sales_change*100:.1f}% vs prior comparable period while "
            f"SPLH is ${splh:.2f}/hr. Workforce forecast is available (see evidence); "
            "recommend a manager-led review of staffing distribution across the day."
        )
        direction = "up"
        confidence = "medium"
        risk = "Rebalancing without coaching could affect service during peak hours."
    elif sales_change is not None and sales_change > 0.10:
        title = "Confirm current staffing matches improved demand"
        rationale = (
            f"Net sales rose {sales_change*100:.1f}% vs prior period. Validate that "
            "upcoming schedules reflect the sustained demand uplift before setting "
            "next-week targets."
        )
        direction = "neutral"
        confidence = "medium"
        risk = "Momentum may be event-driven; cross-check weather and local events."
    else:
        title = "Hold current plan; continue monitoring"
        rationale = "No material deviation versus prior comparable period. Continue scheduled monitoring."
        direction = "neutral"
        confidence = "low"
        risk = "None identified."

    violations = _policy_check(title, rationale)
    if violations:
        title = "Hold current plan; continue monitoring"
        rationale = ("Draft recommendation blocked by responsible-workforce policy: "
                     f"forbidden language detected ({', '.join(violations)}). "
                     "Returning the safe default.")
        direction = "neutral"
        confidence = "low"
        risk = "None identified."

    evidence = {
        "store_perf_summary": {
            "sales_change_pct": sales_change,
            "splh": splh,
            "net_sales_usd": kpis.get("net_sales", {}).get("value"),
        },
        "workforce_summary": {
            "first_forecast_point": coverage_gap,
            "labor_cost_pct": workforce.get("labor_cost_pct", {}).get("value"),
            "labor_rate_source": workforce.get("labor_rate", {}).get("source"),
            "labor_rate_version": workforce.get("labor_rate", {}).get("version"),
        },
        "inbound_summary": {
            "source": "inbound_logistics" if inbound else ("workforce.inbound_plan" if workforce.get("inbound_plan") else None),
            "findings": inbound_findings,
            "plan_summary": (inbound or {}).get("summary") or (workforce.get("inbound_plan") or {}).get("summary"),
            "tms_server": ((inbound or {}).get("tms") or {}).get("server")
                          or ((workforce.get("inbound_plan") or {}).get("lineage") or {}).get("tms_server"),
        },
        "investigations": [i.get("hypotheses", []) for i in (investigations or [])],
    }
    if energy:
        es = (energy.get("check") or {}).get("summary") or {}
        evidence["energy_summary"] = {
            "source": "energy_analyst",
            "findings": energy.get("findings", []),
            "current_schedule": es.get("current_schedule"),
            "recommended_schedule": es.get("recommended_schedule"),
            "avoidable_kwh": es.get("avoidable_kwh"),
            "avoidable_usd": es.get("avoidable_usd"),
            "annualized_usd": es.get("annualized_usd"),
            "comfort_guard": _comfort_guard(energy),
            "bms_server": ((energy.get("check") or {}).get("lineage") or {}).get("bms_server"),
        }
    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "recommendation": {
            "recommendation_id": str(uuid.uuid4()),
            "store_id": store_id,
            "title": title,
            "rationale": rationale,
            "evidence": evidence,
            "expected_direction": direction,
            "confidence": confidence,
            "risk": risk,
            "status": "pending_review",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "prompt_version": PROMPT_VERSION,
        },
        "last_seq": start_seq,
    }
