import json
from unittest.mock import MagicMock

from kube_assistant_mcp.llm_client import LLMClient, LLMConfig
from kube_assistant_mcp.models import IssueType, PodIssue, PodRef, Severity


def _issue() -> PodIssue:
    return PodIssue(
        pod=PodRef(name="app-1", namespace="default", container="app"),
        issue_type=IssueType.CRASH_LOOP_BACKOFF,
        severity=Severity.HIGH,
        restart_count=7,
    )


def test_diagnose_parses_well_formed_json_response():
    fake_backend = MagicMock()
    fake_backend.complete.return_value = json.dumps(
        {
            "explanation": "The app panics on startup due to a missing config key.",
            "fixes": [
                {
                    "title": "Add missing config key",
                    "explanation": "Set FEATURE_FLAG in the ConfigMap.",
                    "commands": ["kubectl -n default edit configmap app-config"],
                    "risk": "low",
                }
            ],
        }
    )
    client = LLMClient(config=LLMConfig(backend="ollama"), backend=fake_backend)

    diagnosis = client.diagnose(_issue(), log_excerpt="panic: missing key", events=[])

    assert diagnosis.source == "llm"
    assert "missing config key" in diagnosis.llm_explanation
    assert len(diagnosis.suggestions) == 1
    assert diagnosis.suggestions[0].risk == Severity.LOW


def test_diagnose_strips_markdown_fences():
    fake_backend = MagicMock()
    fake_backend.complete.return_value = '```json\n{"explanation": "ok", "fixes": []}\n```'
    client = LLMClient(config=LLMConfig(backend="ollama"), backend=fake_backend)

    diagnosis = client.diagnose(_issue(), log_excerpt="", events=[])

    assert diagnosis.llm_explanation == "ok"
    assert diagnosis.suggestions == []


def test_diagnose_falls_back_gracefully_when_backend_raises():
    fake_backend = MagicMock()
    fake_backend.complete.side_effect = ConnectionError("Ollama not running")
    client = LLMClient(config=LLMConfig(backend="ollama"), backend=fake_backend)

    diagnosis = client.diagnose(_issue(), log_excerpt="", events=[])

    assert diagnosis.source == "rules"
    assert "LLM unavailable" in diagnosis.llm_explanation
    assert diagnosis.suggestions == []


def test_diagnose_falls_back_gracefully_on_invalid_json():
    fake_backend = MagicMock()
    fake_backend.complete.return_value = "not json at all"
    client = LLMClient(config=LLMConfig(backend="ollama"), backend=fake_backend)

    diagnosis = client.diagnose(_issue(), log_excerpt="", events=[])

    assert diagnosis.source == "rules"
    assert diagnosis.suggestions == []
