# Implementation Status

Updated as work progresses. The source of truth for "what actually runs."

**Last updated:** 2026-11-10
**Suite:** 253 tests passing

## Milestone progress

| Milestone | Status | Notes |
|---|---|---|
| M0 · Plan & decisions | ✅ complete | `IMPLEMENTATION_PLAN.md`, `DECISIONS.md` |
| M1 · SPLH vertical slice | ✅ complete | Rolled into v1.0 |
| M2 · Core KPI catalog & scoped endpoints | ✅ complete | 8 KPIs + regions + metric-catalog + RBAC |
| M3 · Workforce forecast & anomalies | ✅ complete | Seasonal baseline (WAPE 4.2%) + robust-z anomaly detector |
| M4 · Agentic workflows & MCP labor-rate | ✅ complete | 5 specialists + supervisor + typed gateway + MCP adapter + approval workflow |
| M4.5 · **TMS integration & inbound labor planning (v1.1)** | ✅ complete | TMS mirror + MCP-style adapter (5-day cap) · `inbound_labor_plan_check` · 6th specialist `inbound_logistics` · `plan_inbound_labor` workflow · Inbound screen |
| M4.6 · **Forecast-driven HVAC / energy (v1.2)** | ✅ complete | BMS mirror (`hvac_schedule`, `energy_reading`) + MCP-style adapter · `visitors` forecast · `energy_hvac_schedule_check` (comfort-guarded) · 7th specialist `energy_analyst` · `plan_store_energy` workflow · Energy screen |
| M5 · Full React frontend | ⏳ | Static UI shipped (Regional, Inbound, Energy, Investigation, Approvals); React+Vite blocked on Node |
| M6 · Governance, security, observability hardening | 🟡 | JSON logs + Prometheus /metrics shipped; formal OWASP + threat-model sign-off pending |
| M7 · Release hardening (Docker, Postgres) | ⏳ | ANSI-SQL already Postgres-ready; container work pending |

## v1.2 deliverables — all green

| Item | Status | Where |
|---|---|---|
| Deterministic synthetic generator (+ TMS inbound feed, 5-day lookahead, + BMS schedule & hourly meter) | ✅ | `scripts/generate_data.py` |
| Pandera validation + quarantine (7 datasets incl. `inbound_shipment`, `hvac_schedule`, `energy_reading`) | ✅ | `packages/validation/schemas.py`, `infrastructure/db/ingest.py` |
| SQLAlchemy models (15 tables) | ✅ | `infrastructure/db/models.py` |
| KPI service — 8 metrics with lineage | ✅ | `packages/analytics/kpi_service.py` |
| Seasonal baseline forecast + WAPE/MAE | ✅ | `packages/forecasting/*` |
| Robust-z + coverage-gap anomaly detector | ✅ | `packages/anomaly_detection/detector.py` |
| **Inbound labor-plan check (TMS trucks ↔ labor plan, per day, with lineage)** | ✅ | `packages/inbound_planning/service.py` |
| **6 specialist agents** (incl. Inbound Logistics Planner) + supervisor (4 workflows) | ✅ | `packages/ai_orchestration/*` |
| Typed tool gateway with per-agent allowlists (16 tools) | ✅ | `packages/tool_gateway/*` |
| Deterministic fake LLM provider | ✅ | `packages/ai_orchestration/llm_provider.py` |
| Local MCP labor-rate reference server | ✅ | `services/labor_rate_mcp/server.py` |
| **MCP-style TMS adapter (read-only, horizon capped at 5 days)** | ✅ | `services/tms_mcp/server.py` |
| **MCP-style BMS adapter (read-only HVAC schedule + meter, 14-day history cap)** | ✅ | `services/bms_mcp/server.py` |
| **Energy HVAC-schedule check (BMS × visitor forecast × meter × TMS, per day, with lineage)** | ✅ | `packages/energy_planning/service.py` |
| **Energy Analyst (7th specialist) + `plan_store_energy` workflow, comfort guard** | ✅ | `packages/ai_orchestration/agents/energy_analyst.py`, `operations_advisor.py` |
| Energy API (`/energy/schedule`, `/energy/hvac-check`, `/agents/plan-store-energy`) + Energy screen | ✅ | `apps/api/routers/energy.py`, `apps/api/static/energy.html` |
| Audit trail + approval workflow | ✅ | `packages/governance/*` |
| Demo auth + RBAC + region/store scope | ✅ | `apps/api/security.py` |
| JSON structured logs + correlation IDs | ✅ | `packages/observability/logging.py` |
| Prometheus-text `/metrics` | ✅ | `packages/observability/metrics.py` |
| Full FastAPI surface (incl. `/inbound/shipments`, `/inbound/labor-plan`, `/agents/plan-inbound-labor`) | ✅ | `apps/api/routers/*` |
| Static UI: Regional, **Inbound (TMS)**, Investigation, Approvals | ✅ | `apps/api/static/*` |
| Unit tests (184 across 30 files) | ✅ | `tests/unit/` |
| Integration tests (27) | ✅ | `tests/integration/` |
| Security tests (2) | ✅ | `tests/security/` |
| README with SVG diagrams + live screenshots of the Inbound screen | ✅ | `README.md` + `docs/images/*` |

