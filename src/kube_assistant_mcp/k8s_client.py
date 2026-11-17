"""Thin, testable wrapper around the official Kubernetes Python client.

Every method that talks to the cluster goes through `self.core` / `self.apps`
so tests can inject a fake client instead of hitting a real API server.
"""

from __future__ import annotations

import logging

from .models import IssueType, PodIssue, PodRef, Severity

logger = logging.getLogger(__name__)

# Container wait-reasons that map directly to a known IssueType.
_WAITING_REASON_MAP = {
    "CrashLoopBackOff": IssueType.CRASH_LOOP_BACKOFF,
    "ImagePullBackOff": IssueType.IMAGE_PULL_BACKOFF,
    "ErrImagePull": IssueType.IMAGE_PULL_BACKOFF,
    "CreateContainerConfigError": IssueType.CONFIG_ERROR,
    "InvalidImageName": IssueType.CONFIG_ERROR,
}


class KubernetesClientError(RuntimeError):
    """Raised when the cluster can't be reached or the context is invalid."""


class K8sClient:
    """Wraps kubernetes.client.CoreV1Api / AppsV1Api for the operations this
    project needs: listing pods, reading logs/events. Read-only against the
    cluster — this project never mutates the cluster it's pointed at.

    The kubernetes package is imported lazily inside __init__ so that the
    rest of the codebase (and the test suite) can run without it installed
    or without a kubeconfig present.
    """

    def __init__(self, kube_context: str | None = None, in_cluster: bool = False):
        try:
            from kubernetes import client, config  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised via mocks in tests
            raise KubernetesClientError(
                "The 'kubernetes' package is not installed. Run "
                "`pip install kube-assistant-mcp[k8s]` or `pip install kubernetes`."
            ) from exc

        try:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(context=kube_context)
        except Exception as exc:
            raise KubernetesClientError(
                f"Could not load kube config (context={kube_context!r}): {exc}"
            ) from exc

        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.context = kube_context

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #
    def list_namespaces(self) -> list[str]:
        return [ns.metadata.name for ns in self.core.list_namespace().items]

    def list_pods(self, namespace: str | None = None) -> list:
        if namespace:
            return self.core.list_namespaced_pod(namespace).items
        return self.core.list_pod_for_all_namespaces().items

    def get_pod_logs(
        self,
        name: str,
        namespace: str,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> str:
        try:
            return self.core.read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                container=container,
                previous=previous,
                tail_lines=tail_lines,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch logs for %s/%s: %s", namespace, name, exc)
            return ""

    def get_pod_events(self, name: str, namespace: str) -> list[str]:
        field_selector = f"involvedObject.name={name}"
        try:
            events = self.core.list_namespaced_event(namespace, field_selector=field_selector).items
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch events for %s/%s: %s", namespace, name, exc)
            return []
        return [f"{e.reason}: {e.message}" for e in events]

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #
    def find_failing_pods(self, namespace: str | None = None) -> list[PodIssue]:
        """Scan pods and turn container statuses into structured PodIssue objects."""
        issues: list[PodIssue] = []
        for pod in self.list_pods(namespace):
            issue = self._classify_pod(pod)
            if issue is not None:
                issues.append(issue)
        return issues

    def _classify_pod(self, pod) -> PodIssue | None:
        meta = pod.metadata
        status = pod.status
        if status is None or not status.container_statuses:
            return None

        for cs in status.container_statuses:
            restart_count = cs.restart_count or 0
            waiting = getattr(cs.state, "waiting", None)
            terminated = getattr(cs.state, "terminated", None)
            last_term = getattr(cs.last_state, "terminated", None) if cs.last_state else None

            issue_type = None
            reason = ""
            message = ""
            exit_code = None
            last_reason = None

            if waiting is not None and waiting.reason in _WAITING_REASON_MAP:
                issue_type = _WAITING_REASON_MAP[waiting.reason]
                reason = waiting.reason
                message = waiting.message or ""
            elif waiting is not None and waiting.reason == "CrashLoopBackOff":
                issue_type = IssueType.CRASH_LOOP_BACKOFF
                reason = waiting.reason

            if last_term is not None:
                last_reason = last_term.reason
                exit_code = last_term.exit_code
                if last_reason == "OOMKilled":
                    issue_type = IssueType.OOM_KILLED
                    reason = "OOMKilled"

            if terminated is not None and terminated.reason == "OOMKilled":
                issue_type = IssueType.OOM_KILLED
                reason = "OOMKilled"
                exit_code = terminated.exit_code

            if issue_type is None and restart_count >= 5:
                # Repeated restarts without a clean "waiting" reason still
                # deserve attention (e.g. probe failures).
                issue_type = IssueType.UNKNOWN

            if issue_type is None:
                continue

            severity = self._severity_for(issue_type, restart_count)
            pod_ref = PodRef(
                name=meta.name,
                namespace=meta.namespace,
                node=getattr(pod.spec, "node_name", None),
                container=cs.name,
            )
            return PodIssue(
                pod=pod_ref,
                issue_type=issue_type,
                severity=severity,
                restart_count=restart_count,
                reason=reason,
                message=message,
                last_termination_reason=last_reason,
                exit_code=exit_code,
            )
        return None

    @staticmethod
    def _severity_for(issue_type: IssueType, restart_count: int) -> Severity:
        if issue_type == IssueType.OOM_KILLED:
            return Severity.HIGH
        if issue_type == IssueType.CRASH_LOOP_BACKOFF and restart_count >= 10:
            return Severity.CRITICAL
        if issue_type in (IssueType.CRASH_LOOP_BACKOFF, IssueType.IMAGE_PULL_BACKOFF):
            return Severity.HIGH
        if issue_type == IssueType.CONFIG_ERROR:
            return Severity.MEDIUM
        return Severity.LOW

    # ------------------------------------------------------------------ #
    # Manifest generation support
    # ------------------------------------------------------------------ #
    def apply_yaml(self, manifest: dict, namespace: str) -> None:
        """Deprecated compatibility stub: investigation never permits cluster writes."""
        raise PermissionError("Cluster writes are disabled; apply changes through your approved deployment process")
