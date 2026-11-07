"""LLM provider abstraction + a deterministic fake for offline tests and demos.

The fake does not hallucinate: it summarises the structured evidence the agent
already assembled. This is the correct behaviour for this product — LLMs
communicate verified evidence; they never invent calculations (ADR-0007).

Swap in a real provider by implementing `LlmProvider` for your SDK and
registering it through dependency injection in `apps/api/main.py`.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model_name: str
    model_version: str
    tokens_used: int


class LlmProvider(ABC):
    """Abstract provider — one method, text-in / text-out, no streaming for v1."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def summarize(self, system_prompt: str, user_prompt: str,
                  evidence: dict, max_tokens: int = 400) -> LlmResponse: ...


class DeterministicProvider(LlmProvider):
    """Templated summariser. Byte-stable given byte-stable evidence input.

    The output is not a free-text invention; it reads back the structured
    evidence in a human-readable form so the summary is always traceable to
    the exact numbers in the lineage block. This is the on-by-default provider
    for tests and for environments without an LLM credential.
    """

    @property
    def name(self) -> str:
        return "deterministic-template"

    @property
    def version(self) -> str:
        return "1.0.0"

    def summarize(self, system_prompt: str, user_prompt: str,
                  evidence: dict, max_tokens: int = 400) -> LlmResponse:
        # Narrative is strictly derived from evidence keys.
        lines: list[str] = []
        store_id = evidence.get("store_id")
        window = evidence.get("window", {})
        if store_id and window:
            lines.append(
                f"Store {store_id} · {window.get('from_utc')} → {window.get('to_utc')}."
            )
        kpis = evidence.get("kpis", {})
        for name, kpi in kpis.items():
            val = kpi.get("value")
            unit = kpi.get("unit", "")
            if val is None:
                lines.append(f"- {name}: insufficient evidence ({unit}).")
            else:
                lines.append(f"- {name}: {val:.4g} {unit}")
        for finding in evidence.get("anomalies", []):
            lines.append(
                f"- anomaly {finding['kind']} {finding.get('severity')} "
                f"(obs={finding.get('observed')}, baseline={finding.get('baseline')})."
            )
        hypotheses = evidence.get("hypotheses", [])
        if hypotheses:
            lines.append("Potential contributing factors (ranked):")
            for h in hypotheses:
                lines.append(f"  * {h['factor']} — {h['rationale']}")
        if evidence.get("recommendation"):
            r = evidence["recommendation"]
            lines.append(
                f"Suggested next action: {r['title']} (confidence={r['confidence']})."
            )
        text = "\n".join(lines) if lines else "Insufficient evidence to report."
        tokens = min(max_tokens, max(32, len(text) // 3))
        return LlmResponse(
            text=text,
            model_name=self.name,
            model_version=self.version,
            tokens_used=tokens,
        )
