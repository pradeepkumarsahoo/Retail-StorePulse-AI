"""Recommendation + approval endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import (
    APPROVER_ROLES,
    Principal,
    authenticate,
    principal_can_see_store,
    require_role,
)
from packages.analytics.kpi_service import list_stores
from packages.governance.approval_workflow import (
    InvalidTransition,
    NotFound,
    get_recommendation,
    list_recommendations,
    transition,
)
from packages.shared_contracts.api_schemas import (
    ApprovalResponse,
    RecommendationResponse,
    ReviewRequest,
)


router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


_DECISION_MAP = {
    "approve": "approved",
    "reject": "rejected",
    "start": "in_progress",
    "complete": "completed",
}


@router.get("", response_model=list[RecommendationResponse])
def listing(
    status: str | None = None,
    store_id: str | None = None,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> list[RecommendationResponse]:
    rows = list_recommendations(engine, status=status, store_id=store_id)
    stores = {s["store_id"]: s["region"] for s in list_stores(engine)}
    visible = [r for r in rows if principal_can_see_store(p, r["store_id"], stores.get(r["store_id"], ""))]
    return [RecommendationResponse(**r) for r in visible]


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
def detail(
    recommendation_id: str,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(authenticate),
) -> RecommendationResponse:
    row = get_recommendation(engine, recommendation_id)
    if not row:
        raise HTTPException(status_code=404, detail="recommendation not found")
    stores = {s["store_id"]: s["region"] for s in list_stores(engine)}
    if not principal_can_see_store(p, row["store_id"], stores.get(row["store_id"], "")):
        raise HTTPException(status_code=403, detail="scope denied")
    return RecommendationResponse(**row)


@router.post("/{recommendation_id}/review", response_model=ApprovalResponse)
def review(
    recommendation_id: str,
    body: ReviewRequest,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(require_role(*APPROVER_ROLES)),
) -> ApprovalResponse:
    if body.decision not in _DECISION_MAP:
        raise HTTPException(status_code=422, detail="unknown decision")
    try:
        res = transition(engine, recommendation_id, _DECISION_MAP[body.decision],
                         actor=p.subject, comment=body.comment)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ApprovalResponse(**res)
