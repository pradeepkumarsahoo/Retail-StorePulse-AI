# Architecture Decision Log

Append-only. Each decision records context, options, choice, and consequences.

---

## ADR-0001 · SQLite for M1, Postgres from M7

**Status:** Accepted · 2026-11-07

**Context.** Local dev machine has Python 3.11 but no Docker and no Postgres client. The master plan targets Postgres for production. The first vertical slice must be runnable on a fresh clone without infrastructure.

**Options considered.**
1. Require Postgres locally → blocks progress until Docker is installed.
2. Postgres via embedded/portable binary (e.g. `pg_embed`) → non-standard, fragile on Windows.
3. **SQLite behind SQLAlchemy** → zero-install, ANSI-compatible path to Postgres.

**Decision.** Option 3. Use SQLAlchemy ORM + Core. Avoid SQLite-specific syntax (no `json_each`, no partial indexes with `WHERE`, no `JULIANDAY`). All datetimes stored as UTC ISO strings in SQLite, mapped to `TIMESTAMPTZ` on Postgres.

**Consequences.**
- Swap in M7 is a connection-string change plus `alembic upgrade head`.
- Transactional guarantees and concurrency differ; we do not rely on row-level locking in M1.
- Pandera validation runs in Python, not in DB, so the engine difference does not affect data-quality behavior.

---

## ADR-0002 · Static HTML tile for M1, React + Vite from M5

**Status:** Accepted · 2026-11-07

**Context.** Node is not installed on the dev machine. The prompt calls for a polished React frontend, but the M1 goal is a backed, trusted KPI tile — not a production UI.

**Decision.** Serve a single static HTML page at `/` from FastAPI's `StaticFiles` that calls the real API. The existing design prototypes in `docs/screens/` are referenced but not wired as the live UI.

**Consequences.**
- No build step, no npm, no bundler — the slice runs with `pip install` and `uvicorn`.
- Full React app lands in M5 when Node is available.

---

## ADR-0003 · Pandera for data contracts

**Status:** Accepted · 2026-11-07

**Context.** We need column-, row-, and cross-field-level validation at ingest with clear quarantine behavior. Pydantic covers payload validation; it is not ergonomic for dataframe-wide rules.

**Decision.** Pandera (pandas backend) for every ingest path. Validation runs once per batch, failures routed to a `quarantine` table with `reason_code` and the raw row payload serialized as JSON.

**Consequences.**
- `pandera[pandas]` pinned. API hot path is unaffected — validation is at ingest only.

---

## ADR-0004 · KPI lineage is part of every response

**Status:** Accepted · 2026-11-07

**Context.** Problem card 05 ("KPIs that disagree across teams") is a stated top-tier failure mode. Every KPI value must be defensible.

**Decision.** Every KPI response carries a `lineage` object: `metric_name`, `metric_version`, `formula`, `window_utc`, `source_tables`, `row_counts`, `assumptions`. No endpoint returns a bare number.

**Consequences.**
- Response payload is larger. Acceptable — this is a decision-support platform; audit > brevity.
- The `metric_catalog` table seeds metric definitions; the KPI service reads version and formula from it.

---

## ADR-0005 · Metric: Sales per Labor Hour (SPLH) first

**Status:** Accepted · 2026-11-07

**Context.** Problem card 05 names SPLH as the canonical disagreement metric. Shipping it end-to-end first proves the entire pipeline — generator, validation, DB, KPI service, API, lineage — on a metric the business will interrogate.

**Decision.** M1 ships SPLH only. Formula: `sum(net_sales_cents) / 100.0 / nullif(sum(labor_minutes) / 60.0, 0)` over the requested window, scoped to one store. Unit: `USD per labor hour`.

**Consequences.**
- Edge cases enumerated and tested: empty window, zero labor hours, zero sales, partial-hour labor records.
- Later metrics (ATV, UPT, labor cost %, conversion) reuse the same lineage envelope in M2.

---

## ADR-0006 · Money stored in integer cents

**Status:** Accepted · 2026-11-07

**Context.** Floating-point money is a well-documented source of KPI drift.

**Decision.** All currency amounts stored as integer cents (`BIGINT`). Division to USD happens at the presentation boundary only.

**Consequences.**
- Simple, portable, audit-friendly. Currency column reserved for later multi-currency work.
