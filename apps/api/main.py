"""FastAPI entrypoint for the M1 vertical slice.

Three endpoints:
  GET /api/v1/health
  GET /api/v1/stores
  GET /api/v1/kpi/splh?store_id=&from=&to=

Plus a static HTML tile at / that calls the KPI endpoint.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Engine

from infrastructure.db.engine import get_engine, init_schema
from packages.analytics.kpi_service import compute_splh, list_stores
from packages.domain.entities import KpiWindow
from packages.shared_contracts.api_schemas import (
    HealthResponse,
    KpiResponse,
    LineageResponse,
    StoreResponse,
    WindowResponse,
)

API_VERSION = "0.1.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s cid=%(message)s")
log = logging.getLogger("storepulse.api")

app = FastAPI(
    title="StorePulse-AI API",
    description="M1 vertical slice: Regional Command Center — SPLH KPI service.",
    version=API_VERSION,
)

_engine: Engine | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = get_engine()
        init_schema(_engine)
    return _engine


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:12]
    request.state.cid = cid
    response = await call_next(request)
    response.headers["x-correlation-id"] = cid
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_request",
            "message": str(exc),
            "correlation_id": getattr(request.state, "cid", None),
        },
    )


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=API_VERSION)


@app.get("/api/v1/stores", response_model=list[StoreResponse])
def stores(engine: Engine = Depends(_get_engine)) -> list[StoreResponse]:
    return [StoreResponse(**row) for row in list_stores(engine)]


@app.get("/api/v1/kpi/splh", response_model=KpiResponse)
def splh(
    store_id: str = Query(..., pattern=r"^S\d{3}$"),
    from_utc: datetime = Query(..., alias="from"),
    to_utc: datetime = Query(..., alias="to"),
    engine: Engine = Depends(_get_engine),
) -> KpiResponse:
    # Verify the store exists — any unknown id is 404, not a silent zero value.
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM store WHERE store_id = :sid"), {"sid": store_id}
        ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")

    window = KpiWindow(from_utc=from_utc, to_utc=to_utc)
    result = compute_splh(engine, store_id, window)

    return KpiResponse(
        value=result.value,
        unit=result.unit,
        window=WindowResponse(from_utc=window.from_utc, to_utc=window.to_utc),
        lineage=LineageResponse(
            metric_name=result.lineage.metric_name,
            metric_version=result.lineage.metric_version,
            formula=result.lineage.formula,
            source_tables=list(result.lineage.source_tables),
            row_counts=result.lineage.row_counts,
            assumptions=list(result.lineage.assumptions),
            synthetic_data=result.lineage.synthetic_data,
        ),
        status="ok" if result.value is not None else "insufficient_evidence",
    )


# Static tile — must come last so it does not shadow /api/v1/*.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
