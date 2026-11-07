"""Allowlisted, typed tools the agent gateway can expose.

Each tool:
- Declares a JSON-schema-like `args_schema` (Pydantic).
- Declares a `result_schema` (Pydantic).
- Is read-only. No tool here mutates sales, labor, schedules, pay or systems.
- Receives a bound `Context` with the engine and principal — never raw auth
  headers or user text. Arguments are validated BEFORE the implementation runs.

Tool descriptions are not security. The scope check in the gateway is.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from apps.api.security import Principal, principal_can_see_store
from packages.analytics.kpi_service import (
    compute_atv,
    compute_conversion_rate,
    compute_labor_cost_pct,
    compute_net_sales,
    compute_splh,
    compute_transactions,
    compute_upt,
    compute_visitors,
    list_stores,
)
from packages.anomaly_detection.detector import get_anomaly, list_anomalies
from packages.domain.entities import KpiWindow
from packages.forecasting.service import get_forecast
from services.labor_rate_mcp.server import LaborRateMcp


@dataclass
class Context:
    engine: Engine
    principal: Principal


# --- Argument / result schemas --------------------------------------------

class WindowArgs(BaseModel):
    store_id: str = Field(pattern=r"^S\d{3}$")
    from_utc: datetime
    to_utc: datetime


class KpiResultModel(BaseModel):
    metric_name: str
    metric_version: str
    value: float | None
    unit: str
    row_counts: dict[str, int]
    formula: str


class ListStoresArgs(BaseModel):
    region: str | None = None


class StoresResultModel(BaseModel):
    stores: list[dict]


class ForecastArgs(BaseModel):
    store_id: str = Field(pattern=r"^S\d{3}$")
    from_utc: datetime
    to_utc: datetime
    kind: str = "labor_minutes"


class ForecastResultModel(BaseModel):
    points: list[dict]


class AnomalyListArgs(BaseModel):
    store_id: str | None = None
    status: str = "open"
    limit: int = Field(default=50, ge=1, le=200)


class AnomalyDetailArgs(BaseModel):
    anomaly_id: str


class AnomalyModel(BaseModel):
    anomalies: list[dict]


class LaborRateArgs(BaseModel):
    region: str
    role_category: str = "associate"
    effective_date: str  # YYYY-MM-DD


class LaborRateResult(BaseModel):
    region: str
    role_category: str
    effective_from: str
    rate_per_hour_usd: float
    currency: str
    source: str
    version: str


# --- Tool implementations --------------------------------------------------

def _scope_check(ctx: Context, store_id: str) -> None:
    stores = list_stores(ctx.engine)
    region = next((s["region"] for s in stores if s["store_id"] == store_id), None)
    if region is None:
        raise ValueError(f"Unknown store_id: {store_id}")
    if not principal_can_see_store(ctx.principal, store_id, region):
        raise PermissionError(f"Principal lacks scope for store {store_id}")


def _kpi_to_model(name: str, kpi) -> dict:
    return {
        "metric_name": kpi.lineage.metric_name,
        "metric_version": kpi.lineage.metric_version,
        "value": kpi.value,
        "unit": kpi.unit,
        "row_counts": kpi.lineage.row_counts,
        "formula": kpi.lineage.formula,
    }


def tool_kpi_splh(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("splh", compute_splh(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_atv(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("atv", compute_atv(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_upt(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("upt", compute_upt(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_net_sales(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("net_sales", compute_net_sales(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_transactions(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("transactions", compute_transactions(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_conversion_rate(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("conversion_rate", compute_conversion_rate(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_visitors(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    return _kpi_to_model("visitors", compute_visitors(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc)
    ))


def tool_kpi_labor_cost_pct(ctx: Context, args: WindowArgs) -> dict:
    _scope_check(ctx, args.store_id)
    stores = list_stores(ctx.engine)
    region = next(s["region"] for s in stores if s["store_id"] == args.store_id)
    mcp = LaborRateMcp(ctx.engine)
    rate = mcp.call_tool("get_labor_rate", {
        "region": region,
        "role_category": "associate",
        "effective_date": args.from_utc.strftime("%Y-%m-%d"),
    })
    kpi = compute_labor_cost_pct(
        ctx.engine, args.store_id, KpiWindow(args.from_utc, args.to_utc),
        rate_per_hour_usd=rate["rate_per_hour_usd"],
    )
    return _kpi_to_model("labor_cost_pct", kpi)


def tool_forecast_labor(ctx: Context, args: ForecastArgs) -> dict:
    _scope_check(ctx, args.store_id)
    pts = get_forecast(ctx.engine, args.store_id, args.from_utc, args.to_utc, args.kind)
    # Serialize datetime
    out = []
    for p in pts:
        d = dict(p)
        d["ts_utc"] = d["ts_utc"].isoformat() if hasattr(d["ts_utc"], "isoformat") else str(d["ts_utc"])
        out.append(d)
    return {"points": out}


def tool_list_anomalies(ctx: Context, args: AnomalyListArgs) -> dict:
    if args.store_id:
        _scope_check(ctx, args.store_id)
    anomalies = list_anomalies(ctx.engine, args.store_id, args.status, args.limit)
    # Filter out stores the principal cannot see
    visible_store_ids = {
        s["store_id"] for s in list_stores(ctx.engine)
        if principal_can_see_store(ctx.principal, s["store_id"], s["region"])
    }
    filtered = [a for a in anomalies if a["store_id"] in visible_store_ids]
    for a in filtered:
        a["ts_utc"] = a["ts_utc"].isoformat() if hasattr(a["ts_utc"], "isoformat") else str(a["ts_utc"])
        a["created_at_utc"] = (
            a["created_at_utc"].isoformat() if hasattr(a["created_at_utc"], "isoformat")
            else str(a["created_at_utc"])
        )
    return {"anomalies": filtered}


def tool_anomaly_detail(ctx: Context, args: AnomalyDetailArgs) -> dict:
    anom = get_anomaly(ctx.engine, args.anomaly_id)
    if not anom:
        raise ValueError(f"Unknown anomaly_id: {args.anomaly_id}")
    _scope_check(ctx, anom["store_id"])
    anom["ts_utc"] = anom["ts_utc"].isoformat() if hasattr(anom["ts_utc"], "isoformat") else str(anom["ts_utc"])
    anom["created_at_utc"] = (
        anom["created_at_utc"].isoformat() if hasattr(anom["created_at_utc"], "isoformat")
        else str(anom["created_at_utc"])
    )
    return {"anomaly": anom}


def tool_list_stores(ctx: Context, args: ListStoresArgs) -> dict:
    stores = list_stores(ctx.engine, args.region)
    visible = [s for s in stores if principal_can_see_store(ctx.principal, s["store_id"], s["region"])]
    return {"stores": visible}


def tool_mcp_labor_rate(ctx: Context, args: LaborRateArgs) -> dict:
    mcp = LaborRateMcp(ctx.engine)
    return mcp.call_tool("get_labor_rate", {
        "region": args.region,
        "role_category": args.role_category,
        "effective_date": args.effective_date,
    })


# --- Allowlist ------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    impl: Callable[[Context, BaseModel], dict]


TOOLS: dict[str, ToolSpec] = {
    "stores.list":          ToolSpec("stores.list",          "List stores visible to the caller.",                   ListStoresArgs,    tool_list_stores),
    "kpi.splh":             ToolSpec("kpi.splh",             "Sales per labor hour for one store.",                  WindowArgs,        tool_kpi_splh),
    "kpi.net_sales":        ToolSpec("kpi.net_sales",        "Net sales in USD for one store.",                      WindowArgs,        tool_kpi_net_sales),
    "kpi.transactions":     ToolSpec("kpi.transactions",     "Transaction count for one store.",                     WindowArgs,        tool_kpi_transactions),
    "kpi.atv":              ToolSpec("kpi.atv",              "Average transaction value for one store.",             WindowArgs,        tool_kpi_atv),
    "kpi.upt":              ToolSpec("kpi.upt",              "Units per transaction for one store.",                 WindowArgs,        tool_kpi_upt),
    "kpi.visitors":         ToolSpec("kpi.visitors",         "Footfall for one store.",                              WindowArgs,        tool_kpi_visitors),
    "kpi.conversion_rate":  ToolSpec("kpi.conversion_rate",  "Conversion rate for one store.",                       WindowArgs,        tool_kpi_conversion_rate),
    "kpi.labor_cost_pct":   ToolSpec("kpi.labor_cost_pct",   "Labor cost percentage of net sales for one store.",    WindowArgs,        tool_kpi_labor_cost_pct),
    "forecast.labor":       ToolSpec("forecast.labor",       "Hourly labor-minute forecast for one store.",          ForecastArgs,      tool_forecast_labor),
    "anomaly.list":         ToolSpec("anomaly.list",         "List open anomalies visible to the caller.",           AnomalyListArgs,   tool_list_anomalies),
    "anomaly.detail":       ToolSpec("anomaly.detail",       "Fetch the evidence record for one anomaly.",           AnomalyDetailArgs, tool_anomaly_detail),
    "mcp.labor_rate.get":   ToolSpec("mcp.labor_rate.get",   "Approved labor-rate reference via the MCP adapter.",   LaborRateArgs,     tool_mcp_labor_rate),
}


# Per-agent allowlists — each specialist sees only what it needs.
AGENT_ALLOWLISTS: dict[str, frozenset[str]] = {
    "store_performance":   frozenset({"kpi.splh", "kpi.net_sales", "kpi.transactions", "kpi.atv", "kpi.upt", "kpi.visitors", "kpi.conversion_rate", "stores.list"}),
    "workforce":           frozenset({"kpi.splh", "kpi.labor_cost_pct", "forecast.labor", "mcp.labor_rate.get", "stores.list"}),
    "anomaly_investigator":frozenset({"anomaly.list", "anomaly.detail", "kpi.net_sales", "kpi.splh", "kpi.transactions", "kpi.visitors", "kpi.conversion_rate", "forecast.labor"}),
    "operations_advisor":  frozenset({"kpi.splh", "kpi.net_sales", "kpi.labor_cost_pct", "kpi.conversion_rate", "anomaly.list", "forecast.labor", "mcp.labor_rate.get"}),
    "briefing":            frozenset({"kpi.splh", "kpi.net_sales", "kpi.transactions", "kpi.atv", "kpi.conversion_rate", "anomaly.list", "forecast.labor"}),
    # Supervisor itself does not call tools; it dispatches.
}
