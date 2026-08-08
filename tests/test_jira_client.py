import pytest

from kube_assistant_mcp.jira_client import (
    JiraClient,
    JiraClientError,
    JiraConfig,
    _markdown_to_adf_content,
    build_ticket_markdown,
    severity_to_priority,
)


def _config(**overrides) -> JiraConfig:
    defaults = {
        "base_url": "https://example.atlassian.net",
        "email": "bot@example.com",
        "api_token": "token-123",
        "project_key": "OPS",
        "issue_type": "Bug",
    }
    defaults.update(overrides)
    return JiraConfig(**defaults)


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data or {}
        self.text = text or str(json_data)

    def json(self):
        return self._json_data


def test_create_issue_returns_key_and_url(mocker):
    fake_post = mocker.patch(
        "requests.post", return_value=_FakeResponse(201, {"key": "OPS-42"})
    )

    client = JiraClient(_config())
    result = client.create_issue(summary="Pod OOMKilled", description="Root cause + fix")

    assert result == {"key": "OPS-42", "url": "https://example.atlassian.net/browse/OPS-42"}
    called_kwargs = fake_post.call_args.kwargs
    assert called_kwargs["auth"] == ("bot@example.com", "token-123")
    assert called_kwargs["json"]["fields"]["project"] == {"key": "OPS"}
    assert called_kwargs["json"]["fields"]["issuetype"] == {"name": "Bug"}
    assert called_kwargs["json"]["fields"]["description"]["type"] == "doc"


def test_create_issue_missing_config_raises_without_network_call(mocker):
    fake_post = mocker.patch("requests.post")
    client = JiraClient(JiraConfig())

    with pytest.raises(JiraClientError, match="JIRA_URL"):
        client.create_issue(summary="x", description="y")

    fake_post.assert_not_called()


def test_create_issue_raises_on_non_2xx_with_jira_error_detail(mocker):
    mocker.patch(
        "requests.post",
        return_value=_FakeResponse(400, text='{"errorMessages": ["issuetype is required"]}'),
    )
    client = JiraClient(_config())

    with pytest.raises(JiraClientError, match="issuetype is required"):
        client.create_issue(summary="x", description="y")


def test_create_issue_uses_project_key_override(mocker):
    fake_post = mocker.patch("requests.post", return_value=_FakeResponse(201, {"key": "SEC-1"}))
    client = JiraClient(_config())

    client.create_issue(summary="x", description="y", project_key="SEC")

    assert fake_post.call_args.kwargs["json"]["fields"]["project"] == {"key": "SEC"}


def test_create_issue_sets_priority_field(mocker):
    fake_post = mocker.patch("requests.post", return_value=_FakeResponse(201, {"key": "OPS-1"}))
    client = JiraClient(_config())

    client.create_issue(summary="x", description="y", priority="High")

    assert fake_post.call_args.kwargs["json"]["fields"]["priority"] == {"name": "High"}


def test_create_issue_retries_without_priority_when_only_priority_rejected(mocker):
    rejected = _FakeResponse(400, {"errors": {"priority": "not on screen"}, "errorMessages": []})
    accepted = _FakeResponse(201, {"key": "OPS-2"})
    fake_post = mocker.patch("requests.post", side_effect=[rejected, accepted])
    client = JiraClient(_config())

    result = client.create_issue(summary="x", description="y", priority="High")

    assert result["key"] == "OPS-2"
    assert "warning" in result
    assert fake_post.call_count == 2
    assert "priority" not in fake_post.call_args.kwargs["json"]["fields"]


def test_create_issue_does_not_retry_when_other_fields_also_rejected(mocker):
    rejected = _FakeResponse(400, {"errors": {"priority": "x", "issuetype": "y"}, "errorMessages": []})
    fake_post = mocker.patch("requests.post", return_value=rejected)
    client = JiraClient(_config())

    with pytest.raises(JiraClientError):
        client.create_issue(summary="x", description="y", priority="High")

    assert fake_post.call_count == 1


def test_severity_to_priority_maps_known_values():
    assert severity_to_priority("critical") == "Highest"
    assert severity_to_priority("HIGH") == "High"
    assert severity_to_priority("unknown-severity") is None


def test_build_ticket_markdown_includes_expected_sections():
    body = build_ticket_markdown(
        pod_name="api-1",
        namespace="prod",
        severity="high",
        root_cause="OOMKilled repeatedly.",
        recommended_fix="Raise the memory limit.",
    )
    assert "## Summary" in body
    assert "prod/api-1" in body
    assert "## Root cause" in body
    assert "## Recommended fix" in body
    assert "OOMKilled repeatedly." in body


def test_markdown_to_adf_renders_heading_bullets_and_code_block():
    content = _markdown_to_adf_content(
        "## Root cause\nSomething broke.\n\n- first point\n- second point\n\n```yaml\nfoo: bar\n```"
    )
    types = [node["type"] for node in content]
    assert types == ["heading", "paragraph", "bulletList", "codeBlock"]
    assert content[0]["attrs"]["level"] == 3
    assert content[2]["content"][0]["content"][0]["content"][0]["text"] == "first point"
    assert content[3]["content"][0]["text"] == "foo: bar"


def test_markdown_to_adf_renders_blockquote_as_info_panel():
    content = _markdown_to_adf_content("> heads up, this is automated")
    assert content[0]["type"] == "panel"
    assert content[0]["attrs"]["panelType"] == "info"
