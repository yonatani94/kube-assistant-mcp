"""Minimal Jira Cloud client used to open a bug ticket at the end of an
investigation, instead of mutating the cluster directly.

Design goal: the rest of the app only ever calls `JiraClient.create_issue(...)`.
Only Jira Cloud (email + API token) is supported — see JiraConfig.from_env().
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class JiraClientError(RuntimeError):
    """Raised when Jira isn't configured, or the API call fails."""


@dataclass
class JiraConfig:
    base_url: str | None = None
    email: str | None = None
    api_token: str | None = None
    project_key: str | None = None
    issue_type: str = "Bug"
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> JiraConfig:
        return cls(
            base_url=(os.environ.get("JIRA_URL") or "").rstrip("/") or None,
            email=os.environ.get("JIRA_EMAIL"),
            api_token=os.environ.get("JIRA_API_TOKEN"),
            project_key=os.environ.get("JIRA_PROJECT_KEY"),
            issue_type=os.environ.get("JIRA_ISSUE_TYPE", "Bug"),
            timeout_seconds=int(os.environ.get("JIRA_TIMEOUT", "30")),
        )


def _text_to_adf(text: str) -> dict:
    """Converts plain text into minimal Atlassian Document Format, which
    Jira Cloud's REST API v3 requires for the `description` field.
    Blank lines separate paragraphs; single newlines stay in the same
    paragraph (joined with a space).
    """
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": " ".join(p.splitlines())}],
            }
            for p in paragraphs
        ],
    }


class JiraClient:
    """Thin wrapper around Jira Cloud's REST API v3 `/issue` endpoint."""

    def __init__(self, config: JiraConfig | None = None):
        self.config = config or JiraConfig.from_env()

    def _require_config(self, project_key: str | None) -> tuple[str, str, str, str]:
        missing = [
            name
            for name, value in (
                ("JIRA_URL", self.config.base_url),
                ("JIRA_EMAIL", self.config.email),
                ("JIRA_API_TOKEN", self.config.api_token),
                ("JIRA_PROJECT_KEY", project_key or self.config.project_key),
            )
            if not value
        ]
        if missing:
            raise JiraClientError(
                "Jira isn't configured — missing env var(s): " + ", ".join(missing) + ". "
                "See the README's Jira configuration section."
            )
        return self.config.base_url, self.config.email, self.config.api_token, (project_key or self.config.project_key)  # type: ignore[return-value]

    def create_issue(
        self,
        summary: str,
        description: str,
        project_key: str | None = None,
        issue_type: str | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
    ) -> dict:
        """Opens a Jira issue. Returns {"key": ..., "url": ...} on success."""
        import requests

        base_url, email, api_token, resolved_project = self._require_config(project_key)

        fields: dict = {
            "project": {"key": resolved_project},
            "summary": summary,
            "issuetype": {"name": issue_type or self.config.issue_type},
            "description": _text_to_adf(description),
        }
        if labels:
            fields["labels"] = labels
        if priority:
            fields["priority"] = {"name": priority}

        try:
            resp = requests.post(
                f"{base_url}/rest/api/3/issue",
                auth=(email, api_token),
                json={"fields": fields},
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise JiraClientError(f"Could not reach Jira at {base_url}: {exc}") from exc

        if not resp.ok:
            raise JiraClientError(f"Jira rejected the ticket (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        key = data["key"]
        return {"key": key, "url": f"{base_url}/browse/{key}"}