## Verified pipeline

```
python -m scripts.generate_data --seed 42 --days 90 --out data/synthetic
# wrote 5 stores + 5,400 rows per fact table + 144 TMS inbound shipments (deterministic)

python -m scripts.load_db --in data/synthetic
# loaded cleanly (inbound_shipment 144 / quarantined 0), metric_catalog 8, labor_rate 8

python -m scripts.run_forecast --horizon-days 14
# model=seasonal_baseline_dow_hour_trimmed_mean v1.0.0 wape=0.042 mae=19.5 rmse=25.0 n=840

python -m scripts.run_anomalies --days 30
# persisted anomalies: sales=220 labor_coverage=0

python -m scripts.run_inbound_check --as-of 2026-10-12
# S001: trucks=3 idle_risk_days=1 (2026-10-14, 325 min at risk) · S002 @ 10-20: heavy day, 67% share

python -m scripts.run_forecast --kind visitors --horizon-days 14
python -m scripts.run_energy_check
# S003 Loop Center: BMS 05:00-22:00 -> recommended 07:30-21:30, avoidable 515 kWh ($77.28) in 5 days, ~$4,986/yr
# All stores: ~$9,681/yr from schedules alone

python -m pytest tests/ -v
# 253 passed

python -m uvicorn apps.api.main:app --port 8000
# POST /api/v1/agents/plan-inbound-labor → 200, 17-step evidence trail, pending_review recommendation
# POST /api/v1/agents/plan-store-energy  → 200, 12-step evidence trail, comfort guard passed, pending_review
```

## Resolved issues during v1.0

- **SQLAlchemy 2.0 Row reserved attributes** (`Row.t` conflict) — SQL aliases renamed to `cents`, `txns`, `unts`, `mins`, `vis`, `n_rows`.
- **Pydantic `model_` namespace collision** on `ForecastPointResponse.model_name` — resolved with `model_config = {"protected_namespaces": ()}`.
- **Pandera 0.20 import path** — pinned version uses flat `pandera` namespace (vs 0.21+ `pandera.pandas`).
- **In-memory SQLite shared across connections** — engine factory uses `StaticPool` for `:memory:` URLs.
- **SQLite datetime format mismatch** — query params normalized to pandas-written `YYYY-MM-DD HH:MM:SS.ffffff`.
- **Windows cp1252 console** — unicode arrow glyph in print statements replaced with ASCII.

## Resolved issues during v1.1 (TMS)

- **Receiving share against a whole day's labor was meaningless (~4%).** The check now measures unload minutes against labor planned in the morning receiving window (09:00–12:00), where the load is actually worked.
- **Generator determinism.** The TMS feed is drawn *after* the sales/labor/traffic loop with the same RNG, so seed-42 fact tables stay byte-identical to v1.0.
- **Forecast timestamps carry a 36-minute offset** (train split at 80% of span). The plan check buckets by UTC day and by hour, so the offset does not matter.

## Resolved issues during v1.2 (energy)

- **Overnight forecast hours.** The seasonal baseline only predicts hours it has seen, so the visitor forecast covers open hours only and cleanly defines the occupied window.
- **Pricing an early start.** Shifting the start later still requires the warm-up, so early-start savings are priced at the metered *holding* load between reaching setpoint and opening, not at the warm-up surge.
- **Generator determinism.** The BMS feed is drawn after the TMS feed; a test pins that sales/labor/traffic/TMS tables are unchanged with the BMS feed on or off.

## Still deferred

- React + Vite frontend (M8).
- Real OIDC and TLS (M7).
- OpenTelemetry collector + Grafana dashboards (M6).
- Formal OWASP control-mapping sign-off (M6).
- MCP SDK stdio/sse transport for both adapters (M7).
- Live TMS vendor API / telematics GPS ETA (the adapter contract is ready; synthetic feed today).
- Live BMS / BACnet integration, weather-normalised M&V, and peak-demand optimisation (energy adapter contract is ready; synthetic feed today).
- External signals (weather/events/news), camera sentiment (M9, M11).
