"""Energy endpoints (forecast-driven HVAC, BMS integration).

- `GET /api/v1/stores/{store_id}/energy/schedule`   — the HVAC timetable the BMS runs today.
- `GET /api/v1/stores/{store_id}/energy/hvac-check` — deterministic per-day join of the
  timetable to occupied hours, meter kWh and trucks (avoidable energy), with lineage.

Both are read-only and store-scoped. The building-management system is never written to.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, authenticate, ensure_store_access
from packages.analytics.kpi_service import list_stores
from packages.energy_planning.service import ENERGY_MAX_HORIZON_DAYS, hvac_schedule_check
from packages.observability import metrics
from packages.shared_contracts.api_schemas import EnergyCheckResponse, HvacScheduleResponse
from services.bms_mcp.server import BmsMcp


router = APIRouter(prefix="/api/v1", tags=["energy"])


def _assert_scope(engine: Engine, p: Principal, store_id: str) -> None:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])


@router.get("/stores/{store_id}/energy/schedule", response_model=HvacScheduleResponse)
def energy_schedule(
    store_id: str,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> HvacScheduleResponse:
    _assert_scope(engine, p, store_id)
    metrics.inc("storepulse_bms_requests_total", endpoint="schedule")
    try:
        out = BmsMcp(engine).call_tool("get_hvac_schedule", {"store_id": store_id})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return HvacScheduleResponse(**out)


@router.get("/stores/{store_id}/energy/hvac-check", response_model=EnergyCheckResponse)
def energy_hvac_check(
    store_id: str,
    as_of_utc: datetime | None = Query(default=None, alias="as_of"),
    horizon_days: int = Query(default=ENERGY_MAX_HORIZON_DAYS, ge=1, le=ENERGY_MAX_HORIZON_DAYS),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> EnergyCheckResponse:
    _assert_scope(engine, p, store_id)
    metrics.inc("storepulse_bms_requests_total", endpoint="hvac_check")
    aso = as_of_utc or datetime.now(timezone.utc)
    return EnergyCheckResponse(**hvac_schedule_check(engine, store_id, aso, horizon_days))
