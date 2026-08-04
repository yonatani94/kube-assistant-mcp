"""LLM backend abstraction: local Ollama by default, OpenAI-compatible as a fallback.

Design goal: the rest of the app only ever calls `LLMClient.explain(...)` /
`.suggest_fix(...)`. Swapping the backend is a config/env-var change, never
a code change in the callers.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .models import Diagnosis, FixSuggestion, PodIssue

_SYSTEM_PROMPT = """You are an expert Kubernetes SRE assistant embedded in a CLI tool.
You are given a structured description of a failing Pod (status, restart count,
recent log lines, and events). Respond ONLY with a compact JSON object with this shape:

{
  "explanation": "<2-4 sentences, plain language root cause>",
  "fixes": [
    {
      "title": "<short imperative title>",
      "explanation": "<why this fixes it>",
      "commands": ["kubectl ...", "..."],
      "risk": "low|medium|high"
    }
  ]
}

Rules:
- Prefer the smallest safe change (e.g. raising a resource limit) over a rewrite.
- Never suggest deleting a namespace or a PVC.
- If you are not confident, say so plainly in "explanation" and return an empty fixes list.
- Return JSON only, no markdown fences, no prose before or after.
"""


@dataclass
class LLMConfig:
    backend: str = "ollama"  # "ollama" | "openai"
    model: str = "llama3.1"
    base_url: str = "http://localhost:11434"
    api_key: Optional[str] = None
    timeout_seconds: int = 60
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            backend=os.environ.get("KUBE_ASSISTANT_LLM_BACKEND", "ollama"),
            model=os.environ.get("KUBE_ASSISTANT_LLM_MODEL", "llama3.1"),
            base_url=os.environ.get("KUBE_ASSISTANT_LLM_BASE_URL", "http://localhost:11434"),
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("KUBE_ASSISTANT_LLM_API_KEY"),
            timeout_seconds=int(os.environ.get("KUBE_ASSISTANT_LLM_TIMEOUT", "60")),
        )


class LLMBackend(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the raw text completion for a system+user prompt pair."""


class OllamaBackend(LLMBackend):
    def __init__(self, config: LLMConfig):
        self.config = config

    def complete(self, system: str, user: str) -> str:
        import requests  # local import: keep it optional for users who only use OpenAI

        resp = requests.post(
            f"{self.config.base_url}/api/chat",
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": self.config.temperature},
            },
            timeout=self.config.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")


class OpenAIBackend(LLMBackend):
    def __init__(self, config: LLMConfig):
        self.config = config
        if not config.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Export it or configure "
                "KUBE_ASSISTANT_LLM_API_KEY before using the openai backend."
            )

    def complete(self, system: str, user: str) -> str:
        import requests

        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
            },
            timeout=self.config.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _build_backend(config: LLMConfig) -> LLMBackend:
    if config.backend == "ollama":
        return OllamaBackend(config)
    if config.backend == "openai":
        return OpenAIBackend(config)
    raise ValueError(f"Unknown LLM backend: {config.backend!r}")


class LLMClient:
    """High-level entry point used by the CLI and the MCP server."""

    def __init__(self, config: Optional[LLMConfig] = None, backend: Optional[LLMBackend] = None):
        self.config = config or LLMConfig.from_env()
        self.backend = backend or _build_backend(self.config)

    def diagnose(
        self,
        issue: PodIssue,
        log_excerpt: str,
        events: list[str],
        log_findings_summary: str = "",
    ) -> Diagnosis:
        """Ask the LLM to explain the failure and propose fixes; falls back
        to a bare Diagnosis (no LLM text) if the backend call fails, so the
        CLI always has something usable to show.
        """
        user_prompt = self._build_user_prompt(issue, log_excerpt, events, log_findings_summary)
        try:
            raw = self.backend.complete(_SYSTEM_PROMPT, user_prompt)
            parsed = self._parse_response(raw)
        except Exception as exc:  # noqa: BLE001
            return Diagnosis(
                issue=issue,
                suggestions=[],
                llm_explanation=f"(LLM unavailable: {exc})",
                source="rules",
            )

        suggestions = [
            FixSuggestion(
                title=f.get("title", "Suggested fix"),
                explanation=f.get("explanation", ""),
                commands=f.get("commands", []) or [],
                risk=self._risk_from_str(f.get("risk", "low")),
            )
            for f in parsed.get("fixes", [])
        ]
        return Diagnosis(
            issue=issue,
            suggestions=suggestions,
            llm_explanation=parsed.get("explanation"),
            source="llm",
        )

    @staticmethod
    def _risk_from_str(value: str):
        from .models import Severity

        try:
            return Severity(value.lower())
        except ValueError:
            return Severity.LOW

    @staticmethod
    def _build_user_prompt(
        issue: PodIssue, log_excerpt: str, events: list[str], log_findings_summary: str
    ) -> str:
        payload = {
            "pod": f"{issue.pod.namespace}/{issue.pod.name}",
            "container": issue.pod.container,
            "issue_type": issue.issue_type.value,
            "restart_count": issue.restart_count,
            "reason": issue.reason,
            "message": issue.message,
            "last_termination_reason": issue.last_termination_reason,
            "exit_code": issue.exit_code,
            "recent_events": events[-10:],
            "log_pattern_findings": log_findings_summary,
            "log_tail": log_excerpt[-4000:],
        }
        return json.dumps(payload, indent=2)

    @staticmethod
    def _parse_response(raw: str) -> dict:
        text = raw.strip()
        # Be lenient: strip accidental markdown fences some models still emit.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        return json.loads(text)
