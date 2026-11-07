# StorePulse-AI

> **AI-Powered Store Operations · Workforce Intelligence · Decision Platform**
> Governed decision intelligence for multi-store retailers.

---

## Current status — M1 vertical slice (SPLH end-to-end)

This repository is in milestone **M1**. One metric — **Sales per Labor Hour (SPLH)** — ships end-to-end as a trusted KPI with lineage. The point is not to flash a dashboard; it is to earn the metric layer every later capability (forecasts, anomalies, agents, briefings) will depend on.

**What runs today**

- Deterministic synthetic data generator (5 stores × 90 days × hourly sales / labor / traffic).
- Pandera-validated ingest; invalid rows go to a `quarantine` table with a reason code.
- SQLAlchemy ORM against SQLite (ANSI-SQL; the swap to Postgres in M7 is a URL change — see [`docs/DECISIONS.md`](docs/DECISIONS.md) ADR-0001).
- `GET /api/v1/kpi/splh` returning value, unit, window, and a full **lineage** block (formula, metric version, source tables, row counts, assumptions).
- `GET /api/v1/stores`, `GET /api/v1/health`.
- Correlation ID on every response.
- Static HTML tile at `/` that exercises the KPI endpoint.
- 17 tests — unit (KPI math, Pandera rules, generator determinism) + integration (API).

**What is explicitly deferred** (see [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md))

| Deferred to | Items |
|---|---|
| M2 | Other KPIs (ATV, UPT, labor cost %, conversion); regional/store/anomaly APIs; auth/RBAC |
| M3 | Workforce forecasts; anomaly detection |
| M4 | Supervisor + specialist agents; typed tool gateway; MCP labor-rate server; human approval workflow |
| M5 | React + Vite frontend (Node not installed on this box — ADR-0002) |
| M6 | OWASP mapping, security test suite, OpenTelemetry/Prometheus |
| M7 | Docker Compose, Postgres container, release hardening |

**Synthetic data disclaimer.** All data in `data/synthetic/` is machine-generated from a deterministic seed. It is not real sales, labor, or customer data and is labeled as such in every KPI response (`lineage.synthetic_data: true`).

---

## Problem framing

Large multi-store retailers run on fragmented data: POS, workforce management, inventory, labor sheets, exception emails, BI tools. A regional manager typically touches 6+ systems before 9am and reconciles the gaps in a spreadsheet. The data exists. The decision window doesn't.

Problem card 05 in the architecture briefing names the first failure mode StorePulse-AI must fix: **"KPIs that disagree across teams."** Three teams, three versions of "sales per labor hour," decision cycles stall on definitional debates instead of action. That is why the first production metric shipped is SPLH, with a lineage block every consumer can audit.

The full problem framing, 9-capability map, system architecture, and agentic workflow diagrams are in [`docs/architecture.html`](docs/architecture.html).

---

## Quick start

> Requirements: Python 3.11+. No Docker, no Node, no Postgres needed for M1.

```bash
# 1. Install pinned dependencies
python -m pip install -e ".[dev]"

# 2. Generate the deterministic synthetic dataset
python -m scripts.generate_data --seed 42 --days 90 --out data/synthetic

# 3. Create the SQLite schema, validate, and load
python -m scripts.load_db --in data/synthetic

# 4. Run the tests (17 pass)
python -m pytest tests/ -v

# 5. Start the API + tile
python -m uvicorn apps.api.main:app --port 8000
```

Open `http://localhost:8000/` for the SPLH tile. The API is at `http://localhost:8000/api/v1/*` and OpenAPI docs at `http://localhost:8000/docs`.

### Sample API call

```bash
curl -s "http://localhost:8000/api/v1/kpi/splh?store_id=S001&from=2026-09-24T00:00:00Z&to=2026-10-01T00:00:00Z" | python -m json.tool
```

Example response (synthetic):

```json
{
  "value": 13.4171,
  "unit": "USD per labor hour",
  "window": { "from_utc": "2026-09-24T00:00:00Z", "to_utc": "2026-10-01T00:00:00Z" },
  "lineage": {
    "metric_name": "sales_per_labor_hour",
    "metric_version": "1.0.0",
    "formula": "sum(net_sales_cents) / 100.0 / nullif(sum(labor_minutes) / 60.0, 0) scoped to one store over [from_utc, to_utc)",
    "source_tables": ["sales_txn", "labor_hours"],
    "row_counts": { "sales_txn": 84, "labor_hours": 84 },
    "assumptions": [
      "Net sales exclude tax; stored in integer cents.",
      "Labor hours exclude unpaid breaks (synthetic data assumption).",
      "Window is half-open [from_utc, to_utc); times in UTC."
    ],
    "synthetic_data": true
  },
  "status": "ok"
}
```

