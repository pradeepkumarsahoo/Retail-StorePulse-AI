from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.api.security import Principal
from packages.tool_gateway.gateway import ToolDenied, ToolNotFound, call_tool
from packages.tool_gateway.tools import Context


ADMIN = Principal(subject="alex.admin", role="platform_admin",
                  regions=frozenset({"*"}), stores=frozenset({"*"}))
WEST = Principal(subject="rachel.west", role="regional_manager",
                 regions=frozenset({"West"}), stores=frozenset())


def _seed_ai_run(engine, run_id: str = "RUN1"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ai_run (run_id, created_at_utc, user_subject, intent, store_scope, "
            "status, step_count, token_budget, tokens_used, model_name, prompt_version) "
            "VALUES (:r, '2026-10-01 00:00:00', :u, 'test', NULL, 'ok', 0, 1000, 0, "
            "'deterministic-template', '1.0.0')"
        ), {"r": run_id, "u": ADMIN.subject})


def test_unknown_tool_rejected(engine):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    with pytest.raises(ToolNotFound):
        call_tool(ctx, "RUN1", 1, "store_performance", "kpi.imaginary", {})


def test_tool_denied_when_outside_allowlist(engine):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    # briefing agent must not be able to call mcp.labor_rate.get
    with pytest.raises(ToolDenied):
        call_tool(ctx, "RUN1", 1, "briefing", "mcp.labor_rate.get",
                  {"region": "West", "role_category": "associate", "effective_date": "2026-10-01"})


def test_scope_denied_when_principal_cannot_see_store(engine):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=WEST)
    # S002 is Midwest — WEST principal cannot access it.
    with pytest.raises(ToolDenied):
        call_tool(ctx, "RUN1", 1, "store_performance", "kpi.net_sales",
                  {"store_id": "S002",
                   "from_utc": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
                   "to_utc": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)})


def test_call_recorded_in_tool_call_table(engine):
    _seed_ai_run(engine)
    ctx = Context(engine=engine, principal=ADMIN)
    out = call_tool(ctx, "RUN1", 1, "store_performance", "kpi.net_sales",
                    {"store_id": "S001",
                     "from_utc": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
                     "to_utc": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)})
    assert out["metric_name"] == "net_sales_usd"
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM tool_call WHERE run_id='RUN1' AND status='ok'"
        )).scalar()
    assert n == 1
