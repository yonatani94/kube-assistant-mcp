"""Core data models used across the kube-assistant-mcp package."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IssueType(str, Enum):
    """Known categories of pod failure that the analyzer can recognize."""

    CRASH_LOOP_BACKOFF = "CrashLoopBackOff"
    IMAGE_PULL_BACKOFF = "ImagePullBackOff"
    OOM_KILLED = "OOMKilled"
    PENDING_UNSCHEDULABLE = "PendingUnschedulable"
    READINESS_PROBE_FAILED = "ReadinessProbeFailed"
    LIVENESS_PROBE_FAILED = "LivenessProbeFailed"
    CONFIG_ERROR = "ConfigError"
    UNKNOWN = "Unknown"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PodRef:
    """Lightweight reference to a pod, used to avoid passing the raw k8s object around."""

    name: str
    namespace: str
    node: str | None = None
    container: str | None = None


@dataclass
class PodIssue:
    """A single detected problem with a pod, produced by the LogAnalyzer / K8sClient."""

    pod: PodRef
    issue_type: IssueType
    severity: Severity
    restart_count: int = 0
    reason: str = ""
    message: str = ""
    last_termination_reason: str | None = None
    exit_code: int | None = None
    evidence: list[str] = field(default_factory=list)
    """Raw log lines / event messages that led to the classification."""

    def summary(self) -> str:
        return (
            f"[{self.severity.value.upper()}] {self.pod.namespace}/{self.pod.name} "
            f"-> {self.issue_type.value} (restarts={self.restart_count})"
        )


@dataclass
class FixSuggestion:
    """A concrete, actionable fix suggested either by rules or by the LLM."""

    title: str
    explanation: str
    commands: list[str] = field(default_factory=list)
    """Human-runnable kubectl / helm commands, shown to the user."""
    patch: dict | None = None
    """Optional strategic-merge-patch body, used for --apply."""
    manifest_yaml: str | None = None
    """Optional full manifest/Helm values suggested as a replacement or addition."""
    risk: Severity = Severity.LOW
    requires_confirmation: bool = True


@dataclass
class Diagnosis:
    """Full diagnostic result for one pod: the issue plus one or more fix suggestions."""

    issue: PodIssue
    suggestions: list[FixSuggestion] = field(default_factory=list)
    llm_explanation: str | None = None
    source: str = "rules"
    """'rules' | 'llm' | 'rules+llm' — where the diagnosis came from."""
