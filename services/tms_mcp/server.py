"""MCP-style Transport Management System (TMS) adapter.

The TMS is the system of record for inbound DC → store trucks. StorePulse-AI
never writes to it. This adapter exposes an MCP-shaped, read-only surface
(`list_tools`, `call_tool`, typed args/results) over the validated
`inbound_shipment` mirror so agents can look up to **5 days ahead** and plan
labor around receiving workload and ETA slips.

Why an adapter and not a direct query (ADR-0013):
- Same shape as the labor-rate server, so swapping in the real TMS vendor API
  or the official MCP SDK transport is below the tool gateway.
- A hard horizon cap keeps agents from pulling unbounded shipment history.
- Every response carries source, freshness, and `synthetic` labeling.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine


MCP_SERVER_NAME = "tms-inbound-shipments"
MCP_SERVER_VERSION = "1.0.0"

# Planning horizon. The business rule is "see trucks 5 days before they land."
TMS_MAX_HORIZON_DAYS = int(os.environ.get("STOREPULSE_TMS_HORIZON_DAYS", "5"))


class ListShipmentsArgs(BaseModel):
    store_id: str = Field(pattern=r"^S\d{3}$")
    from_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")   # inclusive, UTC calendar day
    horizon_days: int = Field(default=TMS_MAX_HORIZON_DAYS, ge=1, le=TMS_MAX_HORIZON_DAYS)


class GetShipmentArgs(BaseModel):
    shipment_id: str = Field(pattern=r"^SHP-\d{6}$")


class MetadataArgs(BaseModel):
    pass


class McpTool(BaseModel):
    name: str
    description: str
    input_schema: dict


TOOL_SCHEMAS = [
    McpTool(
        name="list_inbound_shipments",
        description=(
            "List DC trucks planned or en route to one store from `from_date` for up to "
            f"{TMS_MAX_HORIZON_DAYS} days ahead (planned arrival or live ETA in window)."
        ),
        input_schema=ListShipmentsArgs.model_json_schema(),
    ),
    McpTool(
        name="get_inbound_shipment",
        description="Return one shipment record by shipment_id.",
        input_schema=GetShipmentArgs.model_json_schema(),
    ),
    McpTool(
        name="get_tms_metadata",
        description="Return source, version, horizon cap, and freshness metadata for the TMS adapter.",
        input_schema=MetadataArgs.model_json_schema(),
    ),
]


_SELECT = (
    "SELECT shipment_id, store_id, dc_id, truck_id, carrier, planned_arrival_utc, eta_utc, "
    "status, delay_minutes, pallets, cases, unload_minutes_est, source_system, schema_version "
    "FROM inbound_shipment "
)


def _iso(v) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    # SQLite returns canonical 'YYYY-MM-DD HH:MM:SS.ffffff' strings.
    return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["planned_arrival_utc"] = _iso(d["planned_arrival_utc"])
    d["eta_utc"] = _iso(d["eta_utc"])
    d["synthetic"] = d["source_system"].startswith("synthetic")
    return d


class TmsMcp:
    """In-process MCP-style adapter over the validated TMS mirror. Read-only."""

    name = MCP_SERVER_NAME
    version = MCP_SERVER_VERSION

    def __init__(self, engine: Engine):
        self.engine = engine

    def list_tools(self) -> list[dict]:
        return [t.model_dump() for t in TOOL_SCHEMAS]

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "list_inbound_shipments":
            args = ListShipmentsArgs.model_validate(arguments)
            start = datetime.combine(date.fromisoformat(args.from_date), datetime.min.time(),
                                     tzinfo=timezone.utc)
            end = start + timedelta(days=args.horizon_days)
            fmt = "%Y-%m-%d %H:%M:%S.%f"
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        _SELECT
                        + "WHERE store_id=:sid AND ("
                        "  (planned_arrival_utc>=:fr AND planned_arrival_utc<:to) OR "
                        "  (eta_utc>=:fr AND eta_utc<:to)) "
                        "ORDER BY eta_utc, shipment_id"
                    ),
                    {"sid": args.store_id, "fr": start.strftime(fmt), "to": end.strftime(fmt)},
                ).mappings().all()
            return {
                "store_id": args.store_id,
                "window": {"from_utc": start.isoformat(), "to_utc": end.isoformat(),
                           "horizon_days": args.horizon_days},
                "shipments": [_row_to_dict(r) for r in rows],
                "server": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        if name == "get_inbound_shipment":
            args = GetShipmentArgs.model_validate(arguments)
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(_SELECT + "WHERE shipment_id=:sid"), {"sid": args.shipment_id}
                ).mappings().one_or_none()
            if row is None:
                raise ValueError(f"Unknown shipment_id: {args.shipment_id}")
            out = _row_to_dict(row)
            out["server"] = {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION}
            out["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
            return out
        if name == "get_tms_metadata":
            with self.engine.connect() as conn:
                n = conn.execute(text("SELECT COUNT(*) FROM inbound_shipment")).scalar() or 0
                src = conn.execute(
                    text("SELECT DISTINCT source_system FROM inbound_shipment ORDER BY source_system")
                ).scalars().all()
            return {
                "server": MCP_SERVER_NAME,
                "version": MCP_SERVER_VERSION,
                "max_horizon_days": TMS_MAX_HORIZON_DAYS,
                "shipments_available": int(n),
                "source_systems": list(src),
                "read_only": True,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "synthetic": all(s.startswith("synthetic") for s in src) if src else True,
            }
        raise ValueError(f"Unknown tool: {name}")
