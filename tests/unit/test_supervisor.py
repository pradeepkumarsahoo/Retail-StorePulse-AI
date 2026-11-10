"""Unit tests for the Supervisor orchestration.

Each workflow writes exactly one AiRun row with the correct intent/status,
records a matching audit entry, and bubbles the specialist output back out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.api.security import Principal
from packages.ai_orchestration.supervisor import RunBudget, Supervisor
from packages.anomaly_detection.detector import AnomalyFinding, persist_findings
from packages.tool_gateway.tools import Context


ADMIN = Principal(subject="alex.admin", role="platform_admin",
                  regions=frozenset({"*"}), stores=frozenset({"*"}))


# --- investigate_store -----------------------------------------------------

def test_investigate_store_ok_writes_ai_run_and_audit(engine, seed_window):
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.investigate_store(ctx, "S001", start, end)
    assert result.status == "ok"
    assert result.intent == "store_investigation"
    assert "advisor" in result.output
    with engine.connect() as conn:
        n_run = conn.execute(
            text("SELECT COUNT(*) FROM ai_run WHERE run_id=:r"), {"r": result.run_id}
        ).scalar()
        n_audit = conn.execute(
            text("SELECT COUNT(*) FROM audit_event WHERE target_id=:r"),
            {"r": result.run_id},
        ).scalar()
    assert n_run == 1
    assert n_audit == 1


def test_investigate_store_unknown_store_marks_error(engine, seed_window):
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.investigate_store(ctx, "S999", start, end)
    assert result.status == "error"
    assert "Unknown store_id" in (result.error or "")
    # Still persists an ai_run (observability), but with status=error.
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, output_json FROM ai_run WHERE run_id=:r"),
            {"r": result.run_id},
        ).one()
    assert row.status == "error"
    assert row.output_json is None


def test_budget_step_enforced(engine, seed_window):
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    # Force step budget below what store_performance alone needs.
    sup = Supervisor(budget=RunBudget(max_steps=1, max_tokens=2000, timeout_s=20.0))
    result = sup.investigate_store(ctx, "S001", start, end)
    assert result.status == "error"
    assert "Step budget exceeded" in (result.error or "")


# --- investigate_anomaly ---------------------------------------------------

def _seed_anomaly(engine, store_id: str, ts) -> str:
    finding = AnomalyFinding(
        anomaly_id="A-TEST",
        store_id=store_id,
        ts_utc=ts,
        kind="net_sales_hourly",
        severity="medium",
        observed=100.0,
        baseline=50.0,
        deviation=50.0,
        z_score=2.1,
        evidence={"detector": "robust_zscore_v1", "detector_version": "1.0.0"},
    )
    persist_findings(engine, [finding])
    return "A-TEST"


def test_investigate_anomaly_ok(engine, seed_window):
    start, _ = seed_window
    aid = _seed_anomaly(engine, "S001", start)
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.investigate_anomaly(ctx, aid)
    assert result.status == "ok"
    assert result.intent == "anomaly_investigation"
    inv = result.output["investigation"]
    assert inv["anomaly"]["anomaly_id"] == aid
    assert isinstance(inv["hypotheses"], list) and inv["hypotheses"]


def test_investigate_anomaly_missing_marks_error(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.investigate_anomaly(ctx, "does-not-exist")
    assert result.status == "error"


# --- plan_inbound_labor (TMS) ---------------------------------------------

def test_plan_inbound_labor_ok_writes_ai_run_and_audit(engine, seed_window):
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.plan_inbound_labor(ctx, "S001", start, horizon_days=3)
    assert result.status == "ok", result.error
    assert result.intent == "inbound_labor_planning"
    assert set(result.output) == {"inbound_logistics", "store_performance", "workforce", "advisor"}
    # 3 (inbound) + 9 (store perf) + 5 (workforce) tool calls, advisor adds none.
    assert result.step_count == 17
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT intent, status, prompt_version, store_scope FROM ai_run WHERE run_id=:r"),
            {"r": result.run_id},
        ).one()
        n_audit = conn.execute(
            text("SELECT COUNT(*) FROM audit_event WHERE target_id=:r AND action='ai_run.inbound_labor_planning'"),
            {"r": result.run_id},
        ).scalar()
        agents = conn.execute(
            text("SELECT DISTINCT agent_name FROM tool_call WHERE run_id=:r"), {"r": result.run_id}
        ).scalars().all()
    assert (row.intent, row.status, row.prompt_version, row.store_scope) == (
        "inbound_labor_planning", "ok", "1.1.0", "S001")
    assert n_audit == 1
    assert "inbound_logistics" in agents


def test_plan_inbound_labor_unknown_store_marks_error(engine, seed_window):
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    result = Supervisor().plan_inbound_labor(ctx, "S999", start)
    assert result.status == "error"
    assert "Unknown store_id" in (result.error or "")


def test_plan_inbound_labor_respects_step_budget(engine, seed_window):
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor(budget=RunBudget(max_steps=5, max_tokens=2000, timeout_s=20.0))
    result = sup.plan_inbound_labor(ctx, "S001", start)
    assert result.status == "error"
    assert "Step budget exceeded" in (result.error or "")


# --- daily_briefing --------------------------------------------------------

def test_daily_briefing_ok_tracks_tokens(engine, seed_window):
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    result = sup.daily_briefing(ctx, "S001", start + timedelta(days=2))
    assert result.status == "ok"
    assert result.intent == "daily_briefing"
    assert result.tokens_used > 0  # deterministic provider reports a token count
    assert result.output["briefing"]["model"]["name"] == "deterministic-template"


def test_daily_briefing_bad_store_still_persists_error_row(engine, seed_window):
    ctx = Context(engine=engine, principal=ADMIN)
    sup = Supervisor()
    start, _ = seed_window
    result = sup.daily_briefing(ctx, "S404", start)
    assert result.status == "error"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM ai_run WHERE run_id=:r"), {"r": result.run_id}
        ).one()
    assert row.status == "error"
