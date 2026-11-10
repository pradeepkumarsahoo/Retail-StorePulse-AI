"""Inbound (TMS) API: shipments, labor-plan check, BOLA, and the planning workflow."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text


def test_shipments_endpoint_returns_tms_rows(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get(f"/api/v1/stores/S001/inbound/shipments",
                       params={"from": start.isoformat(),
                               "to": (start + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    ids = [s["shipment_id"] for s in r.json()]
    assert ids == ["SHP-000001", "SHP-000002"]
    assert r.json()[1]["status"] == "delayed"


def test_labor_plan_endpoint_shape_and_lineage(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get("/api/v1/stores/S001/inbound/labor-plan",
                       params={"as_of": start.isoformat(), "horizon_days": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert [d["status"] for d in body["days"]] == ["balanced", "insufficient_evidence",
                                                   "insufficient_evidence"]
    assert body["lineage"]["check_name"] == "inbound_labor_plan_check"
    assert body["summary"]["trucks_in_window"] == 2


def test_horizon_above_five_is_rejected(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get("/api/v1/stores/S001/inbound/labor-plan",
                       params={"as_of": start.isoformat(), "horizon_days": 6})
    assert r.status_code == 422


def test_west_manager_cannot_read_midwest_trucks(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get("/api/v1/stores/S002/inbound/shipments",
                       headers={"Authorization": "Bearer demo-regional-west"},
                       params={"from": start.isoformat(),
                               "to": (start + timedelta(days=2)).isoformat()})
    assert r.status_code == 403
    r2 = api_client.get("/api/v1/stores/S002/inbound/labor-plan",
                        headers={"Authorization": "Bearer demo-regional-west"},
                        params={"as_of": start.isoformat()})
    assert r2.status_code == 403


def test_store_manager_reads_own_trucks_only(api_client, seed_window):
    start, _ = seed_window
    hdr = {"Authorization": "Bearer demo-store-s001"}
    ok = api_client.get("/api/v1/stores/S001/inbound/labor-plan", headers=hdr,
                        params={"as_of": start.isoformat()})
    assert ok.status_code == 200
    denied = api_client.get("/api/v1/stores/S002/inbound/labor-plan", headers=hdr,
                            params={"as_of": start.isoformat()})
    assert denied.status_code == 403


def test_plan_inbound_labor_workflow_creates_pending_recommendation(api_client, engine, seed_window):
    start, _ = seed_window
    r = api_client.post("/api/v1/agents/plan-inbound-labor",
                        params={"store_id": "S001", "as_of": start.isoformat(), "horizon_days": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["intent"] == "inbound_labor_planning"
    out = body["output"]
    assert out["inbound_logistics"]["tms"]["trucks_in_window"] == 2
    assert out["workforce"]["inbound_plan"]["summary"]["trucks_in_window"] == 2
    rec = out["advisor"]["recommendation"]
    assert rec["status"] == "pending_review"
    assert rec["evidence"]["inbound_summary"]["source"] == "inbound_logistics"

    with engine.connect() as conn:
        tools = conn.execute(text(
            "SELECT DISTINCT agent_name FROM tool_call WHERE run_id=:r"), {"r": body["run_id"]}
        ).scalars().all()
        n_rec = conn.execute(text(
            "SELECT COUNT(*) FROM recommendation WHERE run_id=:r AND status='pending_review'"),
            {"r": body["run_id"]}).scalar()
    assert {"inbound_logistics", "store_performance", "workforce"} <= set(tools)
    assert n_rec == 1


def test_plan_inbound_labor_is_scope_checked(api_client, seed_window):
    start, _ = seed_window
    r = api_client.post("/api/v1/agents/plan-inbound-labor",
                        headers={"Authorization": "Bearer demo-regional-west"},
                        params={"store_id": "S002", "as_of": start.isoformat()})
    assert r.status_code == 403
