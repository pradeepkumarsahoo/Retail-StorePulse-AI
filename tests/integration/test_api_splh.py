"""API-level integration tests for the SPLH endpoint."""
from __future__ import annotations


def test_health(api_client):
    r = api_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stores_returns_seeded_store(api_client):
    r = api_client.get("/api/v1/stores")
    assert r.status_code == 200
    body = r.json()
    assert any(s["store_id"] == "S001" for s in body)


def test_splh_happy_path(api_client, seed_window):
    start, end = seed_window
    r = api_client.get(
        "/api/v1/kpi/splh",
        params={"store_id": "S001", "from": start.isoformat(), "to": end.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["unit"] == "USD per labor hour"
    assert body["value"] is not None
    assert abs(body["value"] - 155.5556) < 1e-3
    # Lineage is required
    lin = body["lineage"]
    assert lin["metric_name"] == "sales_per_labor_hour"
    assert lin["metric_version"] == "1.0.0"
    assert lin["synthetic_data"] is True
    assert "sales_txn" in lin["source_tables"] and "labor_hours" in lin["source_tables"]
    # Correlation id echoed
    assert "x-correlation-id" in r.headers


def test_splh_unknown_store_is_404(api_client, seed_window):
    start, end = seed_window
    r = api_client.get(
        "/api/v1/kpi/splh",
        params={"store_id": "S999", "from": start.isoformat(), "to": end.isoformat()},
    )
    assert r.status_code == 404


def test_splh_bad_store_id_format_is_422(api_client, seed_window):
    start, end = seed_window
    r = api_client.get(
        "/api/v1/kpi/splh",
        params={"store_id": "bad", "from": start.isoformat(), "to": end.isoformat()},
    )
    assert r.status_code == 422


def test_splh_reversed_window_is_400(api_client, seed_window):
    start, end = seed_window
    r = api_client.get(
        "/api/v1/kpi/splh",
        params={"store_id": "S001", "from": end.isoformat(), "to": start.isoformat()},
    )
    assert r.status_code == 400
    assert "to_utc" in r.json()["message"]


def test_splh_empty_window_insufficient_evidence(api_client):
    r = api_client.get(
        "/api/v1/kpi/splh",
        params={"store_id": "S001", "from": "2020-01-01T00:00:00Z", "to": "2020-01-02T00:00:00Z"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["value"] is None
    assert body["status"] == "insufficient_evidence"
