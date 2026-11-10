"""Run the TMS-driven inbound labor-plan check for every store and print it.

Looks `--horizon-days` (max 5) ahead of `--as-of` (default: the first day of the
published labor forecast, so the check has a labor plan to compare against).

Usage:
    python -m scripts.run_inbound_check --as-of 2026-10-20 --horizon-days 5
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import text

from infrastructure.db.engine import get_engine
from packages.analytics.kpi_service import list_stores
from packages.inbound_planning.service import labor_plan_check
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS


STATUS_GLYPH = {
    "balanced": "ok  ",
    "under_coverage": "HEAVY",
    "idle_risk": "IDLE",
    "no_inbound": "--  ",
    "insufficient_evidence": "n/a ",
}


def main() -> None:
    p = argparse.ArgumentParser(description="StorePulse-AI inbound labor-plan check (TMS)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD (UTC)")
    p.add_argument("--horizon-days", type=int, default=TMS_MAX_HORIZON_DAYS)
    args = p.parse_args()

    engine = get_engine()
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
    else:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT MIN(ts_utc) AS mn FROM forecast WHERE kind='labor_minutes'"
            )).one()
        if not row.mn:
            raise SystemExit("No labor forecast loaded. Run `python -m scripts.run_forecast` first.")
        as_of = datetime.fromisoformat(str(row.mn)).replace(tzinfo=timezone.utc)

    print(f"inbound labor-plan check  as_of={as_of.date()}  horizon={args.horizon_days}d  "
          f"(TMS horizon cap {TMS_MAX_HORIZON_DAYS}d)")
    for store in list_stores(engine):
        out = labor_plan_check(engine, store["store_id"], as_of, args.horizon_days)
        s = out["summary"]
        print(f"\n{store['store_id']}  {store['name']:<22s}  trucks={s['trucks_in_window']:>2}  "
              f"idle_risk_days={s['by_status']['idle_risk']}  heavy_days={s['by_status']['under_coverage']}  "
              f"idle_minutes_at_risk={s['idle_minutes_at_risk']}  labor_source={s['labor_source']}")
        for d in out["days"]:
            labor = f"{d['planned_labor_minutes']:.0f}" if d["planned_labor_minutes"] is not None else "   -"
            share = f"{d['receiving_share']:.0%}" if d["receiving_share"] is not None else "  -"
            print(f"   {d['date']}  [{STATUS_GLYPH[d['status']]}]  trucks_due={d['trucks_due']}  "
                  f"slipped={d['trucks_slipped']}  unload_min={d['unload_minutes_est']:>4}  "
                  f"labor_min={labor:>5}  share={share:>4}")


if __name__ == "__main__":
    main()
