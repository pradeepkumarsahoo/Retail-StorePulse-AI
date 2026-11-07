"""API tests for every KPI endpoint + authorization boundaries (BOLA)."""
from __future__ import annotations


def params(start, end, sid="S001"):
    return {"store_id": sid, "from": start.isoformat(), "to": end.isoformat()}


def test_all_kpi_endpoints(api_client, seed_window):
    start, end = seed_window
    for metric in ["net_sales", "transactions", "atv", "upt", "splh",
                   "labor_cost_pct", "visitors", "conversion_rate"]:
        r = api_client.get(f"/api/v1/kpi/{metric}", params=params(start, end))
        assert r.status_code == 200, (metric, r.text)
        body = r.json()
        assert "lineage" in body and body["lineage"]["synthetic_data"] is True


def test_metric_catalog_lists_all_metrics(api_client, engine):
    # Seed catalog via the loader helper.
    from scripts.load_db import _seed_metric_catalog
    _seed_metric_catalog(engine)
    r = api_client.get("/api/v1/metric-catalog")
    assert r.status_code == 200
    body = r.json()
    names = {m["metric_name"] for m in body}
    assert "sales_per_labor_hour" in names
    assert "atv_usd" in names
    assert "labor_cost_pct" in names


def test_metrics_endpoint_returns_prometheus_text(api_client, seed_window):
    start, end = seed_window
    api_client.get("/api/v1/kpi/splh", params=params(start, end))
    r = api_client.get("/metrics")
    assert r.status_code == 200
    assert "storepulse_http_requests_total" in r.text
    assert "storepulse_kpi_requests_total" in r.text


# --- Authorization boundary tests (OWASP API #1 BOLA / #5 BFLA) -----------

WEST_HEADERS = {"Authorization": "Bearer demo-regional-west"}
S001_HEADERS = {"Authorization": "Bearer demo-store-s001"}
REVIEWER_HEADERS = {"Authorization": "Bearer demo-reviewer"}


def test_west_manager_cannot_see_s002(api_client, seed_window):
    start, end = seed_window
    r = api_client.get("/api/v1/kpi/net_sales",
                       params=params(start, end, sid="S002"),
                       headers=WEST_HEADERS)
    assert r.status_code == 403


def test_west_manager_can_see_s001(api_client, seed_window):
    start, end = seed_window
    r = api_client.get("/api/v1/kpi/net_sales",
                       params=params(start, end, sid="S001"),
                       headers=WEST_HEADERS)
    assert r.status_code == 200


def test_s001_manager_cannot_see_s002(api_client, seed_window):
    start, end = seed_window
    r = api_client.get("/api/v1/kpi/net_sales",
                       params=params(start, end, sid="S002"),
                       headers=S001_HEADERS)
    assert r.status_code == 403


def test_stores_list_is_scoped(api_client):
    r_admin = api_client.get("/api/v1/stores")
    r_west = api_client.get("/api/v1/stores", headers=WEST_HEADERS)
    assert len(r_admin.json()) >= len(r_west.json())
    assert all(s["region"] == "West" for s in r_west.json())


def test_audit_requires_elevated_role(api_client):
    r = api_client.get("/api/v1/audit", headers=S001_HEADERS)
    assert r.status_code == 403
    r = api_client.get("/api/v1/audit", headers=REVIEWER_HEADERS)
    assert r.status_code == 200


def test_invalid_token_is_401(api_client):
    r = api_client.get("/api/v1/stores", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
