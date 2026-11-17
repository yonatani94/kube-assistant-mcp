"""Cluster-free synthetic OOM diagnosis; optional real local RAG and chat inference."""
import argparse
import json

from kube_assistant_mcp.diagnostics import diagnose_pod
from kube_assistant_mcp.models import IssueType, PodIssue, PodRef, Severity


class SyntheticEvidence:
    def find_failing_pods(self, namespace):
        return [PodIssue(PodRef('payments-demo', namespace, container='worker'),
                         IssueType.OOM_KILLED, Severity.HIGH, restart_count=3,
                         reason='OOMKilled', last_termination_reason='OOMKilled', exit_code=137)]

    def get_pod_logs(self, *args, **kwargs):
        return 'Synthetic fixture: processing batch\nOOMKilled: memory cgroup out of memory'

    def get_pod_events(self, *args):
        return ['Synthetic fixture: Killing: container exceeded memory limit']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rag', action='store_true')
    parser.add_argument('--llm', action='store_true')
    args = parser.parse_args()
    result = diagnose_pod(SyntheticEvidence(), 'payments-demo', 'demo', use_rag=args.rag, use_llm=args.llm)
    print(json.dumps({'synthetic': True, **result.to_mcp()}, indent=2))


if __name__ == '__main__':
    main()
