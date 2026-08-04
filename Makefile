# One-command bootstrap for anyone cloning this repo.
# `pip install kube-assistant-mcp` users get `kube-assistant doctor` directly;
# this Makefile is for people working from the source repo.

.PHONY: setup test lint doctor demo clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:  ## Create a venv, install the package + dev deps, then run the doctor check.
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"
	@echo "Running environment check..."
	$(VENV)/bin/kube-assistant doctor || true

test:  ## Run the test suite (mocked — no cluster/network needed).
	$(VENV)/bin/pytest -v

lint:  ## Run ruff.
	$(VENV)/bin/ruff check src tests

doctor:  ## Re-check that Python/kubectl/cluster/Ollama are all ready.
	$(VENV)/bin/kube-assistant doctor

demo:  ## Deploy the intentionally-broken demo app to your current cluster context.
	kubectl apply -f examples/crashy-demo-app.yaml
	@echo "Deployed. Run 'kubectl get pods -w' and then 'kube-assistant scan' once it's crash-looping."

clean:  ## Remove the venv and generated artifacts.
	rm -rf $(VENV) generated dist build *.egg-info
