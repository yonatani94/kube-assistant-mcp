"""Shared deterministic diagnosis with independently optional retrieval and inference."""

from dataclasses import asdict, dataclass, field

from .fixer import RuleBasedFixer
from .llm_client import LLMClient, LLMConfig
from .log_analyzer import LogAnalyzer
from .models import FixSuggestion, PodIssue
from .rag.retrieval import create_retriever, rag_enabled
from .security import redact, sanitize


@dataclass
class DiagnosisResult:
    issue: PodIssue
    findings_summary: str
    suggestions: list[FixSuggestion]
    llm_explanation: str | None
    llm_label: str | None
    findings: list = field(default_factory=list)
    rule_suggestions: list = field(default_factory=list)
    llm_suggestions: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    retrieved_guidance: list = field(default_factory=list)
    citation_ids: list = field(default_factory=list)
    rag_status: str = "disabled"
    llm_status: str = "disabled"

    def to_mcp(self):
        def fixes(items):
            return [
                {
                    "title": s.title,
                    "explanation": s.explanation,
                    "commands": s.commands,
                    "manifest_yaml": s.manifest_yaml,
                    "risk": s.risk.value,
                }
                for s in items
            ]

        result = {
            "issue": {
                "type": self.issue.issue_type.value,
                "severity": self.issue.severity.value,
                "restart_count": self.issue.restart_count,
                "reason": self.issue.reason,
            },
            "log_findings": [
                {"pattern": f.pattern_name, "description": f.description, "line": redact(f.matched_line)}
                for f in self.findings
            ],
            "rule_based_fixes": fixes(self.rule_suggestions),
            "evidence": self.evidence,
            "retrieved_guidance": self.retrieved_guidance,
            "citation_ids": self.citation_ids,
            "rag_status": self.rag_status,
            "llm_status": self.llm_status,
        }
        if self.llm_status != "disabled":
            result.update(llm_explanation=self.llm_explanation, llm_fixes=fixes(self.llm_suggestions))
        return result


def diagnose_pod(
    client,
    pod_name,
    namespace="default",
    use_llm=True,
    llm_backend=None,
    llm_model=None,
    use_rag=None,
    retriever=None,
):
    issues = [i for i in client.find_failing_pods(namespace) if i.pod.name == pod_name]
    if not issues:
        return None
    issue = issues[0]
    logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container, previous=True)
    if not logs:
        logs = client.get_pod_logs(pod_name, namespace, container=issue.pod.container)
    events = client.get_pod_events(pod_name, namespace)
    findings = LogAnalyzer().analyze(logs)
    summary = "; ".join(f"{f.pattern_name}: {f.description}" for f in findings)
    rules = RuleBasedFixer().suggest(issue)
    evidence = [{"id": "status-1", "kind": "pod_status", "value": sanitize(asdict(issue))}]
    evidence += [
        {"id": f"log-{i}", "kind": "log_match", "value": redact(f.matched_line)}
        for i, f in enumerate(findings, 1)
    ]
    evidence += [
        {"id": f"event-{i}", "kind": "event", "value": redact(e[:1000])}
        for i, e in enumerate(events[-10:], 1)
    ]
    result = DiagnosisResult(
        issue, summary, list(rules), None, None, findings=findings, rule_suggestions=rules, evidence=evidence
    )
    should_retrieve = use_rag if use_rag is not None else rag_enabled()
    if should_retrieve:
        owned = retriever is None
        try:
            retriever = retriever or create_retriever()
            result.retrieved_guidance = retriever.retrieve(f"{issue.issue_type.value}: {summary}")
            result.rag_status = "available" if result.retrieved_guidance else "no_matches"
        except Exception:  # noqa: BLE001 — optional subsystem isolation
            result.rag_status = "unavailable"
        finally:
            if owned and retriever:
                try:
                    retriever.close()
                except Exception:  # noqa: BLE001 — cleanup must not discard the report
                    result.rag_status = "unavailable"
    if use_llm:
        result.llm_status = "unavailable"
        result.llm_explanation = "(LLM unavailable: configuration or inference failed)"
        try:
            config = LLMConfig.from_env(backend=llm_backend, model=llm_model)
            result.llm_label = f"{config.backend}/{config.model}"
            diagnosis = LLMClient(config).diagnose(
                issue, logs, events, summary, retrieved_guidance=result.retrieved_guidance
            )
            result.llm_explanation = diagnosis.llm_explanation
            result.llm_suggestions = diagnosis.suggestions
            result.suggestions = diagnosis.suggestions + rules
            result.citation_ids = diagnosis.citation_ids
            result.llm_status = "available" if diagnosis.source == "llm" else "unavailable"
        except Exception:  # noqa: BLE001 — optional subsystem isolation
            result.llm_status = "unavailable"
    return result
