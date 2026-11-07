"""Store Briefing Generator agent.

Composes a role-specific daily briefing from already-approved evidence.
Introduces no new facts; adds no unsupported causal claims.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.ai_orchestration.llm_provider import LlmProvider
from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "briefing"
PROMPT_VERSION = "1.0.0"


def run(ctx: Context, run_id: str, store_id: str, as_of_utc: datetime,
        llm: LlmProvider, start_seq: int = 0) -> dict:
    """Produce a concise daily briefing for `store_id`.

    The briefing summarises yesterday's performance and open anomalies.
    The LLM only composes natural language from structured evidence; the
    numbers come from tool calls.
    """
    seq = start_seq
    yesterday = as_of_utc - timedelta(days=1)
    day_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    day_to = day_from + timedelta(days=1)

    seq += 1
    net_sales = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.net_sales", {
        "store_id": store_id, "from_utc": day_from, "to_utc": day_to})
    seq += 1
    txns = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.transactions", {
        "store_id": store_id, "from_utc": day_from, "to_utc": day_to})
    seq += 1
    splh = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.splh", {
        "store_id": store_id, "from_utc": day_from, "to_utc": day_to})
    seq += 1
    atv = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "kpi.atv", {
        "store_id": store_id, "from_utc": day_from, "to_utc": day_to})
    seq += 1
    anomalies = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, "anomaly.list", {
        "store_id": store_id, "status": "open", "limit": 5})

    evidence = {
        "store_id": store_id,
        "window": {"from_utc": day_from.isoformat(), "to_utc": day_to.isoformat()},
        "kpis": {
            "net_sales_usd": net_sales,
            "transactions": txns,
            "splh": splh,
            "atv_usd": atv,
        },
        "anomalies": anomalies["anomalies"],
    }
    resp = llm.summarize(
        system_prompt=(
            "You are a store operations briefing composer. Summarise the evidence "
            "provided. Do not invent numbers. Do not state causal claims that are "
            "not already in the evidence. Flag anything marked insufficient."
        ),
        user_prompt=f"Compose a brief for {store_id} covering {day_from.date().isoformat()}.",
        evidence=evidence,
    )
    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "store_id": store_id,
        "as_of_utc": as_of_utc.isoformat(),
        "window": evidence["window"],
        "evidence": evidence,
        "narrative": resp.text,
        "model": {"name": resp.model_name, "version": resp.model_version,
                  "tokens_used": resp.tokens_used},
        "last_seq": seq,
    }
