"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from cognitive_mesh import __version__
from cognitive_mesh.agents import AgentContext, AgentRegistry, default_registry
from cognitive_mesh.config import ConfigError, load_settings, load_settings_if_configured
from cognitive_mesh.graph import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    DependencyGraph,
    GraphError,
    build_dependency_graph,
    build_view,
    render_html,
    render_mermaid,
)
from cognitive_mesh.identity import AppIdentity, IdentityError, derive_identity
from cognitive_mesh.ingestion import IngestionError, IngestionResult, ingest_application
from cognitive_mesh.orchestrator import (
    Orchestrator,
    PlanError,
    PlanGenerationError,
    TaskPlan,
    TaskRun,
    TaskStatus,
    ValidatedPlan,
    generate_plan,
    plan_task,
)
from cognitive_mesh.providers import build_model_client
from cognitive_mesh.traces import (
    RunTrace,
    TraceNotFoundError,
    TraceRecorder,
    TraceStatus,
    TraceStore,
    TraceStoreError,
    utc_now,
)
from cognitive_mesh.validity import current_environment, utc_today

if TYPE_CHECKING:
    from collections.abc import Callable

    from cognitive_mesh.agents.context import ClaimItem
    from cognitive_mesh.experience import ExperienceGraph, SourceReport
    from cognitive_mesh.experience.assess import ClaimAssessment
    from cognitive_mesh.experience.current import CurrentSource
    from cognitive_mesh.experience.outcomes import Outcome
    from cognitive_mesh.experience.reviews import Review
    from cognitive_mesh.experience.store import LoadedGraph
    from cognitive_mesh.memory import Embedder
    from cognitive_mesh.workflow import PreparedContext

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INGESTION_ERROR = 3
EXIT_GRAPH_ERROR = 4
EXIT_OUTPUT_ERROR = 5
EXIT_PLAN_ERROR = 6
EXIT_TASK_NOT_SUCCEEDED = 7
EXIT_TRACE_STORE_ERROR = 8
EXIT_TRACE_NOT_FOUND = 9
EXIT_PLAN_GENERATION_ERROR = 10
EXIT_MEMORY_ERROR = 11
EXIT_EXPERIENCE_ERROR = 12
EXIT_EXPERIENCE_NODE_NOT_FOUND = 13
EXIT_EXPERIENCE_SOURCES = 14
EXIT_REVIEW_STORE_ERROR = 15
EXIT_OUTCOME_STORE_ERROR = 16
EXIT_PROMOTION_REFUSED = 17
MAX_PLAN_BYTES = 256 * 1024
DEFAULT_MIN_SIMILARITY = 0.2  # from one live run on 3 documents (ADR-0014); a rough starting point


