"""LLM backend abstraction: local Ollama, or one of several hosted providers.

Design goal: the rest of the app only ever calls `LLMClient.diagnose(...)`.
Swapping the backend is a config/env-var change, never a code change in the
callers, and adding a new provider only means adding one small class here.

Supported backends: ollama (local, default), openai, anthropic, gemini, groq.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

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

# Sensible out-of-the-box models per backend. All overridable with
# KUBE_ASSISTANT_LLM_MODEL — provider model names/aliases change over time,
# so treat these as reasonable starting points, not guarantees.
_DEFAULT_MODELS: dict[str, str] = {
    "ollama": "llama3.1",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
}

# Which env var holds the API key for each hosted backend (ollama needs none).
_API_KEY_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


@dataclass
class LLMConfig:
    backend: str = "ollama"  # "ollama" | "openai" | "anthropic" | "gemini" | "groq"
    model: str = "llama3.1"
    base_url: str = "http://localhost:11434"
    api_key: str | None = None
    timeout_seconds: int = 60
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> LLMConfig:
        backend = os.environ.get("KUBE_ASSISTANT_LLM_BACKEND", "ollama")
        model = os.environ.get("KUBE_ASSISTANT_LLM_MODEL") or _DEFAULT_MODELS.get(backend, "llama3.1")

        api_key_env = _API_KEY_ENV_VARS.get(backend)
        api_key = os.environ.get("KUBE_ASSISTANT_LLM_API_KEY") or (
            os.environ.get(api_key_env) if api_key_env else None
        )

        return cls(
            backend=backend,
            model=model,
            base_url=os.environ.get("KUBE_ASSISTANT_LLM_BASE_URL", "http://localhost:11434"),
            api_key=api_key,
            timeout_seconds=int(os.environ.get("KUBE_ASSISTANT_LLM_TIMEOUT", "60")),
        )

    def required_api_key_env(self) -> str | None:
        """Name of the env var this backend needs, or None for ollama."""
        return _API_KEY_ENV_VARS.get(self.backend)


class LLMBackend(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the raw text completion for a system+user prompt pair."""


def _require_api_key(config: LLMConfig) -> str:
    env_var = _API_KEY_ENV_VARS.get(config.backend, "KUBE_ASSISTANT_LLM_API_KEY")
    if not config.api_key:
        raise ValueError(
            f"{env_var} is not set. Export it, or set KUBE_ASSISTANT_LLM_API_KEY, "
            f"before using the '{config.backend}' backend."
        )
    return config.api_key


class OllamaBackend(LLMBackend):
    """Local inference via the Ollama daemon — no API key needed."""

    def __init__(self, config: LLMConfig):
        self.config = config

    def complete(self, system: str, user: str) -> str:
        import requests  # local import: keep it optional for users who only use a hosted backend

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


class OpenAICompatibleBackend(LLMBackend):
    """Base for any provider that speaks the OpenAI chat-completions shape
    (OpenAI itself, Groq, and most self-hosted/local-server gateways). New
    OpenAI-compatible providers only need a one-line subclass.
    """

    api_base_url: str = "https://api.openai.com/v1"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = _require_api_key(config)

    def complete(self, system: str, user: str) -> str:
        import requests

        resp = requests.post(
            f"{self.api_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
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


class OpenAIBackend(OpenAICompatibleBackend):
    api_base_url = "https://api.openai.com/v1"


class GroqBackend(OpenAICompatibleBackend):
    """Groq hosts open models (Llama, Mixtral, ...) behind an OpenAI-compatible
    API, and is typically much faster/cheaper than the big hosted providers.
    """

    api_base_url = "https://api.groq.com/openai/v1"


class AnthropicBackend(LLMBackend):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = _require_api_key(config)

    def complete(self, system: str, user: str) -> str:
        import requests

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.config.model,
                "max_tokens": 1024,
                "temperature": self.config.temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.config.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return "".join(parts)


class GeminiBackend(LLMBackend):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = _require_api_key(config)

    def complete(self, system: str, user: str) -> str:
        import requests

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.config.model}:generateContent?key={self.api_key}"
        )
        resp = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"temperature": self.config.temperature},
            },
            timeout=self.config.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)


_BACKEND_CLASSES: dict[str, type[LLMBackend]] = {
    "ollama": OllamaBackend,
    "openai": OpenAIBackend,
    "anthropic": AnthropicBackend,
    "gemini": GeminiBackend,
    "groq": GroqBackend,
}


def _build_backend(config: LLMConfig) -> LLMBackend:
    backend_cls = _BACKEND_CLASSES.get(config.backend)
    if backend_cls is None:
        supported = ", ".join(sorted(_BACKEND_CLASSES))
        raise ValueError(f"Unknown LLM backend: {config.backend!r}. Supported: {supported}")
    return backend_cls(config)


class LLMClient:
    """High-level entry point used by the CLI and the MCP server."""

    def __init__(self, config: LLMConfig | None = None, backend: LLMBackend | None = None):
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
