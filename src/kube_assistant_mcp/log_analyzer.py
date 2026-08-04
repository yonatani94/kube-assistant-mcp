"""Pattern-based log analysis.

This module is intentionally free of any Kubernetes/network dependency so
it can be unit tested with plain strings. It complements the container-status
based detection in k8s_client.py by looking *inside* the log text for the
actual root cause (stack traces, missing env vars, connection refusals...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class LogFinding:
    pattern_name: str
    description: str
    matched_line: str
    suggestion_hint: str


# Each entry: (name, compiled regex, human description, short fix hint)
_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    (
        "oom_killed",
        re.compile(r"\bOOMKilled\b|Out of memory|memory cgroup out of memory", re.IGNORECASE),
        "Container was killed for exceeding its memory limit.",
        "Increase the memory limit/request or fix a memory leak in the app.",
    ),
    (
        "connection_refused",
        re.compile(r"connection refused|ECONNREFUSED|dial tcp.*refused", re.IGNORECASE),
        "The application couldn't reach a dependency (DB, cache, another service).",
        "Check the target Service/Endpoint name, port, and NetworkPolicy rules.",
    ),
    (
        "dns_resolution",
        re.compile(r"no such host|nodename nor servname|lookup .* no such host", re.IGNORECASE),
        "DNS resolution failed for a hostname referenced by the app.",
        "Verify the Service name/namespace and CoreDNS health.",
    ),
    (
        "missing_env_var",
        re.compile(r"(env(ironment)? variable .* (not set|missing|required))", re.IGNORECASE),
        "A required environment variable is missing.",
        "Add the missing key to the ConfigMap/Secret referenced by the Deployment.",
    ),
    (
        "permission_denied",
        re.compile(r"permission denied|EACCES|forbidden", re.IGNORECASE),
        "A filesystem or RBAC permission check failed.",
        "Check the container's runAsUser/fsGroup or the ServiceAccount's RBAC Role.",
    ),
    (
        "image_pull",
        re.compile(r"ErrImagePull|ImagePullBackOff|manifest unknown|pull access denied", re.IGNORECASE),
        "The container image could not be pulled.",
        "Verify the image tag exists and imagePullSecrets are configured correctly.",
    ),
    (
        "config_parse_error",
        re.compile(r"(yaml|json).*(parse|unmarshal|invalid).*error", re.IGNORECASE),
        "The application failed to parse its configuration file.",
        "Validate the mounted ConfigMap/Secret content against the app's expected schema.",
    ),
    (
        "port_in_use",
        re.compile(r"address already in use|EADDRINUSE", re.IGNORECASE),
        "The application's port is already bound, likely by a duplicate process.",
        "Check for a stuck previous process or a container restart race condition.",
    ),
    (
        "liveness_probe",
        re.compile(r"liveness probe (failed|error)", re.IGNORECASE),
        "The liveness probe is failing, causing Kubernetes to restart the container.",
        "Increase initialDelaySeconds/timeoutSeconds or check the health endpoint logic.",
    ),
    (
        "stack_trace_unhandled",
        re.compile(r"(traceback \(most recent call last\)|unhandled exception|panic:)", re.IGNORECASE),
        "The application crashed with an unhandled exception/panic.",
        "Inspect the stack trace below for the exact line that raised the error.",
    ),
]


class LogAnalyzer:
    """Scans raw pod log text and returns structured findings."""

    def __init__(self, extra_patterns: list[tuple[str, re.Pattern, str, str]] | None = None):
        self._patterns = list(_PATTERNS) + (extra_patterns or [])

    def analyze(self, log_text: str, max_findings_per_pattern: int = 1) -> list[LogFinding]:
        if not log_text:
            return []

        findings: list[LogFinding] = []
        lines = log_text.splitlines()

        for name, pattern, description, hint in self._patterns:
            matches = 0
            for line in lines:
                if pattern.search(line):
                    findings.append(
                        LogFinding(
                            pattern_name=name,
                            description=description,
                            matched_line=line.strip()[:500],
                            suggestion_hint=hint,
                        )
                    )
                    matches += 1
                    if matches >= max_findings_per_pattern:
                        break
        return findings

    def tail_relevant(self, log_text: str, context_lines: int = 15) -> str:
        """Return the last N lines of a log — used as compact context for the LLM."""
        if not log_text:
            return ""
        lines = log_text.splitlines()
        return "\n".join(lines[-context_lines:])
