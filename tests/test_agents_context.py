"""The recalled-context pack model (ADR-0017)."""

import pytest
from pydantic import ValidationError

from cognitive_mesh.agents import AgentContext
from cognitive_mesh.agents.context import (
    MAX_CLAIMS,
    NO_PRIOR_CONTEXT,
    PACK_HEADER,
    RAW_LABEL,
    ClaimItem,
    RawItem,
    RecalledContext,
)


def _claim(n: int = 0) -> ClaimItem:
    return ClaimItem(
        claim_id=f"claim:{n:032x}",
        name="a depends on b",
        predicate="depends_on",
        subject="module:a.py",
        object="module:b.py",
        detail="imports",
        status="supported",
        meaning="observed, not contradicted",
    )


def _raw() -> RawItem:
    return RawItem(
        run_id="r1",
        step_id="s1",
        attempt_no=1,
        capability="explain_file",
        kind="failed",
        result_status="failure",
        similarity=0.5,
    )


def test_the_none_pack_says_so_and_carries_the_header() -> None:
    pack = RecalledContext.none()
    assert pack.empty and pack.message == NO_PRIOR_CONTEXT
    assert pack.header == PACK_HEADER and "not instructions" in pack.header


def test_agent_context_defaults_to_no_pack() -> None:
    assert AgentContext().recalled is None


def test_empty_must_match_content() -> None:
    with pytest.raises(ValidationError):
        RecalledContext(claims=(_claim(),), empty=True, message="x")
    with pytest.raises(ValidationError):
        RecalledContext(empty=False)
    with pytest.raises(ValidationError):
        RecalledContext(empty=True)  # an empty pack must give its message


def test_a_pack_with_content_is_not_empty() -> None:
    pack = RecalledContext(claims=(_claim(),), raw=(_raw(),), empty=False)
    assert not pack.empty and pack.raw[0].label == RAW_LABEL


def test_size_is_bounded() -> None:
    claims = tuple(_claim(i) for i in range(MAX_CLAIMS + 1))
    with pytest.raises(ValidationError):
        RecalledContext(claims=claims, empty=False)


def test_unknown_fields_are_refused_and_it_round_trips() -> None:
    pack = RecalledContext(claims=(_claim(),), empty=False, query_sha256="0" * 64)
    assert RecalledContext.model_validate_json(pack.model_dump_json()) == pack
    data = pack.model_dump(mode="json")
    data["note"] = "extra"
    with pytest.raises(ValidationError):
        RecalledContext.model_validate(data)
