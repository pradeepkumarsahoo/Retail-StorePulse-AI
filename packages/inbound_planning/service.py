"""Inbound labor-plan check: joins the TMS 5-day truck outlook to labor plans.

Deterministic. No LLM. For each day in the horizon it answers one question a
workforce planner asks every morning: *"Given the trucks the TMS says are
coming, does the labor we plan to have on the floor make sense?"*

Per-day statuses (mutually exclusive, evaluated in this order):

* ``insufficient_evidence`` — no labor plan (forecast or schedule) for the day.
* ``idle_risk``             — a truck planned for this day has slipped to a later
                              day or was cancelled, so receiving labor planned
                              for it would be idle.
* ``under_coverage``        — receiving workload would consume more than
                              ``UNDER_COVERAGE_SHARE`` of the day's labor plan.
* ``no_inbound``            — no truck is due; nothing to plan around.
* ``balanced``              — trucks due and labor plan can absorb them.

Every result carries a lineage block (check name, version, formula, source
tables, row counts, assumptions, synthetic flag) exactly like a KPI response.
Recommendations built on it still route to a human reviewer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS, TmsMcp


CHECK_NAME = "inbound_labor_plan_check"
CHECK_VERSION = "1.0.0"

UNDER_COVERAGE_SHARE = 0.35   # receiving minutes / labor minutes planned in the receiving window
LATE_THRESHOLD_MINUTES = 120  # a slip of 2h+ inside the same day is flagged as "late" evidence
ACTIVE_STATUSES = ("scheduled", "in_transit", "delayed", "arrived")
# Trucks are planned before open; the load is worked in the first open hours.
RECEIVING_WINDOW_START_HOUR = 9   # store open (UTC-equivalent, same convention as the generator)
RECEIVING_WINDOW_HOURS = 3

FORMULA = (
    "per day: unload_minutes = sum(unload_minutes_est of trucks with eta on day); "
    "labor_minutes = sum(forecast.predicted kind=labor_minutes) else sum(labor_hours.labor_minutes); "
    f"receiving_window_labor_minutes = same source restricted to hours "
    f"[{RECEIVING_WINDOW_START_HOUR:02d}:00, {RECEIVING_WINDOW_START_HOUR + RECEIVING_WINDOW_HOURS:02d}:00); "
    "receiving_share = unload_minutes / receiving_window_labor_minutes; "
    "idle_risk if a truck planned for the day slipped to another day or was cancelled; "
    f"under_coverage if receiving_share > {UNDER_COVERAGE_SHARE}"
)
ASSUMPTIONS = (
    "TMS ETA is the best-known arrival; the TMS is the system of record and is never written to.",
    "Receiving labor is estimated from unload_minutes_est supplied by the TMS feed.",
    "Labor plan prefers the published labor-minute forecast; falls back to scheduled labor_hours.",
    f"Inbound loads are worked in the first {RECEIVING_WINDOW_HOURS} open hours "
    f"(from {RECEIVING_WINDOW_START_HOUR:02d}:00 UTC-equivalent); receiving_share is measured against that window.",
    "Days are UTC calendar days; the window is half-open [as_of, as_of + horizon_days).",
    "This check estimates workload. It never names, ranks, or schedules an individual employee.",
)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc)


def _in_receiving_window(ts: datetime) -> bool:
    return RECEIVING_WINDOW_START_HOUR <= ts.hour < RECEIVING_WINDOW_START_HOUR + RECEIVING_WINDOW_HOURS


def _labor_by_day(engine: Engine, store_id: str, start: datetime, end: datetime
                  ) -> tuple[dict[date, float], dict[date, float], str | None, dict[str, int]]:
    """Planned labor minutes per UTC day (whole day, receiving window).

    Forecast first, scheduled hours as fallback. Returns
    (day_totals, receiving_window_totals, source, row_counts)."""
    with engine.connect() as conn:
        fc = conn.execute(
            text(
                "SELECT ts_utc, predicted FROM forecast "
                "WHERE store_id=:sid AND kind='labor_minutes' AND ts_utc>=:fr AND ts_utc<:to"
            ),
            {"sid": store_id, "fr": _fmt(start), "to": _fmt(end)},
        ).mappings().all()
        sched = conn.execute(
            text(
                "SELECT ts_utc, labor_minutes FROM labor_hours "
                "WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"
            ),
            {"sid": store_id, "fr": _fmt(start), "to": _fmt(end)},
        ).mappings().all()
    counts = {"forecast": len(fc), "labor_hours": len(sched)}
    by_day: dict[date, float] = defaultdict(float)
    by_day_recv: dict[date, float] = defaultdict(float)

    def _accumulate(rows, col: str) -> None:
        for r in rows:
            ts = _parse(r["ts_utc"])
            by_day[ts.date()] += float(r[col])
            if _in_receiving_window(ts):
                by_day_recv[ts.date()] += float(r[col])

    if fc:
        _accumulate(fc, "predicted")
        return dict(by_day), dict(by_day_recv), "forecast", counts
    if sched:
        _accumulate(sched, "labor_minutes")
        return dict(by_day), dict(by_day_recv), "scheduled", counts
    return {}, {}, None, counts


def labor_plan_check(engine: Engine, store_id: str, as_of_utc: datetime,
                     horizon_days: int = TMS_MAX_HORIZON_DAYS) -> dict:
    """Join TMS trucks to the labor plan for each of the next `horizon_days` days."""
    if not 1 <= horizon_days <= TMS_MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be between 1 and {TMS_MAX_HORIZON_DAYS}")
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    start = as_of_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=horizon_days)

    tms = TmsMcp(engine)
    feed = tms.call_tool("list_inbound_shipments", {
        "store_id": store_id, "from_date": start.date().isoformat(), "horizon_days": horizon_days,
    })
    shipments = feed["shipments"]
    labor_by_day, recv_by_day, labor_source, counts = _labor_by_day(engine, store_id, start, end)

    days: list[dict] = []
    for i in range(horizon_days):
        day = (start + timedelta(days=i)).date()
        due = [s for s in shipments
               if _parse(s["eta_utc"]).date() == day and s["status"] in ACTIVE_STATUSES]
        slipped = [s for s in shipments
                   if _parse(s["planned_arrival_utc"]).date() == day
                   and (s["status"] == "cancelled" or _parse(s["eta_utc"]).date() != day)]
        late_same_day = [s for s in due
                         if s["delay_minutes"] >= LATE_THRESHOLD_MINUTES
                         and _parse(s["planned_arrival_utc"]).date() == day]
        unload = int(sum(s["unload_minutes_est"] for s in due))
        idle_minutes = int(sum(s["unload_minutes_est"] for s in slipped))
        labor = labor_by_day.get(day)
        recv_labor = recv_by_day.get(day)
        share = round(unload / recv_labor, 4) if recv_labor else None

        if labor is None:
            status = "insufficient_evidence"
        elif slipped:
            status = "idle_risk"
        elif share is not None and share > UNDER_COVERAGE_SHARE:
            status = "under_coverage"
        elif not due:
            status = "no_inbound"
        else:
            status = "balanced"

        days.append({
            "date": day.isoformat(),
            "status": status,
            "trucks_due": len(due),
            "trucks_slipped": len(slipped),
            "trucks_late_same_day": len(late_same_day),
            "unload_minutes_est": unload,
            "idle_minutes_at_risk": idle_minutes,
            "planned_labor_minutes": round(labor, 1) if labor is not None else None,
            "receiving_window_labor_minutes": round(recv_labor, 1) if recv_labor is not None else None,
            "receiving_share": share,
            "shipments": [
                {k: s[k] for k in ("shipment_id", "truck_id", "dc_id", "carrier", "status",
                                   "planned_arrival_utc", "eta_utc", "delay_minutes",
                                   "pallets", "unload_minutes_est")}
                for s in sorted(due + slipped, key=lambda s: s["eta_utc"])
            ],
        })

    summary = {
        "days_checked": horizon_days,
        "trucks_in_window": len(shipments),
        "by_status": {k: sum(1 for d in days if d["status"] == k)
                      for k in ("balanced", "under_coverage", "idle_risk", "no_inbound",
                                "insufficient_evidence")},
        "idle_minutes_at_risk": int(sum(d["idle_minutes_at_risk"] for d in days)),
        "unload_minutes_est": int(sum(d["unload_minutes_est"] for d in days)),
        "labor_source": labor_source,
    }
    return {
        "store_id": store_id,
        "window": {"from_utc": start.isoformat(), "to_utc": end.isoformat(),
                   "horizon_days": horizon_days},
        "days": days,
        "summary": summary,
        "lineage": {
            "check_name": CHECK_NAME,
            "check_version": CHECK_VERSION,
            "formula": FORMULA,
            "thresholds": {"under_coverage_share": UNDER_COVERAGE_SHARE,
                           "late_threshold_minutes": LATE_THRESHOLD_MINUTES,
                           "receiving_window_start_hour": RECEIVING_WINDOW_START_HOUR,
                           "receiving_window_hours": RECEIVING_WINDOW_HOURS},
            "source_tables": ["inbound_shipment", "forecast", "labor_hours"],
            "row_counts": {"inbound_shipment": len(shipments), **counts},
            "tms_server": feed["server"],
            "assumptions": list(ASSUMPTIONS),
            "synthetic_data": all(s.get("synthetic", True) for s in shipments) if shipments else True,
        },
        "status": "ok" if labor_source else "insufficient_evidence",
    }


def list_inbound_shipments(engine: Engine, store_id: str, from_utc: datetime,
                           to_utc: datetime) -> list[dict]:
    """Plain scoped read for the API. Clamped to the TMS horizon cap."""
    if to_utc <= from_utc:
        raise ValueError("to must be strictly after from")
    span_days = max(1, (to_utc.date() - from_utc.date()).days + (1 if to_utc.time() != datetime.min.time() else 0))
    horizon = min(TMS_MAX_HORIZON_DAYS, span_days)
    feed = TmsMcp(engine).call_tool("list_inbound_shipments", {
        "store_id": store_id, "from_date": from_utc.date().isoformat(), "horizon_days": horizon,
    })
    return feed["shipments"]
