from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.engine import Engine

from apps.api.dependencies import get_api_engine
from apps.api.security import Principal, require_role
from packages.governance.audit import list_audit_events
from packages.shared_contracts.api_schemas import AuditEventResponse


router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit", response_model=list[AuditEventResponse])
def audit(
    limit: int = Query(default=100, ge=1, le=500),
    actor: str | None = None,
    target_type: str | None = None,
    engine: Engine = Depends(get_api_engine),
    p: Principal = Depends(require_role("platform_admin", "ops_reviewer")),
) -> list[AuditEventResponse]:
    rows = list_audit_events(engine, limit=limit, actor=actor, target_type=target_type)
    return [AuditEventResponse(**r) for r in rows]
