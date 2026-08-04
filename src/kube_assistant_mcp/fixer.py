"""Rule-based fix suggestions and the (careful) apply-to-cluster logic.

Two layers of safety are baked in on purpose, since this touches a live cluster:
1. Every AutoFixer method that mutates the cluster requires `confirm=True`
   unless the caller explicitly passes `dry_run=False, confirm=True`.
2. `plan()` never touches the network — it only returns what *would* happen,
   so the CLI can always show a diff before asking the user to confirm.
"""

from __future__ import annotations

from dataclasses import dataclass

from .k8s_client import K8sClient
from .models import FixSuggestion, PodIssue, Severity


@dataclass
class FixPlan:
    """What the fixer intends to do, before anything touches the cluster."""

    description: str
    kind: str  # "patch_deployment" | "delete_pod" | "manual"
    target_name: str
    target_namespace: str
    patch: dict | None = None
    risk: Severity = Severity.LOW


class RuleBasedFixer:
    """Generates FixSuggestion objects purely from the IssueType — no LLM
    call needed. Used as a fast first pass, and as a fallback when the LLM
    backend is unreachable (e.g. Ollama not running).
    """

    def suggest(self, issue: PodIssue) -> list[FixSuggestion]:
        method = getattr(self, f"_suggest_{issue.issue_type.name.lower()}", None)
        if method is None:
            return [self._generic_suggestion(issue)]
        return method(issue)

    def _suggest_oom_killed(self, issue: PodIssue) -> list[FixSuggestion]:
        return [
            FixSuggestion(
                title="Raise the container memory limit",
                explanation=(
                    "The container was OOMKilled, meaning it hit its memory limit. "
                    "Increasing the limit (and request) gives it headroom; if it "
                    "keeps growing, this may only mask a leak."
                ),
                commands=[
                    (
                        f"kubectl -n {issue.pod.namespace} set resources deployment/<deployment> "
                        f"-c={issue.pod.container} --limits=memory=1Gi --requests=memory=256Mi"
                    )
                ],
                risk=Severity.MEDIUM,
            )
        ]

    def _suggest_crash_loop_backoff(self, issue: PodIssue) -> list[FixSuggestion]:
        return [
            FixSuggestion(
                title="Inspect previous container logs",
                explanation=(
                    "CrashLoopBackOff means the container keeps exiting right after "
                    "start. The previous container's logs almost always show the "
                    "real exception."
                ),
                commands=[
                    (
                        f"kubectl -n {issue.pod.namespace} logs {issue.pod.name} "
                        f"-c {issue.pod.container} --previous"
                    )
                ],
                risk=Severity.LOW,
                requires_confirmation=False,
            ),
            FixSuggestion(
                title="Roll back to the last known-good image tag",
                explanation="If this started after a deploy, rolling back is the fastest mitigation.",
                commands=[f"kubectl -n {issue.pod.namespace} rollout undo deployment/<deployment>"],
                risk=Severity.MEDIUM,
            ),
        ]

    def _suggest_image_pull_backoff(self, issue: PodIssue) -> list[FixSuggestion]:
        return [
            FixSuggestion(
                title="Verify the image reference and pull secret",
                explanation=(
                    "The kubelet could not pull the image. Common causes: a typo in "
                    "the tag, a private registry without imagePullSecrets, or a "
                    "network policy blocking egress to the registry."
                ),
                commands=[
                    f"kubectl -n {issue.pod.namespace} describe pod {issue.pod.name}",
                    f"kubectl -n {issue.pod.namespace} get secrets",
                ],
                risk=Severity.LOW,
                requires_confirmation=False,
            )
        ]

    def _suggest_config_error(self, issue: PodIssue) -> list[FixSuggestion]:
        return [
            FixSuggestion(
                title="Validate ConfigMap / Secret references",
                explanation=(
                    "CreateContainerConfigError usually means a referenced "
                    "ConfigMap, Secret, or key does not exist in this namespace."
                ),
                commands=[f"kubectl -n {issue.pod.namespace} describe pod {issue.pod.name}"],
                risk=Severity.LOW,
                requires_confirmation=False,
            )
        ]

    def _generic_suggestion(self, issue: PodIssue) -> FixSuggestion:
        return FixSuggestion(
            title="Gather more diagnostic data",
            explanation=(
                f"No specific rule matched issue type {issue.issue_type.value!r}. "
                "Start by describing the pod and reviewing recent events."
            ),
            commands=[
                f"kubectl -n {issue.pod.namespace} describe pod {issue.pod.name}",
                f"kubectl -n {issue.pod.namespace} get events --sort-by=.lastTimestamp",
            ],
            requires_confirmation=False,
        )


class AutoFixer:
    """Turns a chosen FixSuggestion into cluster mutations, behind an
    explicit dry-run/confirm gate.
    """

    def __init__(self, client: K8sClient):
        self.client = client

    def plan_restart(self, issue: PodIssue) -> FixPlan:
        return FixPlan(
            description=f"Delete pod {issue.pod.namespace}/{issue.pod.name} to force a restart",
            kind="delete_pod",
            target_name=issue.pod.name,
            target_namespace=issue.pod.namespace,
            risk=Severity.LOW,
        )

    def plan_resource_patch(
        self, deployment_name: str, namespace: str, container: str, memory_limit: str, memory_request: str
    ) -> FixPlan:
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container,
                                "resources": {
                                    "limits": {"memory": memory_limit},
                                    "requests": {"memory": memory_request},
                                },
                            }
                        ]
                    }
                }
            }
        }
        return FixPlan(
            description=(
                f"Patch deployment {namespace}/{deployment_name}: set memory "
                f"limit={memory_limit}, request={memory_request} on container {container}"
            ),
            kind="patch_deployment",
            target_name=deployment_name,
            target_namespace=namespace,
            patch=patch,
            risk=Severity.MEDIUM,
        )

    def apply(self, plan: FixPlan, dry_run: bool = True, confirm: bool = False) -> str:
        """Executes a FixPlan. Returns a human-readable result string.

        Safety gate: real cluster mutation only happens when dry_run=False
        AND confirm=True. Any other combination returns a description of
        what *would* run, without touching the cluster.
        """
        if dry_run or not confirm:
            return f"[DRY RUN] Would execute: {plan.description}"

        if plan.kind == "delete_pod":
            self.client.delete_pod(plan.target_name, plan.target_namespace)
            return f"Deleted pod {plan.target_namespace}/{plan.target_name} (it will be recreated by its controller)."

        if plan.kind == "patch_deployment":
            self.client.patch_deployment(plan.target_name, plan.target_namespace, plan.patch)
            return f"Patched deployment {plan.target_namespace}/{plan.target_name}."

        return f"No-op: fix kind {plan.kind!r} requires manual action. {plan.description}"
