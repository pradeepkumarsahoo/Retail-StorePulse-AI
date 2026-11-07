"""Unit tests for the SPLH KPI math and edge cases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from infrastructure.db.ingest import validate_and_load
from packages.analytics.kpi_service import compute_splh
from packages.domain.entities import KpiWindow


def test_splh_normal_case(engine, seed_window):
    start, end = seed_window
    window = KpiWindow(from_utc=start, to_utc=end)
    result = compute_splh(engine, "S001", window)

    # Total sales = 20000 + 35000 + 15000 cents = $700
    # Total labor = 60 + 120 + 90 min = 270 min = 4.5 hr
    # SPLH = 700 / 4.5 = 155.5556
    assert result.value == pytest.approx(155.5556, abs=1e-3)
    assert result.unit == "USD per labor hour"
    assert result.lineage.metric_name == "sales_per_labor_hour"
    assert result.lineage.metric_version == "1.0.0"
    assert result.lineage.row_counts == {"sales_txn": 3, "labor_hours": 3}
    assert result.lineage.synthetic_data is True


def test_splh_window_outside_data_returns_none(engine):
    # Far past, no rows → value None (insufficient evidence), not zero
    w = KpiWindow(
        from_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
        to_utc=datetime(2020, 1, 2, tzinfo=timezone.utc),
    )
    result = compute_splh(engine, "S001", w)
    assert result.value is None
    assert result.lineage.row_counts == {"sales_txn": 0, "labor_hours": 0}
    assert any("insufficient evidence" in a for a in result.lineage.assumptions)


def test_splh_zero_labor_but_sales_returns_none(engine, seed_window):
    """If a window has sales but zero labor minutes, SPLH is undefined → None."""
    start, end = seed_window
    # Wipe labor for the window
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM labor_hours WHERE store_id = :sid"), {"sid": "S001"}
        )
    result = compute_splh(engine, "S001", KpiWindow(from_utc=start, to_utc=end))
    assert result.value is None
    assert result.lineage.row_counts["labor_hours"] == 0


def test_splh_half_open_window_excludes_to_utc(engine, seed_window):
    """ts_utc == to_utc must NOT be included (half-open [from, to) semantics)."""
    start, end = seed_window
    # Shrink window to the first hour only: [09:00, 10:00) — one sales/labor row
    w = KpiWindow(from_utc=start, to_utc=start + timedelta(hours=1))
    result = compute_splh(engine, "S001", w)
    # One row: $200 sales, 60 min labor = $200 / 1 hr = 200
    assert result.value == pytest.approx(200.0, abs=1e-3)
    assert result.lineage.row_counts == {"sales_txn": 1, "labor_hours": 1}


def test_kpi_window_rejects_reversed_bounds():
    with pytest.raises(ValueError):
        KpiWindow(
            from_utc=datetime(2026, 10, 2, tzinfo=timezone.utc),
            to_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
