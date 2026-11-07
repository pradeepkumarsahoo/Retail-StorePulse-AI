from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, authenticate, principal_can_see_store
from packages.analytics.kpi_service import list_stores
from packages.anomaly_detection.detector import get_anomaly, list_anomalies
from packages.shared_contracts.api_schemas import AnomalyResponse


router = APIRouter(prefix="/api/v1", tags=["anomaly"])


@router.get("/anomalies", response_model=list[AnomalyResponse])
def anomalies(
    store_id: str | None = None,
    status: str = "open",
    limit: int = Query(default=100, ge=1, le=500),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> list[AnomalyResponse]:
    rows = list_anomalies(engine, store_id=store_id, status=status, limit=limit)
    stores = {s["store_id"]: s["region"] for s in list_stores(engine)}
    visible = [r for r in rows if principal_can_see_store(p, r["store_id"], stores.get(r["store_id"], ""))]
    return [AnomalyResponse(**r) for r in visible]


@router.get("/anomalies/{anomaly_id}", response_model=AnomalyResponse)
def anomaly_detail(
    anomaly_id: str,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> AnomalyResponse:
    row = get_anomaly(engine, anomaly_id)
    if not row:
        raise HTTPException(status_code=404, detail="anomaly not found")
    stores = {s["store_id"]: s["region"] for s in list_stores(engine)}
    if not principal_can_see_store(p, row["store_id"], stores.get(row["store_id"], "")):
        raise HTTPException(status_code=403, detail="scope denied")
    return AnomalyResponse(**row)
