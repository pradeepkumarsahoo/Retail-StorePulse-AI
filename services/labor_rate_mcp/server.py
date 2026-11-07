"""MCP-style Labor Rate Reference server.

Shape of a real MCP server (`list_tools`, `call_tool`, typed args/results,
read-only) kept in a pluggable in-process adapter. Swapping in the official
MCP Python SDK transport is an adapter change — the tool contracts stay as is.

Every rate carries source, version, effective date, and freshness metadata.
Never writes. Never approves. Never changes employment or pay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine


MCP_SERVER_NAME = "labor-rate-reference"
MCP_SERVER_VERSION = "1.0.0"

# Synthetic reference rates. Clearly labeled.
SYNTHETIC_RATES: list[dict] = [
    {"region": "West",      "role_category": "associate",  "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 1850, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "West",      "role_category": "lead",       "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 2450, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "Midwest",   "role_category": "associate",  "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 1600, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "Midwest",   "role_category": "lead",       "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 2150, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "South",     "role_category": "associate",  "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 1550, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "South",     "role_category": "lead",       "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 2050, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "Northeast", "role_category": "associate",  "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 1950, "source": "synthetic_reference",
     "version": "1.0.0"},
    {"region": "Northeast", "role_category": "lead",       "effective_from": "2026-01-01",
     "currency": "USD", "rate_per_hour_cents": 2550, "source": "synthetic_reference",
     "version": "1.0.0"},
]

ALLOWED_REGIONS = frozenset(r["region"] for r in SYNTHETIC_RATES)
ALLOWED_ROLES = frozenset(r["role_category"] for r in SYNTHETIC_RATES)


class GetRateArgs(BaseModel):
    region: str
    role_category: str = "associate"
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ListRegionsArgs(BaseModel):
    pass


class MetadataArgs(BaseModel):
    pass


class McpTool(BaseModel):
    name: str
    description: str
    input_schema: dict


TOOL_SCHEMAS = [
    McpTool(
        name="get_labor_rate",
        description="Return the approved hourly labor rate for the requested region/role/effective date.",
        input_schema=GetRateArgs.model_json_schema(),
    ),
    McpTool(
        name="list_labor_rate_regions",
        description="List the regions for which labor-rate reference data exists.",
        input_schema=ListRegionsArgs.model_json_schema(),
    ),
    McpTool(
        name="get_labor_rate_metadata",
        description="Return source, version, and freshness metadata for the labor-rate server.",
        input_schema=MetadataArgs.model_json_schema(),
    ),
]


def seed_rates(engine: Engine) -> int:
    """Seed the DB table with the synthetic reference rates."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM labor_rate"))
        conn.execute(
            text(
                "INSERT INTO labor_rate (region, role_category, effective_from, currency, "
                "rate_per_hour_cents, source, version) "
                "VALUES (:region, :role_category, :effective_from, :currency, "
                ":rate_per_hour_cents, :source, :version)"
            ),
            SYNTHETIC_RATES,
        )
    return len(SYNTHETIC_RATES)


class LaborRateMcp:
    """In-process MCP-style adapter. Real MCP SDK transport pluggable in M7."""

    name = MCP_SERVER_NAME
    version = MCP_SERVER_VERSION

    def __init__(self, engine: Engine):
        self.engine = engine

    # --- MCP-style surface ------------------------------------------------

    def list_tools(self) -> list[dict]:
        return [t.model_dump() for t in TOOL_SCHEMAS]

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "get_labor_rate":
            args = GetRateArgs.model_validate(arguments)
            if args.region not in ALLOWED_REGIONS:
                raise ValueError(f"region not in allowlist: {args.region}")
            if args.role_category not in ALLOWED_ROLES:
                raise ValueError(f"role_category not in allowlist: {args.role_category}")
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT region, role_category, effective_from, currency, "
                        "rate_per_hour_cents, source, version "
                        "FROM labor_rate "
                        "WHERE region=:r AND role_category=:rc AND effective_from<=:ed "
                        "ORDER BY effective_from DESC LIMIT 1"
                    ),
                    {"r": args.region, "rc": args.role_category, "ed": args.effective_date},
                ).mappings().one_or_none()
            if row is None:
                raise ValueError(
                    f"No labor rate for region={args.region} role={args.role_category} "
                    f"effective<={args.effective_date}"
                )
            return {
                "region": row["region"],
                "role_category": row["role_category"],
                "effective_from": row["effective_from"],
                "rate_per_hour_usd": round(row["rate_per_hour_cents"] / 100.0, 4),
                "currency": row["currency"],
                "source": row["source"],
                "version": row["version"],
                "server": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "synthetic": True,
            }
        if name == "list_labor_rate_regions":
            return {"regions": sorted(ALLOWED_REGIONS)}
        if name == "get_labor_rate_metadata":
            return {
                "server": MCP_SERVER_NAME,
                "version": MCP_SERVER_VERSION,
                "rates_available": len(SYNTHETIC_RATES),
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "synthetic": True,
            }
        raise ValueError(f"Unknown tool: {name}")
