from __future__ import annotations

from packages.ai_orchestration.llm_provider import DeterministicProvider


def test_deterministic_output_is_byte_stable():
    llm = DeterministicProvider()
    evidence = {
        "store_id": "S001",
        "window": {"from_utc": "2026-10-01T00:00:00Z", "to_utc": "2026-10-08T00:00:00Z"},
        "kpis": {
            "net_sales_usd": {"value": 123.45, "unit": "USD"},
            "splh": {"value": None, "unit": "USD per labor hour"},
        },
        "anomalies": [{"kind":"sales","severity":"medium","observed":10,"baseline":5}],
    }
    a = llm.summarize("sys", "user", evidence)
    b = llm.summarize("sys", "user", evidence)
    assert a.text == b.text
    assert a.model_name == "deterministic-template"
    assert "insufficient evidence" in a.text.lower()


def test_empty_evidence_emits_insufficient_message():
    llm = DeterministicProvider()
    r = llm.summarize("sys", "user", evidence={})
    assert "Insufficient evidence" in r.text or "insufficient" in r.text.lower()
