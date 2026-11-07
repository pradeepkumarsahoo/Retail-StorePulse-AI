"""KPI service. Deterministic. Every return carries lineage."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.domain.entities import KpiLineage, KpiValue, KpiWindow


def _as_db_dt(dt: datetime) -> str:
    """Canonical SQLite datetime string.

    Pandas `to_sql` writes `YYYY-MM-DD HH:MM:SS.ffffff` (UTC-naive) when given
    a tz-aware UTC column, so we normalize query params the same way to keep
    string comparisons honest in SQLite. On Postgres (M7) the same binds work
    against `TIMESTAMPTZ` columns because the string is ISO-parseable.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None) if False else dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


SPLH_METRIC_NAME = "sales_per_labor_hour"
SPLH_METRIC_VERSION = "1.0.0"
SPLH_FORMULA = (
    "sum(net_sales_cents) / 100.0 / nullif(sum(labor_minutes) / 60.0, 0) "
    "scoped to one store over [from_utc, to_utc)"
)


def compute_splh(engine: Engine, store_id: str, window: KpiWindow) -> KpiValue:
    """Compute Sales per Labor Hour for a single store over a UTC window.

    Returns a KpiValue whose `value` is None when the window has no labor
    hours (insufficient evidence), rather than raising or dividing by zero.
    """
    sales_sql = text(
        "SELECT COALESCE(SUM(net_sales_cents), 0) AS cents, COUNT(*) AS rows "
        "FROM sales_txn "
        "WHERE store_id = :sid AND ts_utc >= :fr AND ts_utc < :to"
    )
    labor_sql = text(
        "SELECT COALESCE(SUM(labor_minutes), 0) AS minutes, COUNT(*) AS rows "
        "FROM labor_hours "
        "WHERE store_id = :sid AND ts_utc >= :fr AND ts_utc < :to"
    )
    params = {
        "sid": store_id,
        "fr": _as_db_dt(window.from_utc),
        "to": _as_db_dt(window.to_utc),
    }
    with engine.connect() as conn:
        sales = conn.execute(sales_sql, params).one()
        labor = conn.execute(labor_sql, params).one()

    sales_cents = int(sales.cents or 0)
    labor_minutes = int(labor.minutes or 0)
    labor_hours = labor_minutes / 60.0

    value: Optional[float]
    assumptions: list[str] = [
        "Net sales exclude tax; stored in integer cents.",
        "Labor hours exclude unpaid breaks (synthetic data assumption).",
        "Window is half-open [from_utc, to_utc); times in UTC.",
    ]

    if labor_hours == 0:
        value = None
        assumptions.append("No labor hours in window: value reported as null (insufficient evidence).")
    else:
        value = round((sales_cents / 100.0) / labor_hours, 4)

    lineage = KpiLineage(
        metric_name=SPLH_METRIC_NAME,
        metric_version=SPLH_METRIC_VERSION,
        formula=SPLH_FORMULA,
        source_tables=("sales_txn", "labor_hours"),
        row_counts={"sales_txn": int(sales.rows), "labor_hours": int(labor.rows)},
        assumptions=tuple(assumptions),
        synthetic_data=True,
    )
    return KpiValue(value=value, unit="USD per labor hour", window=window, lineage=lineage)


def list_stores(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT store_id, name, region, timezone FROM store ORDER BY store_id")
        ).mappings().all()
    return [dict(r) for r in rows]
