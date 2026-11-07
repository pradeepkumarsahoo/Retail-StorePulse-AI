"""The synthetic generator must be deterministic under a fixed seed."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from scripts.generate_data import generate


def test_seed_42_is_byte_stable():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = generate(seed=42, start_date=start, days=3)
    b = generate(seed=42, start_date=start, days=3)
    for name in ["sales_txn", "labor_hours", "traffic_count"]:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_different_seeds_diverge():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = generate(seed=42, start_date=start, days=3)
    b = generate(seed=43, start_date=start, days=3)
    assert not a["sales_txn"]["net_sales_cents"].equals(b["sales_txn"]["net_sales_cents"])
