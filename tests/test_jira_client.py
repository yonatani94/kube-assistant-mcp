import pytest

from kube_assistant_mcp.jira_client import JiraClient, JiraClientError, JiraConfig


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
