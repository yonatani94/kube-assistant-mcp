from kube_assistant_mcp.k8s_client import K8sClient
from kube_assistant_mcp.models import IssueType, Severity


def _classify(pod):
    """Call the classification logic without going through __init__
    (which would try to load a real kubeconfig)."""
    return K8sClient._classify_pod(K8sClient.__new__(K8sClient), pod)


def test_classifies_crash_loop_backoff(fake_pod_factory):
    pod = fake_pod_factory("api-7f8", restart_count=12, waiting_reason="CrashLoopBackOff")
    issue = _classify(pod)
    assert issue is not None
    assert issue.issue_type == IssueType.CRASH_LOOP_BACKOFF
    assert issue.severity == Severity.CRITICAL  # restart_count >= 10
    assert issue.pod.name == "api-7f8"


def test_classifies_image_pull_backoff(fake_pod_factory):
    pod = fake_pod_factory("worker-1", waiting_reason="ImagePullBackOff")
    issue = _classify(pod)
    assert issue.issue_type == IssueType.IMAGE_PULL_BACKOFF
    assert issue.severity == Severity.HIGH


def test_classifies_oom_killed_from_last_state(fake_pod_factory):
    pod = fake_pod_factory(
        "cache-0",
        restart_count=3,
        last_terminated_reason="OOMKilled",
        last_terminated_exit_code=137,
    )
    issue = _classify(pod)
    assert issue.issue_type == IssueType.OOM_KILLED
    assert issue.severity == Severity.HIGH
    assert issue.exit_code == 137


def test_healthy_pod_returns_none(fake_pod_factory):
    pod = fake_pod_factory("healthy-pod", restart_count=0)
    issue = _classify(pod)
    assert issue is None


def test_config_error_classified_as_medium(fake_pod_factory):
    pod = fake_pod_factory("bad-config", waiting_reason="CreateContainerConfigError")
    issue = _classify(pod)
    assert issue.issue_type == IssueType.CONFIG_ERROR
    assert issue.severity == Severity.MEDIUM


def test_pod_with_no_container_statuses_returns_none():
    from types import SimpleNamespace

    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="x", namespace="default"),
        status=SimpleNamespace(container_statuses=None),
        spec=SimpleNamespace(node_name="node-1"),
    )
    issue = _classify(pod)
    assert issue is None
