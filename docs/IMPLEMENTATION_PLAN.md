# StorePulse-AI — Implementation Plan

> Working plan, kept in lockstep with code. Status in `IMPLEMENTATION_STATUS.md`.
> Decisions in `DECISIONS.md`.

## Guiding principles

- Modular monolith, clean architecture, framework-independent domain layer.
- Deterministic analytics first; LLMs only summarise verified evidence.
- Every KPI value carries lineage: source rows, window, metric version.
- Synthetic data is **always** labeled synthetic.
- Agents can only reach the world through the typed tool gateway.
- External systems of record (labor-rate reference, TMS) are mirrored read-only and exposed through MCP-shaped adapters; StorePulse never writes back.
- Human review is required for any operational recommendation.

## Milestone roadmap

| # | Milestone | Scope | Status |
|---|-----------|-------|--------|
| M0 | Repo discovery & plan | `IMPLEMENTATION_PLAN.md` + `DECISIONS.md` | ✅ |
| M1 | SPLH end-to-end | Synthetic data → SQLite → Pandera → KPI service → FastAPI → HTML tile → tests | ✅ |
| M2 | Core KPI catalog & regional/store APIs | ATV, UPT, labor cost %, conversion, visitors, net sales; metric catalog; RBAC scope; data-quality endpoint | ✅ |
| M3 | Workforce forecast & anomaly detection | Seasonal baseline + WAPE/MAE; robust z-score anomalies; evidence records | ✅ |
| M4 | Agentic workflows & MCP | Supervisor + 5 specialists; typed tool gateway; MCP labor-rate server; approval workflow; audit trail | ✅ |
| M4.5 | **TMS integration & inbound labor planning (v1.1)** | TMS feed → Pandera → `inbound_shipment` mirror; MCP-style TMS adapter capped at 5 days; deterministic `inbound_labor_plan_check`; 6th specialist `inbound_logistics`; `plan_inbound_labor` workflow; Inbound screen; ADR-0013/0014 | ✅ |
| M4.6 | **Forecast-driven HVAC / energy (v1.2)** | BMS feed → Pandera → `hvac_schedule` + `energy_reading` mirrors; MCP-style BMS adapter; `visitors` forecast kind; deterministic, comfort-guarded `energy_hvac_schedule_check`; 7th specialist `energy_analyst`; `plan_store_energy` workflow; Energy screen + prototype; stakeholder briefing slides; ADR-0015 | ✅ |
| M5 | React + Vite frontend | All 12 screens, accessibility, responsive | ⏳ |
| M6 | Governance, security, observability | OWASP control mapping; threat model; OpenTelemetry + Grafana; pip-audit CI | 🟡 (logs + metrics shipped) |
| M7 | Release hardening | Docker Compose; Postgres; TLS; OIDC; MCP SDK transport; live TMS vendor API behind the same adapter | ⏳ |

## v1.2 snapshot — what's running now

Everything in M1 through M4.6 ships together as v1.2. See `IMPLEMENTATION_STATUS.md` for the full checklist; headline items:

- 7 specialist agents + 1 supervisor with 5 workflows (`investigate_store`, `plan_inbound_labor`, `plan_store_energy`, `investigate_anomaly`, `daily_briefing`)
- Typed tool gateway with per-agent allowlists (18 tools)
- Three in-process MCP-style adapters: labor-rate reference, TMS inbound shipments (5-day horizon cap), BMS HVAC schedule + meter (read-only)
- Deterministic LLM provider (production provider pluggable)
- 8 KPIs with lineage, 1 forecast model (labor, sales, visitors), 1 anomaly detector, 1 inbound labor-plan check, 1 energy HVAC-schedule check, 1 approval workflow
- 253 tests green: unit + integration + security
- Structured JSON logs + Prometheus-text `/metrics`
- 5 static UI screens: Regional, Inbound (TMS), Energy (HVAC), Investigation, Approvals

## Risk register

- **SQLite ↔ Postgres parity.** Mitigated by SQLAlchemy ORM + ANSI-SQL discipline. (ADR-0001)
- **Deterministic LLM is not as expressive as a real one.** By design for v1.0. Swap is a one-line DI change. (ADR-0007)
- **MCP SDK transport not yet integrated.** v1.x ships MCP-shaped in-process adapters; agents call them through the same tool gateway, so the transport change is below the gateway. (ADR-0008, ADR-0013)
- **TMS data quality.** Real TMS feeds carry stale ETAs and duplicate shipment ids. The Pandera contract (unique ids, `delay == eta − planned`, status enum) quarantines them with a reason code instead of letting them reach the planner. (ADR-0013)
- **Receiving-labor estimates are TMS-supplied.** `unload_minutes_est` is treated as evidence with a named source; actual unload time varies by load mix. Thresholds are constants in the lineage block and business-overridable. (ADR-0014)
- **Energy savings are estimates until measured.** The check prices avoidable hours from metered kWh, but annualized dollars assume the pattern repeats and use a configurable tariff. A pilot should confirm with weather-normalised before/after metering. (ADR-0015)
- **Comfort and food safety outrank savings.** The recommended start is derived from the BMS-measured p90 warm-up and re-verified by the advisor's comfort guard; nothing is written to the BMS. (ADR-0015)
- **Synthetic labor model is intentionally generous** (SPLH ~ $13/hr on seed 42). The pipeline is correct; demand-aligned labor baselines land in a later milestone.
