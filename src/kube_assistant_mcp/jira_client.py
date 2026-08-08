"""Minimal Jira Cloud client used to open a bug ticket at the end of an
investigation, instead of mutating the cluster directly.

Design goal: the rest of the app only ever calls `JiraClient.create_issue(...)`.
Only Jira Cloud (email + API token) is supported — see JiraConfig.from_env().
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

_SEVERITY_TO_PRIORITY = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "critical": "Highest",
}


def severity_to_priority(severity: str) -> str | None:
    """Maps this project's severity scale to Jira's default priority names.
    Returns None for anything unrecognized, so callers can just skip setting
    a priority rather than guessing.
    """
    return _SEVERITY_TO_PRIORITY.get(severity.lower())


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


def _markdown_to_adf_content(text: str) -> list[dict]:
    """Converts a small, deliberate subset of Markdown into Atlassian Document
    Format nodes: `## `/`### ` headings, fenced ```code``` blocks (with an
    optional language tag), `- ` bullet lists, `> ` info panels, and plain
    paragraphs. This is what lets a ticket render as a properly structured
    Jira issue (headings, a monospaced fix snippet, a callout footer) instead
    of one flat wall of text.
    """
    lines = text.strip("\n").split("\n")
    content: list[dict] = []
    paragraph_buf: list[str] = []
    bullet_buf: list[str] = []
    quote_buf: list[str] = []

    def flush_paragraph() -> None:
        joined = " ".join(paragraph_buf).strip()
        if joined:
            content.append({"type": "paragraph", "content": [{"type": "text", "text": joined}]})
        paragraph_buf.clear()

    def flush_bullets() -> None:
        if bullet_buf:
            content.append(
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": item}]}],
                        }
                        for item in bullet_buf
                    ],
                }
            )
            bullet_buf.clear()

    def flush_quote() -> None:
        joined = " ".join(quote_buf).strip()
        if joined:
            content.append(
                {
                    "type": "panel",
                    "attrs": {"panelType": "info"},
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": joined}]}],
                }
            )
        quote_buf.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_bullets()
            flush_quote()
            language = stripped[3:].strip() or None
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            node: dict = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code_lines)}]}
            if language:
                node["attrs"] = {"language": language}
            content.append(node)
            i += 1
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            flush_bullets()
            flush_quote()
            content.append(
                {"type": "heading", "attrs": {"level": 4}, "content": [{"type": "text", "text": stripped[4:].strip()}]}
            )
            i += 1
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_bullets()
            flush_quote()
            content.append(
                {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": stripped[3:].strip()}]}
            )
            i += 1
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            flush_quote()
            bullet_buf.append(stripped[2:].strip())
            i += 1
            continue

        if stripped.startswith("> "):
            flush_paragraph()
            flush_bullets()
            quote_buf.append(stripped[2:].strip())
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            flush_bullets()
            flush_quote()
            i += 1
            continue

        flush_bullets()
        flush_quote()
        paragraph_buf.append(stripped)
        i += 1

    flush_paragraph()
    flush_bullets()
    flush_quote()
    return content or [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]


def _text_to_adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": _markdown_to_adf_content(text)}


def build_ticket_markdown(pod_name: str, namespace: str, severity: str, root_cause: str, recommended_fix: str) -> str:
    """Composes the standard kube-assistant ticket body as Markdown — shared
    by the CLI and the MCP tool so every ticket has the same structure:
    a Summary, the root cause, and the recommended fix (which callers should
    pass with fenced ```yaml/```bash blocks for exact commands or manifest
    lines, so they render as real code blocks instead of a wall of prose).
    """
    return (
        "## Summary\n"
        f"- Pod: `{namespace}/{pod_name}`\n"
        f"- Severity: {severity}\n\n"
        "## Root cause\n"
        f"{root_cause}\n\n"
        "## Recommended fix\n"
        f"{recommended_fix}\n\n"
        "> Opened automatically by kube-assistant-mcp. This environment grants "
        "read-only cluster access — an engineer with write access needs to "
        "apply the fix above."
    )


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

    def _post_issue(self, base_url: str, email: str, api_token: str, fields: dict) -> requests.Response:
        try:
            return requests.post(
                f"{base_url}/rest/api/3/issue",
                auth=(email, api_token),
                json={"fields": fields},
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise JiraClientError(f"Could not reach Jira at {base_url}: {exc}") from exc

    @staticmethod
    def _only_priority_rejected(resp: requests.Response) -> bool:
        """True if Jira's 400 response blames the `priority` field and
        nothing else — many team-managed projects don't have Priority on
        their create screen, and that alone shouldn't sink the whole ticket.
        """
        if resp.status_code != 400:
            return False
        try:
            errors = resp.json().get("errors", {})
        except ValueError:
            return False
        return set(errors) == {"priority"}

    def create_issue(
        self,
        summary: str,
        description: str | dict,
        project_key: str | None = None,
        issue_type: str | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
    ) -> dict:
        """Opens a Jira issue. `description` is either plain text/Markdown
        (converted to ADF) or an already-built ADF `dict`. Returns
        {"key": ..., "url": ...} on success, plus a "warning" key if the
        priority had to be dropped because the project doesn't support it.
        """
        base_url, email, api_token, resolved_project = self._require_config(project_key)

        adf_description = description if isinstance(description, dict) else _text_to_adf(description)

        fields: dict = {
            "project": {"key": resolved_project},
            "summary": summary,
            "issuetype": {"name": issue_type or self.config.issue_type},
            "description": adf_description,
        }
        if labels:
            fields["labels"] = labels
        if priority:
            fields["priority"] = {"name": priority}

        resp = self._post_issue(base_url, email, api_token, fields)

        if not resp.ok and priority and self._only_priority_rejected(resp):
            fields.pop("priority")
            resp = self._post_issue(base_url, email, api_token, fields)
            if resp.ok:
                key = resp.json()["key"]
                return {
                    "key": key,
                    "url": f"{base_url}/browse/{key}",
                    "warning": "This project doesn't expose a Priority field; ticket created without one.",
                }

        if not resp.ok:
            raise JiraClientError(f"Jira rejected the ticket (HTTP {resp.status_code}): {resp.text}")

        key = resp.json()["key"]
        return {"key": key, "url": f"{base_url}/browse/{key}"}
