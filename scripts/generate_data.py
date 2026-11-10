"""Deterministic synthetic data generator.

Writes labeled synthetic CSVs for 5 stores × 90 days × hourly open-period rows
for sales, labor, and traffic, plus a synthetic TMS (Transport Management
System) inbound-shipment feed covering the same period and a 5-day lookahead.
Fixed seed → byte-identical output across runs.

Usage:
    python -m scripts.generate_data --seed 42 --out data/synthetic
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


SYNTHETIC_VERSION = "synthetic-v1"
SCHEMA_VERSION = "1.0.0"

STORES = [
    {"store_id": "S001", "name": "Market St Flagship",   "region": "West",    "timezone": "America/Los_Angeles"},
    {"store_id": "S002", "name": "Mission District",     "region": "West",    "timezone": "America/Los_Angeles"},
    {"store_id": "S003", "name": "Loop Center",          "region": "Midwest", "timezone": "America/Chicago"},
    {"store_id": "S004", "name": "Buckhead Pavilion",    "region": "South",   "timezone": "America/New_York"},
    {"store_id": "S005", "name": "Back Bay Boutique",    "region": "Northeast","timezone": "America/New_York"},
]

# Store opens 09:00 local, closes 21:00 local. We simulate 09–21 UTC-equivalent
# for simplicity — the synthetic layer calls out this assumption. Real TZ handling
# arrives when a real POS feed lands (M7).
OPEN_HOUR = 9
CLOSE_HOUR = 21  # exclusive

# Typical store baselines (deliberately diverse so SPLH varies store-to-store)
STORE_BASELINE = {
    "S001": {"traffic": 140.0, "atv_usd": 38.0, "units_per_txn": 1.8, "staff": 12},
    "S002": {"traffic":  70.0, "atv_usd": 29.0, "units_per_txn": 1.5, "staff":  7},
    "S003": {"traffic": 110.0, "atv_usd": 42.0, "units_per_txn": 2.0, "staff": 10},
    "S004": {"traffic":  95.0, "atv_usd": 31.0, "units_per_txn": 1.7, "staff":  9},
    "S005": {"traffic":  60.0, "atv_usd": 55.0, "units_per_txn": 1.3, "staff":  6},
}

CONVERSION_RATE = 0.22  # 22% of visitors transact on average

# --- TMS (Transport Management System) synthetic feed ----------------------
# Each store receives DC trucks on a fixed weekly pattern (Mon=0 … Sun=6).
# The TMS feed is generated for `days + TMS_LOOKAHEAD_DAYS` so the 5-day
# labor-planning horizon always has inbound records beyond the fact tables.
TMS_LOOKAHEAD_DAYS = 5
TMS_PLANNED_ARRIVAL_HOUR = 7          # trucks are planned before store open (UTC-equivalent)
TMS_DELAY_PROBABILITY = 0.18          # share of trucks that slip
TMS_ROLL_TO_NEXT_DAY_PROBABILITY = 0.05  # share that roll a full day (the "idle labor" case)
TMS_CANCEL_PROBABILITY = 0.03
TMS_EXTRA_TRUCK_PROBABILITY = 0.10    # promo/reset weeks: a second, heavier truck the same day
UNLOAD_MINUTES_PER_PALLET = 25        # receiving labor estimate per pallet (synthetic)

STORE_DELIVERY_DOWS = {
    "S001": (0, 2, 4),   # Mon · Wed · Fri — flagship, three trucks a week
    "S002": (1, 3),      # Tue · Thu
    "S003": (0, 3),      # Mon · Thu
    "S004": (1, 4),      # Tue · Fri
    "S005": (2,),        # Wed
}
STORE_DC = {"S001": "DC01", "S002": "DC01", "S003": "DC02", "S004": "DC03", "S005": "DC04"}
CARRIERS = ("NorthStar Freight", "Lakeside Logistics", "Blue Ridge Carriers")


def _hourly_traffic_multiplier(hour: int) -> float:
    # Smooth intraday curve: morning ramp, lunch peak, evening peak, late decline
    centers = [12.0, 18.0]
    sigmas = [2.0, 2.5]
    heights = [1.0, 1.2]
    val = 0.3  # base
    for c, s, h in zip(centers, sigmas, heights):
        val += h * math.exp(-((hour - c) ** 2) / (2 * s * s))
    return val


def _day_of_week_multiplier(dow: int) -> float:
    # Mon=0 … Sun=6. Weekend lift, Monday dip.
    return [0.85, 0.95, 1.00, 1.05, 1.15, 1.35, 1.20][dow]


def generate(seed: int, start_date: datetime, days: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    sales_rows: list[dict] = []
    labor_rows: list[dict] = []
    traffic_rows: list[dict] = []

    for day_idx in range(days):
        day = start_date + timedelta(days=day_idx)
        dow_mul = _day_of_week_multiplier(day.weekday())
        for hour in range(OPEN_HOUR, CLOSE_HOUR):
            ts = day.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            hour_mul = _hourly_traffic_multiplier(hour)
            for store in STORES:
                base = STORE_BASELINE[store["store_id"]]
                # Traffic — Poisson around expected
                expected_traffic = base["traffic"] * hour_mul * dow_mul / 12.0
                visitors = int(rng.poisson(max(expected_traffic, 0.1)))
                traffic_rows.append({
                    "store_id": store["store_id"],
                    "ts_utc": ts,
                    "visitors": visitors,
                    "source_system": "synthetic_traffic",
                    "schema_version": SCHEMA_VERSION,
                })

                # Transactions = binomial(visitors, conversion) with small noise
                conv = max(0.0, min(1.0, rng.normal(CONVERSION_RATE, 0.03)))
                txns = int(rng.binomial(visitors, conv))

                # Net sales: sum of txns independent lognormal draws around ATV
                if txns == 0:
                    net_sales_cents = 0
                    units = 0
                else:
                    per_txn = rng.lognormal(mean=math.log(base["atv_usd"]), sigma=0.25, size=txns)
                    net_sales_cents = int(round(per_txn.sum() * 100))
                    units = int(round(base["units_per_txn"] * txns + rng.normal(0, 0.5)))
                    units = max(units, txns)  # at least one unit per txn
                sales_rows.append({
                    "store_id": store["store_id"],
                    "ts_utc": ts,
                    "net_sales_cents": net_sales_cents,
                    "transactions": txns,
                    "units": units,
                    "currency": "USD",
                    "source_system": "synthetic_pos",
                    "schema_version": SCHEMA_VERSION,
                })

                # Labor: baseline staff per hour, slight scheduling noise
                # (schedules often use averages → under/over-coverage on peaks).
                scheduled_staff_hour = max(1.0, base["staff"] * (0.5 + 0.6 * hour_mul) / 1.4)
                scheduled_staff_hour += rng.normal(0, 0.4)
                scheduled_staff_hour = max(1.0, scheduled_staff_hour)
                labor_minutes = int(round(scheduled_staff_hour * 60))
                labor_rows.append({
                    "store_id": store["store_id"],
                    "ts_utc": ts,
                    "labor_minutes": labor_minutes,
                    "source_system": "synthetic_wfm",
                    "schema_version": SCHEMA_VERSION,
                })

    # TMS feed is drawn AFTER the fact tables so sales/labor/traffic output
    # stays byte-identical to earlier synthetic versions under the same seed.
    shipment_rows = _generate_inbound_shipments(rng, start_date, days + TMS_LOOKAHEAD_DAYS)

    stores_df = pd.DataFrame(STORES)
    return {
        "store": stores_df,
        "sales_txn": pd.DataFrame(sales_rows),
        "labor_hours": pd.DataFrame(labor_rows),
        "traffic_count": pd.DataFrame(traffic_rows),
        "inbound_shipment": pd.DataFrame(shipment_rows),
    }


def _generate_inbound_shipments(rng: np.random.Generator, start_date: datetime,
                                days: int) -> list[dict]:
    """Synthetic TMS export: one row per DC truck planned into a store.

    Mirrors the fields a real TMS exposes (planned arrival, live ETA, status,
    delay, load size) plus a receiving-labor estimate the planner needs.
    """
    rows: list[dict] = []
    seq = 0

    def _truck(sid: str, planned: datetime, pallet_scale: float) -> dict:
        nonlocal seq
        seq += 1
        base = STORE_BASELINE[sid]
        pallets = int(max(2, rng.poisson(base["staff"] * 1.1 * pallet_scale)))
        cases = int(max(pallets, round(pallets * rng.normal(42, 6))))
        unload_minutes = int(pallets * UNLOAD_MINUTES_PER_PALLET)

        u = rng.random()
        if u < TMS_CANCEL_PROBABILITY:
            status, delay = "cancelled", 0
        elif u < TMS_CANCEL_PROBABILITY + TMS_ROLL_TO_NEXT_DAY_PROBABILITY:
            status, delay = "delayed", 24 * 60 + int(rng.integers(0, 120))
        elif u < TMS_CANCEL_PROBABILITY + TMS_ROLL_TO_NEXT_DAY_PROBABILITY + TMS_DELAY_PROBABILITY:
            status, delay = "delayed", int(min(600, max(30, rng.gamma(2.0, 75.0))))
        else:
            status = "scheduled"
            delay = int(rng.integers(-20, 21))  # small early/late jitter
        eta = planned + timedelta(minutes=delay)
        return {
            "shipment_id": f"SHP-{seq:06d}",
            "store_id": sid,
            "dc_id": STORE_DC[sid],
            "truck_id": f"TRK-{int(rng.integers(1000, 9999)):04d}",
            "carrier": CARRIERS[int(rng.integers(0, len(CARRIERS)))],
            "planned_arrival_utc": planned,
            "eta_utc": eta,
            "status": status,
            "delay_minutes": delay,
            "pallets": pallets,
            "cases": cases,
            "unload_minutes_est": unload_minutes,
            "source_system": "synthetic_tms",
            "schema_version": SCHEMA_VERSION,
        }

    for day_idx in range(days):
        day = start_date + timedelta(days=day_idx)
        for store in STORES:
            sid = store["store_id"]
            if day.weekday() not in STORE_DELIVERY_DOWS[sid]:
                continue
            planned = day.replace(hour=TMS_PLANNED_ARRIVAL_HOUR, minute=0, second=0,
                                  microsecond=0, tzinfo=timezone.utc)
            rows.append(_truck(sid, planned, pallet_scale=1.0))
            if rng.random() < TMS_EXTRA_TRUCK_PROBABILITY:
                # Second trailer an hour later — the "heavy inbound day" case.
                rows.append(_truck(sid, planned + timedelta(hours=1), pallet_scale=1.6))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="StorePulse-AI synthetic data generator")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--start", type=str, default="2026-08-01")
    p.add_argument("--out", type=str, default="data/synthetic")
    args = p.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables = generate(seed=args.seed, start_date=start, days=args.days)
    for name, df in tables.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False)
        print(f"wrote {len(df):>7,} rows  ->  {path}")

    # Marker file so humans don't mistake this for real data.
    (out / "_SYNTHETIC_DATA.txt").write_text(
        "StorePulse-AI synthetic dataset. Not real sales or staffing data.\n"
        f"version={SYNTHETIC_VERSION} seed={args.seed} days={args.days} start={args.start}\n"
    )


if __name__ == "__main__":
    main()
