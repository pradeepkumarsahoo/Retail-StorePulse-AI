"""KPI service. Deterministic. Every return carries lineage.

All KPIs share a `_window_aggregates` single-query building block so that
`row_counts` and inclusion rules stay consistent across metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.domain.entities import KpiLineage, KpiValue, KpiWindow


# --- Metric definitions (also seeded into metric_catalog) ------------------

METRICS: dict[str, dict] = {
    "sales_per_labor_hour": {
        "version": "1.0.0",
        "unit": "USD per labor hour",
        "formula": "sum(net_sales_cents)/100.0 / nullif(sum(labor_minutes)/60.0, 0)",
        "source_tables": ("sales_txn", "labor_hours"),
        "owner": "analytics-platform",
        "description": "Net sales divided by labor hours for a single store over [from_utc, to_utc).",
    },
    "net_sales_usd": {
        "version": "1.0.0",
        "unit": "USD",
        "formula": "sum(net_sales_cents)/100.0",
        "source_tables": ("sales_txn",),
        "owner": "analytics-platform",
        "description": "Net sales in USD over [from_utc, to_utc).",
    },
    "transactions": {
        "version": "1.0.0",
        "unit": "count",
        "formula": "sum(transactions)",
        "source_tables": ("sales_txn",),
        "owner": "analytics-platform",
        "description": "Total paid transactions over [from_utc, to_utc).",
    },
    "atv_usd": {
        "version": "1.0.0",
        "unit": "USD per transaction",
        "formula": "sum(net_sales_cents)/100.0 / nullif(sum(transactions), 0)",
        "source_tables": ("sales_txn",),
        "owner": "analytics-platform",
        "description": "Average transaction value.",
    },
    "upt": {
        "version": "1.0.0",
        "unit": "units per transaction",
        "formula": "sum(units) / nullif(sum(transactions), 0)",
        "source_tables": ("sales_txn",),
        "owner": "analytics-platform",
        "description": "Units per transaction.",
    },
    "labor_cost_pct": {
        "version": "1.0.0",
        "unit": "ratio (0-1)",
        "formula": "labor_cost_usd / nullif(net_sales_usd, 0)",
        "source_tables": ("sales_txn", "labor_hours", "labor_rate"),
        "owner": "analytics-platform",
        "description": "Labor cost as a fraction of net sales. Labor cost = labor_hours * approved regional rate.",
    },
    "conversion_rate": {
        "version": "1.0.0",
        "unit": "ratio (0-1)",
        "formula": "sum(transactions) / nullif(sum(visitors), 0)",
        "source_tables": ("sales_txn", "traffic_count"),
        "owner": "analytics-platform",
        "description": "Transactions divided by visitors. Only reported when traffic data is complete in the window.",
    },
    "visitors": {
        "version": "1.0.0",
        "unit": "count",
        "formula": "sum(visitors)",
        "source_tables": ("traffic_count",),
        "owner": "analytics-platform",
        "description": "Footfall over [from_utc, to_utc).",
    },
}


# Back-compat exports for existing imports.
SPLH_METRIC_NAME = "sales_per_labor_hour"
SPLH_METRIC_VERSION = METRICS[SPLH_METRIC_NAME]["version"]
SPLH_FORMULA = (
    "sum(net_sales_cents) / 100.0 / nullif(sum(labor_minutes) / 60.0, 0) "
    "scoped to one store over [from_utc, to_utc)"
)


def _as_db_dt(dt: datetime) -> str:
    """Canonical SQLite datetime string matching pandas' default ingest format."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


@dataclass(frozen=True)
class _WindowAggregates:
    net_sales_cents: int
    transactions: int
    units: int
    labor_minutes: int
    visitors: int
    sales_rows: int
    labor_rows: int
    traffic_rows: int


def _aggregates(engine: Engine, store_id: str, window: KpiWindow) -> _WindowAggregates:
    params = {
        "sid": store_id,
        "fr": _as_db_dt(window.from_utc),
        "to": _as_db_dt(window.to_utc),
    }
    sales_sql = text(
        "SELECT COALESCE(SUM(net_sales_cents),0) AS cents, "
        "       COALESCE(SUM(transactions),0) AS txns, "
        "       COALESCE(SUM(units),0) AS unts, "
        "       COUNT(*) AS n_rows "
        "FROM sales_txn WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"
    )
    labor_sql = text(
        "SELECT COALESCE(SUM(labor_minutes),0) AS mins, COUNT(*) AS n_rows "
        "FROM labor_hours WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"
    )
    traffic_sql = text(
        "SELECT COALESCE(SUM(visitors),0) AS vis, COUNT(*) AS n_rows "
        "FROM traffic_count WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"
    )
    with engine.connect() as conn:
        s = conn.execute(sales_sql, params).one()
        lab = conn.execute(labor_sql, params).one()
        tr = conn.execute(traffic_sql, params).one()
    return _WindowAggregates(
        net_sales_cents=int(s.cents),
        transactions=int(s.txns),
        units=int(s.unts),
        labor_minutes=int(lab.mins),
        visitors=int(tr.vis),
        sales_rows=int(s.n_rows),
        labor_rows=int(lab.n_rows),
        traffic_rows=int(tr.n_rows),
    )


