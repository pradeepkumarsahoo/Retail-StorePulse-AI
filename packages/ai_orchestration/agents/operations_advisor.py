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
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "operations_advisor"
PROMPT_VERSION = "1.1.0"


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


def run(ctx: Context, run_id: str, store_perf: dict, workforce: dict,
        investigations: list[dict] | None = None, start_seq: int = 0,
        inbound: dict | None = None) -> dict:
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

    if idle_days:
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
