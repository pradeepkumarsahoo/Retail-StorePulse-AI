"""Shared FastAPI dependencies: engine and supervisor."""
from __future__ import annotations

from sqlalchemy.engine import Engine

from infrastructure.db.engine import get_engine, init_schema
from packages.ai_orchestration.llm_provider import DeterministicProvider, LlmProvider
from packages.ai_orchestration.supervisor import Supervisor


_engine: Engine | None = None
_llm: LlmProvider | None = None


def set_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def set_llm(llm: LlmProvider) -> None:
    global _llm
    _llm = llm


def get_api_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = get_engine()
        init_schema(_engine)
    return _engine


def get_api_llm() -> LlmProvider:
    global _llm
    if _llm is None:
        _llm = DeterministicProvider()
    return _llm


def get_supervisor() -> Supervisor:
    return Supervisor(llm=get_api_llm())
