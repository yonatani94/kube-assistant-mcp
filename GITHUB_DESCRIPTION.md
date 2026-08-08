# GitHub Project Description Kit

Copy-paste content for the repo's "About" section, topics, and social preview.

---

## Short description (GitHub "About" field, appears under the repo name)

Use ONE of these (GitHub limits this field to ~350 characters):

**Option A (feature-forward):**
> Agentic Kubernetes troubleshooting CLI + MCP server. Detects CrashLoopBackOff/OOMKilled pods, explains root cause via a local LLM (Ollama) or hosted providers (OpenAI/Anthropic/Gemini/Groq), and proposes guarded auto-fixes or generates Helm manifests. Built for Cursor & Claude Desktop.

**Option B (short/punchy):**
> AI-powered `kubectl` copilot: an MCP server + CLI that diagnoses failing Kubernetes pods with a local LLM and suggests (or applies, with confirmation) fixes.

---

## Topics (GitHub repo tags — add these under "About" → gear icon)

```
kubernetes
mcp
model-context-protocol
llm
ollama
openai
anthropic
gemini
cli
devops
sre
cursor
claude
kubectl
helm
python
ai-agent
observability
troubleshooting
```

---

## Website field

Point it at the PyPI page once published:
```
https://pypi.org/project/kube-assistant-mcp/
```

---

## Full "About this project" writeup (for LinkedIn / portfolio site / repo wiki)

### Kube-Assistant MCP Agent

**The problem:** debugging a failing Kubernetes pod is a repetitive manual
loop — `kubectl describe`, `kubectl logs --previous`, `kubectl get events`,
cross-reference, guess, fix. It's mechanical, and most of that mechanical
work can be automated so a human (or an AI agent) only has to make the
actual judgment call.

**What I built:** a Python CLI and an MCP (Model Context Protocol) server
that together let a local LLM (via Ollama) or a hosted one (OpenAI,
Anthropic, Google Gemini, or Groq) — used directly, or through an agent
inside Cursor / Claude Desktop:

1. **Scan** a cluster or namespace and classify every failing pod
   (`CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, config errors) with
   a severity score, based on container status + restart history.
2. **Correlate** that with a regex-based log analyzer that recognizes ~10
   common root-cause signatures (OOM, connection refused, DNS failures,
   missing env vars, permission errors, bad config, port conflicts,
   unhandled exceptions/panics) — this works even with the LLM turned off.
3. **Diagnose** by sending a compact, structured JSON payload (not a raw
   log dump) to the LLM and getting back a plain-language root cause plus
   concrete, runnable `kubectl` commands.
4. **Fix**, carefully: every mutating action (pod restart, resource patch)
   is dry-run by default and only touches the live cluster when the caller
   passes an explicit confirmation — whether that caller is a human typing
   `--yes` or an AI agent passing `confirm=true`.
5. **Generate** ready-to-use Deployment/Service manifests or a minimal Helm
   chart for a new or replacement workload.

**Why MCP:** rather than building a chatbot wrapper, I exposed the same
capabilities as typed MCP tools (`list_failing_pods`, `diagnose_pod`,
`apply_fix`, `generate_manifest`) using FastMCP. That means any MCP-aware
client — Cursor, Claude Desktop, or a custom agent — can call this tool
directly, with the model choosing which tool to invoke and when, instead of
me hand-rolling a prompt-parsing layer.

**Stack:** Python, FastAPI-style typed tool definitions via FastMCP, Typer +
Rich for the CLI, the official `kubernetes` Python client, and a pluggable
LLM layer (Ollama, OpenAI, Anthropic, Gemini, Groq) for diagnosis, PyYAML
for manifest generation.
Packaged and published as an installable PyPI package
(`pip install kube-assistant-mcp`), with CI (GitHub Actions) running the
test suite against every PR and auto-publishing to PyPI on tagged releases.

**Engineering choices worth calling out:**
- The Kubernetes and LLM clients are fully mockable — the test suite runs
  with zero cluster access and zero network calls.
- Safety is structural, not just a warning in the README: `AutoFixer.apply()`
  requires *both* `dry_run=False` and `confirm=True` to mutate anything;
  any other combination is a guaranteed no-op.
- The LLM prompt sends structured JSON (issue metadata + log tail + recent
  events), not a raw terminal dump — smaller context, more consistent
  parsing, and it works the same whether the backend is a local 8B model or
  GPT-4-class.
- The LLM layer is a small `LLMBackend` abstract base class with one
  subclass per provider (including a shared `OpenAICompatibleBackend` for
  OpenAI/Groq, since they speak the same wire format) — adding a sixth
  provider is a ~15-line class, not a refactor.

Repo: https://github.com/YOUR_USERNAME/kube-assistant-mcp
PyPI: https://pypi.org/project/kube-assistant-mcp/
