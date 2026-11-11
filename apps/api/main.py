"""FastAPI entrypoint: full v1.0 surface.

Routers: health, kpi, forecast, inbound (TMS), energy (BMS), anomaly, agents, recommendations,
audit, metrics, plus a static UI at /.

Cross-cutting:
- Correlation ID on every response.
- Structured JSON logs (packages.observability.logging).
- Prometheus-style counters/histograms (packages.observability.metrics).
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.routers import agents as agents_router
from apps.api.routers import anomaly as anomaly_router
from apps.api.routers import audit as audit_router
from apps.api.routers import energy as energy_router
from apps.api.routers import forecast as forecast_router
from apps.api.routers import inbound as inbound_router
from apps.api.routers import kpi as kpi_router
from apps.api.routers import metrics_router
from apps.api.routers import recommendations as reco_router
from packages.observability import metrics
from packages.observability.logging import configure_logging, set_correlation_id
from packages.shared_contracts.api_schemas import HealthResponse


API_VERSION = "1.1.0"
configure_logging()
log = logging.getLogger("storepulse.api")


app = FastAPI(
    title="StorePulse-AI API",
    description="Governed retail decision intelligence — v1.0.",
    version=API_VERSION,
)


@app.middleware("http")
async def correlation_and_metrics(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:12]
    request.state.cid = cid
    set_correlation_id(cid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = int((time.perf_counter() - started) * 1000)
        metrics.inc("storepulse_http_requests_total", method=request.method,
                    path=request.url.path, status="500")
        metrics.observe_ms("storepulse_http_request_ms", duration,
                           method=request.method, path=request.url.path)
        log.exception("unhandled_error", extra={"path": request.url.path})
        raise
    duration = int((time.perf_counter() - started) * 1000)
    response.headers["x-correlation-id"] = cid
    metrics.inc("storepulse_http_requests_total", method=request.method,
                path=request.url.path, status=str(response.status_code))
    metrics.observe_ms("storepulse_http_request_ms", duration,
                       method=request.method, path=request.url.path)
    log.info("http_request", extra={
        "method": request.method, "path": request.url.path,
        "status": response.status_code, "duration_ms": duration,
    })
    set_correlation_id(None)
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


@app.get("/api/v1/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=API_VERSION)


# Routers
app.include_router(kpi_router.router)
app.include_router(forecast_router.router)
app.include_router(inbound_router.router)
app.include_router(energy_router.router)
app.include_router(anomaly_router.router)
app.include_router(agents_router.router)
app.include_router(reco_router.router)
app.include_router(audit_router.router)
app.include_router(metrics_router.router)


# Static UI last so it does not shadow /api/v1/*.
_STATIC = Path(__file__).parent / "static"
if _STATIC.exists():
    app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
