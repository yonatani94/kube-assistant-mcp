"""Small input boundaries; redaction is best effort, not a data-loss prevention system."""

import re
from pathlib import Path

_SECRET = re.compile(
    r"(?i)([\"']?(?:password|passwd|token|api[_-]?key|secret|authorization)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"']*[\"']|[^\s,;]+)"
)


def redact(text: str) -> str:
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,}", "[REDACTED JWT]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    return _SECRET.sub(r"\1[REDACTED]", text)


def validate_name(name: str) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
        raise ValueError("Name must be a DNS label: 1–63 lowercase letters, digits or internal hyphens")


def safe_output_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts)
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Output path escapes configured directory")
    # Reject symlinks even if they currently point inside the directory.
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValueError("Output path contains a symlink")
        current = current.parent
    return candidate


def sanitize(value):
    """Redact string values without altering serialized JSON syntax."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value
