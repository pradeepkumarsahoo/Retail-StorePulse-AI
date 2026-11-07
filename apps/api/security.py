"""Demo-grade auth + RBAC scope enforcement.

Three roles with region/store scope — enforced on every protected endpoint.
Real OIDC integration is a drop-in replacement for `authenticate()`; the
scope-check semantics stay unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from fastapi import Depends, Header, HTTPException, status


# --- Static demo principal store (local-only; real auth is M6/M7) ----------

@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    regions: frozenset[str] = field(default_factory=frozenset)
    stores: frozenset[str] = field(default_factory=frozenset)


DEMO_PRINCIPALS: dict[str, Principal] = {
    # Token : principal
    "demo-admin": Principal(
        subject="alex.admin",
        role="platform_admin",
        regions=frozenset({"*"}),
        stores=frozenset({"*"}),
    ),
    "demo-regional-west": Principal(
        subject="rachel.west",
        role="regional_manager",
        regions=frozenset({"West"}),
        stores=frozenset(),
    ),
    "demo-store-s001": Principal(
        subject="sam.manager",
        role="store_manager",
        regions=frozenset(),
        stores=frozenset({"S001"}),
    ),
    "demo-reviewer": Principal(
        subject="rae.reviewer",
        role="ops_reviewer",
        regions=frozenset({"*"}),
        stores=frozenset({"*"}),
    ),
}

# Roles that may approve/reject recommendations.
APPROVER_ROLES = {"platform_admin", "regional_manager", "ops_reviewer"}


def authenticate(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing Authorization header.")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Expected 'Bearer <token>'.")
    token = authorization.split(" ", 1)[1].strip()
    principal = DEMO_PRINCIPALS.get(token)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid token.")
    return principal


def require_role(*roles: str):
    allowed = set(roles)

    def _dep(p: Principal = Depends(authenticate)) -> Principal:
        if p.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Role '{p.role}' is not permitted here.")
        return p
    return _dep


def principal_can_see_store(principal: Principal, store_id: str, store_region: str) -> bool:
    if "*" in principal.regions:
        return True
    if "*" in principal.stores:
        return True
    if store_id in principal.stores:
        return True
    if store_region in principal.regions:
        return True
    return False


def ensure_store_access(principal: Principal, store_id: str, store_region: str) -> None:
    if not principal_can_see_store(principal, store_id, store_region):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{principal.subject} is not authorized for store {store_id}.",
        )
