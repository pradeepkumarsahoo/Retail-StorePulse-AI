"""Run the forecast-driven HVAC check for every store and print it.

Looks `--horizon-days` (max 5) ahead of `--as-of` (default: the first day of the
published visitor forecast, so occupied hours come from the forecast).

Usage:
    python -m scripts.run_energy_check --as-of 2026-10-14 --horizon-days 5
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import text

from infrastructure.db.engine import get_engine
from packages.analytics.kpi_service import list_stores
from packages.energy_planning.service import ENERGY_MAX_HORIZON_DAYS, hvac_schedule_check


STATUS_GLYPH = {"waste": "WASTE", "comfort_risk": "COMF ", "aligned": "ok   ",
                "insufficient_evidence": "n/a  "}


def main() -> None:
    p = argparse.ArgumentParser(description="StorePulse-AI energy HVAC-schedule check (BMS)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD (UTC)")
    p.add_argument("--horizon-days", type=int, default=ENERGY_MAX_HORIZON_DAYS)
    args = p.parse_args()

    engine = get_engine()
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
    else:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT MIN(ts_utc) AS mn FROM forecast WHERE kind='visitors'")).one()
        if not row.mn:
            raise SystemExit("No visitor forecast loaded. Run `python -m scripts.run_forecast --kind visitors` first.")
        as_of = datetime.fromisoformat(str(row.mn)).replace(tzinfo=timezone.utc)
        as_of = as_of.replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"energy HVAC-schedule check  as_of={as_of.date()}  horizon={args.horizon_days}d")
    region_kwh = region_usd = region_annual = 0.0
    for store in list_stores(engine):
        out = hvac_schedule_check(engine, store["store_id"], as_of, args.horizon_days)
        s = out["summary"]
        cur, rec = s["current_schedule"] or {}, s["recommended_schedule"] or {}
        region_kwh += s["avoidable_kwh"]
        region_usd += s["avoidable_usd"]
        region_annual += s["annualized_usd"]
        print(f"\n{store['store_id']}  {store['name']:<22s}  BMS {cur.get('hvac_on', '-')}-{cur.get('hvac_off', '-')}"
              f"  ->  recommended {rec.get('hvac_on', '-')}-{rec.get('hvac_off', '-')}"
              f"  avoidable={s['avoidable_kwh']:.0f} kWh (${s['avoidable_usd']:.2f})"
              f"  annualized=${s['annualized_usd']:,.0f}/yr ({s['annualized_co2_t']} t CO2)")
        for d in out["days"]:
            if d["status"] == "insufficient_evidence":
                print(f"   {d['date']}  [{STATUS_GLYPH[d['status']]}]  {d['reason']}")
                continue
            print(f"   {d['date']}  [{STATUS_GLYPH[d['status']]}]  occupied {d['first_occupied']}-{d['last_occupied']}"
                  f"  early={d['early_start_kwh']:>5.1f}  late={d['late_stop_kwh']:>5.1f}  dock={d['dock_heat_kwh']:>5.1f} kWh"
                  f"  trucks_due={d['trucks_due']}  ({d['occupancy_source']}, {d['meter_source']})")
    print(f"\nAll stores: avoidable {region_kwh:.0f} kWh (${region_usd:.2f}) in window; "
          f"~${region_annual:,.0f}/yr from schedules alone")


if __name__ == "__main__":
    main()
