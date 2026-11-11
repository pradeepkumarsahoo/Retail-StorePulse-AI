"""Energy Analyst agent, Operations Advisor energy branch, and the energy_planning workflow."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from apps.api.security import Principal
from packages.ai_orchestration.agents import energy_analyst, operations_advisor, store_performance
from packages.ai_orchestration.supervisor import Supervisor
from packages.tool_gateway import gateway
from packages.tool_gateway.gateway import ToolDenied
from packages.tool_gateway.tools import Context


def _ctx(engine) -> Context:
    p = Principal(subject="test-admin", role="platform_admin", regions=frozenset({"*"}), stores=frozenset({"*"}))
    return Context(engine=engine, principal=p)


def _start_run(engine, run_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ai_run (run_id, created_at_utc, user_subject, intent, status, step_count, "
            "token_budget, tokens_used, model_name, prompt_version) VALUES "
            "(:r, '2026-10-01 00:00:00.000000', 't', 'energy_planning', 'ok', 0, 1, 0, 'm', '1')"),
            {"r": run_id})


def test_energy_analyst_reads_bms_and_check(engine, seed_window):
    start, _ = seed_window
    run_id = str(uuid.uuid4())
    _start_run(engine, run_id)
    out = energy_analyst.run(_ctx(engine), run_id, "S001", start, horizon_days=1)
    assert out["last_seq"] == 3
    assert out["bms"]["schedule"]["hvac_on"] == "05:00"
    # The trailing 7 days before 09:00 on 10-01 have no traffic: null, not a guess.
    assert out["footfall_7d"] == {"value": None, "unit": "count"}
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f["kind"] == "waste" and f["avoidable_kwh"] == 360.0
    assert "starts 150 min before it needs to" in f["rationale"]
    with engine.connect() as conn:
        tools = conn.execute(text(
            "SELECT tool_name FROM tool_call WHERE run_id=:r ORDER BY seq"), {"r": run_id}).scalars().all()
    assert tools == ["bms.hvac_schedule.get", "energy.hvac_schedule_check", "kpi.visitors"]


def test_energy_analyst_cannot_read_raw_tms(engine, seed_window):
    start, _ = seed_window
    run_id = str(uuid.uuid4())
    _start_run(engine, run_id)
    with pytest.raises(ToolDenied):
        gateway.call_tool(_ctx(engine), run_id, 1, "energy_analyst", "tms.shipments.list",
                          {"store_id": "S001", "from_date": "2026-10-01", "horizon_days": 1})


def test_other_agents_cannot_read_the_bms(engine):
    run_id = str(uuid.uuid4())
    _start_run(engine, run_id)
    for agent in ("workforce", "inbound_logistics", "operations_advisor", "briefing"):
        with pytest.raises(ToolDenied):
            gateway.call_tool(_ctx(engine), run_id, 1, agent, "bms.hvac_schedule.get", {"store_id": "S001"})


def test_advisor_drafts_comfort_guarded_hvac_recommendation(engine, seed_window):
    start, _ = seed_window
    run_id = str(uuid.uuid4())
    _start_run(engine, run_id)
    ctx = _ctx(engine)
    en = energy_analyst.run(ctx, run_id, "S001", start, horizon_days=1)
    sp = store_performance.run(ctx, run_id, "S001", start, start.replace(hour=12), start_seq=en["last_seq"])
    rec = operations_advisor.run(ctx, run_id, sp, {}, start_seq=sp["last_seq"], energy=en)["recommendation"]
    assert rec["title"] == "Align the store's HVAC schedule with store activity"
    assert "Starting at 07:30 still reaches setpoint by 08:45" in rec["rationale"]
    assert rec["expected_direction"] == "down"
    assert rec["confidence"] == "high"                      # metered day, not a baseline
    assert rec["status"] == "pending_review"
    es = rec["evidence"]["energy_summary"]
    assert es["comfort_guard"] == {"passed": True, "setpoint_ready_at": "08:45"}
    assert es["recommended_schedule"]["hvac_on"] == "07:30"
    assert operations_advisor._policy_check(rec["title"], rec["rationale"]) == []


def test_advisor_comfort_risk_branch():
    energy = {"findings": [], "check": {
        "summary": {"current_schedule": {"hvac_on": "08:00", "hvac_off": "21:00", "warmup_minutes_p90": 60},
                    "recommended_schedule": {"hvac_on": "07:45", "hvac_off": "21:30"}},
        "lineage": {"thresholds": {"comfort_lead_minutes": 15}},
        "days": [{"date": "2026-10-01", "status": "comfort_risk", "first_occupied": "09:00",
                  "recommended_on": "07:45", "comfort_risk_minutes": 15}]}}
    title, rationale, direction, confidence, _risk = operations_advisor._energy_recommendation(energy)
    assert title == "Start HVAC earlier so the store is comfortable at opening"
    assert direction == "up" and "07:45" in rationale


def test_comfort_guard_fails_when_setpoint_would_be_late():
    energy = {"check": {
        "summary": {"current_schedule": {"warmup_minutes_p90": 90},
                    "recommended_schedule": {"hvac_on": "08:00", "hvac_off": "21:30"}},
        "lineage": {"thresholds": {"comfort_lead_minutes": 15}},
        "days": [{"date": "2026-10-01", "status": "waste", "first_occupied": "09:00"}]}}
    guard = operations_advisor._comfort_guard(energy)
    assert guard["passed"] is False and "2026-10-01" in guard["reason"]


def test_plan_store_energy_writes_ai_run_and_audit(engine, seed_window):
    start, _ = seed_window
    result = Supervisor().plan_store_energy(_ctx(engine), "S001", start, horizon_days=1)
    assert result.status == "ok", result.error
    assert result.intent == "energy_planning"
    assert set(result.output) == {"energy_analyst", "store_performance", "advisor"}
    assert result.step_count == 12                          # 3 energy + 9 store performance
    with engine.connect() as conn:
        n_run = conn.execute(text("SELECT COUNT(*) FROM ai_run WHERE run_id=:r AND intent='energy_planning'"),
                             {"r": result.run_id}).scalar()
        n_audit = conn.execute(text("SELECT COUNT(*) FROM audit_event WHERE target_id=:r "
                                    "AND action='ai_run.energy_planning'"), {"r": result.run_id}).scalar()
    assert (n_run, n_audit) == (1, 1)


def test_plan_store_energy_unknown_store_marks_error(engine, seed_window):
    start, _ = seed_window
    result = Supervisor().plan_store_energy(_ctx(engine), "S999", start, horizon_days=1)
    assert result.status == "error"
    assert "Unknown store_id" in result.error
