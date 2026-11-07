"""Pydantic response schemas used by the FastAPI layer."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LineageResponse(BaseModel):
    metric_name: str
    metric_version: str
    formula: str
    source_tables: list[str]
    row_counts: dict[str, int]
    assumptions: list[str]
    synthetic_data: bool


class WindowResponse(BaseModel):
    from_utc: datetime
    to_utc: datetime


class KpiResponse(BaseModel):
    value: float | None = Field(
        description="KPI value. Null when the window has no labor hours or no data."
    )
    unit: str
    window: WindowResponse
    lineage: LineageResponse
    status: Literal["ok", "insufficient_evidence"]


class StoreResponse(BaseModel):
    store_id: str
    name: str
    region: str
    timezone: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str = "storepulse-api"
    version: str
