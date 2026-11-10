"""Pydantic response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    value: float | None = Field(description="Null when insufficient evidence.")
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


class PrincipalResponse(BaseModel):
    subject: str
    role: str
    regions: list[str]
    stores: list[str]


class ForecastPointResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    ts_utc: datetime
    predicted: float
    lower_80: float | None = None
    upper_80: float | None = None
    run_id: str
    model_name: str
    model_version: str
    wape: float | None = None


class AnomalyResponse(BaseModel):
    anomaly_id: str
    store_id: str
    ts_utc: datetime
    kind: str
    severity: str
    observed: float
    baseline: float
    deviation: float
    z_score: float | None = None
    detector_name: str
    detector_version: str
    status: str
    evidence: dict
    created_at_utc: datetime


class RecommendationResponse(BaseModel):
    recommendation_id: str
    run_id: str | None
    store_id: str
    title: str
    rationale: str
    evidence: dict
    expected_direction: str
    confidence: str
    risk: str | None
    status: str
    created_at_utc: datetime
    created_by: str
    reviewer: str | None = None
    reviewed_at_utc: datetime | None = None
    reviewer_comment: str | None = None
    prompt_version: str


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "start", "complete"]
    comment: str | None = None


class ApprovalResponse(BaseModel):
    recommendation_id: str
    from_status: str
    to_status: str
    reviewer: str
    reviewed_at_utc: str
    comment: str | None


class InboundShipmentResponse(BaseModel):
    """One TMS truck record. Timestamps are ISO-8601 UTC strings from the adapter."""
    shipment_id: str
    store_id: str
    dc_id: str
    truck_id: str
    carrier: str
    planned_arrival_utc: datetime
    eta_utc: datetime
    status: str
    delay_minutes: int
    pallets: int
    cases: int
    unload_minutes_est: int
    source_system: str
    schema_version: str
    synthetic: bool


class InboundPlanDayResponse(BaseModel):
    date: str
    status: Literal["balanced", "under_coverage", "idle_risk", "no_inbound", "insufficient_evidence"]
    trucks_due: int
    trucks_slipped: int
    trucks_late_same_day: int
    unload_minutes_est: int
    idle_minutes_at_risk: int
    planned_labor_minutes: float | None
    receiving_window_labor_minutes: float | None
    receiving_share: float | None
    shipments: list[dict]


class InboundPlanWindowResponse(BaseModel):
    from_utc: datetime
    to_utc: datetime
    horizon_days: int


class InboundLaborPlanResponse(BaseModel):
    store_id: str
    window: InboundPlanWindowResponse
    days: list[InboundPlanDayResponse]
    summary: dict
    lineage: dict
    status: Literal["ok", "insufficient_evidence"]


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    intent: str
    output: dict | None = None
    tokens_used: int
    step_count: int
    error: str | None = None


class AuditEventResponse(BaseModel):
    id: int
    at_utc: datetime
    actor: str
    action: str
    target_type: str
    target_id: str | None
    correlation_id: str | None
    payload: dict | None
