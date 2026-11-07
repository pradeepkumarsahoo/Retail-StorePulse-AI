"""Tiny in-process counters/histograms, rendered as Prometheus text.

No external dependency. In M6 the shape stays the same; the exporter swaps for
prometheus_client when a real Prometheus is plumbed in.
"""
from __future__ import annotations

import threading
from collections import defaultdict


_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_hist: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
_help: dict[str, str] = {
    "storepulse_http_requests_total":    "Count of HTTP requests served.",
    "storepulse_http_request_ms":        "HTTP request duration in milliseconds.",
    "storepulse_kpi_requests_total":     "KPI endpoint invocations by metric.",
    "storepulse_agent_runs_total":       "Agent workflow runs by intent and status.",
    "storepulse_tool_calls_total":       "Tool gateway invocations by tool and status.",
    "storepulse_anomalies_detected_total": "Anomalies persisted by severity.",
    "storepulse_recommendations_total":  "Recommendations created by status.",
}


def _key(name: str, labels: dict[str, str] | None):
    items = tuple(sorted((labels or {}).items()))
    return (name, items)


def inc(name: str, value: float = 1.0, **labels: str) -> None:
    with _lock:
        _counters[_key(name, labels)] += value


def observe_ms(name: str, duration_ms: float, **labels: str) -> None:
    with _lock:
        _hist[_key(name, labels)].append(float(duration_ms))


def _labels_str(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


def render_prometheus() -> str:
    """Prometheus text exposition (v0.0.4)."""
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        hist = {k: list(v) for k, v in _hist.items()}
    seen_help: set[str] = set()
    for (name, labels), value in sorted(counters.items()):
        if name not in seen_help:
            if name in _help:
                lines.append(f"# HELP {name} {_help[name]}")
            lines.append(f"# TYPE {name} counter")
            seen_help.add(name)
        lines.append(f"{name}{_labels_str(labels)} {value}")
    for (name, labels), values in sorted(hist.items()):
        if not values:
            continue
        hname = name
        if hname not in seen_help:
            if hname in _help:
                lines.append(f"# HELP {hname} {_help[hname]}")
            lines.append(f"# TYPE {hname} summary")
            seen_help.add(hname)
        count = len(values)
        total = sum(values)
        lines.append(f"{hname}_count{_labels_str(labels)} {count}")
        lines.append(f"{hname}_sum{_labels_str(labels)} {total}")
        srt = sorted(values)
        for q, idx_frac in (("0.5", 0.5), ("0.9", 0.9), ("0.99", 0.99)):
            i = max(0, min(len(srt) - 1, int(idx_frac * (len(srt) - 1))))
            qlabels = labels + (("quantile", q),)
            lines.append(f"{hname}{_labels_str(qlabels)} {srt[i]}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    """Testing helper."""
    with _lock:
        _counters.clear()
        _hist.clear()
