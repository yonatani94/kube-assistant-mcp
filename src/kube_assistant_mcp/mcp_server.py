"""MCP server exposing kube-assistant's capabilities as tools for
Cursor / Claude Desktop / any MCP-compatible client.

Built on FastMCP (https://github.com/jlowin/fastmcp), which wraps the
official `mcp` SDK and lets us define tools as plain typed Python functions.

Run with:  kube-assistant serve
Or directly:  python -m kube_assistant_mcp.mcp_server
"""

from __future__ import annotations

from fastmcp import FastMCP

from .fixer import RuleBasedFixer
from .jira_client import JiraClient, JiraClientError, JiraConfig
from .k8s_client import K8sClient, KubernetesClientError
from .llm_client import LLMClient, LLMConfig
from .log_analyzer import LogAnalyzer
from .manifest_gen import DeploymentSpec, ManifestGenerator

mcp = FastMCP(
    name="kube-assistant-mcp",
    instructions=(
        "Tools for diagnosing failing Kubernetes workloads. This server is "
        "read-only against the cluster — it never mutates it. Always call "
        "list_failing_pods before diagnose_pod so you know which "
        "namespace/pod to target. Once you have a diagnosis, if a human with "
        "cluster write access needs to act on it, call open_jira_ticket with "
        "a clear summary, description of the root cause, and recommended fix."
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
def open_jira_ticket(
    pod_name: str,
    summary: str,
    description: str,
    recommended_fix: str,
    namespace: str = "default",
    severity: str = "medium",
    labels: list[str] | None = None,
) -> dict:
    """Open a Jira bug ticket documenting a Kubernetes issue this server
    investigated. This server has read-only cluster access and never applies
    fixes itself — use this tool to hand the investigation off to a human
    with write access, once you've already run diagnose_pod and have a clear
    root cause and recommended fix to report.

    summary: short one-line ticket title, e.g. "OOMKilled: payments-api in prod".
    description: plain-language root cause, based on the diagnose_pod output.
    recommended_fix: concrete, actionable remediation (e.g. the fix commands
    diagnose_pod suggested), so whoever picks up the ticket can act on it directly.
    severity: 'low' | 'medium' | 'high' | 'critical' — included in the ticket body.
    """
    ticket_description = (
        f"Pod: {namespace}/{pod_name}\n"
        f"Severity: {severity}\n\n"
        f"Investigation summary:\n{description}\n\n"
        f"Recommended fix:\n{recommended_fix}\n\n"
        "---\n"
        "Opened automatically by kube-assistant-mcp. This environment grants "
        "read-only cluster access, so an engineer with write access needs to "
        "apply the fix above."
    )

    try:
        jira = JiraClient(JiraConfig.from_env())
        result = jira.create_issue(
            summary=summary,
            description=ticket_description,
            labels=labels,
        )
    except JiraClientError as exc:
        return {"error": str(exc)}

    return result


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
