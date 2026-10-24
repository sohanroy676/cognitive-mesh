"""Build the experience database from the agents' reports.

Run it with the Cognitive Mesh environment:
    uv run --project E:/Projects/cognitive-mesh python E:/experience-db/build_experience_db.py DB SLUG [SLUG ...]

For each slug it does three things, in this order, so the database reads like history:
  1. runs Cognitive Mesh's own analyzer (`unresolved_report`, `dependencies_of`) on the project as it was
     before the agent started (extracted from the base commit);
  2. stores the agent's own steps (what it read, each decision with reasons, the change, every check) as
     experiences: model-written and unverified, never as validated knowledge;
  3. runs the same analysis on the project after the agent's last commit.
Nothing here edits a project. A report that does not match the format is refused with a message.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cognitive_mesh import __version__
from cognitive_mesh.agents import (
    AgentFailure,
    AgentSuccess,
    Basis,
    EvidenceRef,
    FailureKind,
    ModelInfo,
)
from cognitive_mesh.identity import IdentityHow, validate_app_id
from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.traces import (
    AttemptTrace,
    RunTrace,
    StepTrace,
    TraceEnvironment,
    TraceStatus,
    TraceStore,
)
from cognitive_mesh.traces.recorder import redact_json

ROOT = Path("E:/experience-db")
REPO = "E:/Projects/cognitive-mesh"
CAPABILITIES = {"read_project", "decide", "change_code", "run_checks", "reflect"}
FAILURE_KINDS = {"invalid_input", "missing_context", "internal_error", "unsupported_capability"}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx"}
MAX_OUTPUT_CHARS = 3000


class ReportError(Exception):
    pass


def load_report(slug: str) -> dict[str, Any]:
    path = ROOT / "reports" / f"{slug}.json"
    try:
        report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportError(f"{slug}: cannot read the report ({exc.__class__.__name__})") from None
    for key in ("project", "slug", "task", "clone", "base_commit", "head_commit", "model", "steps"):
        if not report.get(key):
            raise ReportError(f"{slug}: the report has no '{key}'")
    if report.get("finished") is not True:
        raise ReportError(f"{slug}: the agent did not mark the report finished")
    seen: set[str] = set()
    for step in report["steps"]:
        sid = step.get("step_id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ReportError(f"{slug}: a step id is missing or repeated ({sid!r})")
        seen.add(sid)
        if step.get("capability") not in CAPABILITIES:
            raise ReportError(f"{slug}/{sid}: capability {step.get('capability')!r} is not allowed")
        if not isinstance(step.get("input"), dict):
            raise ReportError(f"{slug}/{sid}: 'input' must be an object")
        if step.get("outcome") == "success":
            out = step.get("output")
            if not isinstance(out, dict):
                raise ReportError(f"{slug}/{sid}: a success needs an 'output' object")
            if len(json.dumps(out)) > MAX_OUTPUT_CHARS:
                raise ReportError(f"{slug}/{sid}: output is over {MAX_OUTPUT_CHARS} characters")
        elif step.get("outcome") == "failure":
            if step.get("failure_kind") not in FAILURE_KINDS or not step.get("failure_message"):
                raise ReportError(f"{slug}/{sid}: a failure needs a known kind and a message")
        else:
            raise ReportError(f"{slug}/{sid}: outcome must be success or failure")
    return report


def git(clone: str, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", "-C", clone, *args], capture_output=True, text=True, check=True, timeout=120
    )
    return done.stdout


def extract_base(report: dict[str, Any]) -> Path:
    """The project as it was before the agent: `git archive` of the base commit."""
    target = ROOT / "before" / report["project"]
    if target.exists():
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(target)], check=False)  # noqa: S603,S607
    target.mkdir(parents=True)
    data = subprocess.run(  # noqa: S603
        ["git", "-C", report["clone"], "archive", "--format=tar", report["base_commit"]],  # noqa: S607
        capture_output=True,
        check=True,
        timeout=300,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(target, filter="data")
    return target


def changed_modules(report: dict[str, Any]) -> list[str]:
    names = git(report["clone"], "diff", "--name-only", report["base_commit"], report["head_commit"])
    return [n for n in names.splitlines() if Path(n).suffix in CODE_SUFFIXES]


def run_plan(db: Path, plan_name: str, root: Path, app_id: str, modules: list[str]) -> str:
    steps: list[dict[str, Any]] = [{"step_id": "report", "capability": "unresolved_report"}]
    for i, mod in enumerate(modules[:5]):
        steps.append(
            {
                "step_id": f"deps-{i + 1}",
                "capability": "dependencies_of",
                "inputs": {"node_id": f"module:{mod}"},
                "depends_on": ["report"],
            }
        )
    plan_path = ROOT / "plans" / f"{plan_name}.json"
    plan_path.write_text(json.dumps({"task_id": plan_name, "steps": steps}, indent=2), encoding="utf-8")
    done = subprocess.run(  # noqa: S603
        [
            "uv", "run", "--project", REPO, "cognitive-mesh", "run", str(plan_path),  # noqa: S607
            "--root", str(root), "--trace-db", str(db), "--app-id", app_id,
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )  # fmt: skip
    return f"exit {done.returncode}: " + (done.stdout.strip().splitlines() or ["(no output)"])[-1]


def sha256_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def record_agent_run(db: Path, report: dict[str, Any], when: datetime) -> str:
    """Store the agent's steps as one run of model-written, unverified experiences."""
    from cognitive_mesh.ingestion import ingest_application
    from cognitive_mesh.traces.recorder import source_fingerprint

    slug, project = report["slug"], report["project"]
    clone = Path(report["clone"])
    if git(str(clone), "status", "--porcelain").strip():
        raise ReportError(f"{slug}: the clone has uncommitted changes; the agent must commit first")
    rules: set[str] = set()
    run_id = f"agent-{slug}-{hashlib.sha256((slug + when.isoformat()).encode()).hexdigest()[:8]}"
    app_id = validate_app_id(project)
    model = ModelInfo(provider="anthropic", model=report["model"])
    agent_id = f"claude-agent:{slug}"
    plan_steps = []
    steps: list[StepTrace] = []
    for seq, step in enumerate(report["steps"]):
        sid, cap = step["step_id"], step["capability"]
        task = f"{slug}.{sid}.1"
        inputs = redact_json(step["input"], rules)
        plan_steps.append({"step_id": sid, "capability": cap, "inputs": inputs})
        if step["outcome"] == "success":
            output = redact_json(step["output"], rules)
            evidence = tuple(
                EvidenceRef(
                    path=str(f["path"]).replace("\\", "/"),
                    sha256=digest,
                    line=f.get("line") if isinstance(f.get("line"), int) else None,
                    rule="agent:changed",
                )
                for f in (output.get("files_changed") or [])  # type: ignore[union-attr]
                if isinstance(f, dict) and f.get("path")
                and (digest := sha256_of(clone / str(f["path"]))) is not None
            )  # fmt: skip
            result: AgentSuccess | AgentFailure = AgentSuccess(
                agent_id=agent_id,
                task_id=task,
                capability=cap,
                output=output,  # type: ignore[arg-type]
                basis=Basis.MODEL_UNVERIFIED,
                evidence=evidence,
                model=model,
            )
            status = StepStatus.SUCCEEDED
            failure_kind = message = None
        else:
            message = redact_json(step["failure_message"], rules)
            result = AgentFailure(
                agent_id=agent_id,
                task_id=task,
                capability=cap,
                kind=FailureKind(step["failure_kind"]),
                message=str(message)[:200],
                retryable=False,
            )
            status = StepStatus.FAILED
            failure_kind, message = step["failure_kind"], str(message)[:200]
        steps.append(
            StepTrace(
                seq=seq,
                step_id=sid,
                capability=cap,
                status=status,
                agent_id=agent_id,
                attempt_count=1,
                failure_kind=failure_kind,
                message=message,
                attempts=(
                    AttemptTrace(attempt_no=1, agent_task_id=task, agent_id=agent_id, result=result),
                ),
            )
        )
    ingestion = ingest_application(clone)
    plan: dict[str, Any] = {
        "task_id": slug,
        "task": redact_json(report["task"], rules),
        # A failed step cannot carry a model, so the run states who made every step in it.
        "made_by": {"provider": "anthropic", "model": report["model"], "kind": "claude agent"},
        "branch": report.get("branch"),
        "base_commit": report["base_commit"],
        "head_commit": report["head_commit"],
        "steps": plan_steps,
    }
    header = RunTrace(
        run_id=run_id,
        task_id=slug,
        status=TraceStatus.INTERRUPTED,
        started_at=when,
        source_root=ingestion.root,
        source_fingerprint=source_fingerprint(ingestion),
        app_id=app_id,
        app_id_how=IdentityHow.DECLARED,
        environment=TraceEnvironment(
            cognitive_mesh_version=__version__,
            python_version=platform.python_version(),
            platform=platform.system(),
        ),
        redactions=tuple(sorted(rules)),
        plan=plan,
    )
    ok = all(s.status is StepStatus.SUCCEEDED for s in steps)
    with TraceStore.create(db) as store:
        store.begin_run(header)
        store.finish_run(
            run_id,
            TraceStatus.SUCCEEDED if ok else TraceStatus.FAILED,
            when + timedelta(seconds=1),
            sorted(rules),
            tuple(steps),
        )
    return run_id


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    db = Path(argv[0])
    for slug in argv[1:]:
        report = load_report(slug)
        project, app_id = report["project"], validate_app_id(report["project"])
        mods = changed_modules(report)
        print(f"== {project} ({slug}): {len(report['steps'])} steps, {len(mods)} changed code files")
        before = extract_base(report)
        in_before = [m for m in mods if (before / m).is_file()]
        in_after = [m for m in mods if (Path(report["clone"]) / m).is_file()]
        print("   analysis before:", run_plan(db, f"{slug}-before", before, app_id, in_before))
        run_id = record_agent_run(db, report, datetime.now(UTC))
        print("   agent steps stored as run", run_id)
        print("   analysis after: ", run_plan(db, f"{slug}-after", Path(report["clone"]), app_id, in_after))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
