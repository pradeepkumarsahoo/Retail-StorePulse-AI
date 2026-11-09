"""Unit tests for the pure-Python domain entities."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from packages.domain.entities import KpiLineage, KpiValue, KpiWindow, Store


def test_kpi_window_valid_bounds_ok():
    w = KpiWindow(
        from_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        to_utc=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    assert w.to_utc > w.from_utc


def test_kpi_window_rejects_equal_bounds():
    ts = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        KpiWindow(from_utc=ts, to_utc=ts)


def test_kpi_window_rejects_reversed_bounds():
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        KpiWindow(from_utc=start, to_utc=end)


def test_entities_are_frozen():
    s = Store(store_id="S001", name="Market", region="West", timezone="UTC")
    with pytest.raises(FrozenInstanceError):
        s.name = "Changed"  # type: ignore[misc]


def test_kpi_value_carries_lineage():
    w = KpiWindow(
        from_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        to_utc=datetime(2026, 10, 1, 1, tzinfo=timezone.utc),
    )
    lin = KpiLineage(
        metric_name="net_sales_usd",
        metric_version="1.0.0",
        formula="sum(net_sales_cents)/100",
        source_tables=("sales_txn",),
        row_counts={"sales_txn": 3},
        assumptions=("Window is half-open",),
        synthetic_data=True,
    )
    v = KpiValue(value=123.45, unit="USD", window=w, lineage=lin)
    assert v.value == 123.45
    assert v.lineage.synthetic_data is True
    assert v.lineage.source_tables == ("sales_txn",)


def test_kpi_value_allows_none_for_insufficient_evidence():
    w = KpiWindow(
        from_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        to_utc=datetime(2026, 10, 1, 1, tzinfo=timezone.utc),
    )
    lin = KpiLineage(
        metric_name="splh", metric_version="1.0.0", formula="x",
        source_tables=("sales_txn", "labor_hours"),
        row_counts={"sales_txn": 0, "labor_hours": 0},
        assumptions=("insufficient evidence",),
    )
    v = KpiValue(value=None, unit="USD per labor hour", window=w, lineage=lin)
    assert v.value is None
