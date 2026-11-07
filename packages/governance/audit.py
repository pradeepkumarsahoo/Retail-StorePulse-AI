"""Append-only audit trail. Never update, never delete."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.observability.logging import get_correlation_id


def record_audit(engine: Engine, *, actor: str, action: str, target_type: str,
                 target_id: str | None, payload: dict | None = None,
                 correlation_id: str | None = None) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO audit_event (at_utc, actor, action, target_type, target_id, "
                "correlation_id, payload_json) VALUES "
                "(:t, :ac, :an, :tt, :ti, :cid, :p)"
            ),
            {
                "t": now, "ac": actor, "an": action, "tt": target_type, "ti": target_id,
                "cid": correlation_id or get_correlation_id(),
                "p": json.dumps(payload, default=str) if payload is not None else None,
            },
        )
    return int(res.lastrowid or 0)


def list_audit_events(engine: Engine, limit: int = 100,
                      actor: str | None = None, target_type: str | None = None) -> list[dict]:
    sql = (
        "SELECT id, at_utc, actor, action, target_type, target_id, correlation_id, payload_json "
        "FROM audit_event WHERE 1=1"
    )
    params: dict = {}
    if actor:
        sql += " AND actor = :actor"
        params["actor"] = actor
    if target_type:
        sql += " AND target_type = :tt"
        params["tt"] = target_type
    sql += " ORDER BY id DESC LIMIT :lim"
    params["lim"] = limit
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else None
        out.append(d)
    return out
