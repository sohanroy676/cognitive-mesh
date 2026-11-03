"""Aggregates over recorded model calls (ADR-0038). Pure functions; no I/O.

Calls are grouped by (catalog entry, provider, model, role, task type) and never merged across task
types. Every group carries its sample count; nothing here ranks models or calls one "best". A token
count the provider did not report is counted as unknown, not as zero.
"""

import math
from collections import Counter
from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.providers.recording import CallResult
from cognitive_mesh.traces import CallListing, CallRow

NOTE = (
    "These numbers describe the recorded calls only. They do not rank models, and a small sample "
    "says little about how a model will do on other tasks."
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TokenSummary(_Frozen):
    total: int  # sum over the successful calls that reported a count
    known_calls: int
    unknown_calls: int  # successful calls whose provider reported no count


class GroupStats(_Frozen):
    entry_id: str | None
    provider: str
    model: str
    role: str
    task_type: str
    calls: int
    results: dict[str, int]
    ok_latency_n: int
    median_latency_ms: int | None
    p90_latency_ms: int | None
    prompt_tokens: TokenSummary
    completion_tokens: TokenSummary
    attempts_success: int
    attempts_failure: int
    attempts_unmatched: int


class ModelStats(_Frozen):
    runs_total: int
    runs_with_calls: int
    task_type: str | None
    groups: tuple[GroupStats, ...]
    malformed_runs: tuple[str, ...]
    note: str = NOTE

    @classmethod
    def from_listing(cls, listing: CallListing, task_type: str | None = None) -> Self:
        rows = [r for r in listing.rows if task_type is None or r.call.task_type == task_type]
        buckets: dict[tuple[str, str, str, str, str], list[CallRow]] = {}
        for row in rows:
            call = row.call
            key = (call.entry_id or "", call.provider, call.model, call.role, call.task_type)
            buckets.setdefault(key, []).append(row)
        groups = tuple(_group(bucket) for _, bucket in sorted(buckets.items()))
        return cls(
            runs_total=listing.runs_total,
            runs_with_calls=listing.runs_with_calls,
            task_type=task_type,
            groups=groups,
            malformed_runs=listing.malformed_runs,
        )


def _nearest_rank(sorted_values: Sequence[int], percent: int) -> int:
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def _tokens(values: Sequence[int | None]) -> TokenSummary:
    known = [v for v in values if v is not None]
    return TokenSummary(
        total=sum(known), known_calls=len(known), unknown_calls=len(values) - len(known)
    )


def _group(rows: Sequence[CallRow]) -> GroupStats:
    first = rows[0].call
    ok = [r.call for r in rows if r.call.result is CallResult.OK]
    latencies = sorted(c.latency_ms for c in ok)
    results = Counter(r.call.result.value for r in rows)
    attempts = Counter(r.attempt_status for r in rows)
    return GroupStats(
        entry_id=first.entry_id,
        provider=first.provider,
        model=first.model,
        role=first.role,
        task_type=first.task_type,
        calls=len(rows),
        results=dict(sorted(results.items())),
        ok_latency_n=len(latencies),
        median_latency_ms=_nearest_rank(latencies, 50) if latencies else None,
        p90_latency_ms=_nearest_rank(latencies, 90) if latencies else None,
        prompt_tokens=_tokens([c.prompt_tokens for c in ok]),
        completion_tokens=_tokens([c.completion_tokens for c in ok]),
        attempts_success=attempts.get("success", 0),
        attempts_failure=attempts.get("failure", 0),
        attempts_unmatched=attempts.get(None, 0),
    )
