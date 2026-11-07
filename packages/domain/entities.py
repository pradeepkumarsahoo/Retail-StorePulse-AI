"""Pure-Python domain entities. No framework, DB, or HTTP imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Store:
    store_id: str
    name: str
    region: str
    timezone: str


@dataclass(frozen=True)
class KpiWindow:
    """UTC window closed-open: [from_utc, to_utc)."""
    from_utc: datetime
    to_utc: datetime

    def __post_init__(self) -> None:
        if self.to_utc <= self.from_utc:
            raise ValueError("to_utc must be strictly after from_utc")


@dataclass(frozen=True)
class KpiLineage:
    metric_name: str
    metric_version: str
    formula: str
    source_tables: tuple[str, ...]
    row_counts: dict[str, int]
    assumptions: tuple[str, ...]
    synthetic_data: bool = True


@dataclass(frozen=True)
class KpiValue:
    value: float | None
    unit: str
    window: KpiWindow
    lineage: KpiLineage
