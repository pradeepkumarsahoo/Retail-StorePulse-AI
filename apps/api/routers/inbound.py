"""Inbound logistics endpoints (TMS integration).

- `GET /api/v1/stores/{store_id}/inbound/shipments` — the TMS 5-day truck outlook.
- `GET /api/v1/stores/{store_id}/inbound/labor-plan` — deterministic per-day join
  of trucks to the labor plan (idle risk / under-coverage), with lineage.

Both are read-only and store-scoped. The TMS itself is never written to.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, authenticate, ensure_store_access
from packages.analytics.kpi_service import list_stores
from packages.inbound_planning.service import labor_plan_check, list_inbound_shipments
from packages.observability import metrics
from packages.shared_contracts.api_schemas import (
    InboundLaborPlanResponse,
    InboundShipmentResponse,
)
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS


router = APIRouter(prefix="/api/v1", tags=["inbound"])


def _assert_scope(engine: Engine, p: Principal, store_id: str) -> None:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])


@router.get("/stores/{store_id}/inbound/shipments", response_model=list[InboundShipmentResponse])
def inbound_shipments(
    store_id: str,
    from_utc: datetime = Query(alias="from"),
    to_utc: datetime = Query(alias="to"),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> list[InboundShipmentResponse]:
    _assert_scope(engine, p, store_id)
    metrics.inc("storepulse_tms_requests_total", endpoint="shipments")
    rows = list_inbound_shipments(engine, store_id, from_utc, to_utc)
    return [InboundShipmentResponse(**r) for r in rows]


@router.get("/stores/{store_id}/inbound/labor-plan", response_model=InboundLaborPlanResponse)
def inbound_labor_plan(
    store_id: str,
    as_of_utc: datetime | None = Query(default=None, alias="as_of"),
    horizon_days: int = Query(default=TMS_MAX_HORIZON_DAYS, ge=1, le=TMS_MAX_HORIZON_DAYS),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> InboundLaborPlanResponse:
    _assert_scope(engine, p, store_id)
    metrics.inc("storepulse_tms_requests_total", endpoint="labor_plan")
    aso = as_of_utc or datetime.now(timezone.utc)
    out = labor_plan_check(engine, store_id, aso, horizon_days)
    return InboundLaborPlanResponse(**out)
