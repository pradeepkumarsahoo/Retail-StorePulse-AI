"""Supervisor / Orchestrator.

- Classifies intent and selects which specialists to invoke.
- Enforces step budget, token budget, and timeout.
- Dispatches through the tool gateway only — never touches a DB directly.
- Records a single AiRun row + ToolCall rows + an AuditEvent for each run.
- Returns a structured JSON result; the caller surfaces it unchanged.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import text

from packages.ai_orchestration.agents import (
    anomaly_investigator,
    briefing,
    inbound_logistics,
    operations_advisor,
    store_performance,
    workforce,
)
from packages.ai_orchestration.llm_provider import DeterministicProvider, LlmProvider
from packages.analytics.kpi_service import list_stores
from packages.governance.audit import record_audit
from packages.observability import metrics
from packages.tool_gateway.tools import Context


log = logging.getLogger("storepulse.supervisor")


Intent = Literal["store_investigation", "anomaly_investigation", "daily_briefing",
                 "inbound_labor_planning"]


@dataclass
class RunBudget:
    max_steps: int = 20
    max_tokens: int = 2000
    timeout_s: float = 20.0


@dataclass
class RunResult:
    run_id: str
    status: str
    intent: Intent
    output: dict
    tokens_used: int
    step_count: int
    error: str | None = None


def _persist_run(ctx: Context, run_id: str, principal_subject: str, intent: str,
                 store_scope: str | None, model_name: str, prompt_version: str,
                 status: str, step_count: int, tokens_used: int, budget: RunBudget,
                 output: dict | None, error: str | None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with ctx.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_run (run_id, created_at_utc, user_subject, intent, "
                "store_scope, status, step_count, token_budget, tokens_used, model_name, "
                "prompt_version, output_json, error) VALUES "
                "(:r, :cr, :us, :in, :ss, :st, :sc, :tb, :tu, :mn, :pv, :oj, :er)"
            ),
            {
                "r": run_id, "cr": now, "us": principal_subject, "in": intent,
                "ss": store_scope, "st": status, "sc": step_count,
                "tb": budget.max_tokens, "tu": tokens_used,
                "mn": model_name, "pv": prompt_version,
                "oj": json.dumps(output, default=str) if output else None,
                "er": error,
            },
        )


class Supervisor:
    def __init__(self, llm: LlmProvider | None = None, budget: RunBudget | None = None):
        self.llm = llm or DeterministicProvider()
        self.budget = budget or RunBudget()

    def _check_budget(self, step: int, tokens: int, started: float) -> None:
        if step > self.budget.max_steps:
            raise RuntimeError(f"Step budget exceeded ({self.budget.max_steps})")
        if tokens > self.budget.max_tokens:
            raise RuntimeError(f"Token budget exceeded ({self.budget.max_tokens})")
        if time.perf_counter() - started > self.budget.timeout_s:
            raise RuntimeError(f"Timeout exceeded ({self.budget.timeout_s}s)")

    # --- Workflows -------------------------------------------------------

    def investigate_store(self, ctx: Context, store_id: str,
                          from_utc: datetime, to_utc: datetime) -> RunResult:
        """Store investigation: KPIs → workforce (incl. TMS 5-day plan check) → advisor."""
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        tokens = 0
        step = 0
        output: dict = {}
        status = "ok"
        error: str | None = None
        try:
            stores = list_stores(ctx.engine)
            match = next((s for s in stores if s["store_id"] == store_id), None)
            if not match:
                raise ValueError(f"Unknown store_id: {store_id}")
            region = match["region"]

            sp = store_performance.run(ctx, run_id, store_id, from_utc, to_utc)
            step = sp["last_seq"]
            self._check_budget(step, tokens, started)

            wf = workforce.run(ctx, run_id, store_id, from_utc, to_utc,
                               region, start_seq=step)
            step = wf["last_seq"]
            self._check_budget(step, tokens, started)

            advisor = operations_advisor.run(ctx, run_id, sp, wf, investigations=None,
                                             start_seq=step)
            output = {
                "store_performance": sp,
                "workforce": wf,
                "advisor": advisor,
            }
        except Exception as e:
            status = "error"
            error = str(e)
            log.exception("investigate_store_failed", extra={"run_id": run_id})
        _persist_run(ctx, run_id, ctx.principal.subject, "store_investigation",
                     store_id, self.llm.name, "1.0.0",
                     status, step, tokens, self.budget, output if status == "ok" else None, error)
        record_audit(ctx.engine, actor=ctx.principal.subject,
                     action="ai_run.store_investigation",
                     target_type="ai_run", target_id=run_id,
                     payload={"store_id": store_id, "status": status})
        metrics.inc("storepulse_agent_runs_total", intent="store_investigation", status=status)
        return RunResult(run_id=run_id, status=status, intent="store_investigation",
                         output=output, tokens_used=tokens, step_count=step, error=error)

    def investigate_anomaly(self, ctx: Context, anomaly_id: str) -> RunResult:
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        tokens = 0
        step = 0
        output: dict = {}
        status = "ok"
        error: str | None = None
        try:
            inv = anomaly_investigator.run(ctx, run_id, anomaly_id)
            step = inv["last_seq"]
            self._check_budget(step, tokens, started)
            output = {"investigation": inv}
        except Exception as e:
            status = "error"
            error = str(e)
            log.exception("investigate_anomaly_failed", extra={"run_id": run_id})
        _persist_run(ctx, run_id, ctx.principal.subject, "anomaly_investigation",
                     None, self.llm.name, "1.0.0",
                     status, step, tokens, self.budget, output if status == "ok" else None, error)
        record_audit(ctx.engine, actor=ctx.principal.subject,
                     action="ai_run.anomaly_investigation",
                     target_type="ai_run", target_id=run_id,
                     payload={"anomaly_id": anomaly_id, "status": status})
        metrics.inc("storepulse_agent_runs_total", intent="anomaly_investigation", status=status)
        return RunResult(run_id=run_id, status=status, intent="anomaly_investigation",
                         output=output, tokens_used=tokens, step_count=step, error=error)

    def plan_inbound_labor(self, ctx: Context, store_id: str, as_of_utc: datetime,
                           horizon_days: int = 5) -> RunResult:
        """TMS-driven labor planning: trucks 5 days out → labor plan check → recommendation.

        Specialists in order: inbound_logistics (TMS outlook + plan check),
        store_performance (trailing 7-day context), workforce (forecast, rate,
        embedded plan check), operations_advisor (draft, policy-guarded).
        """
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        tokens = 0
        step = 0
        output: dict = {}
        status = "ok"
        error: str | None = None
        try:
            stores = list_stores(ctx.engine)
            match = next((s for s in stores if s["store_id"] == store_id), None)
            if not match:
                raise ValueError(f"Unknown store_id: {store_id}")
            region = match["region"]

            inb = inbound_logistics.run(ctx, run_id, store_id, as_of_utc,
                                        horizon_days=horizon_days)
            step = inb["last_seq"]
            self._check_budget(step, tokens, started)

            ctx_from = as_of_utc - timedelta(days=7)
            sp = store_performance.run(ctx, run_id, store_id, ctx_from, as_of_utc,
                                       start_seq=step)
            step = sp["last_seq"]
            self._check_budget(step, tokens, started)

            wf = workforce.run(ctx, run_id, store_id, ctx_from, as_of_utc, region,
                               start_seq=step, inbound_horizon_days=horizon_days)
            step = wf["last_seq"]
            self._check_budget(step, tokens, started)

            advisor = operations_advisor.run(ctx, run_id, sp, wf, investigations=None,
                                             start_seq=step, inbound=inb)
            output = {
                "inbound_logistics": inb,
                "store_performance": sp,
                "workforce": wf,
                "advisor": advisor,
            }
        except Exception as e:
            status = "error"
            error = str(e)
            log.exception("plan_inbound_labor_failed", extra={"run_id": run_id})
        _persist_run(ctx, run_id, ctx.principal.subject, "inbound_labor_planning",
                     store_id, self.llm.name, "1.1.0",
                     status, step, tokens, self.budget, output if status == "ok" else None, error)
        record_audit(ctx.engine, actor=ctx.principal.subject,
                     action="ai_run.inbound_labor_planning",
                     target_type="ai_run", target_id=run_id,
                     payload={"store_id": store_id, "status": status,
                              "horizon_days": horizon_days})
        metrics.inc("storepulse_agent_runs_total", intent="inbound_labor_planning", status=status)
        return RunResult(run_id=run_id, status=status, intent="inbound_labor_planning",
                         output=output, tokens_used=tokens, step_count=step, error=error)

    def daily_briefing(self, ctx: Context, store_id: str, as_of_utc: datetime) -> RunResult:
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        tokens = 0
        step = 0
        output: dict = {}
        status = "ok"
        error: str | None = None
        try:
            br = briefing.run(ctx, run_id, store_id, as_of_utc, self.llm)
            step = br["last_seq"]
            tokens += br.get("model", {}).get("tokens_used", 0)
            self._check_budget(step, tokens, started)
            output = {"briefing": br}
        except Exception as e:
            status = "error"
            error = str(e)
            log.exception("daily_briefing_failed", extra={"run_id": run_id})
        _persist_run(ctx, run_id, ctx.principal.subject, "daily_briefing",
                     store_id, self.llm.name, "1.0.0",
                     status, step, tokens, self.budget, output if status == "ok" else None, error)
        record_audit(ctx.engine, actor=ctx.principal.subject,
                     action="ai_run.daily_briefing",
                     target_type="ai_run", target_id=run_id,
                     payload={"store_id": store_id, "status": status})
        metrics.inc("storepulse_agent_runs_total", intent="daily_briefing", status=status)
        return RunResult(run_id=run_id, status=status, intent="daily_briefing",
                         output=output, tokens_used=tokens, step_count=step, error=error)
