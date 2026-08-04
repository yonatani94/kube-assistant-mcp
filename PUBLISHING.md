# Publishing Guide

Step-by-step for getting this project from local files to a real GitHub repo
and a real, installable PyPI package. Run these on your own machine (this
sandbox has no network access, so it can't push/publish for you).

## 1. Push to GitHub

```bash
cd kube-assistant-mcp

# Replace placeholders first (see "Before you publish" below), then:
git init
git add .
git commit -m "Initial commit: kube-assistant-mcp CLI + MCP server"
git branch -M main

# Create the repo on GitHub first (via github.com/new, name: kube-assistant-mcp),
# then:
git remote add origin https://github.com/yonatani94/kube-assistant-mcp.git
git push -u origin main
```

## 2. Before you publish — replace placeholders

- `pyproject.toml`: `authors` (name/email), `project.urls` (your GitHub username).
- `LICENSE`: copyright name.
- `README.md`: the `git clone` URL and the Claude Desktop config path if you customize it.

## 3. Local sanity check before publishing

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -v
```

Try the CLI against a real (even local, e.g. kind/minikube) cluster:

```bash
kube-assistant scan
kube-assistant diagnose <pod-name> -n default --no-llm
```

## 4. Publish to PyPI

### Option A — manual, one-off

```bash
pip install build twine
python -m build                 # creates dist/*.whl and dist/*.tar.gz
twine check dist/*
twine upload dist/*             # will prompt for a PyPI API token
```

You'll need a PyPI account and an API token: https://pypi.org/manage/account/token/

### Option B — automatic via GitHub Actions (recommended)

This repo already ships `.github/workflows/ci.yml`, which builds and publishes
automatically whenever you push a tag like `v0.1.0`, using PyPI's
["Trusted Publishing"](https://docs.pypi.org/trusted-publishers/) (no stored
secrets needed).

One-time setup on pypi.org:
1. Create the project on PyPI once manually (Option A, or reserve the name).
2. Go to your project → Settings → Publishing → "Add a new publisher".
3. Fill in: GitHub owner = your username, repository = `kube-assistant-mcp`,
   workflow = `ci.yml`, environment = `pypi`.

Then, to ship a new version:

```bash
# bump version in pyproject.toml first, e.g. 0.1.0 -> 0.1.1
git add pyproject.toml
git commit -m "Bump version to 0.1.1"
git tag v0.1.1
git push origin main --tags
```

GitHub Actions will run the tests and, if they pass, publish to PyPI automatically.

## 5. After publishing

```bash
pip install kube-assistant-mcp
kube-assistant --help
```

Add the PyPI badge to your README once published:

```markdown
[![PyPI](https://img.shields.io/pypi/v/kube-assistant-mcp)](https://pypi.org/project/kube-assistant-mcp/)
```
