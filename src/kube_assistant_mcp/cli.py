"""kube-assistant CLI — Typer app.

Commands:
  setup        Install missing prerequisites (kubectl, Helm, Ollama + model) via Homebrew/official scripts.
  doctor       Check that everything needed (Python, kubectl, cluster, LLM) is ready to go.
  scan         List/summarize currently failing pods in a cluster.
  diagnose     Deep-dive a single pod: logs + events + LLM explanation + fixes.
  open-ticket  Diagnose a pod and open a Jira bug ticket with the findings + recommended fix.
  generate     Generate a Deployment/Service manifest or a Helm chart skeleton.
  serve        Start the MCP server (stdio) for Cursor / Claude Desktop.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .diagnostics import diagnose_pod as run_diagnosis
from .jira_client import JiraClient, JiraClientError, JiraConfig, build_ticket_markdown, severity_to_priority
from .k8s_client import K8sClient, KubernetesClientError
from .llm_client import LLMConfig
from .manifest_gen import DeploymentSpec, ManifestGenerator
from .models import FixSuggestion
from .security import safe_output_path

app = typer.Typer(
    name="kube-assistant",
    help="Agentic Kubernetes troubleshooting CLI + MCP server.",
    no_args_is_help=True,
)
console = Console()

_DEFAULT_OUTPUT_DIR = Path("./generated")


def _get_client(context: str | None) -> K8sClient:
    try:
        return K8sClient(kube_context=context)
    except KubernetesClientError as exc:
        console.print(f"[red]Cluster connection failed:[/red] {exc}")
        raise typer.Exit(code=1)


def _diagnose_pod(client, pod_name, namespace, use_llm, llm_backend, llm_model, use_rag=None):
    return run_diagnosis(client, pod_name, namespace, use_llm, llm_backend, llm_model, use_rag)


@app.command()
def scan(
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Limit scan to one namespace."),
    context: str | None = typer.Option(None, "--context", help="kubeconfig context to use."),
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
    context: str | None = typer.Option(None, "--context"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Ask the local/remote LLM for an explanation."),
    llm_backend: str | None = typer.Option(None, "--llm-backend", help="ollama | openai | anthropic | gemini | groq"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    use_rag: bool | None = typer.Option(None, "--rag/--no-rag", help="Retrieve operational guidance."),
):
    """Deep-dive a single pod: fetch logs + events, classify the failure,
    and (optionally) ask the LLM to explain it and suggest fixes.
    """
    client = _get_client(context)
    result = _diagnose_pod(client, pod_name, namespace, use_llm, llm_backend, llm_model, use_rag)
    if result is None:
        console.print(
            f"[yellow]No known failure detected for pod '{pod_name}' in namespace "
            f"'{namespace}'. It may be healthy, or use a status this tool doesn't classify yet.[/yellow]"
        )
        raise typer.Exit(code=0)

    console.print(f"\n[bold]{result.issue.summary()}[/bold]\n")
    if result.findings_summary:
        console.print("[bold]Log pattern findings:[/bold]")
        console.print(f"  {result.findings_summary}")

    if result.llm_explanation:
        console.print(f"\n[bold cyan]LLM explanation ({result.llm_label}):[/bold cyan]")
        console.print(result.llm_explanation, markup=False)

    if result.rag_status != "disabled":
        console.print(f"\nRetrieved guidance ({result.rag_status}; not observed evidence):")
        for passage in result.retrieved_guidance:
            console.print(f"{passage['source']} — {passage['heading']} [{passage['chunk_id']}]", markup=False)
            console.print(passage['text'], markup=False)
    console.print("\n[bold]Suggested fixes:[/bold]")
    for idx, s in enumerate(result.suggestions, 1):
        console.print(f"\n[bold]{idx}. {s.title}[/bold]  [dim](risk: {s.risk.value})[/dim]")
        console.print(f"   {s.explanation}")
        for cmd in s.commands:
            console.print(f"   [green]$ {cmd}[/green]")
        if s.manifest_yaml:
            console.print("   [dim]Manifest change:[/dim]")
            for line in s.manifest_yaml.rstrip("\n").splitlines():
                console.print(f"   [cyan]{line}[/cyan]")

    console.print(
        "\n[dim]Tip: run `kube-assistant open-ticket "
        f"{pod_name} -n {namespace}` to hand this off to Jira for someone with "
        "cluster write access.[/dim]"
    )


def _format_fix_markdown(suggestions: list[FixSuggestion]) -> str:
    """Renders suggestions as Markdown for the Jira ticket body: each
    suggestion's title/explanation as a bullet, followed by its exact
    manifest lines and/or commands as fenced code blocks (not prose), so the
    ticket shows precisely what to change.
    """
    if not suggestions:
        return "No automatic suggestion; manual investigation required."

    blocks: list[str] = []
    for s in suggestions:
        blocks.append(f"- {s.title} (risk: {s.risk.value}): {s.explanation}")
        if s.manifest_yaml:
            blocks.append(f"```yaml\n{s.manifest_yaml.rstrip()}\n```")
        if s.commands:
            blocks.append("```bash\n" + "\n".join(s.commands) + "\n```")
    return "\n\n".join(blocks)


@app.command("open-ticket")
def open_ticket(
    pod_name: str = typer.Argument(..., help="Name of the pod to diagnose and file a ticket for."),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    context: str | None = typer.Option(None, "--context"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Ask the local/remote LLM for an explanation."),
    llm_backend: str | None = typer.Option(None, "--llm-backend", help="ollama | openai | anthropic | gemini | groq"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    use_rag: bool | None = typer.Option(None, "--rag/--no-rag", help="Retrieve operational guidance."),
    project: str | None = typer.Option(None, "--project", help="Jira project key (overrides JIRA_PROJECT_KEY)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation prompt."),
):
    """Diagnose a pod and open a Jira bug ticket with the root cause and a
    recommended fix, for an engineer with cluster write access to action.
    This tool never touches the cluster itself — read-only, always.
    """
    client = _get_client(context)
    result = _diagnose_pod(client, pod_name, namespace, use_llm, llm_backend, llm_model, use_rag)
    if result is None:
        console.print(f"[yellow]Pod '{pod_name}' has no detected issue; nothing to file.[/yellow]")
        raise typer.Exit(code=0)

    issue = result.issue
    summary = f"[{issue.severity.value}] {issue.issue_type.value} in {namespace}/{pod_name}"
    description = result.llm_explanation or result.findings_summary or "No pattern match; manual log review needed."
    fix_text = _format_fix_markdown(result.suggestions)
    if result.citation_ids:
        cited = set(result.citation_ids)
        sources = [f"- {p['source']} — {p['heading']} [{p['chunk_id']}]"
                   for p in result.retrieved_guidance if p['chunk_id'] in cited]
        description += "\n\nRetrieved guidance (not observed evidence):\n" + "\n".join(sources)
    priority = severity_to_priority(issue.severity.value)

    console.print("\n[bold]Ticket preview[/bold]")
    console.print(f"[bold]Summary:[/bold] {summary}")
    console.print(f"[bold]Priority:[/bold] {priority or '(not set — unrecognized severity)'}")
    console.print(f"[bold]Description:[/bold] {description}")
    console.print(f"[bold]Recommended fix:[/bold]\n{fix_text}")

    if not yes:
        confirmed = typer.confirm("\nOpen this ticket in Jira?")
        if not confirmed:
            console.print("Aborted.")
            raise typer.Exit(code=0)

    try:
        jira = JiraClient(JiraConfig.from_env())
        opened = jira.create_issue(
            summary=summary,
            description=build_ticket_markdown(pod_name, namespace, issue.severity.value, description, fix_text),
            project_key=project,
            priority=priority,
        )
    except JiraClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if opened.get("warning"):
        console.print(f"[yellow]{opened['warning']}[/yellow]")
    console.print(f"\n[green]Opened {opened['key']}: {opened['url']}[/green]")


@app.command()
def generate(
    name: str = typer.Argument(..., help="Application / release name."),
    image: str = typer.Option(..., "--image", help="Container image, e.g. myrepo/app:1.2.3"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    port: int = typer.Option(8080, "--port"),
    replicas: int = typer.Option(1, "--replicas"),
    kind_: str = typer.Option("manifest", "--kind", help="manifest | helm"),
    output: Path = typer.Option(_DEFAULT_OUTPUT_DIR, "--output", "-o"),  # noqa: B008
):
    """Generate a plain Deployment+Service manifest, or a minimal Helm chart."""
    spec = DeploymentSpec(name=name, namespace=namespace, image=image, container_port=port, replicas=replicas)
    generator = ManifestGenerator()

    output.mkdir(parents=True, exist_ok=True)

    if kind_ == "manifest":
        deployment = generator.deployment_manifest(spec)
        service = generator.service_manifest(spec)
        yaml_text = generator.to_yaml(deployment, service)
        out_file = safe_output_path(output, f"{name}.yaml")
        out_file.write_text(yaml_text)
        console.print(f"[green]Wrote manifest to {out_file}[/green]")
    elif kind_ == "helm":
        chart_dir = generator.write_helm_chart(spec, output)
        console.print(f"[green]Wrote Helm chart to {chart_dir}[/green]")
    else:
        console.print(f"[red]Unknown --kind: {kind_} (expected 'manifest' or 'helm')[/red]")
        raise typer.Exit(code=1)


@app.command()
def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Run all needed installs without asking for confirmation."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print what would be installed; install nothing."),
    skip_helm: bool = typer.Option(False, "--skip-helm", help="Don't install Helm."),
    skip_ollama: bool = typer.Option(False, "--skip-ollama", help="Don't install Ollama or pull a model."),
    model: str | None = typer.Option(
        None, "--model", help="Ollama model to pull. Defaults to KUBE_ASSISTANT_LLM_MODEL or 'llama3.1'."
    ),
):
    """Install missing prerequisites: kubectl, Helm, and Ollama (+ pull a model).

    Detects what `doctor` would flag as missing, builds an install plan using
    Homebrew (macOS) or the official install scripts (Linux), shows you the
    exact commands, and asks for confirmation before running anything. kubectl
    isn't auto-installed on Linux (too distro-specific) — you'll get a link
    instead. Run `kube-assistant doctor` afterwards to confirm everything is
    ready.
    """
    system = platform.system()  # "Darwin" | "Linux" | "Windows"
    console.print(f"[bold]kube-assistant setup[/bold] — detected platform: {system}\n")

    if system not in ("Darwin", "Linux"):
        console.print(
            f"[red]Automated setup isn't supported on {system}.[/red] "
            "See PREREQUISITES.md for manual install instructions."
        )
        raise typer.Exit(code=1)

    is_mac = system == "Darwin"
    if is_mac and shutil.which("brew") is None:
        console.print(
            "[red]Homebrew isn't installed[/red], and it's needed for automated setup on macOS.\n"
            'Install it first: /bin/bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n'
            "Then re-run `kube-assistant setup`."
        )
        raise typer.Exit(code=1)

    plan: list[tuple[str, list[str]]] = []

    # --- kubectl ---
    if shutil.which("kubectl") is None:
        if is_mac:
            plan.append(("Install kubectl (Homebrew)", ["brew", "install", "kubectl"]))
        else:
            console.print(
                "[yellow]kubectl isn't installed.[/yellow] Linux installs are distro-specific — see "
                "https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/ and re-run doctor after."
            )
    else:
        console.print("[dim]kubectl already installed, skipping.[/dim]")

    # --- Helm (optional) ---
    if not skip_helm:
        if shutil.which("helm") is None:
            if is_mac:
                plan.append(("Install Helm (Homebrew)", ["brew", "install", "helm"]))
            else:
                plan.append(
                    (
                        "Install Helm (official script)",
                        ["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"],
                    )
                )
        else:
            console.print("[dim]helm already installed, skipping.[/dim]")

    # --- Ollama ---
    if not skip_ollama and shutil.which("ollama") is None:
        if is_mac:
            plan.append(("Install Ollama (Homebrew)", ["brew", "install", "ollama"]))
        else:
            plan.append(
                ("Install Ollama (official script)", ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
            )
    elif not skip_ollama:
        console.print("[dim]ollama already installed, skipping.[/dim]")

    if plan:
        console.print("\n[bold]Planned installs:[/bold]")
        for desc, cmd in plan:
            console.print(f"  • {desc}: [green]{' '.join(cmd)}[/green]")
    else:
        console.print("\n[dim]Nothing to install via a package manager.[/dim]")

    if dry_run:
        console.print("\n[dim]Dry run — nothing was installed.[/dim]")
    elif plan:
        if not yes:
            proceed = typer.confirm("\nRun these installs now?")
            if not proceed:
                console.print("Aborted. Nothing installed.")
                raise typer.Exit(code=0)
        for desc, cmd in plan:
            console.print(f"\n[dim]Running: {' '.join(cmd)}[/dim]")
            result = subprocess.run(cmd, check=False)            
            if result.returncode != 0:
                console.print(f"[red]Failed: {desc} (exit code {result.returncode})[/red]")

    # --- Pull the Ollama model ---
    if not skip_ollama and shutil.which("ollama") is not None:
        resolved_model = model or LLMConfig.from_env().model
        console.print(f"\n[bold]Checking Ollama model '{resolved_model}'...[/bold]")
        list_result = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=False)
        have_model = list_result.returncode == 0 and resolved_model in list_result.stdout

        if have_model:
            console.print(f"[dim]Model '{resolved_model}' already pulled.[/dim]")
        elif dry_run:
            console.print(f"[dim]Would run: ollama pull {resolved_model}[/dim]")
        else:
            pull_now = yes or typer.confirm(
                f"Pull the '{resolved_model}' model now? (several GB, can take a few minutes)"
            )
            if pull_now:
                pull_result = subprocess.run(["ollama", "pull", resolved_model], check=False)
                if pull_result.returncode != 0:
                    console.print(
                        "[red]Failed to pull the model.[/red] Is the Ollama server running? "
                        "Try `ollama serve` in another terminal, then re-run `kube-assistant setup`."
                    )

    console.print("\n[bold]Setup pass complete.[/bold] Run [green]kube-assistant doctor[/green] to verify.")


@app.command()
def doctor(
    context: str | None = typer.Option(None, "--context", help="kubeconfig context to check."),
    pull_model: bool = typer.Option(
        False, "--pull-model", help="If Ollama is reachable but the configured model isn't pulled, pull it."
    ),
):
    """Check that everything kube-assistant needs is installed and reachable:
    Python version, kubectl, cluster connectivity, and the configured LLM
    backend (Ollama or OpenAI). Run this right after installing, or any time
    something isn't working, to see exactly what's missing.
    """
    console.print("[bold]kube-assistant doctor[/bold] — checking your environment...\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    all_ok = True

    def row(name: str, ok: bool, details: str):
        nonlocal all_ok
        all_ok = all_ok and ok
        mark = "[green]✓ OK[/green]" if ok else "[red]✗ MISSING[/red]"
        table.add_row(name, mark, details)

    # 1. Python version
    py_ok = sys.version_info >= (3, 10)
    row(
        "Python >= 3.10",
        py_ok,
        f"{sys.version.split()[0]}" if py_ok else f"{sys.version.split()[0]} (upgrade required)",
    )

    # 2. kubectl on PATH
    kubectl_path = shutil.which("kubectl")
    row("kubectl on PATH", kubectl_path is not None, kubectl_path or "brew install kubectl")

    # 3. Cluster connectivity
    cluster_ok = False
    cluster_detail = "no cluster reachable"
    try:
        client = K8sClient(kube_context=context)
        namespaces = client.list_namespaces()
        cluster_ok = True
        cluster_detail = f"connected (context={client.context or 'default'}, {len(namespaces)} namespaces)"
    except KubernetesClientError as exc:
        cluster_detail = str(exc)
    except Exception as exc:  # noqa: BLE001
        cluster_detail = f"unexpected error: {exc}"
    row("Kubernetes cluster reachable", cluster_ok, cluster_detail)

    # 4. Helm (optional)
    helm_path = shutil.which("helm")
    row("helm on PATH (optional)", helm_path is not None, helm_path or "only needed for `--kind helm` output")

    # 5. LLM backend
    llm_config = LLMConfig.from_env()
    if llm_config.backend == "ollama":
        try:
            import requests

            resp = requests.get(f"{llm_config.base_url}/api/tags", timeout=3)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            has_model = any(llm_config.model in m for m in models)
            if has_model:
                row("Ollama reachable + model pulled", True, f"{llm_config.model} available")
            else:
                detail = f"Ollama is up, but '{llm_config.model}' isn't pulled (have: {', '.join(models) or 'none'})"
                if pull_model:
                    console.print(f"[dim]Pulling {llm_config.model}... this may take a few minutes.[/dim]")
                    import subprocess

                    subprocess.run(["ollama", "pull", llm_config.model], check=False)
                    row("Ollama reachable + model pulled", True, f"{llm_config.model} pulled just now")
                else:
                    row(
                        "Ollama reachable + model pulled",
                        False,
                        detail + " — run with --pull-model, or `ollama pull " + llm_config.model + "`",
                    )
        except Exception:  # noqa: BLE001
            row(
                "Ollama reachable",
                False,
                "not reachable at " + llm_config.base_url + " — run `ollama serve`, or see PREREQUISITES.md",
            )
    else:
        import os

        env_var = llm_config.required_api_key_env() or "KUBE_ASSISTANT_LLM_API_KEY"
        has_key = bool(os.environ.get(env_var))
        row(
            f"{llm_config.backend} API key set",
            has_key,
            f"{env_var} is set" if has_key else f"export {env_var}=...",
        )

    console.print(table)

    if all_ok:
        console.print("\n[bold green]Everything looks ready.[/bold green] Try: [green]kube-assistant scan[/green]")
    else:
        console.print(
            "\n[bold yellow]Some checks failed.[/bold yellow] Fix the items marked "
            "MISSING above, or see PREREQUISITES.md for full install instructions. "
            "Rule-based diagnosis (`--no-llm`) still works even without an LLM backend."
        )
        raise typer.Exit(code=1)


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio | sse"),
):
    """Start the MCP server so Cursor / Claude Desktop can call this tool."""
    from .mcp_server import mcp

    Console(stderr=True).print(f"Starting kube-assistant-mcp server (transport={transport})...")
    mcp.run(transport=transport)


knowledge_app = typer.Typer(help="Ingest/search local Markdown knowledge (requires the rag extra).")
app.add_typer(knowledge_app, name="knowledge")


@knowledge_app.command("ingest")
def knowledge_ingest(path: Path = typer.Argument(..., exists=True, file_okay=False)):  # noqa: B008
    import json

    from .rag.documents import load_chunks
    from .rag.retrieval import create_retriever
    retriever = None
    try:
        retriever = create_retriever()
        config = retriever.config
        chunks = load_chunks(path, config.chunk_chars, config.overlap)
        console.print(json.dumps(retriever.store.ingest(chunks, retriever.embedder)), markup=False)
    except Exception as exc:  # noqa: BLE001 — sanitize service errors at CLI boundary
        console.print(f"Knowledge ingestion failed ({type(exc).__name__}); check configuration and services.")
        raise typer.Exit(1)
    finally:
        if retriever:
            retriever.close()


@knowledge_app.command("search")
def knowledge_search(query: str):
    import json

    from .rag.retrieval import create_retriever
    retriever = None
    try:
        retriever = create_retriever()
        print(json.dumps(retriever.retrieve(query), indent=2))
    except Exception as exc:  # noqa: BLE001 — sanitize service errors at CLI boundary
        console.print(f"Knowledge search failed ({type(exc).__name__}); check configuration and services.")
        raise typer.Exit(1)
    finally:
        if retriever:
            retriever.close()


if __name__ == "__main__":
    app()
