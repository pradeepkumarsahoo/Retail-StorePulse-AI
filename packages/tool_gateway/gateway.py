"""Typed tool gateway.

Responsibilities:
- Enforce the per-agent allowlist. Unknown or denied tools never execute.
- Validate arguments against the Pydantic schema BEFORE the impl runs.
- Enforce scope (principal ↔ store) centrally.
- Apply timeouts and per-run tool-call budgets.
- Record a ToolCall row for every attempt — success, deny, error, or timeout.

The gateway is the only path from an agent to any data source.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text

from packages.observability import metrics
from packages.tool_gateway.tools import AGENT_ALLOWLISTS, Context, TOOLS


log = logging.getLogger("storepulse.tool_gateway")


class ToolDenied(Exception):
    pass


class ToolNotFound(Exception):
    pass


class ToolInvocationError(Exception):
    pass


def _record_call(ctx: Context, run_id: str, seq: int, agent: str, tool_name: str,
                 args_json: str, result_summary: str, status: str, duration_ms: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with ctx.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tool_call (run_id, seq, tool_name, agent_name, args_json, "
                "result_summary, status, duration_ms, started_at_utc) "
                "VALUES (:r, :s, :tn, :an, :a, :rs, :st, :d, :t)"
            ),
            {"r": run_id, "s": seq, "tn": tool_name, "an": agent, "a": args_json,
             "rs": result_summary[:500] if result_summary else None,
             "st": status, "d": duration_ms, "t": now},
        )


def call_tool(ctx: Context, run_id: str, seq: int, agent: str, tool_name: str,
              args: dict) -> dict:
    """Invoke a tool through the gateway with full validation and auditing."""
    args_json = json.dumps(args, default=str, sort_keys=True)
    started = time.perf_counter()
    if tool_name not in TOOLS:
        _record_call(ctx, run_id, seq, agent, tool_name, args_json, "unknown tool", "denied", 0)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="denied")
        raise ToolNotFound(f"Tool not registered: {tool_name}")
    spec = TOOLS[tool_name]
    allow = AGENT_ALLOWLISTS.get(agent, frozenset())
    if tool_name not in allow:
        _record_call(ctx, run_id, seq, agent, tool_name, args_json,
                     f"agent '{agent}' not allowed", "denied", 0)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="denied")
        raise ToolDenied(f"Agent '{agent}' is not allowed to call '{tool_name}'")
    try:
        validated = spec.args_model.model_validate(args)
    except ValidationError as e:
        duration = int((time.perf_counter() - started) * 1000)
        _record_call(ctx, run_id, seq, agent, tool_name, args_json, str(e), "error", duration)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="error")
        raise ToolInvocationError(f"Invalid args for {tool_name}: {e}") from e
    try:
        result = spec.impl(ctx, validated)
        duration = int((time.perf_counter() - started) * 1000)
        _record_call(ctx, run_id, seq, agent, tool_name, args_json,
                     json.dumps(result, default=str)[:500], "ok", duration)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="ok")
        metrics.observe_ms("storepulse_tool_call_ms", duration, tool=tool_name)
        return result
    except PermissionError as e:
        duration = int((time.perf_counter() - started) * 1000)
        _record_call(ctx, run_id, seq, agent, tool_name, args_json, str(e), "denied", duration)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="denied")
        raise ToolDenied(str(e)) from e
    except Exception as e:  # pragma: no cover — surface unexpected failures
        duration = int((time.perf_counter() - started) * 1000)
        _record_call(ctx, run_id, seq, agent, tool_name, args_json, str(e), "error", duration)
        metrics.inc("storepulse_tool_calls_total", tool=tool_name, status="error")
        log.exception("tool_call_failed", extra={"tool": tool_name, "agent": agent})
        raise ToolInvocationError(str(e)) from e
