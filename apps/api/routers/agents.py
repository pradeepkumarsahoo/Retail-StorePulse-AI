"""Agent endpoints: investigate a store, plan inbound labor (TMS), investigate an
anomaly, generate a briefing.

Every call creates an AiRun and, where appropriate, a pending-review
Recommendation. Nothing moves in a source system — recommendations route to
human reviewers in /api/v1/recommendations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine, get_supervisor
from apps.api.security import Principal, authenticate, ensure_store_access
from packages.ai_orchestration.supervisor import Supervisor
from packages.analytics.kpi_service import list_stores
from packages.governance.approval_workflow import create_recommendation
from packages.shared_contracts.api_schemas import AgentRunResponse
from packages.tool_gateway.tools import Context
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS


router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("/investigate-store", response_model=AgentRunResponse)
def investigate_store(
    store_id: str = Query(pattern=r"^S\d{3}$"),
    from_utc: datetime = Query(alias="from"),
    to_utc: datetime = Query(alias="to"),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
    sup: Supervisor = Depends(get_supervisor),
) -> AgentRunResponse:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])
    ctx = Context(engine=engine, principal=p)
    result = sup.investigate_store(ctx, store_id, from_utc, to_utc)
    # Persist the advisor's recommendation as pending_review.
    if result.status == "ok" and result.output.get("advisor"):
        rec = result.output["advisor"]["recommendation"]
        rec["status"] = "pending_review"
        create_recommendation(engine, rec, created_by=p.subject, run_id=result.run_id)
    return AgentRunResponse(
        run_id=result.run_id, status=result.status, intent=result.intent,
        output=result.output or None, tokens_used=result.tokens_used,
        step_count=result.step_count, error=result.error,
    )


@router.post("/plan-inbound-labor", response_model=AgentRunResponse)
def plan_inbound_labor(
    store_id: str = Query(pattern=r"^S\d{3}$"),
    as_of_utc: datetime | None = Query(default=None, alias="as_of"),
    horizon_days: int = Query(default=TMS_MAX_HORIZON_DAYS, ge=1, le=TMS_MAX_HORIZON_DAYS),
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
    sup: Supervisor = Depends(get_supervisor),
) -> AgentRunResponse:
    """TMS-driven labor planning: 5-day truck outlook → labor plan check → recommendation.

    The Inbound Logistics Planner reads the TMS mirror through the gateway, the
    Workforce Analyst adds forecast + rate context, and the Operations Advisor
    drafts a pending-review recommendation. No schedule is changed.
    """
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])
    ctx = Context(engine=engine, principal=p)
    aso = as_of_utc or datetime.now(timezone.utc)
    result = sup.plan_inbound_labor(ctx, store_id, aso, horizon_days=horizon_days)
    if result.status == "ok" and result.output.get("advisor"):
        rec = result.output["advisor"]["recommendation"]
        rec["status"] = "pending_review"
        create_recommendation(engine, rec, created_by=p.subject, run_id=result.run_id)
    return AgentRunResponse(
        run_id=result.run_id, status=result.status, intent=result.intent,
        output=result.output or None, tokens_used=result.tokens_used,
        step_count=result.step_count, error=result.error,
    )


@router.post("/investigate-anomaly", response_model=AgentRunResponse)
def investigate_anomaly(
    anomaly_id: str,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
    sup: Supervisor = Depends(get_supervisor),
) -> AgentRunResponse:
    ctx = Context(engine=engine, principal=p)
    result = sup.investigate_anomaly(ctx, anomaly_id)
    return AgentRunResponse(
        run_id=result.run_id, status=result.status, intent=result.intent,
        output=result.output or None, tokens_used=result.tokens_used,
        step_count=result.step_count, error=result.error,
    )


@router.post("/briefing", response_model=AgentRunResponse)
def daily_briefing(
    store_id: str = Query(pattern=r"^S\d{3}$"),
    as_of_utc: datetime | None = None,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
    sup: Supervisor = Depends(get_supervisor),
) -> AgentRunResponse:
    row = next((s for s in list_stores(engine) if s["store_id"] == store_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown store_id: {store_id}")
    ensure_store_access(p, store_id, row["region"])
    ctx = Context(engine=engine, principal=p)
    aso = as_of_utc or datetime.now(timezone.utc)
    result = sup.daily_briefing(ctx, store_id, aso)
    return AgentRunResponse(
        run_id=result.run_id, status=result.status, intent=result.intent,
        output=result.output or None, tokens_used=result.tokens_used,
        step_count=result.step_count, error=result.error,
    )
