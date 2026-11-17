"""MCP server exposing kube-assistant's capabilities as tools for
Cursor / Claude Desktop / any MCP-compatible client.

Built on FastMCP (https://github.com/jlowin/fastmcp), which wraps the
official `mcp` SDK and lets us define tools as plain typed Python functions.

Run with:  kube-assistant serve
Or directly:  python -m kube_assistant_mcp.mcp_server
"""

from __future__ import annotations

from fastmcp import FastMCP

from .diagnostics import diagnose_pod as run_diagnosis
from .jira_client import JiraClient, JiraClientError, JiraConfig, build_ticket_markdown, severity_to_priority
from .k8s_client import K8sClient, KubernetesClientError
from .manifest_gen import DeploymentSpec, ManifestGenerator
from .security import redact

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
    if not 1 <= tail_lines <= 2000:
        raise ValueError("tail_lines must be between 1 and 2000")
    return redact(client.get_pod_logs(pod_name, namespace, previous=previous, tail_lines=tail_lines))


@mcp.tool()
def diagnose_pod(
    pod_name: str,
    namespace: str = "default",
    use_llm: bool = True,
    context: str | None = None,
    use_rag: bool | None = None,
) -> dict:
    """Run a full diagnosis on one pod: fetch logs+events, run pattern
    detection, and (optionally) get an LLM root-cause explanation with
    concrete fix suggestions. This is the main tool an agent should call
    after finding a failing pod with list_failing_pods.
    """
    result = run_diagnosis(_client(context), pod_name, namespace, use_llm, use_rag=use_rag)
    if result is None:
        return {"status": "no_issue_found", "pod": pod_name, "namespace": namespace}
    return result.to_mcp()


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

    Write this like a ticket a senior SRE would file, not a chat reply —
    specific and directly actionable, no filler.

    summary: short one-line ticket title, e.g. "OOMKilled: payments-api in prod".
    description: the root cause, in plain language, based on the diagnose_pod
    output (issue type, restart count, log findings) — 2-4 sentences, not a
    guess. Say what you don't know if the evidence is inconclusive.
    recommended_fix: concrete, actionable remediation. Reuse diagnose_pod's
    `manifest_yaml` (the exact lines to change) and `commands` verbatim where
    available. You may use light Markdown here — fenced ```yaml / ```bash
    blocks render as real code blocks in the ticket, and `- ` lines render as
    a bullet list, so use them for exact commands/manifest snippets instead
    of prose.
    severity: 'low' | 'medium' | 'high' | 'critical' — also sets the ticket's
    Jira Priority field (not just noted in the text).
    """
    priority = severity_to_priority(severity)
    ticket_description = build_ticket_markdown(pod_name, namespace, severity, description, recommended_fix)

    try:
        jira = JiraClient(JiraConfig.from_env())
        result = jira.create_issue(
            summary=summary,
            description=ticket_description,
            labels=labels,
            priority=priority,
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
