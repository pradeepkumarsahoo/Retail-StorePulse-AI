"""Unit tests for the tiny in-process counters / histograms module."""
from __future__ import annotations

from packages.observability import metrics


def setup_function(_) -> None:
    metrics.reset()


def test_counter_increments_with_labels():
    metrics.inc("storepulse_http_requests_total", method="GET", path="/x", status="200")
    metrics.inc("storepulse_http_requests_total", method="GET", path="/x", status="200")
    out = metrics.render_prometheus()
    assert 'storepulse_http_requests_total{method="GET",path="/x",status="200"} 2' in out


def test_counter_distinct_label_sets_are_independent():
    metrics.inc("storepulse_agent_runs_total", intent="daily_briefing", status="ok")
    metrics.inc("storepulse_agent_runs_total", intent="daily_briefing", status="error")
    out = metrics.render_prometheus()
    assert 'storepulse_agent_runs_total{intent="daily_briefing",status="ok"} 1' in out
    assert 'storepulse_agent_runs_total{intent="daily_briefing",status="error"} 1' in out


def test_observe_ms_renders_count_sum_and_quantiles():
    for v in (10.0, 20.0, 30.0, 40.0, 50.0):
        metrics.observe_ms("storepulse_http_request_ms", v, method="GET", path="/")
    out = metrics.render_prometheus()
    assert 'storepulse_http_request_ms_count{method="GET",path="/"} 5' in out
    assert 'storepulse_http_request_ms_sum{method="GET",path="/"} 150.0' in out
    # p50 of [10,20,30,40,50] is 30 by the index formula in render_prometheus.
    assert 'storepulse_http_request_ms{method="GET",path="/",quantile="0.5"} 30.0' in out


def test_render_emits_help_and_type_once_per_metric():
    metrics.inc("storepulse_tool_calls_total", tool="kpi.splh", status="ok")
    metrics.inc("storepulse_tool_calls_total", tool="kpi.splh", status="denied")
    out = metrics.render_prometheus()
    # Only one HELP + one TYPE per metric name regardless of label cardinality.
    assert out.count("# HELP storepulse_tool_calls_total") == 1
    assert out.count("# TYPE storepulse_tool_calls_total counter") == 1


def test_reset_clears_everything():
    metrics.inc("storepulse_http_requests_total", method="GET", path="/", status="200")
    metrics.observe_ms("storepulse_http_request_ms", 1.0, method="GET", path="/")
    metrics.reset()
    out = metrics.render_prometheus()
    # After reset only a trailing newline remains.
    assert out.strip() == ""


def test_counter_without_labels_renders_bare_name():
    metrics.inc("storepulse_recommendations_total")
    out = metrics.render_prometheus()
    assert "storepulse_recommendations_total 1" in out
