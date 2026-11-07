"""Unit tests for every KPI formula and its lineage envelope."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from packages.analytics.kpi_service import (
    compute_atv,
    compute_conversion_rate,
    compute_labor_cost_pct,
    compute_net_sales,
    compute_splh,
    compute_transactions,
    compute_upt,
    compute_visitors,
)
from packages.domain.entities import KpiWindow


def test_net_sales_matches_fixture(engine, seed_window):
    r = compute_net_sales(engine, "S001", KpiWindow(*seed_window))
    # 20000 + 35000 + 15000 = 70000 cents = $700
    assert r.value == pytest.approx(700.00)
    assert r.unit == "USD"
    assert r.lineage.metric_name == "net_sales_usd"


def test_transactions_sum(engine, seed_window):
    r = compute_transactions(engine, "S001", KpiWindow(*seed_window))
    assert r.value == 17.0


def test_atv(engine, seed_window):
    r = compute_atv(engine, "S001", KpiWindow(*seed_window))
    # 700 / 17 ≈ 41.1765
    assert r.value == pytest.approx(41.1765, abs=1e-3)


def test_upt(engine, seed_window):
    r = compute_upt(engine, "S001", KpiWindow(*seed_window))
    # 24 / 17 ≈ 1.4118
    assert r.value == pytest.approx(1.4118, abs=1e-3)


def test_visitors(engine, seed_window):
    r = compute_visitors(engine, "S001", KpiWindow(*seed_window))
    assert r.value == 130.0


def test_conversion_rate(engine, seed_window):
    r = compute_conversion_rate(engine, "S001", KpiWindow(*seed_window))
    # 17 / 130 ≈ 0.1308
    assert r.value == pytest.approx(0.1308, abs=1e-3)


def test_conversion_rate_requires_traffic(engine, seed_window):
    # S002 has no traffic rows in the fixture.
    r = compute_conversion_rate(engine, "S002", KpiWindow(*seed_window))
    assert r.value is None


def test_labor_cost_pct(engine, seed_window):
    # 270 min = 4.5 hr. $18.50/hr * 4.5 = $83.25. 83.25 / 700 = 0.1189
    r = compute_labor_cost_pct(engine, "S001", KpiWindow(*seed_window), rate_per_hour_usd=18.50)
    assert r.value == pytest.approx(0.1189, abs=1e-3)
    assert r.unit.startswith("ratio")


def test_labor_cost_pct_rejects_negative_rate(engine, seed_window):
    with pytest.raises(ValueError):
        compute_labor_cost_pct(engine, "S001", KpiWindow(*seed_window), rate_per_hour_usd=-1)


def test_lineage_lists_source_tables_for_each_metric(engine, seed_window):
    r = compute_splh(engine, "S001", KpiWindow(*seed_window))
    assert "sales_txn" in r.lineage.source_tables
    assert "labor_hours" in r.lineage.source_tables
    assert r.lineage.synthetic_data is True
