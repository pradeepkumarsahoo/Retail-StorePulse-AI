"""SQLAlchemy engine factory. Reads STOREPULSE_DB_URL; defaults to local SQLite."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from infrastructure.db.models import Base


def _default_sqlite_url() -> str:
    root = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(root / 'storepulse.sqlite').as_posix()}"


def get_engine(url: str | None = None) -> Engine:
    db_url = url or os.environ.get("STOREPULSE_DB_URL") or _default_sqlite_url()
    kwargs: dict = {"future": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # Shared in-memory DB across connections (required for tests and TestClient).
        if ":memory:" in db_url or db_url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
    return create_engine(db_url, **kwargs)


def init_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, future=True)
