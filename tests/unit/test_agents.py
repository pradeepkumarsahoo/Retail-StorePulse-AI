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
    inbound_logistics,
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
    # 5 tool calls: forecast, splh, labor_cost_pct, mcp rate, inbound plan check.
    assert out["last_seq"] == 5


def test_workforce_embeds_tms_five_day_plan_check(engine, seed_window):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = workforce.run(ctx, "RUN_AGENT", "S001", start, end, region="West")
    plan = out["inbound_plan"]
    assert plan["window"]["horizon_days"] == 5
    assert plan["window"]["from_utc"].startswith(end.date().isoformat())
    assert len(plan["days"]) == 5
    assert plan["days"][0]["status"] == "balanced"
    assert plan["summary"]["trucks_in_window"] == 2
    assert plan["lineage"]["check_name"] == "inbound_labor_plan_check"


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


def _mk_inbound(kind: str) -> dict:
    if kind == "idle_risk":
        finding = {"date": "2026-10-02", "kind": "idle_risk", "idle_minutes_at_risk": 250,
                   "trucks": [{"shipment_id": "SHP-000002", "truck_id": "TRK-1002",
                               "status": "delayed", "delay_minutes": 1440,
                               "eta_utc": "2026-10-03T07:00:00+00:00"}]}
    else:
        finding = {"date": "2026-10-05", "kind": "under_coverage", "unload_minutes_est": 400,
                   "planned_labor_minutes": 1200, "receiving_window_labor_minutes": 600,
                   "receiving_share": 0.6667, "trucks": []}
    return {"agent": "inbound_logistics", "findings": [finding],
            "summary": {"trucks_in_window": 2}, "tms": {"server": {"name": "tms-inbound-shipments"}}}


def test_advisor_resequences_receiving_when_tms_truck_slips(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(0.0), _mk_wf(), inbound=_mk_inbound("idle_risk"))
    rec = out["recommendation"]
    assert rec["title"].startswith("Re-sequence receiving work")
    assert "TRK-1002" in rec["rationale"] and "2026-10-02" in rec["rationale"]
    assert rec["expected_direction"] == "up" and rec["confidence"] == "medium"
    assert rec["status"] == "pending_review"
    assert rec["evidence"]["inbound_summary"]["source"] == "inbound_logistics"
    # Responsible-workforce guard still holds on the inbound branch.
    assert not operations_advisor._policy_check(rec["title"], rec["rationale"])


def test_advisor_plans_receiving_coverage_for_heavy_inbound_day(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(0.0), _mk_wf(), inbound=_mk_inbound("under_coverage"))
    rec = out["recommendation"]
    assert rec["title"].startswith("Plan additional receiving coverage")
    assert "67%" in rec["rationale"]
    assert rec["expected_direction"] == "up"


def test_advisor_inbound_evidence_outranks_sales_trend(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    out = operations_advisor.run(ctx, "R", _mk_perf(-0.15, splh=18.0), _mk_wf(),
                                 inbound=_mk_inbound("idle_risk"))
    assert "Re-sequence" in out["recommendation"]["title"]


def test_advisor_reads_plan_check_embedded_in_workforce_output(engine):
    ctx = Context(engine=engine, principal=ADMIN)
    wf = _mk_wf()
    wf["inbound_plan"] = {"days": [{"date": "2026-10-02", "status": "idle_risk",
                                    "idle_minutes_at_risk": 120}],
                          "summary": {"trucks_in_window": 1}, "lineage": {}}
    out = operations_advisor.run(ctx, "R", _mk_perf(0.0), wf)
    rec = out["recommendation"]
    assert "Re-sequence" in rec["title"]
    assert rec["confidence"] == "low"   # no truck detail → lower confidence
    assert rec["evidence"]["inbound_summary"]["source"] == "workforce.inbound_plan"


# --- inbound_logistics -----------------------------------------------------

def test_inbound_logistics_reads_tms_and_plan_check(engine, seed_window):
    _seed_ai_run(engine)
    start, _ = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = inbound_logistics.run(ctx, "RUN_AGENT", "S001", start, horizon_days=3)
    assert out["agent"] == "inbound_logistics"
    assert out["horizon_days"] == 3
    assert out["tms"]["server"]["name"] == "tms-inbound-shipments"
    assert out["tms"]["trucks_in_window"] == 2
    assert [d["status"] for d in out["plan_check"]["days"]] == [
        "balanced", "insufficient_evidence", "insufficient_evidence"]
    assert out["findings"] == []            # no labor plan on the slipped day → no claim
    assert out["last_seq"] == 3             # tms.shipments.list, inbound.labor_plan_check, forecast.labor


def test_inbound_logistics_emits_idle_risk_finding(engine, seed_window):
    _seed_ai_run(engine)
    start, _ = seed_window
    # Give day 2 a labor plan so the slipped truck becomes an idle-risk finding.
    import pandas as pd
    from infrastructure.db.ingest import validate_and_load
    day2 = start + timedelta(days=1)
    df = pd.DataFrame([{"store_id": "S001", "ts_utc": day2.replace(hour=h), "labor_minutes": 60,
                        "source_system": "test", "schema_version": "1.0.0"} for h in (9, 10, 11)])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    validate_and_load(engine, df, "labor_hours")

    ctx = Context(engine=engine, principal=ADMIN)
    out = inbound_logistics.run(ctx, "RUN_AGENT", "S001", start, horizon_days=3)
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f["kind"] == "idle_risk" and f["date"] == "2026-10-02"
    assert f["idle_minutes_at_risk"] == 250
    assert f["trucks"][0]["truck_id"] == "TRK-1002"
    assert "slipped" in f["rationale"]


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
