from __future__ import annotations

import uuid

import pytest

from packages.governance.approval_workflow import (
    InvalidTransition,
    NotFound,
    create_recommendation,
    get_recommendation,
    transition,
)


def _mk_rec(store_id: str = "S001") -> dict:
    return {
        "recommendation_id": str(uuid.uuid4()),
        "store_id": store_id,
        "title": "Review staffing mix",
        "rationale": "Net sales soft; review schedule.",
        "evidence": {"sales_change_pct": -0.12},
        "expected_direction": "up",
        "confidence": "medium",
        "risk": "Low; manager-led review only.",
        "status": "pending_review",
        "prompt_version": "1.0.0",
    }


def test_create_and_transition_happy_path(engine):
    rec = _mk_rec()
    rid = create_recommendation(engine, rec, created_by="agent_operations_advisor")
    r = get_recommendation(engine, rid)
    assert r["status"] == "pending_review"
    t1 = transition(engine, rid, "approved", actor="rae.reviewer", comment="LGTM")
    assert t1["from_status"] == "pending_review" and t1["to_status"] == "approved"
    t2 = transition(engine, rid, "in_progress", actor="sam.manager")
    assert t2["to_status"] == "in_progress"
    t3 = transition(engine, rid, "completed", actor="sam.manager")
    assert t3["to_status"] == "completed"


def test_rejection_is_terminal(engine):
    rid = create_recommendation(engine, _mk_rec(), created_by="agent")
    transition(engine, rid, "rejected", actor="rae.reviewer")
    with pytest.raises(InvalidTransition):
        transition(engine, rid, "approved", actor="rae.reviewer")


def test_invalid_transition_rejected(engine):
    rid = create_recommendation(engine, _mk_rec(), created_by="agent")
    with pytest.raises(InvalidTransition):
        transition(engine, rid, "completed", actor="rae.reviewer")


def test_not_found(engine):
    with pytest.raises(NotFound):
        transition(engine, "missing", "approved", actor="rae.reviewer")
