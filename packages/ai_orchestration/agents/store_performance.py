"""Store Performance Analyst agent.

Retrieves authorized store KPIs, identifies gaps vs comparable period, and
returns a structured finding with evidence references. Only read-only tools.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from packages.tool_gateway import gateway
from packages.tool_gateway.tools import Context


AGENT_NAME = "store_performance"
PROMPT_VERSION = "1.0.0"


def run(ctx: Context, run_id: str, store_id: str, from_utc: datetime, to_utc: datetime,
        start_seq: int = 0) -> dict:
    seq = start_seq
    window = {"from_utc": from_utc.isoformat(), "to_utc": to_utc.isoformat()}
    kpis: dict[str, dict] = {}
    for tool in ("kpi.net_sales", "kpi.transactions", "kpi.atv", "kpi.upt",
                 "kpi.splh", "kpi.visitors", "kpi.conversion_rate"):
        seq += 1
        res = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, tool, {
            "store_id": store_id, "from_utc": from_utc, "to_utc": to_utc,
        })
        kpis[tool.split(".")[-1]] = res

    # Comparable prior period of equal length ending at from_utc
    span = to_utc - from_utc
    prior_from = from_utc - span
    prior_to = from_utc
    prior_kpis: dict[str, dict] = {}
    for tool in ("kpi.net_sales", "kpi.splh"):
        seq += 1
        res = gateway.call_tool(ctx, run_id, seq, AGENT_NAME, tool, {
            "store_id": store_id, "from_utc": prior_from, "to_utc": prior_to,
        })
        prior_kpis[tool.split(".")[-1]] = res

    cur_sales = kpis["net_sales"]["value"]
    prev_sales = prior_kpis["net_sales"]["value"]
    sales_change_pct = (
        None if prev_sales in (None, 0) or cur_sales is None
        else round((cur_sales - prev_sales) / prev_sales, 4)
    )
    return {
        "agent": AGENT_NAME,
        "prompt_version": PROMPT_VERSION,
        "store_id": store_id,
        "window": window,
        "prior_window": {"from_utc": prior_from.isoformat(), "to_utc": prior_to.isoformat()},
        "kpis": kpis,
        "prior_kpis": prior_kpis,
        "derived": {"sales_change_pct": sales_change_pct},
        "last_seq": seq,
    }
