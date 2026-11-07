from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, authenticate, ensure_store_access
from packages.analytics.kpi_service import list_stores
from packages.forecasting.service import get_forecast, run_forecast
from packages.shared_contracts.api_schemas import ForecastPointResponse


router = APIRouter(prefix="/api/v1", tags=["forecast"])


@router.get("/stores/{store_id}/workforce/forecast", response_model=list[ForecastPointResponse])
def workforce_forecast(
    store_id: str,
    from_utc: datetime = Query(alias="from"),
    to_utc: datetime = Query(alias="to"),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> list[ForecastPointResponse]:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])
    pts = get_forecast(engine, store_id, from_utc, to_utc, kind="labor_minutes")
    return [ForecastPointResponse(**p) for p in pts]
