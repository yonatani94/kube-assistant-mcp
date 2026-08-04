from kube_assistant_mcp.log_analyzer import LogAnalyzer


def test_detects_oom_pattern():
    analyzer = LogAnalyzer()
    log = "starting app\nFatal error: OOMKilled while allocating buffer\n"
    findings = analyzer.analyze(log)
    names = [f.pattern_name for f in findings]
    assert "oom_killed" in names


def test_detects_connection_refused():
    analyzer = LogAnalyzer()
    log = "connecting to db...\ndial tcp 10.0.0.5:5432: connect: connection refused\n"
    findings = analyzer.analyze(log)
    assert any(f.pattern_name == "connection_refused" for f in findings)


def test_detects_missing_env_var():
    analyzer = LogAnalyzer()
    log = "Error: environment variable DATABASE_URL is required but not set"
    findings = analyzer.analyze(log)
    assert any(f.pattern_name == "missing_env_var" for f in findings)


def test_no_findings_on_clean_log():
    analyzer = LogAnalyzer()
    log = "server started on port 8080\nhandling request GET /health -> 200\n"
    findings = analyzer.analyze(log)
    assert findings == []


def test_empty_log_returns_empty_list():
    analyzer = LogAnalyzer()
    assert analyzer.analyze("") == []
    assert analyzer.analyze(None) == []  # type: ignore[arg-type]


def test_tail_relevant_respects_context_lines():
    analyzer = LogAnalyzer()
    log = "\n".join(f"line {i}" for i in range(50))
    tail = analyzer.tail_relevant(log, context_lines=5)
    assert tail.splitlines() == [f"line {i}" for i in range(45, 50)]


def test_multiple_distinct_patterns_detected_together():
    analyzer = LogAnalyzer()
    log = (
        "permission denied opening /var/lib/data\n"
        "panic: runtime error: index out of range\n"
    )
    findings = analyzer.analyze(log)
    names = {f.pattern_name for f in findings}
    assert "permission_denied" in names
    assert "stack_trace_unhandled" in names
