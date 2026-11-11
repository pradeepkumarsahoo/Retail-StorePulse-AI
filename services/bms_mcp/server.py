"""MCP-style building-management system (BMS) adapter.

The BMS is the system of record for each store's HVAC timetable and its
energy meter. StorePulse-AI never writes to it: a recommended schedule change
is applied by facilities in the BMS after a human approves it. This adapter
exposes an MCP-shaped, read-only surface (`list_tools`, `call_tool`, typed
args/results) over the validated `hvac_schedule` and `energy_reading`
mirrors.

Why an adapter and not a direct query (ADR-0015, same pattern as ADR-0013):
- Same shape as the labor-rate and TMS servers, so swapping in a real BMS
  vendor API (BACnet gateway, cloud BMS) or the MCP SDK transport is below
  the tool gateway.
- A hard cap on meter history keeps agents from pulling unbounded readings.
- Every response carries source, freshness, and `synthetic` labeling.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine


MCP_SERVER_NAME = "bms-hvac"
MCP_SERVER_VERSION = "1.0.0"

# Longest meter-history window one call may return (days).
BMS_MAX_READING_DAYS = int(os.environ.get("STOREPULSE_BMS_MAX_READING_DAYS", "14"))


class GetScheduleArgs(BaseModel):
    store_id: str = Field(pattern=r"^S\d{3}$")


class ListReadingsArgs(BaseModel):
    store_id: str = Field(pattern=r"^S\d{3}$")
    from_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")   # inclusive, UTC calendar day
    days: int = Field(default=1, ge=1, le=BMS_MAX_READING_DAYS)


class MetadataArgs(BaseModel):
    pass


class McpTool(BaseModel):
    name: str
    description: str
    input_schema: dict


TOOL_SCHEMAS = [
    McpTool(
        name="get_hvac_schedule",
        description="Return the HVAC timetable the store's BMS runs today (on/off, warm-up, dock heating).",
        input_schema=GetScheduleArgs.model_json_schema(),
    ),
    McpTool(
        name="list_energy_readings",
        description=(
            "Hourly meter readings (HVAC kWh, total kWh, outdoor °F) for one store, "
            f"up to {BMS_MAX_READING_DAYS} days from `from_date`."
        ),
        input_schema=ListReadingsArgs.model_json_schema(),
    ),
    McpTool(
        name="get_bms_metadata",
        description="Return source, version, history cap, and freshness metadata for the BMS adapter.",
        input_schema=MetadataArgs.model_json_schema(),
    ),
]


def minutes_to_hhmm(m: int) -> str:
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


def _iso(v) -> str:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc).isoformat()


class BmsMcp:
    """In-process MCP-style adapter over the validated BMS mirror. Read-only."""

    name = MCP_SERVER_NAME
    version = MCP_SERVER_VERSION

    def __init__(self, engine: Engine):
        self.engine = engine

    def list_tools(self) -> list[dict]:
        return [t.model_dump() for t in TOOL_SCHEMAS]

    def _server(self) -> dict:
        return {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION}

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "get_hvac_schedule":
            args = GetScheduleArgs.model_validate(arguments)
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT store_id, bms_id, hvac_on_minute, hvac_off_minute, warmup_minutes_p90, "
                        "dock_heat_on_minute, dock_heat_off_minute, dock_heat_kw, setpoint_f, "
                        "source_system, schema_version FROM hvac_schedule WHERE store_id=:sid"
                    ),
                    {"sid": args.store_id},
                ).mappings().one_or_none()
            if row is None:
                raise ValueError(f"No HVAC schedule on record for store {args.store_id}")
            out = dict(row)
            out["hvac_on"] = minutes_to_hhmm(out["hvac_on_minute"])
            out["hvac_off"] = minutes_to_hhmm(out["hvac_off_minute"])
            out["dock_heat_on"] = minutes_to_hhmm(out["dock_heat_on_minute"])
            out["dock_heat_off"] = minutes_to_hhmm(out["dock_heat_off_minute"])
            out["synthetic"] = out["source_system"].startswith("synthetic")
            out["server"] = self._server()
            out["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
            return out
        if name == "list_energy_readings":
            args = ListReadingsArgs.model_validate(arguments)
            start = datetime.combine(date.fromisoformat(args.from_date), datetime.min.time(),
                                     tzinfo=timezone.utc)
            end = start + timedelta(days=args.days)
            fmt = "%Y-%m-%d %H:%M:%S.%f"
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT ts_utc, hvac_kwh, total_kwh, outdoor_temp_f, source_system "
                        "FROM energy_reading WHERE store_id=:sid AND ts_utc>=:fr AND ts_utc<:to "
                        "ORDER BY ts_utc"
                    ),
                    {"sid": args.store_id, "fr": start.strftime(fmt), "to": end.strftime(fmt)},
                ).mappings().all()
            readings = [
                {"ts_utc": _iso(r["ts_utc"]), "hvac_kwh": float(r["hvac_kwh"]),
                 "total_kwh": float(r["total_kwh"]), "outdoor_temp_f": float(r["outdoor_temp_f"])}
                for r in rows
            ]
            return {
                "store_id": args.store_id,
                "window": {"from_utc": start.isoformat(), "to_utc": end.isoformat(), "days": args.days},
                "readings": readings,
                "synthetic": all(r["source_system"].startswith("synthetic") for r in rows) if rows else True,
                "server": self._server(),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        if name == "get_bms_metadata":
            with self.engine.connect() as conn:
                n_sched = conn.execute(text("SELECT COUNT(*) FROM hvac_schedule")).scalar() or 0
                n_read = conn.execute(text("SELECT COUNT(*) FROM energy_reading")).scalar() or 0
                src = conn.execute(
                    text("SELECT DISTINCT source_system FROM hvac_schedule ORDER BY source_system")
                ).scalars().all()
            return {
                "server": MCP_SERVER_NAME,
                "version": MCP_SERVER_VERSION,
                "max_reading_days": BMS_MAX_READING_DAYS,
                "schedules_available": int(n_sched),
                "readings_available": int(n_read),
                "source_systems": list(src),
                "read_only": True,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "synthetic": all(s.startswith("synthetic") for s in src) if src else True,
            }
        raise ValueError(f"Unknown tool: {name}")
