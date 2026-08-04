"""MCP server exposing kube-assistant's capabilities as tools for
Cursor / Claude Desktop / any MCP-compatible client.

Built on FastMCP (https://github.com/jlowin/fastmcp), which wraps the
official `mcp` SDK and lets us define tools as plain typed Python functions.

Run with:  kube-assistant serve
Or directly:  python -m kube_assistant_mcp.mcp_server
"""

from __future__ import annotations

from fastmcp import FastMCP

from .fixer import AutoFixer, RuleBasedFixer
from .k8s_client import K8sClient, KubernetesClientError
from .llm_client import LLMClient, LLMConfig
from .log_analyzer import LogAnalyzer
from .manifest_gen import DeploymentSpec, ManifestGenerator

mcp = FastMCP(
    name="kube-assistant-mcp",
    instructions=(
        "Tools for diagnosing and fixing failing Kubernetes workloads. "
        "Always call list_failing_pods before diagnose_pod so you know which "
        "namespace/pod to target. Mutating tools (apply_fix) default to "
        "dry-run and require an explicit confirm=true to actually change the cluster."
    ),
)

_client_cache: dict[str, K8sClient] = {}


def _client(context: str | None = None) -> K8sClient:
    key = context or "__default__"
    if key not in _client_cache:
        _client_cache[key] = K8sClient(kube_context=context)
    return _client_cache[key]


@mcp.tool()
def list_failing_pods(namespace: str | None = None, context: str | None = None) -> list[dict]:
    """List all pods currently in a failure state (CrashLoopBackOff, OOMKilled,
    ImagePullBackOff, etc). Pass a namespace to narrow the scan, or omit it to
    scan the whole cluster.
    """
    try:
        client = _client(context)
    except KubernetesClientError as exc:
        return [{"error": str(exc)}]

    issues = client.find_failing_pods(namespace)
    return [
        {
            "namespace": i.pod.namespace,
            "pod": i.pod.name,
            "container": i.pod.container,
            "issue_type": i.issue_type.value,
            "severity": i.severity.value,
            "restart_count": i.restart_count,
            "reason": i.reason,
        }
        for i in issues
    ]


@mcp.tool()
def get_pod_logs(
    pod_name: str,
    namespace: str = "default",
    previous: bool = True,
    tail_lines: int = 200,
    context: str | None = None,
) -> str:
    """Fetch recent logs for a pod. Set previous=true (default) to get the
    logs from before the last crash, which is usually what you want for a
    CrashLoopBackOff pod.
    """
    client = _client(context)
    return client.get_pod_logs(pod_name, namespace, previous=previous, tail_lines=tail_lines)


@mcp.tool()
def diagnose_pod(
    pod_name: str,
    namespace: str = "default",
    use_llm: bool = True,
    context: str | None = None,
) -> dict:
    """Run a full diagnosis on one pod: fetch logs+events, run pattern
    detection, and (optionally) get an LLM root-cause explanation with
    concrete fix suggestions. This is the main tool an agent should call
    after finding a failing pod with list_failing_pods.
    """
    client = _client(context)
    issues = [i for i in client.find_failing_pods(namespace) if i.pod.name == pod_name]
    if not issues:
        return {"status": "no_issue_found", "pod": pod_name, "namespace": namespace}

    issue = issues[0]
    logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container, previous=True)
    if not logs:
        logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container)
    events = client.get_pod_events(pod_name, namespace)

    findings = LogAnalyzer().analyze(logs)
    rule_suggestions = RuleBasedFixer().suggest(issue)

    result = {
        "issue": {
            "type": issue.issue_type.value,
            "severity": issue.severity.value,
            "restart_count": issue.restart_count,
            "reason": issue.reason,
        },
        "log_findings": [
            {"pattern": f.pattern_name, "description": f.description, "line": f.matched_line}
            for f in findings
        ],
        "rule_based_fixes": [
            {"title": s.title, "explanation": s.explanation, "commands": s.commands, "risk": s.risk.value}
            for s in rule_suggestions
        ],
    }

    if use_llm:
        llm = LLMClient(LLMConfig.from_env())
        summary = "; ".join(f.description for f in findings)
        diagnosis = llm.diagnose(issue, logs, events, summary)
        result["llm_explanation"] = diagnosis.llm_explanation
        result["llm_fixes"] = [
            {"title": s.title, "explanation": s.explanation, "commands": s.commands, "risk": s.risk.value}
            for s in diagnosis.suggestions
        ]

    return result


@mcp.tool()
def apply_fix(
    pod_name: str,
    namespace: str = "default",
    strategy: str = "restart",
    deployment_name: str | None = None,
    memory_limit: str = "1Gi",
    memory_request: str = "256Mi",
    confirm: bool = False,
    context: str | None = None,
) -> str:
    """Apply a fix to a failing pod. strategy is 'restart' (delete the pod so
    its controller recreates it) or 'memory-patch' (raise the memory
    limit/request on the owning Deployment — requires deployment_name).

    SAFETY: this defaults to a dry run. It only mutates the cluster when
    confirm=true is explicitly passed by the caller. Always show the plan
    to the human and get their explicit go-ahead before passing confirm=true.
    """
    client = _client(context)
    autofixer = AutoFixer(client)

    issues = [i for i in client.find_failing_pods(namespace) if i.pod.name == pod_name]
    if not issues:
        return f"No detected issue for pod {namespace}/{pod_name}; nothing to fix."
    issue = issues[0]

    if strategy == "restart":
        plan = autofixer.plan_restart(issue)
    elif strategy == "memory-patch":
        if not deployment_name:
            return "deployment_name is required for the memory-patch strategy."
        plan = autofixer.plan_resource_patch(
            deployment_name, namespace, issue.pod.container or "app", memory_limit, memory_request
        )
    else:
        return f"Unknown strategy: {strategy!r}. Use 'restart' or 'memory-patch'."

    return autofixer.apply(plan, dry_run=not confirm, confirm=confirm)


@mcp.tool()
def generate_manifest(
    name: str,
    image: str,
    namespace: str = "default",
    port: int = 8080,
    replicas: int = 1,
    kind: str = "manifest",
) -> str:
    """Generate a Kubernetes Deployment+Service YAML manifest, or a minimal
    Helm chart (kind='helm'), for a new or replacement workload. Returns the
    YAML as a string (for kind='manifest') or the path to the written chart
    (for kind='helm').
    """
    spec = DeploymentSpec(name=name, namespace=namespace, image=image, container_port=port, replicas=replicas)
    generator = ManifestGenerator()

    if kind == "helm":
        from pathlib import Path

        chart_dir = generator.write_helm_chart(spec, Path("./generated"))
        return f"Helm chart written to {chart_dir}"

    deployment = generator.deployment_manifest(spec)
    service = generator.service_manifest(spec)
    return generator.to_yaml(deployment, service)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
