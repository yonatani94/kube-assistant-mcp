# kube-assistant-mcp

**Agentic Kubernetes troubleshooting — as a CLI and as an MCP server.**

`kube-assistant-mcp` connects a local LLM (via [Ollama](https://ollama.com)) or a hosted one (OpenAI-compatible) to your Kubernetes cluster. It scans for failing Pods (`CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, ...), correlates logs + events, explains the root cause in plain language, and proposes concrete, runnable fixes — or generates ready-to-use Deployment/Helm manifests.

It ships two ways to use it:

- **CLI** — `kube-assistant scan / diagnose / fix / generate`
- **MCP server** — the same capabilities exposed as tools for **Cursor** or **Claude Desktop**, so an AI agent can diagnose and (with your explicit confirmation) fix your cluster in natural language.

```
$ kube-assistant scan -n production
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Namespace  ┃ Pod          ┃ Issue            ┃ Restarts ┃ Severity ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ production │ api-7f8c9d   │ CrashLoopBackOff │ 14       │ critical │
│ production │ worker-2     │ OOMKilled        │ 3        │ high     │
└────────────┴──────────────┴──────────────────┴──────────┴──────────┘
```

## Why

Debugging a failing Pod is a repetitive, mechanical loop: `describe` → `logs --previous` → `get events` → guess → fix. This tool automates the mechanical part and hands the LLM only the *relevant, structured* context it needs to actually help — instead of dumping a whole terminal session into a chat window.

## Features

- **Detection** — classifies container states (`waiting`/`terminated` reasons) into known failure types with a severity score.
- **Log analysis** — regex-based pattern matching for common root causes (OOM, connection refused, DNS failure, missing env vars, permission errors, bad config, port conflicts, unhandled exceptions) — works even with the LLM turned off.
- **LLM diagnosis** — sends a compact, structured JSON payload (issue + log tail + recent events) to Ollama or an OpenAI-compatible endpoint and gets back a plain-language explanation plus a list of concrete fixes.
- **Guarded auto-fix** — every mutating action (pod restart, resource patch) is dry-run by default and requires an **explicit** `confirm=True` / `--yes` before it touches the cluster.
- **Manifest generation** — emits a Deployment+Service YAML pair, or a minimal, valid Helm chart skeleton.
- **MCP server** — built with [FastMCP](https://github.com/jlowin/fastmcp); drop it into Cursor or Claude Desktop and diagnose your cluster conversationally.

## Installation

### Option A — from PyPI

```bash
pip install kube-assistant-mcp
kube-assistant doctor       # checks Python/kubectl/cluster/Ollama and tells you what's missing
```

### Option B — from source (one command)

```bash
git clone https://github.com/yonatani94/kube-assistant-mcp
cd kube-assistant-mcp
make setup                  # creates a venv, installs everything, runs the doctor check
source .venv/bin/activate
```

Either way, `kube-assistant doctor` is the entry point: it verifies Python 3.10+,
`kubectl` + cluster connectivity, and your configured LLM backend (Ollama or
OpenAI) in one shot, and tells you exactly what to install or run next —
no guessing required.

Requires Python 3.10+ and a working `kubeconfig` (the same one `kubectl` uses).
For LLM-powered diagnosis, either run [Ollama](https://ollama.com) locally
(`ollama pull llama3.1`) or export `OPENAI_API_KEY` and pass `--llm-backend openai`.

New to any of this? See **[PREREQUISITES.md](PREREQUISITES.md)** for full install
instructions (Python, kubectl, Ollama) and a hardware/model-size guide.

## CLI usage

```bash
# List every failing pod in the cluster (or one namespace)
kube-assistant scan -n production

# Deep-dive: logs + events + LLM root-cause explanation + fix suggestions
kube-assistant diagnose api-7f8c9d -n production

# Same, but skip the LLM call and only use the rule-based fixes
kube-assistant diagnose api-7f8c9d -n production --no-llm

# Preview a fix (default: dry-run, no cluster mutation)
kube-assistant fix api-7f8c9d -n production --strategy restart

# Actually apply it (still asks for interactive confirmation unless -y)
kube-assistant fix api-7f8c9d -n production --strategy restart --no-dry-run

# Raise a Deployment's memory limit
kube-assistant fix api-7f8c9d -n production --strategy memory-patch \
    --deployment api --memory-limit 1Gi --no-dry-run

# Generate a plain manifest or a Helm chart
kube-assistant generate myapp --image myrepo/myapp:1.4.0 --kind manifest
kube-assistant generate myapp --image myrepo/myapp:1.4.0 --kind helm
```

## Using it as an MCP server (Cursor / Claude Desktop)

Start it directly:

```bash
kube-assistant serve
```

Or point your MCP client config at it. Example for Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kube-assistant": {
      "command": "kube-assistant",
      "args": ["serve"]
    }
  }
}
```

Exposed tools: `list_failing_pods`, `get_pod_logs`, `diagnose_pod`, `apply_fix` (dry-run by default, needs `confirm=true`), `generate_manifest`.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `KUBE_ASSISTANT_LLM_BACKEND` | `ollama` | `ollama` or `openai` |
| `KUBE_ASSISTANT_LLM_MODEL` | `llama3.1` | Model name |
| `KUBE_ASSISTANT_LLM_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OPENAI_API_KEY` | — | Required when backend=`openai` |

## Architecture

```
CLI (Typer) ──┐
              ├──> K8sClient (kubernetes python client)
MCP (FastMCP)─┘         │
                         ▼
                  LogAnalyzer (regex patterns)
                         │
                         ▼
              RuleBasedFixer  +  LLMClient (Ollama / OpenAI)
                         │
                         ▼
                 AutoFixer (dry-run gated apply)
                         │
                         ▼
                ManifestGenerator (YAML / Helm)
```

## Development

```bash
git clone https://github.com/yonatani94/kube-assistant-mcp
cd kube-assistant-mcp
make setup
pytest
```

The test suite mocks the Kubernetes API and the LLM backend, so `pytest` runs with no cluster and no network access.

## Safety notes

This tool can delete Pods and patch Deployments. It is designed defensively:
- Every write path defaults to `dry_run=True`.
- A mutation only happens when the caller passes both `dry_run=False` **and** `confirm=True` (CLI: `--no-dry-run` + interactive confirm or `--yes`; MCP: `confirm=true`).
- It never deletes namespaces, PVCs, or Secrets.

Still, review the generated plan before confirming, especially in production namespaces.

## License

MIT — see [LICENSE](LICENSE).
