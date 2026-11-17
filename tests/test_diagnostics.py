import asyncio
from unittest.mock import Mock

from typer.testing import CliRunner

from kube_assistant_mcp.cli import app
from kube_assistant_mcp.diagnostics import diagnose_pod
from kube_assistant_mcp.models import IssueType, PodIssue, PodRef, Severity


def fake_client():
    client = Mock()
    client.find_failing_pods.return_value = [PodIssue(PodRef('app', 'default', container='main'),
                                                    IssueType.OOM_KILLED, Severity.HIGH)]
    client.get_pod_logs.return_value = 'OOMKilled password=secret'
    client.get_pod_events.return_value = ['Killing: container stopped']
    return client


def test_disabled_ai_never_constructs_services(mocker):
    factory = mocker.patch('kube_assistant_mcp.diagnostics.create_retriever')
    llm = mocker.patch('kube_assistant_mcp.diagnostics.LLMClient')
    result = diagnose_pod(fake_client(), 'app', use_llm=False, use_rag=False)
    assert result.suggestions and result.rag_status == result.llm_status == 'disabled'
    factory.assert_not_called()
    llm.assert_not_called()
    assert 'secret' not in str(result.evidence)


def test_ai_failures_keep_rules_and_evidence(mocker):
    mocker.patch('kube_assistant_mcp.diagnostics.create_retriever', side_effect=TimeoutError)
    mocker.patch('kube_assistant_mcp.diagnostics.LLMClient', side_effect=ValueError('secret'))
    result = diagnose_pod(fake_client(), 'app', use_rag=True)
    assert result.rag_status == result.llm_status == 'unavailable'
    assert result.suggestions and result.evidence
    assert 'secret' not in str(result.to_mcp())


def test_guidance_available_without_llm():
    retriever = Mock()
    retriever.retrieve.return_value = [{'chunk_id': 'id', 'source': 'oom.md', 'text': 'Check memory.'}]
    result = diagnose_pod(fake_client(), 'app', use_llm=False, use_rag=True, retriever=retriever)
    assert result.rag_status == 'available'
    assert result.retrieved_guidance[0]['source'] == 'oom.md'
    assert all(e['kind'] != 'guidance' for e in result.evidence)
    assert not result.citation_ids  # Retrieval alone is not a model citation.


def test_cli_and_mcp_share_deterministic_behavior(mocker):
    from fastmcp import Client

    from kube_assistant_mcp.mcp_server import mcp
    mocker.patch('kube_assistant_mcp.cli._get_client', return_value=fake_client())
    mocker.patch('kube_assistant_mcp.mcp_server._client', return_value=fake_client())
    result = CliRunner().invoke(app, ['diagnose', 'app', '--no-llm', '--no-rag'])
    assert result.exit_code == 0, result.exception
    assert 'OOMKilled' in result.stdout
    assert 'Raise the container memory limit' in result.stdout

    async def call():
        async with Client(mcp) as c:
            tools = await c.list_tools()
            assert len(tools) == 5
            output = await c.call_tool('diagnose_pod', {'pod_name': 'app', 'use_llm': False, 'use_rag': False})
            return output.data
    data = asyncio.run(call())
    assert data['issue']['type'] == 'OOMKilled'
    assert data['rule_based_fixes'][0]['title'] == 'Raise the container memory limit'
    assert data['rag_status'] == 'disabled'
    assert 'llm_fixes' not in data


def test_no_issue_contract():
    client = fake_client()
    client.find_failing_pods.return_value = []
    assert diagnose_pod(client, 'app', use_llm=False) is None
    client.get_pod_logs.assert_not_called()


def test_investigation_only_calls_read_operations():
    client = fake_client()
    diagnose_pod(client, 'app', use_llm=False, use_rag=False)
    assert {call[0] for call in client.mock_calls} <= {'find_failing_pods', 'get_pod_logs', 'get_pod_events'}


def test_rag_reaches_model_prompt(mocker):
    import json
    backend = Mock(complete=Mock(return_value='{"explanation":"Check memory", "citation_ids":["known"]}'))
    from kube_assistant_mcp.llm_client import LLMClient
    mocker.patch('kube_assistant_mcp.diagnostics.LLMClient', return_value=LLMClient(backend=backend))
    retriever = Mock()
    retriever.retrieve.return_value = [{'chunk_id': 'known', 'text': 'Check measured memory limits.'}]
    result = diagnose_pod(fake_client(), 'app', use_rag=True, retriever=retriever)
    assert result.citation_ids == ['known']
    assert json.loads(backend.complete.call_args.args[1])['retrieved_guidance'] == retriever.retrieve.return_value


def test_stdio_transport_discovers_existing_tools():
    import sys
    from pathlib import Path

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    async def discover():
        transport = StdioTransport(command=sys.executable,
                                   args=['-m', 'kube_assistant_mcp.cli', 'serve'],
                                   cwd=str(Path(__file__).resolve().parents[1]))
        async with Client(transport, timeout=10) as client:
            return {tool.name for tool in await client.list_tools()}

    assert asyncio.run(discover()) == {'list_failing_pods', 'get_pod_logs', 'diagnose_pod',
                                       'open_jira_ticket', 'generate_manifest'}
