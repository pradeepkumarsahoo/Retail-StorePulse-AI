"""Unit tests for the append-only audit trail."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from packages.governance.audit import list_audit_events, record_audit
from packages.observability.logging import set_correlation_id


def test_record_audit_persists_full_row(engine):
    set_correlation_id("cid-test")
    rid = record_audit(
        engine,
        actor="alex.admin",
        action="recommendation.created",
        target_type="recommendation",
        target_id="rec-1",
        payload={"foo": "bar"},
    )
    assert rid > 0
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT actor, action, target_type, target_id, correlation_id, payload_json "
                 "FROM audit_event WHERE id=:i"),
            {"i": rid},
        ).mappings().one()
    assert row["actor"] == "alex.admin"
    assert row["action"] == "recommendation.created"
    assert row["target_id"] == "rec-1"
    assert row["correlation_id"] == "cid-test"
    assert json.loads(row["payload_json"]) == {"foo": "bar"}
    set_correlation_id(None)


def test_record_audit_without_correlation_id_stores_null(engine):
    set_correlation_id(None)
    rid = record_audit(engine, actor="a", action="x", target_type="t", target_id=None)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT correlation_id, payload_json FROM audit_event WHERE id=:i"),
            {"i": rid},
        ).mappings().one()
    assert row["correlation_id"] is None
    assert row["payload_json"] is None


def test_record_audit_explicit_correlation_id_wins_over_thread_local(engine):
    set_correlation_id("thread-local")
    record_audit(engine, actor="a", action="x", target_type="t", target_id="z",
                 correlation_id="explicit")
    events = list_audit_events(engine, target_type="t")
    assert any(e["correlation_id"] == "explicit" for e in events)
    set_correlation_id(None)


def test_list_audit_events_filters_by_actor(engine):
    record_audit(engine, actor="actor-a", action="x", target_type="t", target_id="1")
    record_audit(engine, actor="actor-b", action="x", target_type="t", target_id="2")
    out = list_audit_events(engine, actor="actor-a")
    actors = {e["actor"] for e in out}
    assert actors == {"actor-a"}


def test_list_audit_events_filters_by_target_type(engine):
    record_audit(engine, actor="a", action="x", target_type="widget", target_id="1")
    record_audit(engine, actor="a", action="x", target_type="gadget", target_id="2")
    out = list_audit_events(engine, target_type="widget")
    targets = {e["target_type"] for e in out}
    assert targets == {"widget"}


def test_list_audit_events_ordered_newest_first(engine):
    record_audit(engine, actor="a", action="first", target_type="t", target_id="1")
    record_audit(engine, actor="a", action="second", target_type="t", target_id="2")
    record_audit(engine, actor="a", action="third", target_type="t", target_id="3")
    out = list_audit_events(engine, limit=10)
    # Latest id first
    assert [e["action"] for e in out[:3]] == ["third", "second", "first"]


def test_list_audit_events_decodes_payload(engine):
    record_audit(engine, actor="a", action="y", target_type="t", target_id="p",
                 payload={"n": 42, "s": "ok"})
    out = list_audit_events(engine, target_type="t")
    row = next(e for e in out if e["target_id"] == "p")
    assert row["payload"] == {"n": 42, "s": "ok"}
    assert "payload_json" not in row
