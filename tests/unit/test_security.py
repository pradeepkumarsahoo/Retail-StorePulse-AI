"""Unit tests for RBAC primitives in apps.api.security.

The scope check is the single source of truth for which principal can see
which store, so every path through `principal_can_see_store` is pinned here.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.security import (
    APPROVER_ROLES,
    DEMO_PRINCIPALS,
    Principal,
    authenticate,
    ensure_store_access,
    principal_can_see_store,
    require_role,
)


def _p(role: str, regions=frozenset(), stores=frozenset()) -> Principal:
    return Principal(subject="x", role=role, regions=regions, stores=stores)


# --- principal_can_see_store ------------------------------------------------

def test_wildcard_region_sees_any_store():
    p = _p("platform_admin", regions=frozenset({"*"}))
    assert principal_can_see_store(p, "S999", "Mars")


def test_wildcard_store_sees_any_store():
    p = _p("ops_reviewer", stores=frozenset({"*"}))
    assert principal_can_see_store(p, "S999", "Mars")


def test_explicit_region_match():
    p = _p("regional_manager", regions=frozenset({"West"}))
    assert principal_can_see_store(p, "S001", "West")
    assert not principal_can_see_store(p, "S002", "Midwest")


def test_explicit_store_match():
    p = _p("store_manager", stores=frozenset({"S001"}))
    assert principal_can_see_store(p, "S001", "West")
    assert not principal_can_see_store(p, "S002", "West")


def test_no_match_denies():
    p = _p("store_manager", regions=frozenset(), stores=frozenset({"S001"}))
    assert not principal_can_see_store(p, "S002", "Midwest")


# --- ensure_store_access ----------------------------------------------------

def test_ensure_store_access_raises_403_when_denied():
    p = _p("regional_manager", regions=frozenset({"West"}))
    with pytest.raises(HTTPException) as e:
        ensure_store_access(p, "S002", "Midwest")
    assert e.value.status_code == 403
    assert "S002" in e.value.detail


def test_ensure_store_access_allows_when_scoped():
    p = _p("regional_manager", regions=frozenset({"West"}))
    # No exception = pass
    ensure_store_access(p, "S001", "West")


# --- authenticate -----------------------------------------------------------

def test_authenticate_missing_header_401():
    with pytest.raises(HTTPException) as e:
        authenticate(authorization=None)
    assert e.value.status_code == 401


def test_authenticate_wrong_scheme_401():
    with pytest.raises(HTTPException) as e:
        authenticate(authorization="Basic abc")
    assert e.value.status_code == 401


def test_authenticate_unknown_token_401():
    with pytest.raises(HTTPException) as e:
        authenticate(authorization="Bearer not-a-real-token")
    assert e.value.status_code == 401


def test_authenticate_known_token_returns_principal():
    p = authenticate(authorization="Bearer demo-admin")
    assert p.subject == "alex.admin"
    assert p.role == "platform_admin"


# --- require_role -----------------------------------------------------------

def test_require_role_allows_matching_role():
    dep = require_role("platform_admin")
    p = DEMO_PRINCIPALS["demo-admin"]
    assert dep(p=p) is p


def test_require_role_rejects_non_matching_role():
    dep = require_role("ops_reviewer")
    p = DEMO_PRINCIPALS["demo-store-s001"]  # store_manager
    with pytest.raises(HTTPException) as e:
        dep(p=p)
    assert e.value.status_code == 403


# --- APPROVER_ROLES ---------------------------------------------------------

def test_store_manager_is_not_an_approver():
    """The invariant /api/v1/recommendations/.../review enforces at a demo level."""
    assert "store_manager" not in APPROVER_ROLES
    assert "platform_admin" in APPROVER_ROLES
    assert "regional_manager" in APPROVER_ROLES
    assert "ops_reviewer" in APPROVER_ROLES
