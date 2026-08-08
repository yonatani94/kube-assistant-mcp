# Prerequisites

Everything you need installed and verified *before* using `kube-assistant-mcp`.

## 1. Python 3.10+

```bash
python3 --version
```
If below 3.10:
```bash
brew install python@3.12
```

## 2. kubectl + a working cluster context

```bash
kubectl version --client
kubectl config current-context
kubectl get nodes          # should list at least one Ready node
```
If you don't have a cluster yet, `kind` is the easiest local option:
```bash
brew install kind
kind create cluster
```

## 3. Helm (optional — only needed to actually install/template generated charts)

```bash
brew install helm
helm version
```
Not required for `kube-assistant generate --kind manifest`, only for `--kind helm` if you want to `helm install`/`helm template` the output afterward.

## 4. Ollama (for local LLM diagnosis — optional but recommended)

This is what powers `kube-assistant diagnose` (without `--no-llm`) and the MCP `diagnose_pod` tool's LLM explanation. You can skip this entirely and use `--no-llm` / rule-based diagnosis only, or point the tool at a hosted provider instead (see step 5: OpenAI, Anthropic, Gemini, or Groq).

### Install

**macOS:**
```bash
brew install ollama
```
or download the desktop app: https://ollama.com/download

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Start the Ollama server

```bash
ollama serve
```
Leave this running in its own terminal tab (or, on macOS with the desktop app installed, it runs automatically in the background — check the menu bar icon).

### Pull a model

In a new terminal:
```bash
ollama pull llama3.1
```
This downloads ~4.7GB. Other options if you want something lighter or stronger:

| Model | Size | Notes |
|---|---|---|
| `llama3.2` | ~2GB | Faster, smaller, still decent for structured JSON output |
| `llama3.1` | ~4.7GB | Good default balance |
| `qwen2.5:7b` | ~4.7GB | Strong at following JSON-schema instructions |
| `mistral` | ~4.1GB | Fast, solid general reasoning |

If you use a model other than `llama3.1`, tell the CLI/MCP server:
```bash
kube-assistant diagnose <pod> --llm-model qwen2.5:7b
# or via env var, so you don't need the flag every time:
export KUBE_ASSISTANT_LLM_MODEL=qwen2.5:7b
```

### Verify it's working

```bash
ollama list                 # should show the model you pulled
curl http://localhost:11434/api/tags   # should return JSON, not connection refused
```

### Hardware note

Local LLMs are RAM/CPU (or GPU) hungry. A 7-8B model like `llama3.1` needs roughly 8GB+ of free RAM to run at a usable speed on CPU. If your machine struggles, use `llama3.2` (smaller) or skip local inference and use a hosted provider instead (step 5).

## 5. Alternative: a hosted provider instead of Ollama

If you'd rather not run a local model, kube-assistant supports four hosted
providers as well. Each just needs its API key set:

```bash
# OpenAI
export OPENAI_API_KEY=sk-...
kube-assistant diagnose <pod> --llm-backend openai

# Anthropic (Claude)
export ANTHROPIC_API_KEY=sk-ant-...
kube-assistant diagnose <pod> --llm-backend anthropic

# Google Gemini
export GEMINI_API_KEY=...
kube-assistant diagnose <pod> --llm-backend gemini

# Groq (fast + cheap, hosts open models like Llama behind an OpenAI-style API)
export GROQ_API_KEY=gsk_...
kube-assistant diagnose <pod> --llm-backend groq
```

Or set one as the persistent default so you don't need `--llm-backend` on
every command:
```bash
export KUBE_ASSISTANT_LLM_BACKEND=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

Each backend has a sensible default model (see the table in the main
[README](README.md#configuration)); override it with `--llm-model` or
`KUBE_ASSISTANT_LLM_MODEL` if you want a different one. `kube-assistant doctor`
checks that the correct API key env var is set for whichever backend is active.

## 6. Quick end-to-end sanity check

Once everything above is installed:
```bash
kubectl get nodes                     # cluster reachable
ollama list                           # model available (skip if using a hosted provider)
kube-assistant scan                   # tool + cluster wired up correctly
```
If all three run without errors, you're ready to follow `TESTING_LOCAL.md`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `kube-assistant diagnose` hangs for a long time then errors | Ollama server not running | Run `ollama serve` in another terminal |
| `LLM unavailable: Connection refused` in the diagnosis output | Ollama not running, or wrong `KUBE_ASSISTANT_LLM_BASE_URL` | `ollama serve`, or check the env var (default `http://localhost:11434`) |
| Diagnosis is very slow (30s+) | Model too large for your hardware | Switch to `llama3.2` or a smaller model |
| `ollama: command not found` | Ollama not installed or not on PATH | Re-run the install step; restart your terminal |
| MCP server in Cursor/Claude Desktop can't reach Ollama | The MCP server process doesn't inherit your shell's env vars | Add `KUBE_ASSISTANT_LLM_*` explicitly inside the `env` block of your MCP client config (see `examples/claude_desktop_config.json`) |
