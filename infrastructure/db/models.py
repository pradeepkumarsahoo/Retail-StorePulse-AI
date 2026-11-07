"""SQLAlchemy ORM models. ANSI-SQL only — SQLite today, Postgres from M7."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Store(Base):
    __tablename__ = "store"
    store_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)


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


class MetricCatalog(Base):
    """Central metric catalog. Seeds the single definition of SPLH for v1."""
    __tablename__ = "metric_catalog"
    metric_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    source_tables: Mapped[str] = mapped_column(Text, nullable=False)  # comma-sep
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Quarantine(Base):
    """Rows rejected by Pandera at ingest. Reason + original payload retained."""
    __tablename__ = "quarantine"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_row_json: Mapped[str] = mapped_column(Text, nullable=False)
