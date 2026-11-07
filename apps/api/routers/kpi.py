"""KPI endpoints. Every response carries a lineage block."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, authenticate, ensure_store_access
from packages.analytics.kpi_service import (
    compute_atv,
    compute_conversion_rate,
    compute_labor_cost_pct,
    compute_net_sales,
    compute_splh,
    compute_transactions,
    compute_upt,
    compute_visitors,
    list_regions,
    list_stores,
)
from packages.domain.entities import KpiWindow
from packages.observability import metrics
from packages.shared_contracts.api_schemas import (
    KpiResponse,
    LineageResponse,
    StoreResponse,
    WindowResponse,
)
from services.labor_rate_mcp.server import LaborRateMcp


router = APIRouter(prefix="/api/v1", tags=["kpi"])


def _assert_scope(engine: Engine, p: Principal, store_id: str) -> str:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])
    return row["region"]


def _render(kpi, metric_tag: str) -> KpiResponse:
    metrics.inc("storepulse_kpi_requests_total", metric=metric_tag)
    return KpiResponse(
        value=kpi.value,
        unit=kpi.unit,
        window=WindowResponse(from_utc=kpi.window.from_utc, to_utc=kpi.window.to_utc),
        lineage=LineageResponse(
            metric_name=kpi.lineage.metric_name,
            metric_version=kpi.lineage.metric_version,
            formula=kpi.lineage.formula,
            source_tables=list(kpi.lineage.source_tables),
            row_counts=kpi.lineage.row_counts,
            assumptions=list(kpi.lineage.assumptions),
            synthetic_data=kpi.lineage.synthetic_data,
        ),
        status="ok" if kpi.value is not None else "insufficient_evidence",
    )


@router.get("/stores", response_model=list[StoreResponse])
def stores(
    region: str | None = None,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> list[StoreResponse]:
    all_stores = list_stores(engine, region)
    visible = [
        s for s in all_stores
        if "*" in p.regions or s["region"] in p.regions
        or "*" in p.stores or s["store_id"] in p.stores
    ]
    return [StoreResponse(**s) for s in visible]


@router.get("/regions", response_model=list[str])
def regions(engine: Engine = Depends(get_api_engine),
            p: Principal = Depends(authenticate)) -> list[str]:
    rgs = list_regions(engine)
    if "*" in p.regions:
        return rgs
    return [r for r in rgs if r in p.regions or any(
        s["region"] == r and (s["store_id"] in p.stores or "*" in p.stores)
        for s in list_stores(engine))]


def _window(from_utc: datetime, to_utc: datetime) -> KpiWindow:
    return KpiWindow(from_utc=from_utc, to_utc=to_utc)


@router.get("/kpi/splh", response_model=KpiResponse)
def splh(store_id: str = Query(pattern=r"^S\d{3}$"),
         from_utc: datetime = Query(alias="from"),
         to_utc: datetime = Query(alias="to"),
         engine: Engine = Depends(get_api_engine),
         p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_splh(engine, store_id, _window(from_utc, to_utc)), "splh")


@router.get("/kpi/net_sales", response_model=KpiResponse)
def net_sales(store_id: str = Query(pattern=r"^S\d{3}$"),
              from_utc: datetime = Query(alias="from"),
              to_utc: datetime = Query(alias="to"),
              engine: Engine = Depends(get_api_engine),
              p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_net_sales(engine, store_id, _window(from_utc, to_utc)), "net_sales")


@router.get("/kpi/transactions", response_model=KpiResponse)
def transactions_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
                    from_utc: datetime = Query(alias="from"),
                    to_utc: datetime = Query(alias="to"),
                    engine: Engine = Depends(get_api_engine),
                    p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_transactions(engine, store_id, _window(from_utc, to_utc)), "transactions")


@router.get("/kpi/atv", response_model=KpiResponse)
def atv_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
           from_utc: datetime = Query(alias="from"),
           to_utc: datetime = Query(alias="to"),
           engine: Engine = Depends(get_api_engine),
           p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_atv(engine, store_id, _window(from_utc, to_utc)), "atv")


@router.get("/kpi/upt", response_model=KpiResponse)
def upt_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
           from_utc: datetime = Query(alias="from"),
           to_utc: datetime = Query(alias="to"),
           engine: Engine = Depends(get_api_engine),
           p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_upt(engine, store_id, _window(from_utc, to_utc)), "upt")


@router.get("/kpi/visitors", response_model=KpiResponse)
def visitors_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
                from_utc: datetime = Query(alias="from"),
                to_utc: datetime = Query(alias="to"),
                engine: Engine = Depends(get_api_engine),
                p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_visitors(engine, store_id, _window(from_utc, to_utc)), "visitors")


@router.get("/kpi/conversion_rate", response_model=KpiResponse)
def conversion_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
                  from_utc: datetime = Query(alias="from"),
                  to_utc: datetime = Query(alias="to"),
                  engine: Engine = Depends(get_api_engine),
                  p: Principal = Depends(authenticate)) -> KpiResponse:
    _assert_scope(engine, p, store_id)
    return _render(compute_conversion_rate(engine, store_id, _window(from_utc, to_utc)),
                   "conversion_rate")


@router.get("/kpi/labor_cost_pct", response_model=KpiResponse)
def labor_cost_pct_ep(store_id: str = Query(pattern=r"^S\d{3}$"),
                      from_utc: datetime = Query(alias="from"),
                      to_utc: datetime = Query(alias="to"),
                      engine: Engine = Depends(get_api_engine),
                      p: Principal = Depends(authenticate)) -> KpiResponse:
    region = _assert_scope(engine, p, store_id)
    mcp = LaborRateMcp(engine)
    rate = mcp.call_tool("get_labor_rate", {
        "region": region, "role_category": "associate",
        "effective_date": from_utc.strftime("%Y-%m-%d"),
    })
    return _render(
        compute_labor_cost_pct(engine, store_id, _window(from_utc, to_utc),
                               rate_per_hour_usd=rate["rate_per_hour_usd"]),
        "labor_cost_pct",
    )


@router.get("/metric-catalog")
def metric_catalog(engine: Engine = Depends(get_api_engine),
                   p: Principal = Depends(authenticate)) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT metric_name, metric_version, unit, formula, source_tables, owner, "
                "description FROM metric_catalog ORDER BY metric_name"
            )
        ).mappings().all()
    return [dict(r) for r in rows]