### Insufficient evidence

If the window has no labor hours, the API returns `value: null` and `status: "insufficient_evidence"` rather than fabricating a number — per guardrail 02 in the architecture ("Evidence or silence").

---

## Architecture (M1)

```
apps/api/                 FastAPI app + static HTML tile
packages/
  domain/                 Pure-Python entities (KpiWindow, KpiValue, KpiLineage)
  analytics/              KPI service (SPLH)
  shared_contracts/       Pydantic response schemas
  validation/             Pandera dataframe schemas
infrastructure/db/        SQLAlchemy models + engine + ingest with quarantine
scripts/                  generate_data.py, load_db.py
data/synthetic/           Generator output (gitignored, marker file included)
tests/{unit,integration}/
docs/
  IMPLEMENTATION_PLAN.md  Milestones and acceptance criteria
  DECISIONS.md            ADRs (0001–0006)
  IMPLEMENTATION_STATUS.md  Living status of what runs
  architecture.html       Full architecture briefing (from v0.1 prototype)
  screens/                Design prototypes (not wired to the live API yet)
```

Domain logic depends on neither FastAPI, SQLAlchemy, nor Pandera — all framework concerns live in `infrastructure/` and `apps/`. This boundary pays off at M3 when the forecasting module needs the same `KpiWindow` abstraction.

---

## Design guarantees

- **Deterministic analytics.** No LLM involvement in M1. The KPI service is SQL.
- **Every KPI carries lineage.** Formula, metric version, source tables, row counts, assumptions. Per ADR-0004.
- **Evidence or silence.** No value is returned without lineage. Empty windows return `null`, not `0`.
- **Money in integer cents.** Division to USD happens at the presentation boundary only. Per ADR-0006.
- **Half-open windows `[from, to)`.** Documented in the response and tested.
- **Synthetic data labeled.** Every KPI response carries `lineage.synthetic_data: true` until real data lands.

---

## Known gaps / honest status

- **Postgres is M7**, not now. SQLAlchemy + ANSI-SQL keeps the port cheap. See ADR-0001.
- **React frontend is M5**, not now. The tile at `/` is a single static HTML page that calls the real API. The polished design prototypes in `docs/screens/` are not yet wired to the live backend. See ADR-0002.
- **No auth / RBAC yet**. All endpoints are open on `localhost`. Auth lands in M2 before any shared environment.
- **Observability**: correlation IDs only. OpenTelemetry, Prometheus, and Grafana land in M6.
- **AI governance docs** (`AI_GOVERNANCE.md`, `SECURITY_THREAT_MODEL.md`, `OWASP_CONTROL_MAPPING.md`) are scaffolded but not written — they belong to M4 and M6 where the controls they describe actually exist.
- **No labor-rate MCP, no agents**. Per plan — they land in M4 on top of a trusted KPI layer.
- **The synthetic labor model is intentionally generous** (SPLH around $13/hr in this seed). The pipeline is correct; demand-shaped labor baselines land in M3 along with forecasts.

---

## Running the tests

```bash
python -m pytest tests/ -v
```

Expected result as of this milestone: **17 passed**.

| Suite | Count | Covers |
|---|---|---|
| `tests/unit/test_splh_math.py` | 5 | Normal case, empty window, zero labor, half-open semantics, invalid `KpiWindow` |
| `tests/unit/test_pandera_schemas.py` | 3 | Negative sales, bad store_id format, wrong currency — all quarantined |
| `tests/unit/test_generator_determinism.py` | 2 | Same seed = same output; different seed = different output |
| `tests/integration/test_api_splh.py` | 7 | Health, stores list, happy path, 404 unknown store, 422 bad format, 400 reversed window, insufficient evidence |

---

## Next milestone — M2 preview

- Add remaining KPIs (ATV, UPT, labor cost %, conversion) using the same lineage envelope.
- `/api/v1/regions`, `/api/v1/stores/{id}/performance` endpoints.
- Metric catalog seeded with every metric; version-pin every response.
- Local-demo auth mode (static bearer) + per-store/region scope enforcement.
- Data-quality run view at `/api/v1/data-quality/runs`.

See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) for the full roadmap.
