"""Pattern- and entropy-based secret redaction for ingested text. Best effort: not exhaustive."""

import math
import re
from collections import Counter
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
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0),
    ("stripe_key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}"), 0),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), 0),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        0,
    ),
    # user:password@host in a URL; only the password is replaced.
    ("url_credentials", re.compile(r"://[^\s/:@]+:([^\s/@]{4,})@"), 1),
    (
        "generic_assignment",
        re.compile(
            r"(?i)[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd)[A-Za-z0-9_.-]*"
            r"\s*[=:]\s*[\"']?([^\s\"'#,;]{12,})"
        ),
        1,
    ),
)


# Long unbroken runs that look random (mixed case, digits, high entropy) even without a key name.
# Hex-only runs are left alone on purpose: git/sha256 hashes are everywhere and not secrets.
# Everything here is a heuristic: it misses short or low-entropy secrets and can redact an
# innocent random id.
_RANDOM_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/_=-])")
_MIN_ENTROPY_BITS = 4.2


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _has_sequence(value: str, length: int = 6) -> bool:
    """True for alphabet-like runs (abcdef, 012345): character tables are not secrets."""
    run = 1
    for prev, cur in zip(value, value[1:], strict=False):
        run = run + 1 if ord(cur) == ord(prev) + 1 else 1
        if run >= length:
            return True
    return False


def _looks_random(value: str) -> bool:
    return not _has_sequence(value) and (
        any(c.islower() for c in value)
        and any(c.isupper() for c in value)
        and any(c.isdigit() for c in value)
        and _entropy(value) >= _MIN_ENTROPY_BITS
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
    entropy_priority = len(_RULES)
    for m in _RANDOM_TOKEN.finditer(text):
        if _looks_random(m.group(0)):
            matches.append(_Match(m.start(), m.end(), "high_entropy_token", entropy_priority))
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
