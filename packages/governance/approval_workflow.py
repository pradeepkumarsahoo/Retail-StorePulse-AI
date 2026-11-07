"""Human-in-the-loop recommendation workflow.

State machine (single source of truth):

    draft -> pending_review
    pending_review -> approved | rejected
    approved -> in_progress
    in_progress -> completed | expired
    rejected -> (terminal)

Every transition writes an ApprovalEvent and an AuditEvent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.governance.audit import record_audit
from packages.observability import metrics


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_review"},
    "pending_review": {"approved", "rejected"},
    "approved": {"in_progress"},
    "in_progress": {"completed", "expired"},
    "rejected": set(),
    "completed": set(),
    "expired": set(),
}


class InvalidTransition(Exception):
    pass


class NotFound(Exception):
    pass


def create_recommendation(engine: Engine, rec: dict, created_by: str,
                          run_id: str | None = None) -> str:
    """Persist a draft/pending_review recommendation."""
    rec_id = rec["recommendation_id"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recommendation (recommendation_id, run_id, store_id, title, "
                "rationale, evidence_json, expected_direction, confidence, risk, status, "
                "created_at_utc, created_by, prompt_version) VALUES "
                "(:rid, :run, :sid, :t, :ra, :ev, :dir, :cf, :rk, :st, :ca, :cb, :pv)"
            ),
            {
                "rid": rec_id, "run": run_id, "sid": rec["store_id"], "t": rec["title"],
                "ra": rec["rationale"], "ev": json.dumps(rec.get("evidence", {}), default=str),
                "dir": rec["expected_direction"], "cf": rec["confidence"],
                "rk": rec.get("risk"), "st": rec.get("status", "pending_review"),
                "ca": now, "cb": created_by, "pv": rec.get("prompt_version", "1.0.0"),
            },
        )
        conn.execute(
            text(
                "INSERT INTO approval_event (recommendation_id, from_status, to_status, "
                "actor, comment, at_utc) VALUES (:rid, :fs, :ts, :a, :c, :t)"
            ),
            {"rid": rec_id, "fs": "draft", "ts": rec.get("status", "pending_review"),
             "a": created_by, "c": "created", "t": now},
        )
    record_audit(engine, actor=created_by, action="recommendation.created",
                 target_type="recommendation", target_id=rec_id,
                 payload={"store_id": rec["store_id"], "status": rec.get("status", "pending_review")})
    metrics.inc("storepulse_recommendations_total", status=rec.get("status", "pending_review"))
    return rec_id


def transition(engine: Engine, recommendation_id: str, to_status: str,
               actor: str, comment: str | None = None) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM recommendation WHERE recommendation_id=:r"),
            {"r": recommendation_id},
        ).one_or_none()
        if not row:
            raise NotFound(f"Recommendation {recommendation_id} not found")
        current = row.status
        if to_status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"Cannot transition {current} -> {to_status}")
        conn.execute(
            text(
                "UPDATE recommendation SET status=:ns, reviewer=:rv, reviewed_at_utc=:ra, "
                "reviewer_comment=:cm WHERE recommendation_id=:r"
            ),
            {"ns": to_status, "rv": actor, "ra": now, "cm": comment, "r": recommendation_id},
        )
        conn.execute(
            text(
                "INSERT INTO approval_event (recommendation_id, from_status, to_status, "
                "actor, comment, at_utc) VALUES (:r, :fs, :ts, :a, :c, :t)"
            ),
            {"r": recommendation_id, "fs": current, "ts": to_status,
             "a": actor, "c": comment, "t": now},
        )
    record_audit(engine, actor=actor, action=f"recommendation.{to_status}",
                 target_type="recommendation", target_id=recommendation_id,
                 payload={"from": current, "to": to_status, "comment": comment})
    metrics.inc("storepulse_recommendations_total", status=to_status)
    return {"recommendation_id": recommendation_id, "from_status": current,
            "to_status": to_status, "reviewer": actor,
            "reviewed_at_utc": now, "comment": comment}


def get_recommendation(engine: Engine, recommendation_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT recommendation_id, run_id, store_id, title, rationale, evidence_json, "
                "expected_direction, confidence, risk, status, created_at_utc, created_by, "
                "reviewer, reviewed_at_utc, reviewer_comment, prompt_version "
                "FROM recommendation WHERE recommendation_id=:r"
            ),
            {"r": recommendation_id},
        ).mappings().one_or_none()
    if not row:
        return None
    d = dict(row)
    d["evidence"] = json.loads(d.pop("evidence_json"))
    return d


def list_recommendations(engine: Engine, status: str | None = None,
                         store_id: str | None = None, limit: int = 100) -> list[dict]:
    sql = (
        "SELECT recommendation_id, run_id, store_id, title, rationale, evidence_json, "
        "expected_direction, confidence, risk, status, created_at_utc, created_by, "
        "reviewer, reviewed_at_utc, reviewer_comment, prompt_version "
        "FROM recommendation WHERE 1=1"
    )
    params: dict = {}
    if status:
        sql += " AND status=:st"
        params["st"] = status
    if store_id:
        sql += " AND store_id=:sid"
        params["sid"] = store_id
    sql += " ORDER BY created_at_utc DESC LIMIT :lim"
    params["lim"] = limit
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json"))
        out.append(d)
    return out
