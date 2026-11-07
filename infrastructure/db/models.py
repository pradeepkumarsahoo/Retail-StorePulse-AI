"""SQLAlchemy ORM models. ANSI-SQL only — SQLite today, Postgres from M7."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --- Masters --------------------------------------------------------------

class Store(Base):
    __tablename__ = "store"
    store_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)


# --- Facts ----------------------------------------------------------------

class SalesTxn(Base):
    __tablename__ = "sales_txn"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    net_sales_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transactions: Mapped[int] = mapped_column(Integer, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("store_id", "ts_utc", name="uq_sales_store_ts"),
        Index("ix_sales_store_ts", "store_id", "ts_utc"),
    )


class LaborHours(Base):
    __tablename__ = "labor_hours"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    labor_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("store_id", "ts_utc", name="uq_labor_store_ts"),
        Index("ix_labor_store_ts", "store_id", "ts_utc"),
    )


class TrafficCount(Base):
    __tablename__ = "traffic_count"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visitors: Mapped[int] = mapped_column(Integer, nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("store_id", "ts_utc", name="uq_traffic_store_ts"),
    )


# --- Catalogs & reference data --------------------------------------------

class MetricCatalog(Base):
    """Central metric catalog — the single definition every KPI response cites."""
    __tablename__ = "metric_catalog"
    metric_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    source_tables: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class LaborRate(Base):
    """Approved labor-rate reference, served through the MCP-style adapter.

    Deliberately kept small and read-only; never used to change pay or schedule.
    """
    __tablename__ = "labor_rate"
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_category: Mapped[str] = mapped_column(String(32), primary_key=True)
    effective_from: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    rate_per_hour_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)


# --- Governance: data quality & quarantine --------------------------------

class Quarantine(Base):
    __tablename__ = "quarantine"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_row_json: Mapped[str] = mapped_column(Text, nullable=False)


# --- Forecasts & anomalies ------------------------------------------------

class ForecastRun(Base):
    __tablename__ = "forecast_run"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wape: Mapped[float] = mapped_column(Float, nullable=True)
    mae: Mapped[float] = mapped_column(Float, nullable=True)
    rmse: Mapped[float] = mapped_column(Float, nullable=True)
    train_window_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_window_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)


class Forecast(Base):
    __tablename__ = "forecast"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("forecast_run.run_id"), nullable=False)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "labor_minutes" | "net_sales_cents"
    predicted: Mapped[float] = mapped_column(Float, nullable=False)
    lower_80: Mapped[float] = mapped_column(Float, nullable=True)
    upper_80: Mapped[float] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_forecast_store_ts", "store_id", "ts_utc"),
    )


class Anomaly(Base):
    __tablename__ = "anomaly"
    anomaly_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)   # low|medium|high
    observed: Mapped[float] = mapped_column(Float, nullable=False)
    baseline: Mapped[float] = mapped_column(Float, nullable=False)
    deviation: Mapped[float] = mapped_column(Float, nullable=False)
    z_score: Mapped[float] = mapped_column(Float, nullable=True)
    detector_name: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open|reviewed|dismissed
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_anomaly_store_ts", "store_id", "ts_utc"),
    )


# --- AI runs, tool calls, recommendations, approvals, audit events ---------

class AiRun(Base):
    """A single supervisor-coordinated workflow invocation."""
    __tablename__ = "ai_run"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_subject: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    store_scope: Mapped[str] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok|insufficient_evidence|error
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    output_json: Mapped[str] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)


class ToolCall(Base):
    """Every tool invocation made during an AI run."""
    __tablename__ = "tool_call"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("ai_run.run_id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)   # ok|denied|error|timeout
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Recommendation(Base):
    __tablename__ = "recommendation"
    recommendation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("ai_run.run_id"), nullable=True)
    store_id: Mapped[str] = mapped_column(String(8), ForeignKey("store.store_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(16), nullable=False)  # up|down|neutral
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)          # low|medium|high
    risk: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # draft -> pending_review -> approved|rejected -> in_progress -> completed|expired
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), nullable=True)
    reviewed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_comment: Mapped[str] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)


class ApprovalEvent(Base):
    __tablename__ = "approval_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recommendation.recommendation_id"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=True)
    at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    """Append-only, tamper-evident audit log for user actions and policy decisions."""
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(32), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=True)
