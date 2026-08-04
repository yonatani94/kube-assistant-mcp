from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from kube_assistant_mcp.cli import app
from kube_assistant_mcp.k8s_client import KubernetesClientError

runner = CliRunner()


def test_doctor_reports_missing_cluster_and_llm():
    """When neither the cluster nor Ollama are reachable, doctor should exit
    non-zero and list both as missing, without crashing.
    """
    with patch("kube_assistant_mcp.cli.K8sClient") as mock_client_cls:
        mock_client_cls.side_effect = KubernetesClientError("no kubeconfig found")

        with patch("requests.get", side_effect=ConnectionError("refused")):
            result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "MISSING" in result.stdout


def test_doctor_reports_all_ok_when_everything_reachable():
    fake_client = MagicMock()
    fake_client.list_namespaces.return_value = ["default", "kube-system"]
    fake_client.context = "kind-kind"

    fake_response = MagicMock()
    fake_response.json.return_value = {"models": [{"name": "llama3.1:latest"}]}
    fake_response.raise_for_status.return_value = None

    with patch("kube_assistant_mcp.cli.K8sClient", return_value=fake_client):
        with patch("requests.get", return_value=fake_response):
            result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Everything looks ready" in result.stdout
