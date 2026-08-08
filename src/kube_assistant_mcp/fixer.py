"""Rule-based fix suggestions, generated purely from an issue's classification.

This module never touches the cluster — it only produces suggestion text
(commands to run, explanations). Cluster access from this project is
read-only; the recommended fix ends up in a Jira ticket (see
`jira_client.py`) for someone with write access to action.
"""

from __future__ import annotations

from .models import FixSuggestion, PodIssue, Severity


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
        container = issue.pod.container or "app"
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
                        f"-c={container} --limits=memory=1Gi --requests=memory=256Mi"
                    )
                ],
                manifest_yaml=(
                    "spec:\n"
                    "  template:\n"
                    "    spec:\n"
                    "      containers:\n"
                    f"        - name: {container}\n"
                    "          resources:\n"
                    "            requests:\n"
                    '              memory: "256Mi"  # was lower; raise if this still OOMs\n'
                    "            limits:\n"
                    '              memory: "1Gi"     # was hit at the previous limit; raise headroom\n'
                ),
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
