# Implementation Status

Updated as work progresses. The source of truth for "what actually runs."

**Last updated:** 2026-11-07
**Suite:** 63 tests passing

## Milestone progress

| Milestone | Status | Notes |
|---|---|---|
| M0 · Plan & decisions | ✅ complete | `IMPLEMENTATION_PLAN.md`, `DECISIONS.md` |
| M1 · SPLH vertical slice | ✅ complete | Rolled into v1.0 |
| M2 · Core KPI catalog & scoped endpoints | ✅ complete | 8 KPIs + regions + metric-catalog + RBAC |
| M3 · Workforce forecast & anomalies | ✅ complete | Seasonal baseline (WAPE 4.2%) + robust-z anomaly detector |
| M4 · Agentic workflows & MCP labor-rate | ✅ complete | 5 specialists + supervisor + typed gateway + MCP adapter + approval workflow |
| M5 · Full React frontend | ⏳ | Static UI shipped (Regional, Investigation, Approvals); React+Vite blocked on Node |
| M6 · Governance, security, observability hardening | 🟡 | JSON logs + Prometheus /metrics shipped; formal OWASP + threat-model sign-off pending |
| M7 · Release hardening (Docker, Postgres) | ⏳ | ANSI-SQL already Postgres-ready; container work pending |

## v1.0 deliverables — all green

| Item | Status | Where |
|---|---|---|
| Deterministic synthetic generator | ✅ | `scripts/generate_data.py` |
| Pandera validation + quarantine | ✅ | `packages/validation/schemas.py`, `infrastructure/db/ingest.py` |
| SQLAlchemy models (12 tables) | ✅ | `infrastructure/db/models.py` |
| KPI service — 8 metrics with lineage | ✅ | `packages/analytics/kpi_service.py` |
| Seasonal baseline forecast + WAPE/MAE | ✅ | `packages/forecasting/*` |
| Robust-z + coverage-gap anomaly detector | ✅ | `packages/anomaly_detection/detector.py` |
| 5 specialist agents + supervisor | ✅ | `packages/ai_orchestration/*` |
| Typed tool gateway with per-agent allowlists | ✅ | `packages/tool_gateway/*` |
| Deterministic fake LLM provider | ✅ | `packages/ai_orchestration/llm_provider.py` |
| Local MCP labor-rate reference server | ✅ | `services/labor_rate_mcp/server.py` |
| Audit trail + approval workflow | ✅ | `packages/governance/*` |
| Demo auth + RBAC + region/store scope | ✅ | `apps/api/security.py` |
| JSON structured logs + correlation IDs | ✅ | `packages/observability/logging.py` |
| Prometheus-text `/metrics` | ✅ | `packages/observability/metrics.py` |
| Full FastAPI surface (10+ routes) | ✅ | `apps/api/routers/*` |
| Static UI: Regional, Investigation, Approvals | ✅ | `apps/api/static/*` |
| Unit tests (32) | ✅ | `tests/unit/` |
| Integration tests (17) | ✅ | `tests/integration/` |
| Security tests (2) | ✅ | `tests/security/` |
| Industry-standard README with SVG diagrams | ✅ | `README.md` + `docs/images/*.svg` |

## Verified pipeline

```
python -m scripts.generate_data --seed 42 --days 90 --out data/synthetic
# wrote 5 stores + 5,400 rows per fact table (deterministic)

python -m scripts.load_db --in data/synthetic
# loaded cleanly, metric_catalog seeded with 8 metrics, labor_rate seeded with 8 rates

python -m scripts.run_forecast --horizon-days 14
# model=seasonal_baseline_dow_hour_trimmed_mean v1.0.0 wape=0.042 mae=19.5 rmse=25.0 n=840

python -m scripts.run_anomalies --days 30
# persisted anomalies: sales=220 labor_coverage=0

python -m pytest tests/ -v
# 63 passed in 11.71s

python -m uvicorn apps.api.main:app --port 8000
# http_request logged as structured JSON
# POST /api/v1/agents/investigate-store returns 200 in ~100ms with 13-step evidence trail
# GET /metrics returns Prometheus text exposition
```

## Resolved issues during v1.0

- **SQLAlchemy 2.0 Row reserved attributes** (`Row.t` conflict) — SQL aliases renamed to `cents`, `txns`, `unts`, `mins`, `vis`, `n_rows`.
- **Pydantic `model_` namespace collision** on `ForecastPointResponse.model_name` — resolved with `model_config = {"protected_namespaces": ()}`.
- **Pandera 0.20 import path** — pinned version uses flat `pandera` namespace (vs 0.21+ `pandera.pandas`).
- **In-memory SQLite shared across connections** — engine factory uses `StaticPool` for `:memory:` URLs.
- **SQLite datetime format mismatch** — query params normalized to pandas-written `YYYY-MM-DD HH:MM:SS.ffffff`.
- **Windows cp1252 console** — unicode arrow glyph in print statements replaced with ASCII.

## Still deferred

- React + Vite frontend (M8).
- Real OIDC and TLS (M7).
- OpenTelemetry collector + Grafana dashboards (M6).
- Formal OWASP control-mapping sign-off (M6).
- MCP SDK stdio/sse transport (M7).
- DC/truck tracking, external signals (weather/events/news), camera sentiment (M9-M11).
