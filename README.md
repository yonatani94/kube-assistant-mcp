# kube-assistant-mcp

**Agentic Kubernetes troubleshooting — as a CLI and as an MCP server.**

`kube-assistant-mcp` connects a local LLM (via [Ollama](https://ollama.com)) or a hosted one (OpenAI-compatible) to your Kubernetes cluster. It scans for failing Pods (`CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, ...), correlates logs + events, and explains the root cause in plain language — or generates ready-to-use Deployment/Helm manifests.

It's built for **tier 1/2 support engineers** who get limited, read-only cluster access and aren't trained or authorized to change Kubernetes resources themselves. This tool never mutates the cluster it's pointed at — instead, once it has a diagnosis, it can open a **Jira bug ticket** with the root cause and a recommended fix, so an engineer with write access can act on it. That turns "I don't know k8s well enough to fix this safely" into a five-minute, well-documented handoff instead of a blocked ticket queue.

It ships two ways to use it:

- **CLI** — `kube-assistant scan / diagnose / open-ticket / generate`
- **MCP server** — the same capabilities exposed as tools for **Cursor** or **Claude Desktop**, so an AI agent can diagnose your cluster and file the ticket in natural language.

![kube-assistant-mcp demo: a plain-English request triggers scanning and diagnosing a failing pod, then filing a Jira ticket with the root cause and fix, ending on the real Jira ticket page](docs/demo.gif)

*A plain-English request → the agent scans and diagnoses a real failing pod → files a Jira ticket with the root cause and exact fix → the real ticket in Jira.*

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
- **Jira ticket creation** — once a pod is diagnosed, open a Jira bug ticket with the root cause and a recommended fix in one call — the cluster itself is never touched.
- **Manifest generation** — emits a Deployment+Service YAML pair, or a minimal, valid Helm chart skeleton.
- **MCP server** — built with [FastMCP](https://github.com/jlowin/fastmcp); drop it into Cursor or Claude Desktop and diagnose your cluster conversationally.

## Installation

### Option A — from PyPI

```bash
pip install kube-assistant-mcp
kube-assistant setup       # installs missing prerequisites (kubectl, Helm, Ollama + model) via Homebrew/official scripts
kube-assistant doctor      # verifies everything is ready
```

### Option B — from source (one command)

```bash
git clone https://github.com/yonatani94/kube-assistant-mcp
cd kube-assistant-mcp
make setup                  # creates a venv, installs the Python package, runs the doctor check
source .venv/bin/activate
kube-assistant setup        # installs missing system tools (kubectl/Helm/Ollama) — different from `make setup` above
```

`make setup` bootstraps the *Python* side (venv + package). `kube-assistant setup`
installs the *system* prerequisites the package needs to actually talk to a
cluster and an LLM. Either way, `kube-assistant doctor` is the always-safe,
read-only check: it verifies Python 3.10+, `kubectl` + cluster connectivity,
and your configured LLM backend (Ollama or OpenAI), and tells you exactly
what's missing. `kube-assistant setup` is the read-write counterpart — it
offers to actually install what's missing (with a dry-run mode and a
confirmation prompt before anything runs).

Requires Python 3.10+ and a working `kubeconfig` (the same one `kubectl` uses).
For LLM-powered diagnosis, run [Ollama](https://ollama.com) locally
(`ollama pull llama3.1`, the default — no API key needed), or point at a
hosted provider instead: OpenAI, Anthropic (Claude), Google Gemini, or Groq —
see [Configuration](#configuration) below.

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

# Diagnose, then open a Jira bug ticket with the root cause + recommended
# fix (asks for interactive confirmation unless -y). Requires Jira config —
# see "Jira configuration" below.
kube-assistant open-ticket api-7f8c9d -n production

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

Exposed tools: `list_failing_pods`, `get_pod_logs`, `diagnose_pod`, `open_jira_ticket`, `generate_manifest`. None of these mutate the cluster — the server is read-only.

## Configuration

kube-assistant supports five LLM backends. Set `KUBE_ASSISTANT_LLM_BACKEND`
to pick one — everything else (model default, which API key to read) follows
automatically:

| Backend | `KUBE_ASSISTANT_LLM_BACKEND` | API key env var | Default model |
|---|---|---|---|
| Ollama (local, default) | `ollama` | *none needed* | `llama3.1` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Anthropic (Claude) | `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | `gemini-2.0-flash` |
| Groq | `groq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

```bash
# Example: use Claude instead of a local model
export KUBE_ASSISTANT_LLM_BACKEND=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
kube-assistant diagnose <pod> -n <namespace>

# Or per-command, without changing your shell's defaults:
kube-assistant diagnose <pod> --llm-backend gemini --llm-model gemini-2.0-flash
```

Other env vars:

| Env var | Default | Purpose |
|---|---|---|
| `KUBE_ASSISTANT_LLM_MODEL` | *(per-backend, see table above)* | Overrides the default model for whichever backend is active |
| `KUBE_ASSISTANT_LLM_BASE_URL` | `http://localhost:11434` | Ollama endpoint only |
| `KUBE_ASSISTANT_LLM_API_KEY` | — | Generic override — takes priority over the backend-specific key env var above |
| `KUBE_ASSISTANT_LLM_TIMEOUT` | `60` | Request timeout in seconds |

Model names and aliases change over time — the defaults above are reasonable
starting points, not guarantees; override with `KUBE_ASSISTANT_LLM_MODEL` if
your provider has moved on. `kube-assistant doctor` checks that the right
API key is set for whichever backend you've configured.

Adding a sixth provider is one small class in `llm_client.py`: subclass
`LLMBackend` (or `OpenAICompatibleBackend` if it speaks the OpenAI chat
shape) and register it in `_BACKEND_CLASSES`.

### Jira configuration

`open_jira_ticket` (MCP tool) and `kube-assistant open-ticket` (CLI) file a
ticket in **Jira Cloud**. Set these env vars:

| Env var | Required | Purpose |
|---|---|---|
| `JIRA_URL` | yes | Your Jira Cloud site, e.g. `https://yourcompany.atlassian.net` |
| `JIRA_EMAIL` | yes | Account email tied to the API token |
| `JIRA_API_TOKEN` | yes | API token — create one at [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_PROJECT_KEY` | yes | Default project to file tickets in, e.g. `OPS` (the CLI's `--project` flag overrides this per call) |
| `JIRA_ISSUE_TYPE` | no (default `Bug`) | Issue type name, must exist in the target project |
| `JIRA_TIMEOUT` | no (default `30`) | Request timeout in seconds |

```bash
export JIRA_URL=https://yourcompany.atlassian.net
export JIRA_EMAIL=you@yourcompany.com
export JIRA_API_TOKEN=ATATT3x...
export JIRA_PROJECT_KEY=OPS
kube-assistant open-ticket api-7f8c9d -n production
```

## Architecture

```
CLI (Typer) ──┐
              ├──> K8sClient (kubernetes python client, read-only)
MCP (FastMCP)─┘         │
                         ▼
                  LogAnalyzer (regex patterns)
                         │
                         ▼
              RuleBasedFixer  +  LLMClient (Ollama / OpenAI)
                         │
                         ▼
                JiraClient (opens a bug ticket, never touches the cluster)
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

This tool is **read-only against the cluster** — it never deletes, patches,
or otherwise mutates anything it's pointed at. It only ever calls `list`,
`get`, `logs`, and `events` against the Kubernetes API, and the only
externally-visible side effect it can produce at all is opening a Jira
ticket (an explicit, separate call — `open_jira_ticket` / `open-ticket`).

This is intentional: the tool is designed for support tiers who have
limited, read-only cluster access by policy, not as an implementation
detail. A minimal RBAC `ClusterRole` covering everything this tool needs:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kube-assistant-readonly
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events", "namespaces"]
    verbs: ["get", "list", "watch"]
```

Bind that (not `edit`/`admin`) to the ServiceAccount or user running
`kube-assistant`/the MCP server, and there is no code path — bug or
otherwise — that can change your cluster's state.

## License

MIT — see [LICENSE](LICENSE).
