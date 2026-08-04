from unittest.mock import MagicMock

from kube_assistant_mcp.fixer import AutoFixer, RuleBasedFixer
from kube_assistant_mcp.models import IssueType, PodIssue, PodRef, Severity


def _issue(issue_type: IssueType, restart_count: int = 5) -> PodIssue:
    return PodIssue(
        pod=PodRef(name="app-1", namespace="default", container="app"),
        issue_type=issue_type,
        severity=Severity.HIGH,
        restart_count=restart_count,
    )


def test_rule_fixer_returns_oom_suggestion():
    fixer = RuleBasedFixer()
    suggestions = fixer.suggest(_issue(IssueType.OOM_KILLED))
    assert any("memory" in s.title.lower() for s in suggestions)


def test_rule_fixer_returns_crash_loop_suggestions():
    fixer = RuleBasedFixer()
    suggestions = fixer.suggest(_issue(IssueType.CRASH_LOOP_BACKOFF))
    assert len(suggestions) >= 2
    assert any("--previous" in cmd for s in suggestions for cmd in s.commands)


def test_rule_fixer_falls_back_to_generic_for_unknown():
    fixer = RuleBasedFixer()
    suggestions = fixer.suggest(_issue(IssueType.UNKNOWN))
    assert len(suggestions) == 1
    assert "diagnostic" in suggestions[0].title.lower()


def test_autofixer_dry_run_never_touches_client():
    mock_client = MagicMock()
    autofixer = AutoFixer(mock_client)
    issue = _issue(IssueType.CRASH_LOOP_BACKOFF)
    plan = autofixer.plan_restart(issue)

    result = autofixer.apply(plan, dry_run=True, confirm=False)

    assert "DRY RUN" in result
    mock_client.delete_pod.assert_not_called()


def test_autofixer_without_confirm_never_mutates_even_if_dry_run_false():
    mock_client = MagicMock()
    autofixer = AutoFixer(mock_client)
    issue = _issue(IssueType.CRASH_LOOP_BACKOFF)
    plan = autofixer.plan_restart(issue)

    # dry_run=False but confirm=False -> must still be a no-op (safety gate)
    result = autofixer.apply(plan, dry_run=False, confirm=False)

    assert "DRY RUN" in result
    mock_client.delete_pod.assert_not_called()


def test_autofixer_applies_delete_pod_when_confirmed():
    mock_client = MagicMock()
    autofixer = AutoFixer(mock_client)
    issue = _issue(IssueType.CRASH_LOOP_BACKOFF)
    plan = autofixer.plan_restart(issue)

    result = autofixer.apply(plan, dry_run=False, confirm=True)

    mock_client.delete_pod.assert_called_once_with("app-1", "default")
    assert "Deleted pod" in result


def test_autofixer_applies_memory_patch_when_confirmed():
    mock_client = MagicMock()
    autofixer = AutoFixer(mock_client)
    plan = autofixer.plan_resource_patch(
        deployment_name="app", namespace="default", container="app", memory_limit="1Gi", memory_request="256Mi"
    )

    result = autofixer.apply(plan, dry_run=False, confirm=True)

    mock_client.patch_deployment.assert_called_once()
    called_name, called_ns, called_patch = mock_client.patch_deployment.call_args[0]
    assert called_name == "app"
    assert called_ns == "default"
    assert called_patch["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"] == "1Gi"
    assert "Patched deployment" in result
