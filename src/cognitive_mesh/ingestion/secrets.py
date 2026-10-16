"""Pattern-based secret redaction for ingested text. Best effort: not exhaustive."""

import re
from dataclasses import dataclass

from cognitive_mesh.ingestion.models import Redaction

_PLACEHOLDER_MARKERS = ("your", "example", "changeme", "placeholder", "xxx", "<", "${", "$(", "{{")

# (rule, pattern, group to redact). Order is the tie-break priority for equal-start matches.
_RULES: tuple[tuple[str, re.Pattern[str], int], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
        ),
        0,
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), 0),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9_]{20,}"), 0),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), 0),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), 0),
    (
        "generic_assignment",
        re.compile(
            r"(?i)[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd)[A-Za-z0-9_.-]*"
            r"\s*[=:]\s*[\"']?([^\s\"'#,;]{12,})"
        ),
        1,
    ),
)


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    rule: str
    priority: int


def _is_placeholder(value: str) -> bool:
    lower = value.lower()
    # Values containing "(" are code (calls such as os.environ.get(...)), not literals.
    return "(" in value or any(marker in lower for marker in _PLACEHOLDER_MARKERS)


def redact_secrets(text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Replace likely secrets with `[REDACTED:<rule>]`; report rule and line, never the value."""
    matches: list[_Match] = []
    for priority, (rule, pattern, group) in enumerate(_RULES):
        for m in pattern.finditer(text):
            if rule == "generic_assignment" and _is_placeholder(m.group(group)):
                continue
            matches.append(_Match(m.start(group), m.end(group), rule, priority))
    matches.sort(key=lambda m: (m.start, -(m.end - m.start), m.priority))

    out: list[str] = []
    redactions: list[Redaction] = []
    cursor = 0
    for hit in matches:
        if hit.start < cursor:  # overlaps an earlier, already-redacted span
            continue
        out.append(text[cursor : hit.start])
        out.append(f"[REDACTED:{hit.rule}]")
        redactions.append(Redaction(rule=hit.rule, line=text.count("\n", 0, hit.start) + 1))
        cursor = hit.end
    out.append(text[cursor:])
    return "".join(out), tuple(redactions)
