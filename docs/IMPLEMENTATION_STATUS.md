# Implementation Status

Updated as work progresses. The source of truth for "what actually runs."

**Last updated:** 2026-11-07

## Milestone progress

| Milestone | Status | Notes |
|---|---|---|
| M0 · Plan & decisions | ✅ complete | `IMPLEMENTATION_PLAN.md`, `DECISIONS.md` (ADR-0001 to ADR-0006) |
| M1 · SPLH vertical slice | ✅ **complete — 17/17 tests passing** | See table below |
| M2 · Core KPI catalog & regional/store APIs | ⏳ not started | |
| M3 · Workforce forecast & anomaly detection | ⏳ | |
| M4 · Agentic workflows & MCP labor-rate server | ⏳ | |
| M5 · React + Vite frontend | ⏳ | blocked on Node install |
| M6 · Governance, security, observability | ⏳ | |
| M7 · Release hardening (Postgres, Docker) | ⏳ | |

## M1 deliverables — all green

| Item | Status | Evidence |
|---|---|---|
| Repo scaffold (modular monolith layout) | ✅ | `apps/`, `packages/`, `infrastructure/`, `scripts/`, `tests/` |
| Deterministic synthetic generator | ✅ | `python -m scripts.generate_data --seed 42` → 5 stores × 5,400 rows per fact table, byte-stable across runs (test verified) |
| SQLAlchemy models (ANSI-SQL, Postgres-ready) | ✅ | `infrastructure/db/models.py` — `store`, `sales_txn`, `labor_hours`, `traffic_count`, `metric_catalog`, `quarantine` |
| Pandera schemas + quarantine on failure | ✅ | `packages/validation/schemas.py`, `infrastructure/db/ingest.py` — tested with 3 failure modes |
| KPI service: SPLH with lineage | ✅ | `packages/analytics/kpi_service.py` — formula, version, row counts, assumptions on every call |
| FastAPI endpoints | ✅ | `/api/v1/health`, `/api/v1/stores`, `/api/v1/kpi/splh` |
| Static HTML tile wired to live API | ✅ | `apps/api/static/index.html` served at `/` |
| Unit tests | ✅ | 10 passing (SPLH math 5, Pandera 3, generator determinism 2) |
| Integration tests | ✅ | 7 passing (health, stores, SPLH happy / 404 / 422 / 400 / insufficient evidence) |
| README with exact run commands | ✅ | `README.md` |
| Correlation ID middleware | ✅ | Echoed back as `x-correlation-id` header |

## Verified end-to-end

```
python -m scripts.generate_data --seed 42 --days 90 --out data/synthetic
# -> wrote 5,400 rows per fact table

python -m scripts.load_db --in data/synthetic
# -> store 5, sales_txn 5400, labor_hours 5400, traffic_count 5400, 0 quarantined
# -> metric_catalog seeded for sales_per_labor_hour v1.0.0

python -m pytest tests/ -v
# -> 17 passed in 2.45s

python -m uvicorn apps.api.main:app --port 8000
# -> GET /api/v1/kpi/splh?store_id=S001&from=2026-09-24T00:00:00Z&to=2026-10-01T00:00:00Z
#    -> {"value":13.4171, "unit":"USD per labor hour", "lineage":{...}, "status":"ok"}
```

## Known gaps / deferred

- **Postgres** — SQLAlchemy keeps it a one-line swap. Target: M7.
- **React + Vite frontend** — Node not installed; static HTML tile stands in. Target: M5.
- **Auth / RBAC** — endpoints are open on localhost only. Target: M2.
- **All KPIs except SPLH** — reserved for M2.
- **LLMs, agents, MCP, human-approval workflow** — target M4.
- **Anomaly & forecast** — target M3.
- **OWASP mapping, security tests, OpenTelemetry/Prometheus** — target M6.

## Issues resolved during M1

- **Pandera import path** — pinned 0.20.4 uses flat `pandera` namespace (not `pandera.pandas` of 0.21+).
- **In-memory SQLite shared across connections** — engine factory now uses `StaticPool` for `:memory:` URLs so FastAPI's TestClient and the fixture see the same DB.
- **SQLite datetime round-trip** — pandas writes `YYYY-MM-DD HH:MM:SS.ffffff`; query params now normalize to the same canonical form so string comparisons match. Documented in `kpi_service._as_db_dt`.
