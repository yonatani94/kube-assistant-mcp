"""kube-assistant CLI — Typer app.

Commands:
  scan       List/summarize currently failing pods in a cluster.
  diagnose   Deep-dive a single pod: logs + events + LLM explanation + fixes.
  fix        Apply (or dry-run) a suggested fix, with an interactive confirm.
  generate   Generate a Deployment/Service manifest or a Helm chart skeleton.
  serve      Start the MCP server (stdio) for Cursor / Claude Desktop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .fixer import AutoFixer, RuleBasedFixer
from .k8s_client import K8sClient, KubernetesClientError
from .llm_client import LLMClient, LLMConfig
from .log_analyzer import LogAnalyzer
from .manifest_gen import DeploymentSpec, ManifestGenerator

app = typer.Typer(
    name="kube-assistant",
    help="Agentic Kubernetes troubleshooting CLI + MCP server.",
    no_args_is_help=True,
)
console = Console()


def _get_client(context: Optional[str]) -> K8sClient:
    try:
        return K8sClient(kube_context=context)
    except KubernetesClientError as exc:
        console.print(f"[red]Cluster connection failed:[/red] {exc}")
        raise typer.Exit(code=1)


@app.command()
def scan(
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="Limit scan to one namespace."),
    context: Optional[str] = typer.Option(None, "--context", help="kubeconfig context to use."),
):
    """Scan the cluster (or a namespace) and list all failing pods."""
    client = _get_client(context)
    issues = client.find_failing_pods(namespace)

    if not issues:
        console.print("[green]No failing pods detected.[/green]")
        return

    table = Table(title="Failing Pods")
    table.add_column("Namespace")
    table.add_column("Pod")
    table.add_column("Issue")
    table.add_column("Restarts", justify="right")
    table.add_column("Severity")

    severity_color = {"low": "yellow", "medium": "orange3", "high": "red", "critical": "bold red"}
    for issue in issues:
        color = severity_color.get(issue.severity.value, "white")
        table.add_row(
            issue.pod.namespace,
            issue.pod.name,
            issue.issue_type.value,
            str(issue.restart_count),
            f"[{color}]{issue.severity.value}[/{color}]",
        )
    console.print(table)


@app.command()
def diagnose(
    pod_name: str = typer.Argument(..., help="Name of the pod to diagnose."),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    context: Optional[str] = typer.Option(None, "--context"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Ask the local/remote LLM for an explanation."),
    llm_backend: Optional[str] = typer.Option(None, "--llm-backend", help="ollama | openai"),
    llm_model: Optional[str] = typer.Option(None, "--llm-model"),
):
    """Deep-dive a single pod: fetch logs + events, classify the failure,
    and (optionally) ask the LLM to explain it and suggest fixes.
    """
    client = _get_client(context)
    issues = [
        i for i in client.find_failing_pods(namespace) if i.pod.name == pod_name
    ]
    if not issues:
        console.print(
            f"[yellow]No known failure detected for pod '{pod_name}' in namespace "
            f"'{namespace}'. It may be healthy, or use a status this tool doesn't classify yet.[/yellow]"
        )
        raise typer.Exit(code=0)

    issue = issues[0]
    logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container, previous=True)
    if not logs:
        logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container)
    events = client.get_pod_events(pod_name, namespace)

    analyzer = LogAnalyzer()
    findings = analyzer.analyze(logs)

    console.print(f"\n[bold]{issue.summary()}[/bold]\n")
    if findings:
        console.print("[bold]Log pattern findings:[/bold]")
        for f in findings:
            console.print(f"  • {f.description} [dim]({f.pattern_name})[/dim]")
            console.print(f"    [dim]{f.matched_line}[/dim]")

    rule_fixer = RuleBasedFixer()
    suggestions = rule_fixer.suggest(issue)

    if use_llm:
        config = LLMConfig.from_env()
        if llm_backend:
            config.backend = llm_backend
        if llm_model:
            config.model = llm_model
        llm = LLMClient(config)
        findings_summary = "; ".join(f"{f.pattern_name}: {f.description}" for f in findings)
        diagnosis = llm.diagnose(issue, logs, events, findings_summary)
        if diagnosis.llm_explanation:
            console.print(f"\n[bold cyan]LLM explanation ({config.backend}/{config.model}):[/bold cyan]")
            console.print(diagnosis.llm_explanation)
        if diagnosis.suggestions:
            suggestions = diagnosis.suggestions + suggestions

    console.print("\n[bold]Suggested fixes:[/bold]")
    for idx, s in enumerate(suggestions, 1):
        console.print(f"\n[bold]{idx}. {s.title}[/bold]  [dim](risk: {s.risk.value})[/dim]")
        console.print(f"   {s.explanation}")
        for cmd in s.commands:
            console.print(f"   [green]$ {cmd}[/green]")

    console.print(
        "\n[dim]Tip: run `kube-assistant fix "
        f"{pod_name} -n {namespace}` to apply a suggestion interactively.[/dim]"
    )


@app.command()
def fix(
    pod_name: str = typer.Argument(...),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    context: Optional[str] = typer.Option(None, "--context"),
    strategy: str = typer.Option(
        "restart", "--strategy", help="restart | memory-patch"
    ),
    memory_limit: str = typer.Option("1Gi", "--memory-limit", help="Used with --strategy memory-patch"),
    memory_request: str = typer.Option("256Mi", "--memory-request"),
    deployment: Optional[str] = typer.Option(None, "--deployment", help="Deployment name (memory-patch)."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview only, default on."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation prompt."),
):
    """Apply a fix to a failing pod. Always dry-runs unless --no-dry-run is
    passed, and always asks for interactive confirmation unless --yes is set.
    """
    client = _get_client(context)
    autofixer = AutoFixer(client)

    issues = [i for i in client.find_failing_pods(namespace) if i.pod.name == pod_name]
    if not issues:
        console.print(f"[yellow]Pod '{pod_name}' has no detected issue; nothing to fix.[/yellow]")
        raise typer.Exit(code=0)
    issue = issues[0]

    if strategy == "restart":
        plan = autofixer.plan_restart(issue)
    elif strategy == "memory-patch":
        if not deployment:
            console.print("[red]--deployment is required for --strategy memory-patch[/red]")
            raise typer.Exit(code=1)
        plan = autofixer.plan_resource_patch(
            deployment, namespace, issue.pod.container or "app", memory_limit, memory_request
        )
    else:
        console.print(f"[red]Unknown strategy: {strategy}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold]Plan:[/bold] {plan.description}")
    console.print(f"[dim]Risk level: {plan.risk.value}[/dim]")

    if dry_run:
        result = autofixer.apply(plan, dry_run=True, confirm=False)
        console.print(result)
        console.print("[dim]Re-run with --no-dry-run to actually apply this.[/dim]")
        return

    if not yes:
        confirmed = typer.confirm("Apply this change to the live cluster?")
        if not confirmed:
            console.print("Aborted.")
            raise typer.Exit(code=0)

    result = autofixer.apply(plan, dry_run=False, confirm=True)
    console.print(f"[green]{result}[/green]")


@app.command()
def generate(
    name: str = typer.Argument(..., help="Application / release name."),
    image: str = typer.Option(..., "--image", help="Container image, e.g. myrepo/app:1.2.3"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    port: int = typer.Option(8080, "--port"),
    replicas: int = typer.Option(1, "--replicas"),
    kind_: str = typer.Option("manifest", "--kind", help="manifest | helm"),
    output: Path = typer.Option(Path("./generated"), "--output", "-o"),
):
    """Generate a plain Deployment+Service manifest, or a minimal Helm chart."""
    spec = DeploymentSpec(name=name, namespace=namespace, image=image, container_port=port, replicas=replicas)
    generator = ManifestGenerator()

    output.mkdir(parents=True, exist_ok=True)

    if kind_ == "manifest":
        deployment = generator.deployment_manifest(spec)
        service = generator.service_manifest(spec)
        yaml_text = generator.to_yaml(deployment, service)
        out_file = output / f"{name}.yaml"
        out_file.write_text(yaml_text)
        console.print(f"[green]Wrote manifest to {out_file}[/green]")
    elif kind_ == "helm":
        chart_dir = generator.write_helm_chart(spec, output)
        console.print(f"[green]Wrote Helm chart to {chart_dir}[/green]")
    else:
        console.print(f"[red]Unknown --kind: {kind_} (expected 'manifest' or 'helm')[/red]")
        raise typer.Exit(code=1)


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio | sse"),
):
    """Start the MCP server so Cursor / Claude Desktop can call this tool."""
    from .mcp_server import mcp

    console.print(f"[dim]Starting kube-assistant-mcp server (transport={transport})...[/dim]")
    mcp.run(transport=transport)


if __name__ == "__main__":
    app()
