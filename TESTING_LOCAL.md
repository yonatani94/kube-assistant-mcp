# Testing kube-assistant-mcp against a local `kind` cluster

Run all of this on YOUR machine (not in a remote sandbox) — it needs to see
your local kubeconfig and Docker daemon.

## 0. Prerequisites check

```bash
kubectl config current-context     # should show your kind context, e.g. kind-kind
kubectl get nodes                  # should list your kind node(s) as Ready
```

If `current-context` isn't the kind cluster, either switch:
```bash
kubectl config use-context kind-<your-cluster-name>
```
or pass `--context kind-<your-cluster-name>` to every `kube-assistant` command below.

## 1. Install the package locally (editable mode, from the unzipped project)

```bash
cd kube-assistant-mcp
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Confirm the CLI is on your PATH:
```bash
kube-assistant --help
```

## 2. Sanity check: scan on a healthy cluster

```bash
kube-assistant scan
```
Expected: "No failing pods detected." (assuming your kind cluster is currently healthy).

## 3. Deploy the intentionally-broken demo app

This reproduces `OOMKilled` → `CrashLoopBackOff` reliably (it allocates memory
past its 64Mi limit, and its startup log includes a fake "connection refused"
line so the log-pattern analyzer has something to catch too):

```bash
kubectl apply -f examples/crashy-demo-app.yaml
kubectl get pods -w        # watch it start crash-looping; Ctrl+C once you see CrashLoopBackOff
```

## 4. Run kube-assistant against it

```bash
# 1) Detection
kube-assistant scan

# 2) Rule-based diagnosis only (no LLM needed, works immediately)
kube-assistant diagnose <pod-name> --no-llm
#    e.g. kube-assistant diagnose crashy-demo-6f9c8d7b5-abcde --no-llm

# 3) Full diagnosis with a local LLM (needs Ollama running: `ollama serve` + `ollama pull llama3.1`)
kube-assistant diagnose <pod-name>

# 4) Diagnose, then open a Jira bug ticket with the root cause + recommended
#    fix (needs Jira env vars set — see README's "Jira configuration")
kube-assistant open-ticket <pod-name>
```

Get the exact pod name from step 1's `scan` output or `kubectl get pods`.

## 5. Test manifest generation

```bash
kube-assistant generate demo-app --image nginx:1.27 --kind manifest
cat generated/demo-app.yaml
kubectl apply -f generated/demo-app.yaml --dry-run=client   # validates against the API server

kube-assistant generate demo-app --image nginx:1.27 --kind helm
ls generated/demo-app/
# if you have helm installed:
helm template generated/demo-app/
```

## 6. Test the MCP server directly (without Cursor/Claude Desktop)

FastMCP servers speak stdio JSON-RPC, so the easiest manual check is the
built-in MCP inspector:

```bash
pip install "mcp[cli]"       # provides `mcp` dev tooling if not already present
npx @modelcontextprotocol/inspector kube-assistant serve
```

This opens a local web UI where you can call `list_failing_pods`,
`diagnose_pod`, etc. by hand and see the raw JSON response — the fastest way
to confirm the tool schema and outputs look right before wiring it into
Cursor/Claude Desktop.

## 7. Test it inside Claude Desktop / Cursor

1. Copy `examples/claude_desktop_config.json` into your client's MCP config
   (see the path noted inside that file).
2. Restart the client.
3. Ask it something like: *"Check my kind cluster for failing pods and tell
   me why crashy-demo is crash-looping."* — the agent should call
   `list_failing_pods` then `diagnose_pod` on its own.
4. Ask for recommended remediation. The server cannot apply it; there is no
   `confirm=true` apply tool. An authorized human uses the approved deployment process.

## 8. Clean up

```bash
kubectl delete -f examples/crashy-demo-app.yaml
rm -rf generated/
```

---

### If something breaks

Paste me the exact command you ran and the terminal output (including any
Python traceback) — I can debug from that even without direct cluster access.
Common early issues:
- `ModuleNotFoundError: kubernetes` → `pip install -e ".[dev]"` didn't finish; rerun it.
- `Could not load kube config` → wrong `--context`, or `KUBECONFIG` env var points elsewhere.
- Ollama diagnosis hangs/times out → confirm `ollama serve` is running and `ollama list` shows the model.

For cluster-free RAG verification, see [the Week 1 guide](README.md#local-rag-setup-and-operation).