def _printable(text: str) -> str:
    """Escape control characters so untrusted file names cannot drive the terminal."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


_APP_ID_HELP = (
    "label for the application (ADR-0021); default: the manifest name plus directory name, "
    "else the directory name. A label, not proof of identity"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cognitive-mesh")
    sub = parser.add_subparsers(dest="command")
    ingest = sub.add_parser("ingest", help="inspect what ingestion discovers in a directory")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    ingest.add_argument(
        "--include-content", action="store_true", help="with --json, include file contents"
    )
    graph = sub.add_parser("graph", help="build the dependency graph of a directory")
    graph.add_argument("path", type=Path)
    graph.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    graph.add_argument("--deps-of", metavar="NODE_ID", help="list what NODE_ID depends on")
    graph.add_argument("--transitive", action="store_true", help="with --deps-of, follow indirect")
    graph.add_argument(
        "--format", choices=["mermaid", "html"], dest="fmt", help="render a diagram instead"
    )
    graph.add_argument("--output", type=Path, metavar="FILE", help="with --format, write to FILE")
    graph.add_argument("--max-nodes", type=_positive_int, help="with --format, node limit")
    graph.add_argument("--max-edges", type=_positive_int, help="with --format, edge limit")
    agents = sub.add_parser("agents", help="list agent roles and their declared capabilities")
    agents.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    run = sub.add_parser("run", help="run a task plan (JSON) against a directory")
    run.add_argument("plan", type=Path, help="path to the plan JSON file")
    run.add_argument("--root", type=Path, required=True, help="application directory to analyze")
    run.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    run.add_argument("--app-id", metavar="NAME", help=_APP_ID_HELP)
    run.add_argument(
        "--trace-db", type=Path, metavar="FILE", help="record the run in this SQLite trace database"
    )
    plan = sub.add_parser(
        "plan", help="draft a plan (JSON) from a request with the configured model; runs nothing"
    )
    plan.add_argument("request", help="what you want done, in plain words")
    plan.add_argument("--task-id", default="generated", help="task id for the plan")
    plan.add_argument("--output", type=Path, metavar="FILE", help="write the plan to FILE")
    plan.add_argument(
        "--root",
        type=Path,
        metavar="DIR",
        help="application directory: the model is shown its files and must name them",
    )
    traces = sub.add_parser("traces", help="read recorded run traces")
    traces_sub = traces.add_subparsers(dest="traces_command", required=True)
    traces_list = traces_sub.add_parser("list", help="list recorded runs")
    traces_list.add_argument("--db", type=Path, required=True, metavar="FILE")
    traces_list.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    traces_show = traces_sub.add_parser("show", help="show one recorded run")
    traces_show.add_argument("run_id")
    traces_show.add_argument("--db", type=Path, required=True, metavar="FILE")
    traces_show.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    memory = sub.add_parser("memory", help="semantic search over recorded run traces")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_index = memory_sub.add_parser("index", help="embed recorded steps into a vector index")
    memory_search = memory_sub.add_parser("search", help="find past steps similar to a query")
    memory_search.add_argument("query", help="what to look for, in plain words")
    memory_search.add_argument("--k", type=_positive_int, default=5, help="at most this many hits")
    memory_search.add_argument(
        "--min-similarity",
        type=float,
        default=DEFAULT_MIN_SIMILARITY,
        metavar="X",
        help="drop hits below this cosine similarity, 0 to 1",
    )
    for cmd in (memory_index, memory_search):
        cmd.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
        cmd.add_argument("--index", type=Path, required=True, metavar="DIR", help="index directory")
        cmd.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    recall = sub.add_parser(
        "recall", help="find past experience by meaning, with the evidence behind each claim"
    )
    recall.add_argument("query", help="what to look for, in plain words")
    recall.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    recall.add_argument("--index", type=Path, required=True, metavar="DIR", help="index directory")
    recall.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
    recall.add_argument("--reviews", type=Path, metavar="FILE", help="reviews file (optional)")
    recall.add_argument("--outcomes", type=Path, metavar="FILE", help="outcomes file (optional)")
    recall.add_argument(
        "--root", type=Path, metavar="DIR", help="application directory: check claims against it"
    )
    recall.add_argument("--k", type=_positive_int, default=5, help="at most this many hits")
    recall.add_argument(
        "--min-similarity",
        type=float,
        default=DEFAULT_MIN_SIMILARITY,
        metavar="X",
        help="drop hits below this cosine similarity, 0 to 1",
    )
    recall.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    task = sub.add_parser(
        "task",
        help="run a plan with recalled experience as context (ADR-0017); records what was offered",
    )
    task.add_argument("request", help="the task in plain words; also the recall query")
    task.add_argument("--plan", type=Path, required=True, metavar="FILE", help="plan JSON file")
    task.add_argument("--root", type=Path, required=True, help="application directory to analyze")
    task.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    task.add_argument("--index", type=Path, required=True, metavar="DIR", help="index directory")
    task.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
    task.add_argument("--reviews", type=Path, metavar="FILE", help="reviews file (optional)")
    task.add_argument("--outcomes", type=Path, metavar="FILE", help="outcomes file (optional)")
    task.add_argument("--app-id", metavar="NAME", help=_APP_ID_HELP)
    task.add_argument(
        "--profile",
        choices=("precision", "recall"),
        default="precision",
        help="how much context to admit (ADR-0023); sets the defaults of --k and --min-similarity",
    )
    task.add_argument(
        "--k", type=_hit_limit, help="at most this many hits (max 20; default 5, recall 10)"
    )
    task.add_argument(
        "--min-similarity",
        type=float,
        metavar="X",
        help="drop hits below this cosine similarity, 0 to 1 (default 0.2, recall 0.1)",
    )
    task.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    experience = sub.add_parser("experience", help="build and query the experience graph")
    exp_sub = experience.add_subparsers(dest="experience_command", required=True)
    exp_build = exp_sub.add_parser("build", help="build the graph file from the trace database")
    exp_show = exp_sub.add_parser("show", help="show a node and what it connects to")
    exp_show.add_argument("node", help="a node id, e.g. run:ID or claim:HASH")
    exp_show.add_argument("--transitive", action="store_true", help="follow edges all the way")
    exp_show.add_argument("--incoming", action="store_true", help="follow edges backwards")
    exp_claims = exp_sub.add_parser("claims", help="list claims with their evidence counts")
    exp_check = exp_sub.add_parser("check", help="verify the graph file and re-check its sources")
    exp_claims.add_argument(
        "--reviews", type=Path, metavar="FILE", help="reviews file: include people's verdicts"
    )
    exp_claims.add_argument(
        "--root",
        type=Path,
        metavar="DIR",
        help="application directory: check claims against it now",
    )
    exp_claims.add_argument(
        "--outcomes", type=Path, metavar="FILE", help="outcomes file: include recorded outcomes"
    )
    exp_outcome = exp_sub.add_parser("outcome", help="record a check's or a person's outcome")
    exp_outcome.add_argument("claim_id", help="a claim id, e.g. claim:HASH")
    exp_outcome.add_argument("--source", choices=("check", "person"), required=True)
    exp_outcome.add_argument(
        "--result", choices=("succeeded", "failed", "ambiguous"), required=True
    )
    exp_outcome.add_argument(
        "--detail", required=True, help="the check's command or the reason; stored as data"
    )
    exp_outcome.add_argument("--by", required=True, metavar="NAME", help="unauthenticated label")
    exp_outcome.add_argument(
        "--valid-until", type=_iso_date, metavar="DATE", help=_VALID_UNTIL_HELP
    )
    exp_outcome.add_argument(
        "--root", type=Path, required=True, metavar="DIR", help="the application as observed"
    )
    exp_outcome.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
    exp_outcome.add_argument(
        "--outcomes", type=Path, required=True, metavar="FILE", help="outcomes file (created)"
    )
    exp_outcome.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    exp_promote = exp_sub.add_parser("promote", help="accept a claim only if validated now")
    exp_promote.add_argument("claim_id", help="a claim id, e.g. claim:HASH")
    exp_promote.add_argument("--by", required=True, metavar="NAME", help="unauthenticated label")
    exp_promote.add_argument(
        "--root", type=Path, required=True, metavar="DIR", help="the application to check against"
    )
    exp_promote.add_argument(
        "--db", type=Path, required=True, metavar="FILE", help="trace database"
    )
    exp_promote.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
    exp_promote.add_argument("--reviews", type=Path, metavar="FILE", help="reviews file (optional)")
    exp_promote.add_argument(
        "--outcomes",
        type=Path,
        required=True,
        metavar="FILE",
        help="outcomes file: read if present; a refusal is recorded here (created)",
    )
    exp_promote.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    exp_review = exp_sub.add_parser("review", help="record a person's verdict on a claim")
    exp_review.add_argument("claim_id", help="a claim id, e.g. claim:HASH")
    exp_review.add_argument("--verdict", choices=("confirmed", "rejected"), required=True)
    exp_review.add_argument("--note", required=True, help="why; stored as data, secrets redacted")
    exp_review.add_argument("--by", required=True, metavar="NAME", help="unauthenticated label")
    exp_review.add_argument("--valid-until", type=_iso_date, metavar="DATE", help=_VALID_UNTIL_HELP)
    exp_review.add_argument(
        "--root", type=Path, required=True, metavar="DIR", help="the application as reviewed"
    )
    exp_review.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
    exp_review.add_argument(
        "--reviews", type=Path, required=True, metavar="FILE", help="reviews file (created)"
    )
    exp_review.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    for cmd in (exp_show, exp_claims):
        cmd.add_argument(
            "--include-unverified",
            action="store_true",
            help="also show elements whose trace source is missing, changed or unreadable",
        )
    for cmd in (exp_build, exp_show, exp_claims, exp_check):
        cmd.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
        cmd.add_argument("--graph", type=Path, required=True, metavar="FILE", help="graph file")
        cmd.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    return parser


def _hit_limit(text: str) -> int:
    value = _positive_int(text)
    if value > 20:
        raise argparse.ArgumentTypeError("must be at most 20")
    return value


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a date; use YYYY-MM-DD") from None


_VALID_UNTIL_HELP = (
    "last day this stays in force (YYYY-MM-DD, not in the past); after it the claim shows as "
    "expired (ADR-0022). Without it nothing expires by time"
)


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {_printable(text)!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _print_ingestion(result: IngestionResult) -> None:
    print(f"ingested {_printable(result.root)}: {len(result.files)} files")
    for file in result.files:
        print(f"  {file.kind.value:<13} {_printable(file.ref.path)}  ({file.matched_rule})")
    if result.diagnostics:
        print(f"diagnostics: {len(result.diagnostics)}")
        for diag in result.diagnostics:
            print(f"  [{diag.severity}] {diag.code.value} {_printable(diag.path)}: {diag.message}")


def _run_ingest(path: Path, as_json: bool, include_content: bool) -> int:
    try:
        result = ingest_application(path)
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    if as_json:
        exclude = None if include_content else {"files": {"__all__": {"content"}}}
        payload = result.model_dump(mode="json", exclude=exclude)
        payload["summary"] = result.summary()
        print(json.dumps(payload, indent=2))
    else:
        _print_ingestion(result)
    return EXIT_OK


def _print_graph(graph: DependencyGraph) -> None:
    summary = graph.summary()
    print(f"nodes: {summary['nodes_by_kind']}")
    print(f"edges: {summary['edges_by_kind']}")
    for node in graph.unresolved():
        print(f"  unresolved: {_printable(node.id)}")
    cycles, truncated = graph.cycles()
    for cycle in cycles:
        print("  cycle: " + " -> ".join(_printable(c) for c in (*cycle, cycle[0])))
    if truncated:
        print("  cycles truncated: more exist than were listed")
    print(f"diagnostics: {len(graph.diagnostics)}")
    for diag in graph.diagnostics:
        print(f"  [{diag.severity}] {diag.code.value} {_printable(diag.path)}: {diag.message}")


def _print_deps(graph: DependencyGraph, node_id: str, transitive: bool) -> None:
    if transitive:
        for reached in graph.transitive_dependencies(node_id):
            via = reached.path[-1].evidence[0]
            print(
                f"  d{reached.depth} {_printable(reached.node.id)}  "
                f"({_printable(via.ref.path)}:{via.line}, {via.rule})"
            )
    else:
        for edge in graph.direct_dependencies(node_id):
            via = edge.evidence[0]
            print(
                f"  {edge.kind.value} {_printable(edge.target)}  "
                f"({_printable(via.ref.path)}:{via.line}, {via.rule})"
            )


def _run_graph(path: Path, as_json: bool, deps_of: str | None, transitive: bool) -> int:
    try:
        graph = build_dependency_graph(ingest_application(path))
        if deps_of is not None:
            graph.node(deps_of)  # fail before printing anything
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    except GraphError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_GRAPH_ERROR
    if as_json:
        print(json.dumps(graph.to_dict(), indent=2))
    elif deps_of is not None:
        print(f"dependencies of {_printable(deps_of)}:")
        _print_deps(graph, deps_of, transitive)
    else:
        _print_graph(graph)
    return EXIT_OK


def _run_graph_view(
    path: Path, fmt: str, output: Path | None, max_nodes: int | None, max_edges: int | None
) -> int:
    try:
        graph = build_dependency_graph(ingest_application(path))
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    view = build_view(
        graph, max_nodes or DEFAULT_MAX_NODES[fmt], max_edges or DEFAULT_MAX_EDGES[fmt]
    )
    text = render_html(view) if fmt == "html" else render_mermaid(view)
    if view.truncation.truncated:
        print(view.truncation.notice(), file=sys.stderr)
    if output is None:
        print(text, end="")
        return EXIT_OK
    try:
        output.write_text(text, encoding="ascii", newline="\n")
    except OSError as exc:
        print(_printable(f"cannot write output: {exc}"), file=sys.stderr)
        return EXIT_OUTPUT_ERROR
    print(f"wrote {fmt} graph to {_printable(str(output))}")
    return EXIT_OK


def _run_agents(as_json: bool) -> int:
    specs = default_registry().specs()
    if as_json:
        print(json.dumps({"agents": [s.model_dump(mode="json") for s in specs]}, indent=2))
        return EXIT_OK
    for spec in specs:
        print(f"{spec.id}  ({spec.role})")
        print(f"  {_printable(spec.description)}")
        for cap in spec.capabilities:
            kind = "uses model" if cap.uses_model else "deterministic"
            print(f"  - {cap.name} [{kind}]: {_printable(cap.description)}")
    return EXIT_OK


def _load_plan(path: Path) -> TaskPlan:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_PLAN_BYTES + 1)
    except OSError:
        raise PlanError("cannot read the plan file") from None
    if len(data) > MAX_PLAN_BYTES:
        raise PlanError(f"the plan file is larger than {MAX_PLAN_BYTES} bytes")
    try:
        return TaskPlan.model_validate_json(data, strict=True)
    except ValidationError as exc:
        kinds = ", ".join(sorted({str(e["type"]) for e in exc.errors()}))
        raise PlanError(f"the plan file is not a valid plan ({kinds})") from None


def _run_plan_request(request: str, task_id: str, output: Path | None, root: Path | None) -> int:
    """Draft a plan with the configured model. The plan is printed or written, never executed."""
    from cognitive_mesh.ingestion import IngestionError, ingest_application

    files: list[str] | None = None
    if root is not None:
        try:
            files = [f.ref.path for f in ingest_application(root).files]
        except IngestionError as exc:
            print(_printable(str(exc)), file=sys.stderr)
            return EXIT_INGESTION_ERROR
    try:
        settings = load_settings()
        client = build_model_client(settings)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        plan, response = generate_plan(
            request,
            default_registry(),
            client,
            task_id=task_id,
            timeout_s=settings.model_timeout_s,
            files=files,
        )
    except PlanGenerationError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_PLAN_GENERATION_ERROR
    text = plan.model_dump_json(indent=2)
    if output is None:
        print(text)
    else:
        try:
            output.write_text(text + "\n", encoding="utf-8", newline="\n")
        except OSError:
            print("cannot write the plan file", file=sys.stderr)
            return EXIT_OUTPUT_ERROR
    print(
        f"drafted by {_printable(response.provider)}/{_printable(response.model)}: "
        "model-written and unreviewed; read it before running it with `run`",
        file=sys.stderr,
    )
    return EXIT_OK


def _print_run(run: TaskRun) -> None:
    print(f"task {run.task_id}: {run.status.value} ({run.total_attempts} attempts)")
    for step in run.steps:
        line = f"  {step.step_id:<20} {step.status.value:<9} {step.capability}"
        if step.agent_id:
            line += f"  agent={step.agent_id}"
        if step.attempt_count > 1:
            line += f"  attempts={step.attempt_count}"
        print(line)
        if step.blocked_by:
            print(f"    blocked by: {', '.join(step.blocked_by)}")
        if step.failure_kind:
            print(f"    failure: {step.failure_kind}: {_printable(step.message or '')}")


_NO_TIMES = {"steps": {"__all__": {"started_at", "finished_at"}}}


def _emit_run(run: TaskRun, as_json: bool, trace_id: str | None) -> None:
    if as_json:
        payload = run.model_dump(mode="json", exclude=None if trace_id else _NO_TIMES)
        if trace_id:
            payload["trace_id"] = trace_id
        print(json.dumps(payload, indent=2))
        return
    _print_run(run)
    if trace_id:
        print(f"trace: {trace_id}")


def _registry_for(validated: ValidatedPlan) -> AgentRegistry:
    """Config is read only when the plan has a model step; no provider set means no client."""
    registry = default_registry()
    model_caps = {c.name for spec in registry.specs() for c in spec.capabilities if c.uses_model}
    if not any(step.capability in model_caps for step in validated.order):
        return registry
    settings = load_settings_if_configured()
    if settings is None:
        return registry
    return default_registry(build_model_client(settings), model_timeout_s=settings.model_timeout_s)


def _load_run_inputs(
    plan_path: Path, root: Path
) -> tuple[ValidatedPlan, IngestionResult, AgentRegistry] | int:
    """Validate the plan, ingest the application and build the registry, or return an exit code."""
    registry = default_registry()
    try:
        validated = plan_task(_load_plan(plan_path), registry)
        ingestion = ingest_application(root)
        registry = _registry_for(validated)
    except PlanError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_PLAN_ERROR
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return validated, ingestion, registry


def _identity_for(root: str, declared: str | None) -> AppIdentity | None | int:
    """The application id label for a run (ADR-0021), or an exit code for a bad `--app-id`."""
    try:
        return derive_identity(root, declared)
    except IdentityError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR


def _run_plan(
    plan_path: Path, root: Path, as_json: bool, trace_db: Path | None, app_id: str | None
) -> int:
    loaded = _load_run_inputs(plan_path, root)
    if isinstance(loaded, int):
        return loaded
    validated, ingestion, registry = loaded
    identity = _identity_for(ingestion.root, app_id)
    if isinstance(identity, int):
        return identity
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    if trace_db is None:
        run = Orchestrator(registry).run(validated, context)
        _emit_run(run, as_json, None)
        return EXIT_OK if run.status is TaskStatus.SUCCEEDED else EXIT_TASK_NOT_SUCCEEDED
    try:
        store = TraceStore.create(trace_db)
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    with store:
        recorder = TraceRecorder(store)
        try:
            run_id = recorder.begin(validated, ingestion, identity=identity)
        except TraceStoreError as exc:  # nothing ran: the user asked for a trace we cannot keep
            print(_printable(str(exc)), file=sys.stderr)
            return EXIT_TRACE_STORE_ERROR
        run = Orchestrator(registry, now=utc_now).run(validated, context)
        try:
            recorder.finish(run_id, run)
        except TraceStoreError as exc:
            _emit_run(run, as_json, None)
            print(f"warning: the trace was not saved: {_printable(str(exc))}", file=sys.stderr)
            return EXIT_TRACE_STORE_ERROR
    _emit_run(run, as_json, run_id)
    return EXIT_OK if run.status is TaskStatus.SUCCEEDED else EXIT_TASK_NOT_SUCCEEDED


def _outcome_line(claim: "ClaimItem") -> str | None:
    """The recorded-outcome summary of a pack claim (ADR-0029); None if there is nothing to say."""
    if claim.outcome is None or not (claim.outcomes_current or claim.outcomes_other):
        return None
    return (
        f"outcome: {_printable(claim.outcome)} for the current code version "
        f"({claim.outcomes_current} current, {claim.outcomes_other} other version)"
    )


def _outcomes_note(consulted: bool | None) -> str | None:
    if consulted is None:
        return "outcomes: not recorded in this pack"
    return None if consulted else "outcomes: not consulted (no --outcomes file)"


def _print_offered_context(trace: RunTrace) -> None:
    """What past experience the run was given (ADR-0017); nothing is printed for plain runs."""
    pack = trace.recalled
    if pack is None:
        return
    withheld = pack.withheld
    print(
        f"  offered context: {len(pack.claims)} claim(s), {len(pack.raw)} raw trace(s); withheld "
        f"{withheld.unconfirmed} unconfirmed, {withheld.not_applicable} not applicable, "
        f"{withheld.over_limit} over the size limit; profile {pack.profile}"
    )
    for d in withheld.dropped:
        print(
            f"    dropped for size: {d.kind} {_printable(d.item_id)} {_printable(d.status or '')}"
        )
    if pack.empty:
        print(f"    {_printable(pack.message)}")
    for claim in pack.claims:
        label = f"[{_printable(claim.status)}] {_printable(claim.name)}"
        print(f"    {label}  {_printable(claim.claim_id)}")
        if (line := _outcome_line(claim)) is not None:
            print(f"      {line}")
    if (note := _outcomes_note(pack.outcomes_consulted)) is not None:
        print(f"    {note}")
    for raw in pack.raw:
        print(
            f"    [raw: {_printable(raw.kind)}] run {_printable(raw.run_id)} "
            f"step {_printable(raw.step_id)} {_printable(raw.capability)}"
        )
    if pack.claims or pack.raw:
        print("    none of this is verified truth; statuses say how well a claim is backed")


def _print_trace(trace: RunTrace) -> None:
    print(f"run {_printable(trace.run_id)}: {trace.status.value}  task={_printable(trace.task_id)}")
    finished = f"  finished {trace.finished_at.isoformat()}" if trace.finished_at else ""
    print(f"  started {trace.started_at.isoformat()}{finished}")
    if trace.source_root is not None:
        print(
            f"  source: {_printable(trace.source_root)}  "
            f"fingerprint={_printable(trace.source_fingerprint or '')}"
        )
    if trace.app_id is not None and trace.app_id_how is not None:
        weak = " (weak: directory name only)" if trace.app_id_how.value == "directory" else ""
        print(f"  application id: {_printable(trace.app_id)} [{trace.app_id_how.value}]{weak}")
    elif trace.source_root is not None:
        print("  application id: unrecorded (run made before application ids)")
    env = trace.environment
    print(
        f"  environment: cognitive-mesh {_printable(env.cognitive_mesh_version)}, "
        f"python {_printable(env.python_version)}, {_printable(env.platform)}"
    )
    if trace.redactions:
        print("  redacted: " + ", ".join(_printable(r) for r in trace.redactions))
    _print_offered_context(trace)
    if trace.status is TraceStatus.INTERRUPTED:
        print("  the run never finished; no step data was stored")
    for step in trace.steps:
        print(
            f"  {_printable(step.step_id):<20} {step.status.value:<9} {_printable(step.capability)}"
        )
        if step.agent_id:
            print(f"    agent={_printable(step.agent_id)} attempts={step.attempt_count}")
        if step.blocked_by:
            print(f"    blocked by: {', '.join(_printable(b) for b in step.blocked_by)}")
        if step.failure_kind:
            print(f"    failure: {_printable(step.failure_kind)}: {_printable(step.message or '')}")
        for attempt in step.attempts:
            result = attempt.result
            detail = (
                f"success basis={result.basis.value}"
                if result.status == "success"
                else f"failure {result.kind.value}"
            )
            print(
                f"    attempt {attempt.attempt_no} ({_printable(attempt.agent_task_id)}): {detail}"
            )


def _run_traces_list(db: Path, as_json: bool) -> int:
    try:
        with TraceStore.open_existing(db) as store:
            listing = store.list()
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    if as_json:
        print(json.dumps(listing.model_dump(mode="json"), indent=2))
    else:
        for r in listing.runs:
            print(
                f"{_printable(r.run_id)}  {r.status.value:<11} {r.started_at.isoformat()}  "
                f"task={_printable(r.task_id)}  steps={r.step_count}"
            )
        if not listing.runs and not listing.malformed:
            print("no runs recorded")
    if listing.malformed:
        ids = ", ".join(_printable(i) for i in listing.malformed)
        print(f"{len(listing.malformed)} malformed run(s) not shown: {ids}", file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    return EXIT_OK


def _run_traces_show(run_id: str, db: Path, as_json: bool) -> int:
    try:
        with TraceStore.open_existing(db) as store:
            trace = store.get(run_id)
    except TraceNotFoundError:
        print(f"no run has the id {_printable(run_id)!r}", file=sys.stderr)
        return EXIT_TRACE_NOT_FOUND
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    if as_json:
        print(json.dumps(trace.model_dump(mode="json"), indent=2))
    else:
        _print_trace(trace)
    return EXIT_OK


def _make_embedder() -> "Embedder":
    # Imported here so other commands do not pay for loading chromadb and torch.
    from cognitive_mesh.memory import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()


def _run_memory(args: argparse.Namespace) -> int:
    from cognitive_mesh.memory import MemoryIndex, MemoryIndexError

    try:
        embedder = _make_embedder()
        if args.memory_command == "index":
            with TraceStore.open_existing(args.db) as store:
                report = MemoryIndex.open_for_write(args.index, embedder).index_traces(store)
            if args.as_json:
                print(json.dumps(report.model_dump(mode="json"), indent=2))
            else:
                print(
                    f"indexed: {report.added} added, {report.updated} updated, "
                    f"{report.unchanged} unchanged, {report.orphaned} orphaned"
                )
            if report.malformed_runs:
                ids = ", ".join(_printable(i) for i in report.malformed_runs)
                print(
                    f"{len(report.malformed_runs)} malformed run(s) skipped: {ids}", file=sys.stderr
                )
                return EXIT_TRACE_STORE_ERROR
            return EXIT_OK
        with TraceStore.open_existing(args.db) as store:
            index = MemoryIndex.open_existing(args.index, embedder)
            found = index.search(args.query, store, k=args.k, min_similarity=args.min_similarity)
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    except MemoryIndexError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_MEMORY_ERROR
    except ValueError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if args.as_json:
        print(json.dumps(found.model_dump(mode="json"), indent=2))
        return EXIT_OK
    for hit in found.hits:
        basis = f" basis={hit.basis}" if hit.basis else ""
        print(
            f"{hit.similarity:.3f}  {_printable(hit.run_id)} step={_printable(hit.step_id)} "
            f"attempt={hit.attempt_no} {hit.capability} {hit.result_status}{basis}"
        )
    for miss in found.unverified:
        print(
            f"{miss.similarity:.3f}  {_printable(miss.run_id)} step={_printable(miss.step_id)} "
            f"NOT CONFIRMED ({miss.reason.value})"
        )
    if found.message:
        print(found.message)
    if found.dropped_weak:
        print(f"{found.dropped_weak} weak match(es) below {found.min_similarity} dropped")
    print(found.note)
    return EXIT_OK


def _run_recall(args: argparse.Namespace) -> int:
    return _guarded(lambda: _recall(args))


def _recall(args: argparse.Namespace) -> int:
    """Needs no model provider and reads no config; only the embedding model is loaded."""
    from cognitive_mesh.experience import store as graph_store
    from cognitive_mesh.memory import MemoryIndex
    from cognitive_mesh.retrieval import recall

    loaded = graph_store.load(args.graph)
    reviews, current = _evidence_inputs(args)
    today = utc_today()
    with TraceStore.open_existing(args.db) as store:
        index = MemoryIndex.open_existing(args.index, _make_embedder())
        found = recall(
            args.query,
            index,
            store,
            loaded.snapshot,
            reviews=reviews,
            outcomes=_outcomes_input(args.outcomes) if args.outcomes is not None else None,
            current=current,
            k=args.k,
            min_similarity=args.min_similarity,
            today=today,
            environment=current_environment(),
        )
    if args.as_json:
        print(json.dumps(found.model_dump(mode="json"), indent=2))
        return EXIT_OK
    print(f"date used for expiry: {today.isoformat()} (UTC)")
    if current is None:
        print("no --root given: whether claims are current is unknown")
    else:
        print(f"checked against the application at code version {current.fingerprint[:12]}")
    if found.claims:
        print(f"claims ({len(found.claims)}):")
    for item in found.claims:
        a = item.assessment
        print(f"  [{a.status.value}] {_printable(a.name)}")
        for v in item.retrieved_via:
            print(f"    retrieved via {_printable(v.doc_id)} (similarity {v.similarity:.3f})")
        _print_assessment(a)
    if found.experiences_without_claims:
        print(f"raw experiences without claims ({len(found.experiences_without_claims)}):")
    for raw in found.experiences_without_claims:
        print(
            f"  [{raw.kind}] {raw.similarity:.3f}  {_printable(raw.run_id)} "
            f"step={_printable(raw.step_id)} attempt={raw.attempt_no} {_printable(raw.capability)}"
        )
        print(f"    {raw.label}")
    if found.unconfirmed:
        print(f"not confirmed ({len(found.unconfirmed)}), never offered as support:")
    for miss in found.unconfirmed:
        print(
            f"  {miss.similarity:.3f}  {_printable(miss.run_id)} step={_printable(miss.step_id)} "
            f"attempt={miss.attempt_no}  NOT CONFIRMED ({miss.reason})"
        )
    if found.message:
        print(found.message)
    if found.dropped_weak:
        print(f"{found.dropped_weak} weak match(es) below {found.min_similarity} dropped")
    for note in found.notes:
        print(note)
    return EXIT_OK


def _run_task(args: argparse.Namespace) -> int:
    """Run a plan with the experience recalled for the request, and record what was offered."""
    from cognitive_mesh import workflow

    try:
        workflow.validate_request(args.request)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    loaded = _load_run_inputs(args.plan, args.root)
    if isinstance(loaded, int):
        return loaded
    validated, ingestion, registry = loaded
    identity = _identity_for(ingestion.root, args.app_id)
    if isinstance(identity, int):
        return identity
    graph = build_dependency_graph(ingestion)
    try:
        store = TraceStore.create(args.db)
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    with store:
        return _guarded(
            lambda: _task_with_store(args, store, validated, ingestion, graph, registry, identity)
        )


def _task_with_store(
    args: argparse.Namespace,
    store: TraceStore,
    validated: ValidatedPlan,
    ingestion: IngestionResult,
    graph: DependencyGraph,
    registry: AgentRegistry,
    identity: AppIdentity | None,
) -> int:
    from cognitive_mesh import workflow

    default_k, default_cutoff = workflow.PROFILE_DEFAULTS[args.profile]
    prepared = workflow.prepare_context(
        args.request,
        ingestion=ingestion,
        graph=graph,
        store=store,
        index_dir=args.index,
        graph_file=args.graph,
        reviews_file=args.reviews,
        make_embedder=_make_embedder,
        k=args.k if args.k is not None else default_k,
        min_similarity=(args.min_similarity if args.min_similarity is not None else default_cutoff),
        identity=identity,
        today=utc_today(),
        outcomes_file=args.outcomes,
        profile=args.profile,
    )
    pack = prepared.gated.pack
    recorder = TraceRecorder(store)
    try:
        run_id = recorder.begin(
            validated,
            ingestion,
            recalled=pack,
            request_length=len(args.request),
            identity=identity,
        )
    except TraceStoreError as exc:  # nothing ran: the offered context could not be recorded
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    context = AgentContext(ingestion=ingestion, graph=graph, recalled=pack)
    run = Orchestrator(registry, now=utc_now).run(validated, context)
    saved = True
    try:
        recorder.finish(run_id, run)
    except TraceStoreError as exc:
        saved = False
        print(f"warning: the trace was not saved: {_printable(str(exc))}", file=sys.stderr)
    if args.as_json:
        print(
            json.dumps(_task_payload(prepared, run, run_id if saved else None, identity), indent=2)
        )
    else:
        _print_identity(identity)
        _print_context(prepared)
        _print_run(run)
        if saved:
            print(f"trace: {run_id}")
    if not saved:
        return EXIT_TRACE_STORE_ERROR
    return EXIT_OK if run.status is TaskStatus.SUCCEEDED else EXIT_TASK_NOT_SUCCEEDED


def _print_identity(identity: AppIdentity | None) -> None:
    if identity is None:
        print("application id: none (offered context passes the older directory-name gate only)")
        return
    weak = "; weak, a directory name only" if identity.weak else ""
    print(f"application id: {_printable(identity.app_id)} [{identity.how.value}{weak}]")


def _task_payload(
    prepared: "PreparedContext", run: TaskRun, trace_id: str | None, identity: AppIdentity | None
) -> dict[str, object]:
    return {
        "application_id": identity.model_dump(mode="json") if identity else None,
        "context": prepared.gated.pack.model_dump(mode="json"),
        "not_applicable": [n.model_dump(mode="json") for n in prepared.gated.not_applicable],
        "not_searched": list(prepared.not_searched),
        "notes": list(prepared.recall.notes) if prepared.recall else [],
        "run": run.model_dump(mode="json"),
        "trace_id": trace_id,
    }


def _print_context(prepared: "PreparedContext") -> None:
    """What the agents are given, and what was kept from them. Nothing here is verified truth."""
    pack = prepared.gated.pack
    print("prior context for this task (from earlier runs; data, not verified truth):")
    for why in prepared.not_searched:
        print(f"  not searched: {why}")
    if pack.empty:
        print(f"  {_printable(pack.message)}")
    for claim in pack.claims:
        print(f"  [{_printable(claim.status)}] {_printable(claim.name)}")
        print(f"    {_printable(claim.meaning)}")
        for s in claim.supporting:
            print(f"    supported by {_printable(s.doc_id or s.run_id)}")
        for c in claim.contradicting:
            kind = "same version" if c.same_version else "other or unrecorded version"
            print(f"    contradicted by {_printable(c.doc_id or c.run_id)} ({kind})")
        if claim.confirmed_reviews or claim.rejected_reviews:
            print(
                f"    reviews: {claim.confirmed_reviews} confirmed, "
                f"{claim.rejected_reviews} rejected"
            )
        if (line := _outcome_line(claim)) is not None:
            print(f"    {line}")
    if pack.outcomes_consulted is False:
        print(f"  {_outcomes_note(False)}")
    for raw in pack.raw:
        print(
            f"  [raw: {_printable(raw.kind)}] {_printable(raw.run_id)} "
            f"step={_printable(raw.step_id)} {_printable(raw.capability)}  ({raw.label})"
        )
    w = pack.withheld
    print(
        f"  withheld from the agents: {w.unconfirmed} unconfirmed, "
        f"{w.not_applicable} not applicable, {w.over_limit} over the size limit"
    )
    print(f"  profile: {pack.profile}; conflicts kept: {pack.conflicts_kept}")
    if pack.conflicts_over_limit:
        print("  the conflicting claims alone exceed the size limit; all of them are included")
    for d in w.dropped:
        print(f"  dropped for size: {d.kind} {_printable(d.item_id)} {_printable(d.status or '')}")
    for held in prepared.gated.not_applicable:
        print(
            f"  retrieved but not applicable ({_printable(held.reason)}): "
            f"{_printable(held.label)} (similarity {held.similarity:.3f})"
        )
    if prepared.recall is not None:
        for note in prepared.recall.notes:
            print(note)


def _run_experience(args: argparse.Namespace) -> int:
    handlers = {
        "build": _experience_build,
        "show": _experience_show,
        "claims": _experience_claims,
        "check": _experience_check,
        "review": _experience_review,
        "outcome": _experience_outcome,
        "promote": _experience_promote,
    }
    return _guarded(lambda: handlers[args.experience_command](args))


def _guarded(action: "Callable[[], int]") -> int:
    """Run a graph/evidence command and map its known failures to exit codes."""
    from cognitive_mesh.experience import ExperienceGraphError, ExperienceNodeNotFoundError
    from cognitive_mesh.experience.outcomes import OutcomeStoreError
    from cognitive_mesh.experience.reviews import ReviewStoreError
    from cognitive_mesh.memory import MemoryIndexError

    try:
        return action()
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    except ExperienceGraphError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_EXPERIENCE_ERROR
    except ExperienceNodeNotFoundError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_EXPERIENCE_NODE_NOT_FOUND
    except ReviewStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_REVIEW_STORE_ERROR
    except OutcomeStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_OUTCOME_STORE_ERROR
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    except MemoryIndexError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_MEMORY_ERROR
    except ValueError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_CONFIG_ERROR


def _evidence_inputs(
    args: argparse.Namespace,
) -> "tuple[list[Review], CurrentSource | None]":
    """Optional reviews and current source for evidence status. Failures raise to `_guarded`."""
    from cognitive_mesh.experience.current import observe_current
    from cognitive_mesh.experience.reviews import ReviewStore

    reviews: list[Review] = []
    if args.reviews is not None:
        with ReviewStore.open_existing(args.reviews) as review_store:
            reviews = review_store.list_reviews()
    current = observe_current(args.root) if args.root is not None else None
    return reviews, current


def _outcomes_input(path: Path) -> "list[Outcome]":
    """Every outcome in an existing outcomes file, oldest first. Failures raise to `_guarded`."""
    from cognitive_mesh.experience.outcomes import OutcomeStore

    with OutcomeStore.open_existing(path) as outcome_store:
        return outcome_store.list_outcomes()


def _fp(fingerprint: str | None) -> str:
    return fingerprint[:12] if fingerprint else "unrecorded"


def _print_assessment(a: "ClaimAssessment", indent: str = "    ") -> None:
    """Everything behind a status: both sides, the reviews, and what the status means."""
    c = a.counts
    print(f"{indent}corroboration: {a.corroboration_reason}")
    for g in a.sources:
        print(f"{indent}  source {g.source_class.value}: {_printable(', '.join(g.records))}")
    print(
        f"{indent}support: {c.supporting_ok} confirmed, {c.supporting_not_ok} not confirmed, "
        f"{c.distinct_fingerprints} code version(s); against: {c.contradicting_same_version} "
        f"same version, {c.contradicting_other_version} other or unrecorded version"
    )
    for s in a.supporting:
        print(
            f"{indent}supported by {_printable(s.doc_id or s.attempt_id)} "
            f"version={_fp(s.fingerprint)} source={s.source.value}"
        )
    for x in a.contradicting:
        kind = "same version" if x.same_version else "other or unrecorded version"
        print(
            f"{indent}contradicted by {_printable(x.doc_id or x.attempt_id)} "
            f"(complete listing without it) version={_fp(x.fingerprint)} {kind}"
        )
    for r in a.reviews:
        where = "current" if r.fingerprint == a.current_fingerprint else "other version"
        print(
            f"{indent}review #{r.id} {r.verdict.value} ({where}, version={_fp(r.fingerprint)}) "
            f"by {_printable(r.reviewer)}: {_printable(r.note)}"
        )
    for o in a.outcomes:
        where = "current" if o.fingerprint == a.current_fingerprint else "other version"
        print(
            f"{indent}outcome #{o.id} {o.result.value} ({o.source.value}, {where}, "
            f"version={_fp(o.fingerprint)}) by {_printable(o.recorder)}: {_printable(o.detail)}"
        )
    if a.rederivation is not None:
        print(f"{indent}re-derived from the current source: {a.rederivation.holding.value}")
        for e in a.rederivation.evidence:
            line = f":{e.line}" if e.line else ""
            print(f"{indent}  {_printable(e.path)}{line} ({e.rule}) sha256={e.sha256[:12]}")
    if a.validated_by:
        print(f"{indent}validated by: {', '.join(a.validated_by)}")
    if a.validity is not None and a.validity.state.value not in ("in_force", "unscoped"):
        until = f" (valid_until {a.valid_until.isoformat()})" if a.valid_until else ""
        print(f"{indent}validity: {a.validity.state.value}{until}: {a.validity.reason}")
    print(f"{indent}{a.status.value}: {a.meaning}")


def _experience_review(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience import ExperienceNodeNotFoundError, NodeKind
    from cognitive_mesh.experience.current import observe_current
    from cognitive_mesh.experience.reviews import ReviewStore, Verdict

    graph = _load_graph(args.graph)
    node = graph.node(args.claim_id)
    if node.kind is not NodeKind.CLAIM:
        raise ExperienceNodeNotFoundError("the node is not a claim")
    current = observe_current(args.root)
    with ReviewStore.create(args.reviews) as review_store:
        review = review_store.add(
            node.id,
            current.fingerprint,
            Verdict(args.verdict),
            args.note,
            args.by,
            valid_until=args.valid_until,
        )
    if args.as_json:
        print(json.dumps(review.model_dump(mode="json"), indent=2))
    else:
        print(
            f"review #{review.id} recorded: {review.verdict.value} {_printable(node.name)} "
            f"for code version {review.fingerprint[:12]}"
        )
        print("a review is a person's assertion, unauthenticated; it does not prove the claim")
    return EXIT_OK


def _experience_outcome(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience import ExperienceNodeNotFoundError, NodeKind
    from cognitive_mesh.experience.current import observe_current
    from cognitive_mesh.experience.outcomes import OutcomeResult, OutcomeSource, OutcomeStore

    graph = _load_graph(args.graph)
    node = graph.node(args.claim_id)
    if node.kind is not NodeKind.CLAIM:
        raise ExperienceNodeNotFoundError("the node is not a claim")
    current = observe_current(args.root)
    with OutcomeStore.create(args.outcomes) as outcome_store:
        row = outcome_store.add(
            node.id,
            current.fingerprint,
            OutcomeSource(args.source),
            OutcomeResult(args.result),
            args.detail,
            args.by,
            valid_until=args.valid_until,
        )
    if args.as_json:
        print(json.dumps(row.model_dump(mode="json"), indent=2))
    else:
        print(
            f"outcome #{row.id} recorded: {row.result.value} ({row.source.value}) for "
            f"{_printable(node.name)} at code version {row.fingerprint[:12]}"
        )
        print("an outcome is a recorded check or a person's account; it does not prove the claim")
    return EXIT_OK


def _unmet_checks(a: "ClaimAssessment") -> list[str]:
    """What a promotion needs and lacks. Empty means it may proceed (ADR-0018)."""
    from cognitive_mesh.experience.assess import ClaimStatus, OutcomeState
    from cognitive_mesh.experience.current import Holding
    from cognitive_mesh.experience.reviews import Verdict

    if a.status is ClaimStatus.VALIDATED:
        return []
    unmet = [f"status is {a.status.value}, not validated"]
    if a.rederivation is None or a.rederivation.holding is not Holding.HOLDS:
        unmet.append("no re-derivation holds for the current code (--root)")
    if not any(
        r.verdict is Verdict.CONFIRMED and r.fingerprint == a.current_fingerprint for r in a.reviews
    ):
        unmet.append("no confirming review for the current code version")
    if a.outcome is not OutcomeState.SUCCEEDED:
        unmet.append("no successful outcome for the current code version")
    return unmet


def _experience_promote(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience import ExperienceNodeNotFoundError, NodeKind
    from cognitive_mesh.experience.assess import assess_claims, attempts_behind
    from cognitive_mesh.experience.outcomes import OutcomeResult, OutcomeSource, OutcomeStore
    from cognitive_mesh.experience.verify import SourceReport, check_attempts

    graph = _load_graph(args.graph)
    with TraceStore.open_existing(args.db) as store:
        node = graph.node(args.claim_id)
        if node.kind is not NodeKind.CLAIM:
            raise ExperienceNodeNotFoundError("the node is not a claim")
        # ADR-0028: only the attempts this claim is judged by are checked, as `recall` does
        report = SourceReport(
            statuses=check_attempts(graph, store, attempts_behind(graph, [node])),
            claim_support={},
            diagnostics=(),
        )
    reviews, current = _evidence_inputs(args)
    outcomes = _outcomes_input(args.outcomes) if args.outcomes.is_file() else []
    assessment = assess_claims(
        graph,
        report,
        reviews,
        current,
        outcomes,
        today=utc_today(),
        environment=current_environment(),
    )[node.id]
    unmet = _unmet_checks(assessment)
    refusal_id = None
    if unmet and current is not None:
        with OutcomeStore.create(args.outcomes) as outcome_store:
            refusal_id = outcome_store.add(
                node.id,
                current.fingerprint,
                OutcomeSource.PROMOTION,
                OutcomeResult.REJECTED,
                "; ".join(unmet),
                args.by,
            ).id
    accepted = not unmet
    if args.as_json:
        payload = {
            "claim_id": node.id,
            "accepted": accepted,
            "status": assessment.status.value,
            "unmet": unmet,
            "refusal_outcome_id": refusal_id,
        }
        print(json.dumps(payload, indent=2))
    elif accepted:
        print(f"promotion accepted: {_printable(node.name)} is validated for the current code")
    else:
        print(f"promotion refused: {_printable(node.name)}")
        for reason in unmet:
            print(f"  unmet: {reason}")
        print(f"a refused promotion is recorded as outcome #{refusal_id}")
    return EXIT_OK if accepted else EXIT_PROMOTION_REFUSED


def _load_graph(path: Path) -> "ExperienceGraph":
    from cognitive_mesh.experience import ExperienceGraph
    from cognitive_mesh.experience import store as graph_store

    return ExperienceGraph(graph_store.load(path).snapshot)


def _experience_open(
    args: argparse.Namespace,
) -> "tuple[LoadedGraph, ExperienceGraph, SourceReport]":
    """Load the graph file (hash and structure checked), then re-check it against the traces."""
    from cognitive_mesh.experience import ExperienceGraph, check_sources
    from cognitive_mesh.experience import store as graph_store

    loaded = graph_store.load(args.graph)
    with TraceStore.open_existing(args.db) as store:
        report = check_sources(loaded.snapshot, store)
    return loaded, ExperienceGraph(loaded.snapshot), report


def _experience_build(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience import DiagnosticCode, ExperienceGraph, build_from_traces
    from cognitive_mesh.experience import store as graph_store

    with TraceStore.open_existing(args.db) as store:
        snapshot = build_from_traces(store)
    digest = graph_store.save(args.graph, snapshot)
    summary = ExperienceGraph(snapshot).summary()
    claims = summary["nodes"].get("claim", 0)
    if args.as_json:
        payload = {
            "content_sha256": digest,
            **summary,
            "diagnostics": [d.model_dump(mode="json") for d in snapshot.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"graph written: {len(snapshot.nodes)} nodes, {len(snapshot.edges)} edges, "
            f"{claims} claims (all unvalidated)"
        )
        print(f"content sha256: {digest}")
        for d in snapshot.diagnostics:
            print(f"diagnostic {d.code.value}: run={_printable(d.run_id or '-')} {d.message}")
    if any(d.code is DiagnosticCode.MALFORMED_RUN for d in snapshot.diagnostics):
        print("malformed run(s) skipped; see the diagnostics", file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    return EXIT_OK


def _experience_show(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience.report import scoped_node_view, show_payload

    graph = _load_graph(args.graph)
    with TraceStore.open_existing(args.db) as store:
        view = scoped_node_view(
            graph,
            store,
            args.node,
            incoming=args.incoming,
            transitive=args.transitive,
            include_unverified=args.include_unverified,
        )
    node, report, shown, hidden = view.node, view.report, view.shown, view.hidden
    if args.as_json:
        print(json.dumps(show_payload(view), indent=2))
        return EXIT_OK
    print(
        f"{_printable(node.id)} [{node.kind.value}] {_printable(node.name)} "
        f"status={report.statuses[node.id].value}"
    )
    for r in shown:
        via = r.path[-1] if not args.incoming else r.path[0]
        print(
            f"  depth {r.depth} via {via.kind.value}: {_printable(r.node.id)} "
            f"[{r.node.kind.value}] status={report.statuses[r.node.id].value}"
        )
    if hidden:
        print(f"{hidden} element(s) hidden because their trace source is not confirmed")
    return EXIT_OK


def _experience_claims(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience.assess import assess_claims, orphaned_reviews
    from cognitive_mesh.experience.models import CLAIM_NOTE, REPETITION_NOTE
    from cognitive_mesh.experience.report import claims_payload, claims_view

    _, graph, report = _experience_open(args)
    reviews, current = _evidence_inputs(args)
    outcomes = _outcomes_input(args.outcomes) if args.outcomes is not None else []
    today = utc_today()
    assessed = assess_claims(
        graph, report, reviews, current, outcomes, today=today, environment=current_environment()
    )
    orphans = orphaned_reviews(graph, reviews)
    details = args.reviews is not None or args.root is not None
    view = claims_view(
        graph,
        report,
        assessed,
        orphans,
        current,
        today,
        include_unverified=args.include_unverified,
    )
    rows, versions, hidden, meanings = view.rows, view.versions, view.hidden, view.meanings
    if args.as_json:
        print(json.dumps(claims_payload(view), indent=2))
        return EXIT_OK
    for row, n_versions in zip(rows, versions, strict=True):
        a = assessed[str(row["id"])]
        print(
            f"{row['status']}  {len(a.sources)} independent source(s), {a.repeats} dependent "
            f"repeat(s), {a.corroboration.value} ({row['still_confirmed']} still confirmed, "
            f"{n_versions} code version(s))  {_printable(str(row['name']))}  [{a.status.value}]"
        )
        if details or a.status.value == "conflicting":
            _print_assessment(a)
    print(f"date used for expiry: {today.isoformat()} (UTC)")
    if hidden:
        print(f"{hidden} claim(s) hidden because their trace source is not confirmed")
    if not rows and not hidden:
        print("no claims")
    for r in orphans:
        print(f"orphaned review #{r.id}: its claim is not in the graph ({_printable(r.claim_id)})")
    if current is None:
        print("no --root given: whether claims are current is unknown")
    for label, meaning in meanings.items():
        print(f"{label}: {meaning}")
    print(REPETITION_NOTE if details else CLAIM_NOTE)
    return EXIT_OK


def _experience_check(args: argparse.Namespace) -> int:
    from cognitive_mesh.experience.report import check_payload, status_counts

    loaded, _, report = _experience_open(args)
    counts = status_counts(report)
    if args.as_json:
        print(json.dumps(check_payload(loaded, report), indent=2))
    else:
        print(f"graph file OK: content hash matches ({loaded.content_sha256[:12]}...)")
        print(f"built at {loaded.built_at.isoformat()}")
        print("sources: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for d in loaded.snapshot.diagnostics:
            print(f"build diagnostic {d.code.value}: run={_printable(d.run_id or '-')}")
        for d in report.diagnostics:
            print(
                f"diagnostic {d.code.value}: {_printable(d.node_id or '-')} {d.message}",
                file=sys.stderr,
            )
    return EXIT_OK if report.all_ok else EXIT_EXPERIENCE_SOURCES


def main(argv: Sequence[str] | None = None) -> int:
    """With no subcommand, validate configuration. Other subcommands need no config.

    Only `run` can call a model, and only when the plan has a model step and a provider is set.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _run_ingest(args.path, args.as_json, args.include_content)
    if args.command == "run":
        return _run_plan(args.plan, args.root, args.as_json, args.trace_db, args.app_id)
    if args.command == "plan":
        return _run_plan_request(args.request, args.task_id, args.output, args.root)
    if args.command == "traces":
        if args.traces_command == "list":
            return _run_traces_list(args.db, args.as_json)
        return _run_traces_show(args.run_id, args.db, args.as_json)
    if args.command == "memory":
        return _run_memory(args)
    if args.command == "experience":
        return _run_experience(args)
    if args.command == "recall":
        return _run_recall(args)
    if args.command == "task":
        return _run_task(args)
    if args.command == "agents":
        return _run_agents(args.as_json)
    if args.command == "graph":
        if args.fmt is not None:
            if args.as_json or args.deps_of is not None or args.transitive:
                parser.error("--format cannot be combined with --json, --deps-of or --transitive")
            return _run_graph_view(args.path, args.fmt, args.output, args.max_nodes, args.max_edges)
        if args.output is not None or args.max_nodes is not None or args.max_edges is not None:
            parser.error("--output, --max-nodes and --max-edges require --format")
        return _run_graph(args.path, args.as_json, args.deps_of, args.transitive)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"cognitive-mesh {__version__}: config OK (provider={settings.provider})")
    return EXIT_OK
