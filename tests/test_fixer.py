from kube_assistant_mcp.fixer import RuleBasedFixer
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


def test_rule_fixer_oom_suggestion_includes_exact_manifest_lines():
    fixer = RuleBasedFixer()
    suggestions = fixer.suggest(_issue(IssueType.OOM_KILLED))
    manifest = suggestions[0].manifest_yaml
    assert manifest is not None
    assert "name: app" in manifest
    assert "resources:" in manifest
    assert "memory:" in manifest


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
