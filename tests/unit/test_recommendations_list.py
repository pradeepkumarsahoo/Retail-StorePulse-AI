"""Unit tests for list_recommendations and get_recommendation."""
from __future__ import annotations

import uuid

from packages.governance.approval_workflow import (
    create_recommendation,
    get_recommendation,
    list_recommendations,
    transition,
)


def _mk(store_id: str = "S001", title: str = "Review") -> dict:
    return {
        "recommendation_id": str(uuid.uuid4()),
        "store_id": store_id,
        "title": title,
        "rationale": "Why",
        "evidence": {"splh": 20.0},
        "expected_direction": "up",
        "confidence": "medium",
        "risk": None,
        "status": "pending_review",
        "prompt_version": "1.0.0",
    }


def test_list_default_returns_all(engine):
    create_recommendation(engine, _mk("S001"), created_by="a")
    create_recommendation(engine, _mk("S002"), created_by="a")
    out = list_recommendations(engine)
    assert len(out) >= 2


def test_list_filters_by_status(engine):
    a = _mk("S001"); b = _mk("S001")
    create_recommendation(engine, a, created_by="x")
    rid_b = create_recommendation(engine, b, created_by="x")
    transition(engine, rid_b, "approved", actor="rae.reviewer")

    pending = list_recommendations(engine, status="pending_review")
    approved = list_recommendations(engine, status="approved")
    assert all(r["status"] == "pending_review" for r in pending)
    assert all(r["status"] == "approved" for r in approved)
    assert any(r["recommendation_id"] == rid_b for r in approved)


def test_list_filters_by_store_id(engine):
    create_recommendation(engine, _mk("S001"), created_by="a")
    create_recommendation(engine, _mk("S002"), created_by="a")
    out = list_recommendations(engine, store_id="S001")
    assert all(r["store_id"] == "S001" for r in out)


def test_list_decodes_evidence(engine):
    rec = _mk("S001")
    rec["evidence"] = {"splh": 42.0, "nested": {"x": 1}}
    create_recommendation(engine, rec, created_by="a")
    out = list_recommendations(engine, store_id="S001")
    assert out[0]["evidence"] == {"splh": 42.0, "nested": {"x": 1}}


def test_list_limit_respected(engine):
    for _ in range(5):
        create_recommendation(engine, _mk("S001"), created_by="a")
    out = list_recommendations(engine, limit=2)
    assert len(out) == 2


def test_get_recommendation_missing_is_none(engine):
    assert get_recommendation(engine, "no-such-id") is None


def test_create_triggers_approval_event(engine):
    from sqlalchemy import text
    rec = _mk("S001")
    rid = create_recommendation(engine, rec, created_by="agent")
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM approval_event WHERE recommendation_id=:r"),
            {"r": rid},
        ).scalar()
    assert n == 1


def test_transition_populates_reviewer_fields(engine):
    rec = _mk("S001")
    rid = create_recommendation(engine, rec, created_by="agent")
    transition(engine, rid, "approved", actor="rae.reviewer", comment="LGTM")
    row = get_recommendation(engine, rid)
    assert row["reviewer"] == "rae.reviewer"
    assert row["reviewer_comment"] == "LGTM"
    assert row["reviewed_at_utc"] is not None
