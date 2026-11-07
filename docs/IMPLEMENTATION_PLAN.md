# StorePulse-AI — Implementation Plan

> Working plan, kept in lockstep with code. Status in `IMPLEMENTATION_STATUS.md`.
> Decisions in `DECISIONS.md`.

## Guiding principles

- Modular monolith, clean architecture, framework-independent domain layer.
- Deterministic analytics first; LLMs only summarise verified evidence.
- Every KPI value carries lineage: source rows, window, metric version.
- Synthetic data is **always** labeled synthetic.
- Agents can only reach the world through the typed tool gateway.
- Human review is required for any operational recommendation.

## Milestone roadmap

| # | Milestone | Scope | Status |
|---|-----------|-------|--------|
| M0 | Repo discovery & plan | `IMPLEMENTATION_PLAN.md` + `DECISIONS.md` | ✅ |
| M1 | SPLH end-to-end | Synthetic data → SQLite → Pandera → KPI service → FastAPI → HTML tile → tests | ✅ |
| M2 | Core KPI catalog & regional/store APIs | ATV, UPT, labor cost %, conversion, visitors, net sales; metric catalog; RBAC scope; data-quality endpoint | ✅ |
| M3 | Workforce forecast & anomaly detection | Seasonal baseline + WAPE/MAE; robust z-score anomalies; evidence records | ✅ |
| M4 | Agentic workflows & MCP | Supervisor + 5 specialists; typed tool gateway; MCP labor-rate server; approval workflow; audit trail | ✅ |
| M5 | React + Vite frontend | All 11 screens, accessibility, responsive | ⏳ |
| M6 | Governance, security, observability | OWASP control mapping; threat model; OpenTelemetry + Grafana; pip-audit CI | 🟡 (logs + metrics shipped) |
| M7 | Release hardening | Docker Compose; Postgres; TLS; OIDC; MCP SDK transport | ⏳ |

## v1.0 snapshot — what's running now

Everything in M1 through M4 ships together as v1.0. See `IMPLEMENTATION_STATUS.md` for the full checklist; headline items:

- 5 specialist agents + 1 supervisor
- Typed tool gateway with per-agent allowlists
- In-process MCP-style labor-rate reference server
- Deterministic LLM provider (production provider pluggable)
- 8 KPIs with lineage, 1 forecast model, 1 anomaly detector, 1 approval workflow
- 63 tests green: unit + integration + security
- Structured JSON logs + Prometheus-text `/metrics`
- 3 static UI screens: Regional, Investigation, Approvals

## Risk register

- **SQLite ↔ Postgres parity.** Mitigated by SQLAlchemy ORM + ANSI-SQL discipline. (ADR-0001)
- **Deterministic LLM is not as expressive as a real one.** By design for v1.0. Swap is a one-line DI change. (ADR-0007)
- **MCP SDK transport not yet integrated.** v1.0 ships an MCP-shaped in-process adapter; agents call it through the same tool gateway, so the transport change is below the gateway. (ADR-0008)
- **Synthetic labor model is intentionally generous** (SPLH ~ $13/hr on seed 42). The pipeline is correct; demand-aligned labor baselines land in a later milestone.
