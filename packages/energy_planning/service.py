"""Energy HVAC-schedule check: joins the BMS timetable to store activity.

Deterministic. No LLM. For each day in the horizon it answers the question a
facilities or store manager never gets to ask hour by hour: *"Is the building
being heated or cooled while nobody is in it?"*

Inputs (all read-only mirrors):
* the HVAC timetable from the building-management system (BMS adapter),
* hourly HVAC kWh from the energy meter (that day, else a trailing baseline),
* occupied hours from the visitor forecast (else actual traffic counts),
* trucks due from the TMS (dock heating is only needed when a truck lands).

Per day:
* ``comfort_ready``   = first occupied hour − ``COMFORT_LEAD_MINUTES``
* ``recommended_on``  = comfort_ready − the store's measured warm-up (p90)
* ``recommended_off`` = last occupied hour end + ``CLOSE_GRACE_MINUTES``
* ``early_start_kwh`` = minutes the BMS starts before recommended_on × the
                        store's measured holding load (kW) between reaching
                        setpoint and opening
* ``late_stop_kwh``   = metered HVAC kWh between recommended_off and BMS off
* ``dock_heat_kwh``   = dock heater kW × dock window, when no truck is due

Per-day statuses (evaluated in this order):

* ``insufficient_evidence`` — no occupancy evidence or no meter data.
* ``waste``                 — avoidable energy ≥ ``WASTE_THRESHOLD_KWH``.
* ``comfort_risk``          — the BMS starts later than the store needs to be
                              comfortable at opening (no kWh claimed).
* ``aligned``               — schedule matches store activity.

Every result carries a lineage block (check name, version, formula,
thresholds, tariff, source tables, row counts, assumptions, synthetic flag).
Comfort at opening is a hard constraint: the recommended start always keeps
the store at setpoint before the comfort-ready time. Nothing is written to
the BMS; recommendations built on this route to a human reviewer.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.bms_mcp.server import BmsMcp, minutes_to_hhmm
from services.tms_mcp.server import TMS_MAX_HORIZON_DAYS, TmsMcp


CHECK_NAME = "energy_hvac_schedule_check"
CHECK_VERSION = "1.0.0"

# Same 5-day planning horizon as the TMS: the dock-heating finding needs the truck outlook.
ENERGY_MAX_HORIZON_DAYS = TMS_MAX_HORIZON_DAYS

COMFORT_LEAD_MINUTES = 15     # store must be at setpoint this long before doors open
CLOSE_GRACE_MINUTES = 30      # closing tasks after the last customer
WASTE_THRESHOLD_KWH = 10.0    # below this a day is "aligned" (meter noise, rounding)
OCCUPANCY_MIN_VISITORS = 1.0  # an hour counts as occupied at >= 1 forecast/actual visitor
BASELINE_DAYS = 14            # trailing meter window used for days without readings
MIN_READINGS_PER_DAY = 20     # a day with fewer hourly readings falls back to the baseline

TARIFF_USD_PER_KWH = float(os.environ.get("STOREPULSE_ENERGY_TARIFF_USD_PER_KWH", "0.15"))
CO2_KG_PER_KWH = float(os.environ.get("STOREPULSE_GRID_CO2_KG_PER_KWH", "0.37"))
ACTIVE_TRUCK_STATUSES = ("scheduled", "in_transit", "delayed", "arrived")

FORMULA = (
    "per day: open/close = first/last hour with visitors >= "
    f"{OCCUPANCY_MIN_VISITORS:g} (forecast kind=visitors, else traffic_count); "
    f"recommended_on = open - {COMFORT_LEAD_MINUTES} min - warmup_minutes_p90; "
    f"recommended_off = close + {CLOSE_GRACE_MINUTES} min; "
    "early_start_kwh = max(0, recommended_on - hvac_on) / 60 * holding_kw, where holding_kw = "
    "metered HVAC kWh/h between (hvac_on + warmup) and open; "
    "late_stop_kwh = metered HVAC kWh in [recommended_off, hvac_off); "
    "dock_heat_kwh = dock_heat_kw * dock window hours when no truck is due that day (TMS); "
    f"waste if early + late + dock >= {WASTE_THRESHOLD_KWH:g} kWh; "
    "cost = kWh * tariff; co2 = kWh * grid factor"
)
ASSUMPTIONS = (
    "The BMS is the system of record for the HVAC timetable and is never written to.",
    "Warm-up time is the BMS-measured 90th percentile, so the recommended start keeps comfort at opening.",
    "Starting later removes the hours spent holding an empty store at setpoint; the warm-up itself still happens.",
    "Meter kWh for the day is used when present; otherwise the trailing "
    f"{BASELINE_DAYS}-day hourly mean before the window.",
    "Occupied hours prefer the published visitor forecast; they fall back to actual traffic counts.",
    "Dock heating is needed only on days the TMS shows an active truck arriving.",
    f"Tariff ${TARIFF_USD_PER_KWH:.2f}/kWh and {CO2_KG_PER_KWH} kg CO2/kWh are configurable estimates; "
    "peak-demand charges are not included.",
    "Days are UTC calendar days; the window is half-open [as_of, as_of + horizon_days).",
    "This check is about the building. It never names, ranks, or monitors an individual employee.",
)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc)


def _kwh_between(hourly: dict[int, float], a: int, b: int) -> float:
    """Metered kWh between minute-of-day a and b, prorated within each hour."""
    if b <= a:
        return 0.0
    total = 0.0
    for h in range(a // 60, (b - 1) // 60 + 1):
        lo, hi = max(a, h * 60), min(b, (h + 1) * 60)
        if hi > lo:
            total += hourly.get(h, 0.0) * (hi - lo) / 60.0
    return total


def _occupancy(engine: Engine, store_id: str, start: datetime, end: datetime
               ) -> tuple[dict[date, tuple[int, int, str]], dict[str, int]]:
    """Occupied window per UTC day: {day: (open_minute, close_minute, source)}."""
    with engine.connect() as conn:
        fc = conn.execute(
            text("SELECT ts_utc, predicted AS v FROM forecast "
                 "WHERE store_id=:sid AND kind='visitors' AND ts_utc>=:fr AND ts_utc<:to"),
            {"sid": store_id, "fr": _fmt(start), "to": _fmt(end)},
        ).mappings().all()
        tc = conn.execute(
            text("SELECT ts_utc, visitors AS v FROM traffic_count "
                 "WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"),
            {"sid": store_id, "fr": _fmt(start), "to": _fmt(end)},
        ).mappings().all()

    def _hours(rows) -> dict[date, list[int]]:
        out: dict[date, list[int]] = defaultdict(list)
        for r in rows:
            if float(r["v"]) >= OCCUPANCY_MIN_VISITORS:
                ts = _parse(r["ts_utc"])
                out[ts.date()].append(ts.hour)
        return out

    by_fc, by_tc = _hours(fc), _hours(tc)
    result: dict[date, tuple[int, int, str]] = {}
    for day in set(by_fc) | set(by_tc):
        hours, src = (by_fc[day], "forecast") if by_fc.get(day) else (by_tc[day], "traffic_actual")
        result[day] = (min(hours) * 60, (max(hours) + 1) * 60, src)
    return result, {"forecast_visitors": len(fc), "traffic_count": len(tc)}


def _meter(engine: Engine, store_id: str, start: datetime, end: datetime
           ) -> tuple[dict[date, dict[int, float]], dict[int, float], dict[str, int], bool]:
    """Hourly HVAC kWh per day in [start, end) and a trailing hourly baseline."""
    base_from = start - timedelta(days=BASELINE_DAYS)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ts_utc, hvac_kwh, source_system FROM energy_reading "
                 "WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to"),
            {"sid": store_id, "fr": _fmt(base_from), "to": _fmt(end)},
        ).mappings().all()
    by_day: dict[date, dict[int, float]] = defaultdict(dict)
    base_sum: dict[int, float] = defaultdict(float)
    base_n: dict[int, int] = defaultdict(int)
    for r in rows:
        ts = _parse(r["ts_utc"])
        if ts >= start:
            by_day[ts.date()][ts.hour] = float(r["hvac_kwh"])
        else:
            base_sum[ts.hour] += float(r["hvac_kwh"])
            base_n[ts.hour] += 1
    baseline = {h: base_sum[h] / base_n[h] for h in base_sum}
    synthetic = all(r["source_system"].startswith("synthetic") for r in rows) if rows else True
    return dict(by_day), baseline, {"energy_reading": len(rows)}, synthetic


def _empty_day(day: date, reason: str) -> dict:
    return {"date": day.isoformat(), "status": "insufficient_evidence", "reason": reason,
            "occupancy_source": None, "meter_source": None,
            "first_occupied": None, "last_occupied": None,
            "hvac_on": None, "hvac_off": None, "recommended_on": None, "recommended_off": None,
            "early_start_minutes": 0, "late_stop_minutes": 0, "comfort_risk_minutes": 0,
            "trucks_due": 0, "trucks_slipped": 0, "dock_heat_needed": None,
            "early_start_kwh": 0.0, "late_stop_kwh": 0.0, "dock_heat_kwh": 0.0,
            "avoidable_kwh": 0.0, "avoidable_usd": 0.0, "findings": []}


def hvac_schedule_check(engine: Engine, store_id: str, as_of_utc: datetime,
                        horizon_days: int = ENERGY_MAX_HORIZON_DAYS) -> dict:
    """Join the BMS HVAC timetable to store activity for each of the next `horizon_days` days."""
    if not 1 <= horizon_days <= ENERGY_MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be between 1 and {ENERGY_MAX_HORIZON_DAYS}")
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    start = as_of_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=horizon_days)

    bms = BmsMcp(engine)
    try:
        sched = bms.call_tool("get_hvac_schedule", {"store_id": store_id})
    except ValueError:
        sched = None
    occupancy, occ_counts = _occupancy(engine, store_id, start, end)
    meter_by_day, baseline, meter_counts, meter_synthetic = _meter(engine, store_id, start, end)
    feed = TmsMcp(engine).call_tool("list_inbound_shipments", {
        "store_id": store_id, "from_date": start.date().isoformat(), "horizon_days": horizon_days,
    })
    shipments = feed["shipments"]

    days: list[dict] = []
    for i in range(horizon_days):
        day = (start + timedelta(days=i)).date()
        if sched is None:
            days.append(_empty_day(day, "no BMS schedule on record"))
            continue
        occ = occupancy.get(day)
        if occ is None:
            days.append(_empty_day(day, "no visitor forecast or traffic count for the day"))
            continue
        actual = meter_by_day.get(day, {})
        if len(actual) >= MIN_READINGS_PER_DAY:
            hourly, meter_source = actual, "meter"
        elif baseline:
            hourly, meter_source = baseline, "meter_baseline"
        else:
            days.append(_empty_day(day, "no meter readings for the day or the trailing baseline"))
            continue

        open_m, close_m, occ_source = occ
        on, off = int(sched["hvac_on_minute"]), int(sched["hvac_off_minute"])
        warm = int(sched["warmup_minutes_p90"])
        rec_on = max(0, open_m - COMFORT_LEAD_MINUTES - warm)
        rec_off = min(24 * 60, close_m + CLOSE_GRACE_MINUTES)
        early_min = max(0, rec_on - on)
        late_min = max(0, off - rec_off)
        comfort_min = max(0, on - rec_on)

        hold_from = on + warm
        if open_m - hold_from >= 30:
            holding_kw = _kwh_between(hourly, hold_from, open_m) / ((open_m - hold_from) / 60.0)
        else:
            holding_kw = hourly.get(open_m // 60, 0.0)
        early_kwh = holding_kw * early_min / 60.0
        late_kwh = _kwh_between(hourly, rec_off, off) if late_min else 0.0

        due = [s for s in shipments
               if _parse(s["eta_utc"]).date() == day and s["status"] in ACTIVE_TRUCK_STATUSES]
        slipped = [s for s in shipments
                   if _parse(s["planned_arrival_utc"]).date() == day
                   and (s["status"] == "cancelled" or _parse(s["eta_utc"]).date() != day)]
        d_on, d_off = int(sched["dock_heat_on_minute"]), int(sched["dock_heat_off_minute"])
        dock_window = d_off > d_on and float(sched["dock_heat_kw"]) > 0
        dock_needed = bool(due) if dock_window else None
        dock_kwh = float(sched["dock_heat_kw"]) * (d_off - d_on) / 60.0 if dock_window and not due else 0.0

        findings: list[dict] = []
        if early_min:
            findings.append({
                "kind": "early_start", "window": f"{minutes_to_hhmm(on)}-{minutes_to_hhmm(rec_on)}",
                "minutes": early_min, "kwh": round(early_kwh, 1),
                "evidence": (f"BMS starts {minutes_to_hhmm(on)}; warm-up is {warm} min and the first visitors "
                             f"arrive {minutes_to_hhmm(open_m)}, so {minutes_to_hhmm(rec_on)} is enough"),
            })
        if late_min:
            findings.append({
                "kind": "late_stop", "window": f"{minutes_to_hhmm(rec_off)}-{minutes_to_hhmm(off)}",
                "minutes": late_min, "kwh": round(late_kwh, 1),
                "evidence": (f"last visitors {minutes_to_hhmm(close_m)}; closing tasks end "
                             f"{minutes_to_hhmm(rec_off)}; BMS runs until {minutes_to_hhmm(off)}"),
            })
        if dock_kwh:
            reason = ("truck(s) planned for the day slipped or were cancelled in the TMS: "
                      + ", ".join(s["truck_id"] for s in slipped)) if slipped else "no truck due in the TMS"
            findings.append({
                "kind": "dock_heat_no_truck",
                "window": f"{minutes_to_hhmm(d_on)}-{minutes_to_hhmm(d_off)}",
                "minutes": d_off - d_on, "kwh": round(dock_kwh, 1), "evidence": reason,
                "trucks": [{"shipment_id": s["shipment_id"], "truck_id": s["truck_id"],
                            "status": s["status"], "eta_utc": s["eta_utc"]} for s in slipped],
            })
        if comfort_min:
            findings.append({
                "kind": "comfort_risk", "window": f"{minutes_to_hhmm(rec_on)}-{minutes_to_hhmm(on)}",
                "minutes": comfort_min, "kwh": 0.0,
                "evidence": (f"BMS starts {minutes_to_hhmm(on)} but needs {warm} min to reach setpoint "
                             f"before {minutes_to_hhmm(open_m - COMFORT_LEAD_MINUTES)}"),
            })

        avoidable = early_kwh + late_kwh + dock_kwh
        if avoidable >= WASTE_THRESHOLD_KWH:
            status = "waste"
        elif comfort_min:
            status = "comfort_risk"
        else:
            status = "aligned"

        days.append({
            "date": day.isoformat(),
            "status": status,
            "reason": None,
            "occupancy_source": occ_source,
            "meter_source": meter_source,
            "first_occupied": minutes_to_hhmm(open_m),
            "last_occupied": minutes_to_hhmm(close_m),
            "hvac_on": minutes_to_hhmm(on),
            "hvac_off": minutes_to_hhmm(off),
            "recommended_on": minutes_to_hhmm(rec_on),
            "recommended_off": minutes_to_hhmm(rec_off),
            "early_start_minutes": early_min,
            "late_stop_minutes": late_min,
            "comfort_risk_minutes": comfort_min,
            "trucks_due": len(due),
            "trucks_slipped": len(slipped),
            "dock_heat_needed": dock_needed,
            "early_start_kwh": round(early_kwh, 1),
            "late_stop_kwh": round(late_kwh, 1),
            "dock_heat_kwh": round(dock_kwh, 1),
            "avoidable_kwh": round(avoidable, 1),
            "avoidable_usd": round(avoidable * TARIFF_USD_PER_KWH, 2),
            "findings": findings,
        })

    evidenced = [d for d in days if d["status"] != "insufficient_evidence"]
    total_kwh = sum(d["avoidable_kwh"] for d in days)
    schedule_kwh = [d["early_start_kwh"] + d["late_stop_kwh"] for d in evidenced]
    daily_schedule_kwh = sum(schedule_kwh) / len(schedule_kwh) if schedule_kwh else 0.0
    recommended = None
    if sched is not None and evidenced:
        opens = [occupancy[date.fromisoformat(d["date"])][0] for d in evidenced]
        closes = [occupancy[date.fromisoformat(d["date"])][1] for d in evidenced]
        warm = int(sched["warmup_minutes_p90"])
        # Earliest open / latest close across the window: the comfort-safe schedule.
        recommended = {
            "hvac_on": minutes_to_hhmm(max(0, min(opens) - COMFORT_LEAD_MINUTES - warm)),
            "hvac_off": minutes_to_hhmm(min(24 * 60, max(closes) + CLOSE_GRACE_MINUTES)),
            "skip_dock_heat_without_truck": any(d["dock_heat_kwh"] > 0 for d in evidenced),
        }

    summary = {
        "days_checked": horizon_days,
        "by_status": {k: sum(1 for d in days if d["status"] == k)
                      for k in ("waste", "comfort_risk", "aligned", "insufficient_evidence")},
        "avoidable_kwh": round(total_kwh, 1),
        "avoidable_usd": round(total_kwh * TARIFF_USD_PER_KWH, 2),
        "avoidable_co2_kg": round(total_kwh * CO2_KG_PER_KWH, 1),
        "schedule_kwh_per_day": round(daily_schedule_kwh, 1),
        # Schedule waste repeats every day; dock savings depend on slips, so they are left out.
        "annualized_usd": round(daily_schedule_kwh * 365 * TARIFF_USD_PER_KWH, 0),
        "annualized_mwh": round(daily_schedule_kwh * 365 / 1000, 1),
        "annualized_co2_t": round(daily_schedule_kwh * 365 * CO2_KG_PER_KWH / 1000, 1),
        "current_schedule": ({"hvac_on": sched["hvac_on"], "hvac_off": sched["hvac_off"],
                              "warmup_minutes_p90": sched["warmup_minutes_p90"],
                              "dock_heat": f"{sched['dock_heat_on']}-{sched['dock_heat_off']}"}
                             if sched else None),
        "recommended_schedule": recommended,
        "trucks_in_window": len(shipments),
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
            "thresholds": {"comfort_lead_minutes": COMFORT_LEAD_MINUTES,
                           "close_grace_minutes": CLOSE_GRACE_MINUTES,
                           "waste_threshold_kwh": WASTE_THRESHOLD_KWH,
                           "occupancy_min_visitors": OCCUPANCY_MIN_VISITORS,
                           "baseline_days": BASELINE_DAYS},
            "tariff_usd_per_kwh": TARIFF_USD_PER_KWH,
            "co2_kg_per_kwh": CO2_KG_PER_KWH,
            "source_tables": ["hvac_schedule", "energy_reading", "forecast", "traffic_count",
                              "inbound_shipment"],
            "row_counts": {"hvac_schedule": 1 if sched else 0, **meter_counts, **occ_counts,
                           "inbound_shipment": len(shipments)},
            "bms_server": sched["server"] if sched else {"name": bms.name, "version": bms.version},
            "tms_server": feed["server"],
            "assumptions": list(ASSUMPTIONS),
            "synthetic_data": bool((sched or {}).get("synthetic", True) and meter_synthetic),
        },
        "status": "ok" if evidenced else "insufficient_evidence",
    }
