"""Anomaly Investigator agent.

Retrieves anomaly detail and comparison metrics, ranks potential contributing
factors (never definitive causal claims), and cites evidence for each.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "anomaly_investigator"
PROMPT_VERSION = "1.0.0"


def run(ctx: Context, run_id: str, anomaly_id: str, start_seq: int = 0) -> dict:
    seq = start_seq + 1
    detail = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "anomaly.detail",
                               {"anomaly_id": anomaly_id})["anomaly"]
    store_id = detail["store_id"]
    ts = datetime.fromisoformat(detail["ts_utc"])

    hour_from = ts
    hour_to = ts + timedelta(hours=1)

    seq += 1
    sales = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.net_sales", {
        "store_id": store_id, "from_utc": hour_from, "to_utc": hour_to,
    })
    seq += 1
    txns = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.transactions", {
        "store_id": store_id, "from_utc": hour_from, "to_utc": hour_to,
    })
    seq += 1
    visitors = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.visitors", {
        "store_id": store_id, "from_utc": hour_from, "to_utc": hour_to,
    })
    seq += 1
    conversion = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.conversion_rate", {
        "store_id": store_id, "from_utc": hour_from, "to_utc": hour_to,
    })
    seq += 1
    forecast = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "forecast.labor", {
        "store_id": store_id, "from_utc": hour_from, "to_utc": hour_to,
    })

    # Rank deterministic hypotheses from evidence — ordering is explicit, no LLM guessing.
    hypotheses: list[dict] = []
    deviation = detail["deviation"]
    baseline = detail["baseline"]
    if visitors["value"] is not None and visitors["value"] < baseline * 0.5:
        hypotheses.append({
            "factor": "footfall_shortfall",
            "rationale": f"visitors in hour = {visitors['value']} (observed sales deviation = {deviation}).",
            "evidence_refs": ["kpi.visitors", "anomaly.detail"],
            "confidence": "medium",
        })
    if conversion["value"] is not None and conversion["value"] < 0.1:
        hypotheses.append({
            "factor": "conversion_drop",
            "rationale": f"conversion_rate = {conversion['value']} (<10%).",
            "evidence_refs": ["kpi.conversion_rate"],
            "confidence": "medium",
        })
    fp = forecast["points"][0] if forecast["points"] else None
    if fp:
        # Coverage gap signal
        hypotheses.append({
            "factor": "labor_coverage_check",
            "rationale": f"predicted labor_minutes = {fp['predicted']} for hour; "
                         "compare with scheduled to assess coverage.",
            "evidence_refs": ["forecast.labor"],
            "confidence": "low",
        })
    if not hypotheses:
        hypotheses.append({
            "factor": "insufficient_evidence",
            "rationale": "No contributing factor could be identified from the available data.",
            "evidence_refs": ["anomaly.detail"],
            "confidence": "low",
        })

    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "anomaly": detail,
        "window": {"from_utc": hour_from.isoformat(), "to_utc": hour_to.isoformat()},
        "hour_kpis": {
            "net_sales": sales, "transactions": txns,
            "visitors": visitors, "conversion_rate": conversion,
        },
        "forecast": forecast,
        "hypotheses": hypotheses,
        "last_seq": seq,
    }
