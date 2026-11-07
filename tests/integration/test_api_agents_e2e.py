"""End-to-end test: investigate a store → recommendation enters review queue
→ reviewer approves → audit trail records the chain."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def test_full_investigation_flow(api_client, engine, seed_window):
    start, end = seed_window
    r = api_client.post(
        "/api/v1/agents/investigate-store",
        params={"store_id": "S001", "from": start.isoformat(), "to": end.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    run_id = body["run_id"]

    # An ai_run row was written
    with engine.connect() as conn:
        n_runs = conn.execute(text("SELECT COUNT(*) FROM ai_run WHERE run_id=:r"),
                              {"r": run_id}).scalar()
        n_tools = conn.execute(text("SELECT COUNT(*) FROM tool_call WHERE run_id=:r"),
                               {"r": run_id}).scalar()
        n_audit = conn.execute(text("SELECT COUNT(*) FROM audit_event WHERE target_id=:r"),
                               {"r": run_id}).scalar()
    assert n_runs == 1
    assert n_tools > 0
    assert n_audit >= 1

    # A pending_review recommendation was created.
    recs = api_client.get("/api/v1/recommendations?status=pending_review").json()
    assert len(recs) >= 1
    rec_id = recs[0]["recommendation_id"]
    assert recs[0]["evidence"] is not None

    # Reviewer approves it.
    res = api_client.post(
        f"/api/v1/recommendations/{rec_id}/review",
        headers={"Authorization": "Bearer demo-reviewer"},
        json={"decision": "approve", "comment": "evidence looks reasonable"},
    )
    assert res.status_code == 200
    assert res.json()["to_status"] == "approved"

    # Store manager starts and completes it.
    hdr = {"Authorization": "Bearer demo-store-s001"}
    r2 = api_client.post(f"/api/v1/recommendations/{rec_id}/review",
                         headers=hdr, json={"decision": "start"})
    # S001 manager has 'store_manager' role, which is NOT in APPROVER_ROLES → 403.
    assert r2.status_code == 403

    r3 = api_client.post(f"/api/v1/recommendations/{rec_id}/review",
                         headers={"Authorization": "Bearer demo-admin"},
                         json={"decision": "start"})
    assert r3.status_code == 200
    r4 = api_client.post(f"/api/v1/recommendations/{rec_id}/review",
                         headers={"Authorization": "Bearer demo-admin"},
                         json={"decision": "complete"})
    assert r4.status_code == 200


def test_briefing_endpoint(api_client, seed_window):
    start, _ = seed_window
    r = api_client.post(
        "/api/v1/agents/briefing",
        params={"store_id": "S001",
                "as_of_utc": (start + timedelta(days=2)).isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "briefing" in body["output"]
    assert body["output"]["briefing"]["narrative"]
    assert body["output"]["briefing"]["model"]["name"] == "deterministic-template"