def _lineage(name: str, row_counts: dict[str, int], assumptions: list[str]) -> KpiLineage:
    m = METRICS[name]
    return KpiLineage(
        metric_name=name,
        metric_version=m["version"],
        formula=m["formula"],
        source_tables=m["source_tables"],
        row_counts=row_counts,
        assumptions=tuple(assumptions),
        synthetic_data=True,
    )


def _result(name: str, value: Optional[float], window: KpiWindow,
            row_counts: dict[str, int], extra: list[str] | None = None) -> KpiValue:
    assumptions = [
        "Net sales exclude tax; stored in integer cents.",
        "Window is half-open [from_utc, to_utc); times in UTC.",
    ]
    if extra:
        assumptions.extend(extra)
    if value is None:
        assumptions.append("insufficient evidence: denominator is zero or required data is absent.")
    return KpiValue(value=value, unit=METRICS[name]["unit"], window=window,
                    lineage=_lineage(name, row_counts, assumptions))


# --- Public KPI functions --------------------------------------------------

def compute_splh(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    hours = a.labor_minutes / 60.0
    v = round((a.net_sales_cents / 100.0) / hours, 4) if hours > 0 else None
    return _result(
        "sales_per_labor_hour", v, window,
        {"sales_txn": a.sales_rows, "labor_hours": a.labor_rows},
    )


def compute_net_sales(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    v = round(a.net_sales_cents / 100.0, 2) if a.sales_rows > 0 else None
    return _result("net_sales_usd", v, window, {"sales_txn": a.sales_rows})


def compute_transactions(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    v = float(a.transactions) if a.sales_rows > 0 else None
    return _result("transactions", v, window, {"sales_txn": a.sales_rows})


def compute_atv(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    v = round((a.net_sales_cents / 100.0) / a.transactions, 4) if a.transactions > 0 else None
    return _result("atv_usd", v, window, {"sales_txn": a.sales_rows})


def compute_upt(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    v = round(a.units / a.transactions, 4) if a.transactions > 0 else None
    return _result("upt", v, window, {"sales_txn": a.sales_rows})


def compute_visitors(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    v = float(a.visitors) if a.traffic_rows > 0 else None
    return _result("visitors", v, window, {"traffic_count": a.traffic_rows})


def compute_conversion_rate(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    a = _aggregates(engine, store_id, window)
    rows = {"sales_txn": a.sales_rows, "traffic_count": a.traffic_rows}
    extra = ["Reported only when traffic_count and sales_txn both cover the window."]
    if a.traffic_rows == 0 or a.sales_rows == 0:
        return _result("conversion_rate", None, window, rows, extra)
    if a.visitors == 0:
        return _result("conversion_rate", None, window, rows, extra)
    v = round(a.transactions / a.visitors, 4)
    return _result("conversion_rate", v, window, rows, extra)


def compute_labor_cost_pct(
    engine: Engine, store_id: str, window: KpiWindow, rate_per_hour_usd: float,
) -> KpiValue:
    """Labor cost % uses an approved regional labor-rate supplied by the caller.

    The rate comes from the MCP labor-rate server, not a hard-coded constant,
    so the metric cites a versioned source assumption.
    """
    if rate_per_hour_usd < 0:
        raise ValueError("rate_per_hour_usd must be non-negative")
    a = _aggregates(engine, store_id, window)
    hours = a.labor_minutes / 60.0
    net_sales_usd = a.net_sales_cents / 100.0
    labor_cost_usd = hours * rate_per_hour_usd
    rows = {"sales_txn": a.sales_rows, "labor_hours": a.labor_rows}
    extra = [f"labor_rate_per_hour_usd={rate_per_hour_usd:.2f} (from approved MCP labor-rate reference)."]
    if net_sales_usd <= 0 or hours <= 0:
        return _result("labor_cost_pct", None, window, rows, extra)
    v = round(labor_cost_usd / net_sales_usd, 4)
    return _result("labor_cost_pct", v, window, rows, extra)


# --- Masters ---------------------------------------------------------------

def list_stores(engine: Engine, region: str | None = None) -> list[dict]:
    sql = "SELECT store_id, name, region, timezone FROM store"
    params: dict = {}
    if region:
        sql += " WHERE region = :region"
        params["region"] = region
    sql += " ORDER BY store_id"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def list_regions(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT region FROM store ORDER BY region")).scalars().all()
    return list(rows)
