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

---

## ADR-0013 · TMS integration through a read-only, horizon-capped MCP-style adapter

**Status:** Accepted · 2026-11-10

**Context.** Problem card 09: the Transport Management System (TMS) knows which DC trucks will land at each store days ahead, but labor planning never sees it. Stores staff receiving on the planned day, the truck slips, and paid hours go idle. The business rule is *"see every truck 5 days before it lands and plan labor around it."* The TMS is a vendor system of record; StorePulse must never write to it.

**Options.** (a) Agents query a TMS vendor API directly. (b) Nightly CSV dump read by SQL in each agent. (c) Validate the TMS export into a governed mirror table and expose it through an MCP-shaped adapter behind the typed tool gateway, exactly like the labor-rate reference (ADR-0008).

**Decision.** (c). `services/tms_mcp/server.py` exposes `list_inbound_shipments`, `get_inbound_shipment`, `get_tms_metadata` over the Pandera-validated `inbound_shipment` table. The adapter **caps the lookahead at `STOREPULSE_TMS_HORIZON_DAYS` (default 5)** at the Pydantic layer, so no agent can pull unbounded shipment history. Agents reach it only via `tms.shipments.list` / `tms.shipment.get`, which are allowlisted for the new `inbound_logistics` specialist alone; the scope check runs against the *owning* store so a guessed shipment id cannot leak another region's truck.

**Consequences.** Swapping the synthetic feed for the real TMS API or the official MCP SDK transport is below the gateway and touches no agent code. Every TMS response carries server name/version, `retrieved_at_utc`, and a `synthetic` flag. Write-back (re-routing, re-scheduling a truck) is structurally impossible from the agent side.

---

## ADR-0014 · The TMS → labor join is deterministic code; the LLM never computes it

**Status:** Accepted · 2026-11-10

**Context.** "Based on TMS data, labor planning should be done." The tempting design is to hand the LLM the truck list and the schedule and ask for a plan. That violates ADR-0007 (LLMs summarise, never calculate) and makes the output untestable.

**Decision.** `packages/inbound_planning/service.py::labor_plan_check` performs the per-day join (trucks due, trucks slipped/cancelled, estimated unload minutes, labor minutes planned for the day and for the morning receiving window) and classifies each day as `balanced`, `idle_risk`, `under_coverage`, `no_inbound`, or `insufficient_evidence`. Thresholds (`UNDER_COVERAGE_SHARE = 0.35`, receiving window 09:00–12:00) are named constants and appear in the lineage block. The Inbound Logistics Planner agent turns the classified days into dated findings; the Operations Advisor ranks inbound findings **ahead of** sales-trend heuristics because they are concrete, dated planning facts, and still passes every draft through the forbidden-language guard (ADR-0011). A day with trucks but no labor plan is reported as `insufficient_evidence`, never guessed.

**Consequences.** The same inputs always give the same plan; `tests/unit/test_inbound_planning.py` pins every status transition. Recommendations change *task sequencing and coverage*, never an individual's hours, and always land in `pending_review`.
