"""Shared helpers for the memory tests: fixture traces and a deterministic fake embedder."""

import math
import re
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from cognitive_mesh.agents import (
    AgentFailure,
    AgentResult,
    AgentSuccess,
    Basis,
    FailureKind,
)
from cognitive_mesh.identity import IdentityHow
from cognitive_mesh.memory import TEXT_FORMAT_VERSION, ModelKey
from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.traces import (
    AttemptTrace,
    RunTrace,
    StepTrace,
    TraceEnvironment,
    TraceStatus,
    TraceStore,
)

T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
DIM = 64


class FakeEmbedder:
    """Bag-of-words hashing into 64 dims: shared words mean closeness. No model, no network."""

    def __init__(self, name: str = "fake", dimension: int = DIM) -> None:
        self.key = ModelKey(
            name=name, revision="1", dimension=dimension, text_format=TEXT_FORMAT_VERSION
        )
        self.calls: list[int] = []  # batch sizes, to prove unchanged documents are not re-embedded

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.key.dimension
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vec[zlib.crc32(word.encode()) % self.key.dimension] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def success(
    run_id: str,
    step_id: str,
    capability: str,
    output: dict[str, JsonValue],
    basis: Basis = Basis.DETERMINISTIC,
) -> AgentSuccess:
    return AgentSuccess(
        agent_id="agent",
        task_id=f"{run_id}-task.{step_id}.1",
        capability=capability,
        output=output,
        basis=basis,
    )


def failure(run_id: str, step_id: str, capability: str, message: str) -> AgentFailure:
    return AgentFailure(
        agent_id="agent",
        task_id=f"{run_id}-task.{step_id}.1",
        capability=capability,
        kind=FailureKind.PROVIDER_UNAVAILABLE,
        message=message,
        retryable=True,
    )


def add_run(
    db: Path,
    run_id: str,
    steps: Sequence[tuple[str, str, dict[str, JsonValue], AgentResult]],
    *,
    finish: bool = True,
    fingerprint: str | None = None,
    source_root: str | None = None,
    app_id: str | None = None,
) -> None:
    """Store one run. Each step is (step_id, capability, plan inputs, final result)."""
    task_id = f"{run_id}-task"
    plan: dict[str, JsonValue] = {
        "task_id": task_id,
        "steps": [{"step_id": s, "capability": c, "inputs": i} for s, c, i, _ in steps],
    }
    header = RunTrace(
        run_id=run_id,
        task_id=task_id,
        status=TraceStatus.INTERRUPTED,
        started_at=T0,
        source_root=source_root,
        source_fingerprint=fingerprint,
        app_id=app_id,
        app_id_how=IdentityHow.DECLARED if app_id else None,
        environment=TraceEnvironment(
            cognitive_mesh_version="test", python_version="3.12", platform="test"
        ),
        plan=plan,
    )
    trace_steps = tuple(
        StepTrace(
            seq=n,
            step_id=step_id,
            capability=capability,
            status=StepStatus.SUCCEEDED if isinstance(result, AgentSuccess) else StepStatus.FAILED,
            agent_id=result.agent_id,
            attempt_count=1,
            attempts=(
                AttemptTrace(
                    attempt_no=1,
                    agent_task_id=f"{task_id}.{step_id}.1",
                    agent_id=result.agent_id,
                    result=result,
                ),
            ),
        )
        for n, (step_id, capability, _inputs, result) in enumerate(steps)
    )
    with TraceStore.create(db) as store:
        store.begin_run(header)
        if finish:
            ok = all(isinstance(r, AgentSuccess) for *_, r in steps)
            store.finish_run(
                run_id,
                TraceStatus.SUCCEEDED if ok else TraceStatus.FAILED,
                T0,
                (),
                trace_steps,
            )


def build_fixture_db(db: Path) -> None:
    """Three runs with unrelated meaning: dependency lookup, invoice explanation, a failed call."""
    add_run(
        db,
        "run-deps",
        [
            (
                "deps",
                "dependencies_of",
                {"node_id": "module:src/dep_app/a.py"},
                success(
                    "run-deps",
                    "deps",
                    "dependencies_of",
                    {"dependencies": ["module:src/dep_app/b.py", "package requests"]},
                ),
            )
        ],
    )
    add_run(
        db,
        "run-invoice",
        [
            (
                "explain",
                "explain_file",
                {"path": "billing/invoice.py"},
                success(
                    "run-invoice",
                    "explain",
                    "explain_file",
                    {"summary": "computes invoice totals and sales tax for customers"},
                    Basis.MODEL_UNVERIFIED,
                ),
            )
        ],
    )
    add_run(
        db,
        "run-down",
        [
            (
                "explain",
                "explain_file",
                {"path": "payments/refund.py"},
                failure("run-down", "explain", "explain_file", "the model provider is unavailable"),
            )
        ],
    )
