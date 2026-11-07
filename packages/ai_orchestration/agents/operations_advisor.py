"""Operations Advisor agent.

Combines validated findings and emits a prioritized, safe, store-level
recommendation. Policy-bound:
  * Never recommends reducing an individual's hours.
  * Never produces a definitive causal claim.
  * Always includes direction of impact, risk, confidence, assumptions.
  * Every recommendation routes to a human reviewer (status = pending_review).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "operations_advisor"
PROMPT_VERSION = "1.0.0"


FORBIDDEN_TERMS = ("cut ", "reduce hours", "fire ", "terminate ", "layoff", "lay-off",
                   "lower pay", "cut pay")


def _policy_check(title: str, rationale: str) -> list[str]:
    hits = []
    text = f"{title.lower()} :: {rationale.lower()}"
    for term in FORBIDDEN_TERMS:
        if term in text:
            hits.append(term.strip())
    return hits


def run(ctx: Context, run_id: str, store_perf: dict, workforce: dict,
        investigations: list[dict] | None = None, start_seq: int = 0) -> dict:
    """Synthesize a recommendation. Draft only — human must review."""
    store_id = store_perf["store_id"]
    kpis = store_perf.get("kpis", {})
    splh = kpis.get("splh", {}).get("value")
    sales_change = store_perf.get("derived", {}).get("sales_change_pct")
    coverage_gap = None
    fp = workforce.get("forecast_points", [])
    if fp:
        coverage_gap = fp[0]  # first predicted point — compare downstream

    # Simple, transparent decision table — not a model.
    title: str
    rationale: str
    direction: str
    confidence: str
    risk: str

    if sales_change is not None and sales_change < -0.10 and splh and splh < 20:
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
