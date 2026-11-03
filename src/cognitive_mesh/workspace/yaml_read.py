"""The only module that imports PyYAML (ADR-0037). `safe_load` only, with explicit limits.

The text is untrusted. It is checked as a token stream first (nesting depth, alias count), so a
document built to blow up is refused before any object is constructed.
"""

import json
from typing import Any

import yaml

MAX_DEPTH = 50
MAX_ALIASES = 100


def _within_limits(text: str) -> bool:
    depth = 0
    aliases = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent):
            aliases += 1
            if aliases > MAX_ALIASES:
                return False
        elif isinstance(event, yaml.MappingStartEvent | yaml.SequenceStartEvent):
            depth += 1
            if depth > MAX_DEPTH:
                return False
        elif isinstance(event, yaml.MappingEndEvent | yaml.SequenceEndEvent):
            depth -= 1
    return True


def load_document(path: str, text: str) -> Any | None:
    """Parse a `.json`, `.yaml` or `.yml` file. None when it is malformed or over a limit."""
    try:
        if path.lower().endswith(".json"):
            return json.loads(text)
        if not _within_limits(text):
            return None
        return yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError):
        return None
