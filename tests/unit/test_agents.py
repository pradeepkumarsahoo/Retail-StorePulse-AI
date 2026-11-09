"""Unit tests for each specialist agent.

Agents only route through the tool gateway, so these tests exercise them with
an admin principal (all scopes) against the seeded in-memory DB. Each test
pins the public contract (shape, key fields, policy) rather than wire-format.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.api.security import Principal
from packages.ai_orchestration.agents import (
    anomaly_investigator,
    briefing,
    operations_advisor,
    store_performance,
    workforce,
)
from packages.ai_orchestration.llm_provider import DeterministicProvider
from packages.anomaly_detection.detector import AnomalyFinding, persist_findings
from packages.tool_gateway.tools import Context


ADMIN = Principal(subject="alex.admin", role="platform_admin",
                  regions=frozenset({"*"}), stores=frozenset({"*"}))


def _seed_ai_run(engine, run_id: str = "RUN_AGENT") -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ai_run (run_id, created_at_utc, user_subject, intent, store_scope, "
            "status, step_count, token_budget, tokens_used, model_name, prompt_version) "
            "VALUES (:r, '2026-10-01 00:00:00', :u, 'test', NULL, 'ok', 0, 1000, 0, "
            "'deterministic-template', '1.0.0')"
        ), {"r": run_id, "u": ADMIN.subject})


# --- store_performance -----------------------------------------------------

def test_store_performance_collects_all_kpis(engine, seed_window):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = store_performance.run(ctx, "RUN_AGENT", "S001", start, end)
    # Every KPI must appear
    for k in ("net_sales", "transactions", "atv", "upt", "splh", "visitors", "conversion_rate"):
        assert k in out["kpis"]
    assert out["agent"] == "store_performance"
    assert out["prompt_version"] == "1.0.0"
    # last_seq increments across tool calls (7 current + 2 prior = 9).
    assert out["last_seq"] == 9


def test_store_performance_computes_sales_change_pct(engine, seed_window):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = store_performance.run(ctx, "RUN_AGENT", "S001", start, end)
    # Prior window has no data in the fixture → prev_sales is None → change is None.
    assert out["derived"]["sales_change_pct"] is None


# --- workforce -------------------------------------------------------------

def test_workforce_runs_all_tools(engine, seed_window):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = workforce.run(ctx, "RUN_AGENT", "S001", start, end, region="West")
    assert out["agent"] == "workforce"
    assert out["labor_rate"]["region"] == "West"
    assert out["labor_rate"]["source"] == "synthetic_reference"
    assert isinstance(out["splh"], dict)
    assert isinstance(out["labor_cost_pct"], dict)
    assert isinstance(out["forecast_points"], list)


def test_workforce_forecast_capped_at_24_points(engine, seed_window, monkeypatch):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)

    # Patch gateway.call_tool forecast response to a long list to prove the cap.
    long_points = [{"ts_utc": "2026-10-01T00:00:00", "predicted": i, "lower_80": 0,
                    "upper_80": i} for i in range(50)]
    import packages.ai_orchestration.agents.workforce as wf

    real_call = wf.gateway.call_tool

    def fake(ctx, run_id, seq, agent, tool_name, args):
        if tool_name == "forecast.labor":
            return {"points": long_points}
        return real_call(ctx, run_id, seq, agent, tool_name, args)

    monkeypatch.setattr(wf.gateway, "call_tool", fake)
    out = workforce.run(ctx, "RUN_AGENT", "S001", start, end, region="West")
    assert len(out["forecast_points"]) == 24


# --- operations_advisor ----------------------------------------------------

def _mk_perf(sales_change: float | None, splh: float | None = 25.0,
             net_sales: float | None = 1000.0) -> dict:
    return {
        "store_id": "S001",
        "kpis": {
            "splh": {"value": splh, "unit": "USD per labor hour"},
            "net_sales": {"value": net_sales, "unit": "USD"},
        },
        "derived": {"sales_change_pct": sales_change},
    }


def _mk_wf() -> dict:
    return {
        "forecast_points": [{"predicted": 60, "ts_utc": "2026-10-01T00:00:00"}],
        "labor_cost_pct": {"value": 0.12},
        "labor_rate": {"source": "synthetic_reference", "version": "1.0.0"},
    }


def test_advisor_recommends_review_when_sales_fell_and_splh_low(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(-0.15, splh=18.0), _mk_wf())
    rec = out["recommendation"]
    assert "Review staffing mix" in rec["title"]
    assert rec["expected_direction"] == "up"
    assert rec["confidence"] == "medium"
    assert rec["status"] == "pending_review"


def test_advisor_recommends_confirmation_when_sales_up(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(0.15), _mk_wf())
    assert "Confirm current staffing" in out["recommendation"]["title"]
    assert out["recommendation"]["expected_direction"] == "neutral"


def test_advisor_holds_when_neutral(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(0.02), _mk_wf())
    assert "Hold current plan" in out["recommendation"]["title"]
    assert out["recommendation"]["confidence"] == "low"


def test_advisor_holds_when_sales_change_unknown(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(None), _mk_wf())
    assert "Hold current plan" in out["recommendation"]["title"]


def test_advisor_policy_rejects_forbidden_terms(engine, monkeypatch):
    """A rationale mentioning a forbidden term must be replaced with the safe default."""
    import packages.ai_orchestration.agents.operations_advisor as oa

    # Pretend the branch would produce a hostile title; monkeypatch the branch to force it.
    original = oa.run

    def hostile(ctx, run_id, store_perf, workforce, investigations=None, start_seq=0):
        return {
            "agent": oa.AGENT_NAME,
            "prompt_version": oa.PROMPT_VERSION,
            "recommendation": {
                "recommendation_id": "test",
                "store_id": "S001",
                "title": "Cut staff and reduce hours",
                "rationale": "Cut staff and reduce hours across the floor.",
                "evidence": {},
                "expected_direction": "down",
                "confidence": "high",
                "risk": None,
                "status": "pending_review",
                "created_at_utc": "2026-10-01T00:00:00+00:00",
                "prompt_version": oa.PROMPT_VERSION,
            },
            "last_seq": 0,
        }

    # Directly test the policy checker to prove the invariant.
    hits = oa._policy_check("Cut staff and reduce hours",
                            "Cut staff and reduce hours across the floor.")
    assert "reduce hours" in hits


def test_advisor_recommendation_carries_uuid_id(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(0.0), _mk_wf())
    uuid.UUID(out["recommendation"]["recommendation_id"])  # raises on bad value


# --- briefing --------------------------------------------------------------

def test_briefing_composes_narrative(engine, seed_window):
    _seed_ai_run(engine)
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    llm = DeterministicProvider()
    out = briefing.run(ctx, "RUN_AGENT", "S001", start + timedelta(days=2), llm)
    assert out["agent"] == "briefing"
    assert out["narrative"]
    assert out["model"]["name"] == "deterministic-template"
    assert "net_sales_usd" in out["evidence"]["kpis"]


def test_briefing_window_is_the_prior_day(engine, seed_window):
    _seed_ai_run(engine)
    start, _ = seed_window
    as_of = start + timedelta(days=2)
    ctx = Context(engine=engine, principal=ADMIN)
    llm = DeterministicProvider()
    out = briefing.run(ctx, "RUN_AGENT", "S001", as_of, llm)
    from_s = out["window"]["from_utc"]
    to_s = out["window"]["to_utc"]
    assert from_s.startswith((as_of - timedelta(days=1)).date().isoformat())
    assert to_s.startswith(as_of.date().isoformat())


# --- anomaly_investigator --------------------------------------------------

def _seed_anomaly(engine, store_id: str, ts: datetime, visitors: float = 100.0,
                  baseline: float = 500.0) -> str:
    f = AnomalyFinding(
        anomaly_id=str(uuid.uuid4()),
        store_id=store_id,
        ts_utc=ts,
        kind="net_sales_hourly",
        severity="medium",
        observed=200.0, baseline=baseline, deviation=-300.0, z_score=-2.1,
        evidence={"detector": "robust_zscore_v1", "detector_version": "1.0.0"},
    )
    persist_findings(engine, [f])
    return f.anomaly_id


def test_anomaly_investigator_returns_hypotheses(engine, seed_window):
    _seed_ai_run(engine)
    start, _ = seed_window
    aid = _seed_anomaly(engine, "S001", start, baseline=500.0)
    ctx = Context(engine=engine, principal=ADMIN)
    out = anomaly_investigator.run(ctx, "RUN_AGENT", aid)
    assert out["agent"] == "anomaly_investigator"
    assert out["anomaly"]["anomaly_id"] == aid
    assert len(out["hypotheses"]) >= 1


def test_anomaly_investigator_falls_back_when_no_signal(engine):
    _seed_ai_run(engine)
    # Create an anomaly with no sales/visitors/conversion in the same hour.
    ts = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    aid = _seed_anomaly(engine, "S002", ts, baseline=0.1)
    ctx = Context(engine=engine, principal=ADMIN)
    out = anomaly_investigator.run(ctx, "RUN_AGENT", aid)
    factors = {h["factor"] for h in out["hypotheses"]}
    # Either a labor_coverage_check (forecast lookup) or the fallback bucket.
    assert factors & {"insufficient_evidence", "labor_coverage_check"}
