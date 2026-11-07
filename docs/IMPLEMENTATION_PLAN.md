# StorePulse-AI — Implementation Plan

> Working plan, kept in lockstep with code. Status in `IMPLEMENTATION_STATUS.md`.
> Decisions and trade-offs in `DECISIONS.md`.

## Guiding principles

- Modular monolith, clean architecture, domain-independent core.
- Deterministic analytics first; AI only where it adds interpretation value.
- Every KPI value carries lineage: source rows, window, metric version.
- Synthetic data is **always** labeled synthetic.
- No LLMs, agents, MCP, or human-review workflow until the metric layer is trusted.

## Milestone roadmap

| # | Milestone | Scope | Status |
|---|-----------|-------|--------|
| M0 | Repo discovery & plan | This document + `DECISIONS.md` | ✅ in progress |
| M1 | **Vertical slice: SPLH end-to-end** | Synthetic data → SQLite (Postgres-compatible via SQLAlchemy) → Pandera validation → KPI service → FastAPI endpoint → HTML tile → tests | 🟡 active |
| M2 | Core KPI catalog & regional/store APIs | Add sales-vs-target, ATV, UPT, labor cost %, conversion where valid; per-store and per-region endpoints; data-quality run view | ⏳ |
| M3 | Workforce forecast & anomaly detection | Baseline seasonal + ML forecast with WAPE/MAE; threshold + statistical anomalies with evidence records | ⏳ |
| M4 | Agentic workflows & MCP labor-rate server | Supervisor + specialist agents, typed tool gateway, read-only MCP, deterministic fake LLM provider for tests, human approval workflow | ⏳ |
| M5 | Frontend experience | React + Vite app covering all 11 screens, loading/error/stale states, accessibility | ⏳ |
| M6 | Governance, security, observability | OWASP control mapping, security test suite, OpenTelemetry/Prometheus, operational runbook | ⏳ |
| M7 | Release hardening | Full test suite green, fresh-clone verification, Docker Compose, honest status report | ⏳ |

## M1 — Vertical slice: SPLH end-to-end (this milestone)

### Scope

| Layer | What ships |
|---|---|
| Data | Deterministic generator: 5 stores × 90 days × hourly `sales_txn`, `labor_hours`, `traffic_count` rows. Fixed seed. |
| Schema | SQLAlchemy ORM models (SQLite today, Postgres-ready) for `store`, `sales_txn`, `labor_hours`, `traffic_count`, `metric_catalog`. |
| Validation | Pandera dataframe schemas applied on ingest. Invalid rows quarantined with reason code. |
| KPI service | `kpi.splh(store_id, from, to)` → value + lineage object (sum of net sales, sum of labor hours, row counts, metric version, window UTC). |
| API | `GET /api/v1/kpi/splh?store_id=&from=&to=` returning `{ value, unit, metric_version, window, lineage }`. `GET /api/v1/stores`. `GET /api/v1/health`. |
| UI | One static HTML tile served at `/` that fetches SPLH for a chosen store and date range. |
| Tests | Unit (KPI math edge cases + Pandera rules) + integration (API ↔ DB). Must pass before M2 starts. |

### Out of scope for M1 (explicitly deferred)

- Postgres container (SQLite until M7 Docker Compose). SQLAlchemy keeps the port cheap.
- React + Vite (Node unavailable here). Static HTML tile until M5.
- Auth / RBAC (M2).
- Any LLM or agent code (M4).
- All other KPIs beyond SPLH (M2).
- Anomaly, forecast, briefing (M3+).

### Acceptance criteria (M1)

1. `python -m scripts.generate_data --seed 42` produces identical rows across runs.
2. `python -m scripts.load_db` ingests via Pandera; invalid rows land in `quarantine` with a reason.
3. `pytest tests/` passes — at minimum: SPLH math (normal, zero-hours, empty window), Pandera rules, `/api/v1/kpi/splh` happy path and 404.
4. `uvicorn apps.api.main:app` serves the tile at `http://localhost:8000/`; tile renders the SPLH value with its lineage block.
5. Response payload carries `metric_version` and a `window_utc` field. No value is returned without lineage.

## Repo layout (M1 subset)

```
Retail-StorePulse-AI/
├── apps/api/                 # FastAPI app + static tile
├── packages/
│   ├── domain/               # entities, value objects (pure Python)
│   ├── analytics/            # KPI service (SPLH here)
│   ├── shared_contracts/     # Pydantic response schemas
│   └── validation/           # Pandera schemas
├── infrastructure/
│   └── db/                   # SQLAlchemy models, engine, ingestion
├── data/synthetic/           # generator output (gitignored)
├── scripts/                  # generate_data.py, load_db.py
├── tests/{unit,integration}/
└── docs/
```

Later milestones add `services/labor_rate_mcp/`, `packages/{forecasting, anomaly_detection, ai_orchestration, tool_gateway}`, `apps/web/`, `infra/{docker,prometheus,grafana}/`.

## Risk notes

- **SQLite vs Postgres parity.** Mitigated by using SQLAlchemy Core/ORM and ANSI SQL. No SQLite-specific features in queries.
- **Pandera on hourly rows is slow if applied row-by-row.** Apply once per batch at ingest; not on every API call.
- **Deterministic data drift.** Seed + fixed generator version pinned in `metric_catalog.synthetic_data_version`.
