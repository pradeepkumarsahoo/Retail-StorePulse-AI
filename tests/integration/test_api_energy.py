"""Energy (BMS) API: schedule, HVAC check, BOLA, and the energy planning workflow."""
from __future__ import annotations

from sqlalchemy import text


def test_schedule_endpoint_returns_bms_timetable(api_client):
    r = api_client.get("/api/v1/stores/S001/energy/schedule")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["hvac_on"], body["hvac_off"]) == ("05:00", "22:00")
    assert body["server"]["name"] == "bms-hvac"


def test_hvac_check_endpoint_shape_and_lineage(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get("/api/v1/stores/S001/energy/hvac-check",
                       params={"as_of": start.isoformat(), "horizon_days": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert [d["status"] for d in body["days"]] == ["waste", "insufficient_evidence"]
    assert body["summary"]["avoidable_kwh"] == 360.0
    assert body["lineage"]["check_name"] == "energy_hvac_schedule_check"


def test_horizon_above_five_is_rejected(api_client, seed_window):
    start, _ = seed_window
    r = api_client.get("/api/v1/stores/S001/energy/hvac-check",
                       params={"as_of": start.isoformat(), "horizon_days": 6})
    assert r.status_code == 422


def test_west_manager_cannot_read_midwest_energy(api_client, seed_window):
    start, _ = seed_window
    hdr = {"Authorization": "Bearer demo-regional-west"}
    assert api_client.get("/api/v1/stores/S002/energy/schedule", headers=hdr).status_code == 403
    assert api_client.get("/api/v1/stores/S002/energy/hvac-check", headers=hdr,
                          params={"as_of": start.isoformat()}).status_code == 403
    assert api_client.get("/api/v1/stores/S001/energy/schedule", headers=hdr).status_code == 200


def test_unknown_store_is_404(api_client):
    assert api_client.get("/api/v1/stores/S999/energy/schedule").status_code == 404


def test_plan_store_energy_workflow_creates_pending_recommendation(api_client, engine, seed_window):
    start, _ = seed_window
    r = api_client.post("/api/v1/agents/plan-store-energy",
                        params={"store_id": "S001", "as_of": start.isoformat(), "horizon_days": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["intent"] == "energy_planning"
    rec = body["output"]["advisor"]["recommendation"]
    assert rec["status"] == "pending_review"
    assert rec["evidence"]["energy_summary"]["comfort_guard"]["passed"] is True

    with engine.connect() as conn:
        agents = conn.execute(text(
            "SELECT DISTINCT agent_name FROM tool_call WHERE run_id=:r"), {"r": body["run_id"]}
        ).scalars().all()
        n_rec = conn.execute(text(
            "SELECT COUNT(*) FROM recommendation WHERE run_id=:r AND status='pending_review'"),
            {"r": body["run_id"]}).scalar()
    assert set(agents) == {"energy_analyst", "store_performance"}
    assert n_rec == 1


def test_plan_store_energy_is_scope_checked(api_client, seed_window):
    start, _ = seed_window
    r = api_client.post("/api/v1/agents/plan-store-energy",
                        headers={"Authorization": "Bearer demo-regional-west"},
                        params={"store_id": "S002", "as_of": start.isoformat()})
    assert r.status_code == 403
