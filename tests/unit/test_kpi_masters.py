"""Unit tests for list_stores / list_regions helpers in kpi_service."""
from __future__ import annotations

from packages.analytics.kpi_service import list_regions, list_stores


def test_list_stores_returns_all_without_region(engine):
    stores = list_stores(engine)
    ids = {s["store_id"] for s in stores}
    assert ids == {"S001", "S002"}


def test_list_stores_filters_by_region(engine):
    west = list_stores(engine, region="West")
    assert {s["store_id"] for s in west} == {"S001"}
    midwest = list_stores(engine, region="Midwest")
    assert {s["store_id"] for s in midwest} == {"S002"}


def test_list_stores_unknown_region_empty(engine):
    assert list_stores(engine, region="Antarctica") == []


def test_list_stores_sorted_by_id(engine):
    out = list_stores(engine)
    assert [s["store_id"] for s in out] == sorted(s["store_id"] for s in out)


def test_list_regions_unique_and_sorted(engine):
    regions = list_regions(engine)
    assert regions == sorted(set(regions))
    assert "West" in regions
    assert "Midwest" in regions
