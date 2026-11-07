# Architecture Decision Log

Append-only. Each decision records context, options, choice, and consequences.

---

## ADR-0001 · SQLite for v1.0, Postgres from M7

**Status:** Accepted · 2026-11-07

**Context.** Dev machine has Python 3.11 but no Docker or Postgres client. v1.0 must run on a fresh clone without infrastructure.

**Decision.** SQLite behind SQLAlchemy. Avoid SQLite-specific syntax. All datetimes stored as canonical `YYYY-MM-DD HH:MM:SS.ffffff` UTC strings. Will map to `TIMESTAMPTZ` on Postgres.

**Consequences.** Engine swap in M7 is a connection-string change plus `alembic upgrade head`. Transactional guarantees and concurrency differ; v1.0 does not rely on row-level locking.

---

## ADR-0002 · Static HTML tiles for v1.0, React + Vite from M5/M8

**Status:** Accepted · 2026-11-07

**Context.** Node is not installed. v1.0 goal is a trusted backend + a UI that proves the flow works, not production chrome.

**Decision.** Three static HTML pages at `/`, `/investigation.html`, `/approvals.html`, all against the real API.

**Consequences.** No build step. Full React app lands when Node is available.

---

## ADR-0003 · Pandera for data contracts

**Status:** Accepted · 2026-11-07

**Context.** Dataframe-wide validation needed at ingest with clear quarantine behavior.

**Decision.** Pandera (pandas backend), pinned to 0.20.4 (flat `pandera` namespace). Validation runs once per batch at ingest only; failures routed to `quarantine` with reason codes. Never called in the API hot path.

---

## ADR-0004 · KPI lineage is part of every response

**Status:** Accepted · 2026-11-07

**Context.** Problem card 05 ("KPIs that disagree across teams") is a top failure mode. Every KPI value must be defensible.

**Decision.** Every KPI response carries a `lineage` object: `metric_name`, `metric_version`, `formula`, `window_utc`, `source_tables`, `row_counts`, `assumptions`, `synthetic_data`. No endpoint returns a bare number.

**Consequences.** Response payload is larger. Acceptable — this is a decision-support platform; audit > brevity.

---

## ADR-0005 · Metric: Sales per Labor Hour first; broaden to 8 metrics in v1.0

**Status:** Accepted · 2026-11-07

**Context.** SPLH is the canonical disagreement metric. Shipping it end-to-end first proved the pipeline. v1.0 extends to the full set the agents need.

**Decision.** v1.0 catalog: `net_sales_usd`, `transactions`, `atv_usd`, `upt`, `sales_per_labor_hour`, `visitors`, `conversion_rate`, `labor_cost_pct`.

---

## ADR-0006 · Money stored in integer cents

**Status:** Accepted · 2026-11-07

Floating-point money drifts. All currency amounts stored as `BIGINT` cents; division to USD happens at the presentation boundary only.

---

## ADR-0007 · Deterministic LLM provider by default

**Status:** Accepted · 2026-11-07

**Context.** An LLM in the loop must not invent facts or calculations. Tests must be byte-stable. The build machine has no API credentials.

**Decision.** `LlmProvider` is abstract. The default `DeterministicProvider` renders evidence as human-readable text via a fixed template. Real providers (Anthropic, OpenAI, Bedrock) plug in via the same interface and are swapped in `apps/api/dependencies.get_api_llm()` without touching any agent code.

**Consequences.** The platform is testable and reproducible without external credentials. The LLM can never substitute invented numbers for the ones in `evidence`.

---

## ADR-0008 · In-process MCP-style adapter for v1.0

**Status:** Accepted · 2026-11-07

**Context.** The official MCP Python SDK's stdio/SSE transport adds lifecycle, process-management, and deployment complexity. v1.0 must stay runnable on a plain Python install.

**Decision.** The Labor Rate Reference server exposes an MCP-shaped surface (`list_tools`, `call_tool`, typed input/output, read-only, allowlisted) in process. Pluggable transport in M7.

**Consequences.** All MCP contract tests pass against this shape; swapping transports does not affect agent code because agents call `mcp.call_tool(...)` through the typed tool gateway, not directly.

---

## ADR-0009 · Hand-rolled supervisor over LangGraph

**Status:** Accepted · 2026-11-07

**Context.** LangGraph is powerful, but its node/edge abstraction adds surface area that does not help with v1.0's three bounded workflows.

**Decision.** A small `Supervisor` class with explicit methods `investigate_store`, `investigate_anomaly`, `daily_briefing`. Step/token/timeout budgets enforced inline.

**Consequences.** Full audit trail and budget enforcement in ~200 lines. If future workflows become non-linear with retry/branch logic, migration to LangGraph is still open.

---

## ADR-0010 · Per-agent tool allowlist, not a global one

**Status:** Accepted · 2026-11-07

**Context.** A global "agents can call any tool" allowlist violates least-privilege.

**Decision.** `AGENT_ALLOWLISTS` in `packages/tool_gateway/tools.py` names exactly which tools each agent may call. The gateway enforces this before validation. `test_tool_denied_when_outside_allowlist` proves the briefing agent cannot call the labor-rate MCP tool.

---

## ADR-0011 · Operations Advisor has a forbidden-language guard

**Status:** Accepted · 2026-11-07

**Context.** Even with scope checks, an LLM-composed recommendation could propose action language that violates responsible-workforce commitments.

**Decision.** `operations_advisor._policy_check` runs over the draft title + rationale. Any match ("cut hours", "reduce hours", "layoff", …) rewrites the recommendation to a safe "hold current plan" default and records the violation.

**Consequences.** A hostile or misconfigured LLM cannot leak unsafe suggestions into the approval queue.

---

## ADR-0012 · JSON logs + Prometheus-text exposition, no new deps

**Status:** Accepted · 2026-11-07

**Context.** Observability must ship with v1.0, but dragging in OpenTelemetry + a collector before M6 is premature.

**Decision.** `packages/observability/logging.py` emits one JSON object per log line with correlation ID. `packages/observability/metrics.py` renders counters and summaries in Prometheus text exposition format. Both are pure-stdlib Python.

**Consequences.** Grafana dashboards are a config change when the collector lands; no code rewrite.
