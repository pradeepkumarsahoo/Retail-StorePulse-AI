"""The synthetic generator must be deterministic under a fixed seed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from packages.validation.schemas import INBOUND_SHIPMENT_SCHEMA
from scripts.generate_data import TMS_LOOKAHEAD_DAYS, generate


def test_seed_42_is_byte_stable():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = generate(seed=42, start_date=start, days=3)
    b = generate(seed=42, start_date=start, days=3)
    for name in ["sales_txn", "labor_hours", "traffic_count", "inbound_shipment"]:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_different_seeds_diverge():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = generate(seed=42, start_date=start, days=3)
    b = generate(seed=43, start_date=start, days=3)
    assert not a["sales_txn"]["net_sales_cents"].equals(b["sales_txn"]["net_sales_cents"])


def test_tms_feed_covers_five_day_lookahead_and_validates():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    days = 14
    out = generate(seed=42, start_date=start, days=days)
    shp = out["inbound_shipment"]
    assert not shp.empty
    # Fact tables end at day 14; the TMS feed must reach 5 days further.
    last_fact = out["labor_hours"]["ts_utc"].max()
    assert shp["planned_arrival_utc"].max() > last_fact
    assert shp["planned_arrival_utc"].max() >= start + timedelta(days=days + TMS_LOOKAHEAD_DAYS - 2)
    # Every synthetic row passes the ingest contract (delay == eta - planned, etc.).
    INBOUND_SHIPMENT_SCHEMA.validate(shp, lazy=True)
    assert set(shp["store_id"]) == {"S001", "S002", "S003", "S004", "S005"}
    assert shp["shipment_id"].is_unique
