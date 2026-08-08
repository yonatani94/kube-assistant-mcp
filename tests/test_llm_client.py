import json
from unittest.mock import MagicMock, patch

import pytest

from kube_assistant_mcp.llm_client import (
    AnthropicBackend,
    GeminiBackend,
    GroqBackend,
    LLMClient,
    LLMConfig,
    OllamaBackend,
    OpenAIBackend,
    _build_backend,
)
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


# --------------------------------------------------------------------- #
# Multi-provider config resolution (LLMConfig.from_env / _build_backend)
# --------------------------------------------------------------------- #

_PROVIDER_CASES = [
    ("openai", "OPENAI_API_KEY", OpenAIBackend, "gpt-4o-mini"),
    ("anthropic", "ANTHROPIC_API_KEY", AnthropicBackend, "claude-sonnet-4-5"),
    ("gemini", "GEMINI_API_KEY", GeminiBackend, "gemini-2.0-flash"),
    ("groq", "GROQ_API_KEY", GroqBackend, "llama-3.3-70b-versatile"),
]


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """Every test in this module starts from a clean slate of LLM-related
    env vars, so provider selection tests don't leak into each other.
    """
    for key in [
        "KUBE_ASSISTANT_LLM_BACKEND",
        "KUBE_ASSISTANT_LLM_MODEL",
        "KUBE_ASSISTANT_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    yield


def test_default_backend_is_ollama_no_key_required():
    config = LLMConfig.from_env()
    assert config.backend == "ollama"
    assert config.model == "llama3.1"
    backend = _build_backend(config)
    assert isinstance(backend, OllamaBackend)


@pytest.mark.parametrize("backend_name,key_env,expected_cls,expected_model", _PROVIDER_CASES)
def test_each_hosted_provider_resolves_correct_model_and_class(
    monkeypatch, backend_name, key_env, expected_cls, expected_model
):
    monkeypatch.setenv("KUBE_ASSISTANT_LLM_BACKEND", backend_name)
    monkeypatch.setenv(key_env, "fake-key")

    config = LLMConfig.from_env()

    assert config.backend == backend_name
    assert config.model == expected_model
    assert config.api_key == "fake-key"
    assert isinstance(_build_backend(config), expected_cls)


@pytest.mark.parametrize("backend_name,key_env,_cls,_model", _PROVIDER_CASES)
def test_missing_api_key_raises_clear_error(monkeypatch, backend_name, key_env, _cls, _model):
    monkeypatch.setenv("KUBE_ASSISTANT_LLM_BACKEND", backend_name)
    config = LLMConfig.from_env()

    with pytest.raises(ValueError, match=key_env):
        _build_backend(config)


def test_generic_override_key_works_for_any_backend(monkeypatch):
    monkeypatch.setenv("KUBE_ASSISTANT_LLM_BACKEND", "anthropic")
    monkeypatch.setenv("KUBE_ASSISTANT_LLM_API_KEY", "generic-override-key")

    config = LLMConfig.from_env()

    assert config.api_key == "generic-override-key"
    assert isinstance(_build_backend(config), AnthropicBackend)


def test_unknown_backend_raises_with_supported_list():
    config = LLMConfig(backend="made-up-provider")
    with pytest.raises(ValueError, match="made-up-provider"):
        _build_backend(config)


# --------------------------------------------------------------------- #
# Wire-format checks for each provider's complete() implementation
# --------------------------------------------------------------------- #


def test_anthropic_backend_sends_correct_request_and_parses_response():
    config = LLMConfig(backend="anthropic", model="claude-sonnet-4-5", api_key="fake")
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"content": [{"type": "text", "text": "root cause explanation"}]}

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = AnthropicBackend(config).complete("system prompt", "user prompt")

    assert result == "root cause explanation"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.anthropic.com/v1/messages"
    assert kwargs["headers"]["x-api-key"] == "fake"
    assert kwargs["json"]["system"] == "system prompt"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "user prompt"}]


def test_gemini_backend_sends_correct_request_and_parses_response():
    config = LLMConfig(backend="gemini", model="gemini-2.0-flash", api_key="fake")
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "root cause explanation"}]}}]
    }

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = GeminiBackend(config).complete("system prompt", "user prompt")

    assert result == "root cause explanation"
    url = mock_post.call_args[0][0]
    assert "generativelanguage.googleapis.com" in url
    assert "gemini-2.0-flash" in url
    assert "key=fake" in url


def test_gemini_backend_handles_empty_candidates_without_crashing():
    config = LLMConfig(backend="gemini", model="gemini-2.0-flash", api_key="fake")
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"candidates": []}

    with patch("requests.post", return_value=fake_response):
        result = GeminiBackend(config).complete("system prompt", "user prompt")

    assert result == ""


def test_openai_and_groq_share_backend_but_hit_different_urls():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"choices": [{"message": {"content": "hi"}}]}

    openai_config = LLMConfig(backend="openai", model="gpt-4o-mini", api_key="fake")
    with patch("requests.post", return_value=fake_response) as mock_post:
        OpenAIBackend(openai_config).complete("s", "u")
    assert mock_post.call_args[0][0] == "https://api.openai.com/v1/chat/completions"

    groq_config = LLMConfig(backend="groq", model="llama-3.3-70b-versatile", api_key="fake")
    with patch("requests.post", return_value=fake_response) as mock_post:
        GroqBackend(groq_config).complete("s", "u")
    assert mock_post.call_args[0][0] == "https://api.groq.com/openai/v1/chat/completions"
