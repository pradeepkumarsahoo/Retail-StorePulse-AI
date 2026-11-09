"""Additional unit tests for the tool gateway that complement test_tool_gateway.

Covers:
- Argument validation via Pydantic (ToolInvocationError)
- Metric counters incremented for each outcome
- The MCP labor-rate tool proxies through the gateway
- stores.list respects the principal's visibility
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.api.security import Principal
from packages.observability import metrics
from packages.tool_gateway.gateway import (
    ToolDenied,
    ToolInvocationError,
    ToolNotFound,
    call_tool,
)
from packages.tool_gateway.tools import Context


ADMIN = Principal(subject="alex.admin", role="platform_admin",
                  regions=frozenset({"*"}), stores=frozenset({"*"}))
WEST = Principal(subject="rachel.west", role="regional_manager",
                 regions=frozenset({"West"}), stores=frozenset())


def _seed_ai_run(engine, run_id: str = "RUN_GW") -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ai_run (run_id, created_at_utc, user_subject, intent, store_scope, "
            "status, step_count, token_budget, tokens_used, model_name, prompt_version) "
            "VALUES (:r, '2026-10-01 00:00:00', :u, 'test', NULL, 'ok', 0, 1000, 0, "
            "'deterministic-template', '1.0.0')"
        ), {"r": run_id, "u": ADMIN.subject})


def test_invalid_args_raises_invocation_error_and_records(engine):
    metrics.reset()
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    # Missing required `from_utc`/`to_utc`.
    with pytest.raises(ToolInvocationError):
        call_tool(ctx, "RUN_GW", 1, "store_performance", "kpi.net_sales",
                  {"store_id": "S001"})
    with engine.connect() as conn:
        n_err = conn.execute(text(
            "SELECT COUNT(*) FROM tool_call WHERE run_id='RUN_GW' AND status='error'"
        )).scalar()
    assert n_err == 1


def test_invalid_store_pattern_raises_invocation_error(engine):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    with pytest.raises(ToolInvocationError):
        call_tool(ctx, "RUN_GW", 1, "store_performance", "kpi.net_sales", {
            "store_id": "BAD",  # doesn't match ^S\d{3}$
            "from_utc": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "to_utc": datetime(2026, 10, 1, 1, tzinfo=timezone.utc),
        })


def test_unknown_tool_increments_denied_counter(engine):
    metrics.reset()
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    with pytest.raises(ToolNotFound):
        call_tool(ctx, "RUN_GW", 1, "store_performance", "kpi.nope", {})
    out = metrics.render_prometheus()
    assert 'storepulse_tool_calls_total{status="denied",tool="kpi.nope"} 1' in out


def test_denied_outside_allowlist_increments_denied_counter(engine):
    metrics.reset()
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    with pytest.raises(ToolDenied):
        call_tool(ctx, "RUN_GW", 1, "briefing", "mcp.labor_rate.get",
                  {"region": "West", "role_category": "associate",
                   "effective_date": "2026-10-01"})
    out = metrics.render_prometheus()
    assert 'storepulse_tool_calls_total{status="denied",tool="mcp.labor_rate.get"} 1' in out


def test_successful_call_increments_ok_counter_and_duration(engine, seed_window):
    metrics.reset()
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    out = call_tool(ctx, "RUN_GW", 1, "store_performance", "kpi.net_sales", {
        "store_id": "S001", "from_utc": start, "to_utc": end,
    })
    assert out["metric_name"] == "net_sales_usd"
    rendered = metrics.render_prometheus()
    assert 'storepulse_tool_calls_total{status="ok",tool="kpi.net_sales"} 1' in rendered
    assert "storepulse_tool_call_ms_count" in rendered


def test_mcp_labor_rate_tool_is_proxied_through_gateway(engine, seed_window):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    out = call_tool(ctx, "RUN_GW", 1, "workforce", "mcp.labor_rate.get", {
        "region": "West", "role_category": "associate",
        "effective_date": seed_window[0].strftime("%Y-%m-%d"),
    })
    assert out["region"] == "West"
    assert out["source"] == "synthetic_reference"


def test_stores_list_filters_to_principal_scope(engine):
    _seed_ai_run(engine)
    ctx_west = Context(engine=engine, principal=WEST)
    out = call_tool(ctx_west, "RUN_GW", 1, "store_performance", "stores.list", {})
    regions = {s["region"] for s in out["stores"]}
    assert regions == {"West"}


def test_anomaly_detail_scope_denied(engine):
    """Anomalies the principal cannot see are rejected via PermissionError → ToolDenied."""
    _seed_ai_run(engine)
    # Seed a Midwest anomaly, query it as the WEST principal.
    import uuid
    from packages.anomaly_detection.detector import AnomalyFinding, persist_findings
    aid = str(uuid.uuid4())
    persist_findings(engine, [AnomalyFinding(
        anomaly_id=aid, store_id="S002",
        ts_utc=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        kind="net_sales_hourly", severity="medium",
        observed=1.0, baseline=1.0, deviation=0.0, z_score=0.0,
        evidence={},
    )])
    ctx = Context(engine=engine, principal=WEST)
    with pytest.raises(ToolDenied):
        call_tool(ctx, "RUN_GW", 2, "anomaly_investigator", "anomaly.detail",
                  {"anomaly_id": aid})


def test_tool_call_records_sequence_and_agent(engine, seed_window):
    _seed_ai_run(engine)
    start, end = seed_window
    ctx = Context(engine=engine, principal=ADMIN)
    call_tool(ctx, "RUN_GW", 7, "store_performance", "kpi.splh", {
        "store_id": "S001", "from_utc": start, "to_utc": end,
    })
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT seq, agent_name, tool_name, status FROM tool_call "
            "WHERE run_id='RUN_GW' ORDER BY id DESC LIMIT 1"
        )).one()
    assert row.seq == 7
    assert row.agent_name == "store_performance"
    assert row.tool_name == "kpi.splh"
    assert row.status == "ok"
