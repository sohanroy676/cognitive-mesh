"""The text embedded for a decision, and the check that a hit still matches its row (ADR-0039).

Decisions live in their own collection with their own text format version, so the experiences
collection and its `TEXT_FORMAT_VERSION` are untouched. The id of a decision is already a digest of
its record; the text hash additionally pins what was embedded.
"""

import hashlib
from collections.abc import Mapping
from typing import Final

from cognitive_mesh.decisions.models import CitationKind, Decision, DecisionListing
from cognitive_mesh.memory.models import DecisionDocument, SourceCheck

DECISION_TEXT_FORMAT_VERSION: Final = 1
MAX_DECISION_TEXT_CHARS: Final = 8000  # part of the format: longer text is cut, deterministically


def decision_document(decision: Decision) -> DecisionDocument:
    cited = ", ".join(
        f"{c.kind.value}:{c.target}"
        for c in decision.citations
        if c.kind is not CitationKind.DECISION
    )
    lines = [
        f"summary: {decision.summary}",
        f"options: {' | '.join(decision.options)}",
        f"chosen: {decision.chosen}",
        f"reasoning: {decision.reasoning}",
        f"cites: {cited}",
        f"basis: {decision.basis.value}",
    ]
    text = "\n".join(lines)[:MAX_DECISION_TEXT_CHARS]
    return DecisionDocument(
        decision_id=decision.decision_id,
        app_id=decision.app_id,
        basis=decision.basis.value,
        summary=decision.summary,
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def verify_decision(
    listing: DecisionListing, decision_id: str, expected_sha: str
) -> tuple[DecisionDocument | None, SourceCheck]:
    """Rebuild the document from the row; trust only what still matches."""
    docs: Mapping[str, Decision] = {d.decision_id: d for d in listing.decisions}
    decision = docs.get(decision_id)
    if decision is None:
        if decision_id in listing.malformed:
            return None, SourceCheck.UNREADABLE
        return None, SourceCheck.MISSING
    doc = decision_document(decision)
    if doc.text_sha256 != expected_sha:
        return None, SourceCheck.CHANGED
    return doc, SourceCheck.OK
