"""The chat server's reads and its explicit actions (ADR-0044).

`chat.send` starts a turn on a worker thread: the conductor plans and assigns models, then either
a small change is made in a separate worktree (and an approval request is opened), or a question
is answered. Nothing that changes a project, pushes or records a verdict happens without a POST
that quotes an unused approval request bound to the diff that was shown. Model text is stored and
returned as plain strings; the page shows it as text only.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cognitive_mesh import workflow
from cognitive_mesh.agents import AgentContext, default_registry
from cognitive_mesh.agents.context import PastChange, RecalledContext
from cognitive_mesh.change.cli import approve_change, open_pull_request
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.github import PublishError
from cognitive_mesh.change.models import (
    ChangeEvent,
    ChangeRecord,
    ChangeStatus,
    current_commit,
    derive_status,
    latest_check,
)
from cognitive_mesh.change.pipeline import ChangeRequest, make_change
from cognitive_mesh.config import ConfigError, load_settings
from cognitive_mesh.decisions.models import DecisionError, MadeBy
from cognitive_mesh.decisions.record import FileView, prepare_decision
from cognitive_mesh.evaluate.memory import memory_for_task, refresh_memory
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.identity import derive_identity
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.orchestrator import Orchestrator
from cognitive_mesh.orchestrator.conduct import (
    ConductorError,
    ConductorInputs,
    conduct,
    eligible_entries,
    model_roles,
)
from cognitive_mesh.orchestrator.conduct_record import (
    build_conductor_decisions,
    describe_assignments,
)
from cognitive_mesh.orchestrator.models import PlanError
from cognitive_mesh.orchestrator.planner import plan_task
from cognitive_mesh.orchestrator.routing_evidence import EvidenceTable
from cognitive_mesh.providers import (
    CatalogError,
    ProviderError,
    load_catalog,
)
from cognitive_mesh.providers.factory import build_entry_client, entry_problem
from cognitive_mesh.providers.recording import CallLog, RecordingClient
from cognitive_mesh.providers.routing import RoutedClient
from cognitive_mesh.serve.chat_store import ChatStore, ChatStoreError, clean_text
from cognitive_mesh.serve.config import EmbedderHolder, ServeConfig
from cognitive_mesh.serve.http import ApiError, Handler, Reply, Request, need, need_int
from cognitive_mesh.serve.ws_cache import WorkspaceGraphCache
from cognitive_mesh.traces import (
    TraceRecorder,
    TraceStore,
    TraceStoreError,
    source_fingerprint,
    utc_now,
)
from cognitive_mesh.workspace.cli import load_workspace
from cognitive_mesh.workspace.fetch import (
    FetchError,
    fetch_github,
    looks_like_url,
    normalise_github_url,
    parse_github_url,
    run_git_with,
)
from cognitive_mesh.workspace.models import ProjectKind, WorkspaceError
from cognitive_mesh.workspace.render import to_json
from cognitive_mesh.workspace.store import WorkspaceStore

MAX_DIFF_SHOWN: Final = 100_000
PERSON: Final = "web user"
ACTIONS: Final = (
    "projects.add",
    "projects.attach",
    "projects.link",
    "projects.confirm",
    "chat.send",
    "change.revise",
    "change.approve",
    "change.reject",
    "change.pr",
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Add(_Body):
    source: str = Field(min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=100)


class _Attach(_Body):
    parent: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=100)


class _Link(_Body):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class _Confirm(_Body):
    project: str = Field(min_length=1, max_length=200)


class _Send(_Body):
    session: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)
    memory: bool = True
    mode: Literal["change", "ask"] = "change"


class _Decide(_Body):
    approval_id: str = Field(min_length=16, max_length=64)
    change_id: str = Field(min_length=1, max_length=200)
    diff_sha256: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


def _parse[T: BaseModel](model: type[T], body: Any) -> T:
    try:
        return model.model_validate(dict(body or {}))
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "body" for e in exc.errors()})
        raise ApiError(400, "the request is not valid (" + ", ".join(fields) + ")") from None


def change_diff(record: ChangeRecord, commit: str | None) -> tuple[str, int]:
    """(diff text from the worktree, characters cut). Empty when it cannot be read."""
    worktree = Path(record.worktree)
    if commit is None or not worktree.is_dir():
        return "", 0
    try:
        done = run_git_with(
            ["diff", "--no-color", record.base_commit, commit], worktree, timeout_s=30
        )
    except FetchError:
        return "", 0
    if done.returncode != 0:
        return "", 0
    text = done.stdout
    return text[:MAX_DIFF_SHOWN], max(0, len(text) - MAX_DIFF_SHOWN)


class AppApi:
    def __init__(
        self, config: ServeConfig, chat: ChatStore, project_graph: WorkspaceGraphCache
    ) -> None:
        self.config = config
        self.chat = chat
        self.project_graph = project_graph
        self.embedder = EmbedderHolder(config.embedder_factory)
        self._threads: list[threading.Thread] = []

    def routes_get(self) -> dict[str, Handler]:
        return {
            "/api/state": self.state,
            "/api/graph": self.graph,
            "/api/sessions": self.sessions,
            "/api/session": self.session,
            "/api/turn": self.turn,
            "/api/trace": self.trace,
        }

    def routes_post(self) -> dict[str, Handler]:
        return {
            "/api/projects/add": self.projects_add,
            "/api/projects/attach": self.projects_attach,
            "/api/projects/link": self.projects_link,
            "/api/projects/confirm": self.projects_confirm,
            "/api/chat/send": self.chat_send,
            "/api/change/revise": self.change_revise,
            "/api/change/approve": self.change_approve,
            "/api/change/reject": self.change_reject,
            "/api/change/pr": self.change_pr,
        }

    def wait(self, timeout: float = 60.0) -> None:
        """Join the worker threads (tests and shutdown)."""
        for thread in list(self._threads):
            thread.join(timeout)

    # -- reads -----------------------------------------------------------------------------

    def _workspace(self) -> tuple[Any, Any] | None:
        if not self.config.workspace.is_file():
            return None
        try:
            return load_workspace(self.config.workspace)
        except WorkspaceError:
            return None

    def _stored_projects(self) -> list[Any]:
        """The projects in the workspace database, without analysing their files. A project the
        analysis cannot read must still be listed, or it looks as if adding it failed."""
        if not self.config.workspace.is_file():
            return []
        try:
            with WorkspaceStore.open_existing(self.config.workspace) as store:
                return list(store.projects())
        except WorkspaceError:
            return []

    def graph(self, req: Request) -> Reply:
        """The dependency graph of the stored projects. Reading every file of every project can
        take a long time, so this is separate from `state` (which must answer at once) and the
        analysis is kept (memory and disk) until the workspace changes or a refresh is asked."""
        return 200, self.project_graph.get(refresh=req.params.get("refresh") == "1")

    def state(self, _req: Request) -> Reply:
        projects = [
            {
                "name": p.name,
                "kind": p.kind.value,
                "location": p.location,
                "changeable": p.kind is ProjectKind.LOCAL,
            }
            for p in self._stored_projects()
        ]
        return 200, {
            "projects": projects,
            "model": {
                "configured": self.config.catalog is not None and self.config.conductor is not None,
                "conductor": self.config.conductor,
            },
            "memory": {"index": self.config.paths.index.is_dir()},
            "ports": {
                "app": self.config.app_port,
                "graph": self.config.graph_port,
                "data": self.config.data_port,
            },
            "running_turn": self.chat.running_turn(),
            "checks": "run (not isolated)" if self.config.allow_unisolated_checks else "not run",
            "actions": list(ACTIONS),
        }

    def sessions(self, _req: Request) -> Reply:
        return 200, {"sessions": self.chat.sessions()}

    def session(self, req: Request) -> Reply:
        session_id = need_int(req.params, "id", 0, 1, 2**31)
        if not self.chat.session_exists(session_id):
            raise ApiError(404, "no such session")
        turns = []
        for turn_id in self.chat.session_turns(session_id):
            turn = self.chat.turn(turn_id)
            if turn is not None:
                turns.append(
                    {
                        **turn,
                        "events": self.chat.events(turn_id),
                        "approvals": self.chat.open_approvals(turn_id),
                    }
                )
        return 200, {"id": session_id, "turns": turns}

    def turn(self, req: Request) -> Reply:
        turn_id = need_int(req.params, "id", 0, 1, 2**31)
        after = need_int(req.params, "after", 0, 0, 2**31)
        turn = self.chat.turn(turn_id)
        if turn is None:
            raise ApiError(404, "no such turn")
        return 200, {
            "turn": turn,
            "events": self.chat.events(turn_id, after),
            "approvals": self.chat.open_approvals(turn_id),
        }

    def trace(self, req: Request) -> Reply:
        """From a chat turn to its decisions, calls and change events (ADR-0044 decision 4e)."""
        turn_id = need_int(req.params, "turn", 0, 1, 2**31)
        turn = self.chat.turn(turn_id)
        if turn is None:
            raise ApiError(404, "no such turn")
        events = self.chat.events(turn_id)
        run_ids = sorted({str(e["data"]["run_id"]) for e in events if e["data"].get("run_id")})
        change_ids = sorted(
            {str(e["data"]["change_id"]) for e in events if e["data"].get("change_id")}
        )
        out: dict[str, Any] = {
            "turn": turn,
            "run_ids": run_ids,
            "changes": [],
            "decisions": [],
            "calls": [],
        }
        if not self.config.trace.is_file():
            return 200, out
        try:
            with TraceStore.open_existing(self.config.trace) as store:
                for record, cevents in store.read_changes():
                    if record.change_id in change_ids:
                        out["changes"].append(
                            {
                                "change_id": record.change_id,
                                "project": record.project,
                                "branch": record.branch,
                                "base_commit": record.base_commit,
                                "status": derive_status(cevents).value,
                                "events": [
                                    {
                                        "kind": e.kind.value,
                                        "commit": e.commit,
                                        "detail": e.detail,
                                        "ref": e.ref,
                                        "at": e.created_at.isoformat(),
                                    }
                                    for e in cevents
                                ],
                            }
                        )
                refs = {e.ref for _, ev in store.read_changes() for e in ev if e.ref}
                refs |= {
                    str(e["data"]["decision_id"]) for e in events if e["data"].get("decision_id")
                }
                for d in store.read_decisions().decisions:
                    if d.run_id in run_ids or d.decision_id in refs:
                        out["decisions"].append(
                            {
                                "decision_id": d.decision_id,
                                "summary": d.summary,
                                "chosen": d.chosen,
                                "reasoning": d.reasoning,
                                "options": list(d.options),
                                "made_by": d.made_by.value,
                                "basis": d.basis.value,
                                "model": d.entry_id,
                                "run_id": d.run_id,
                                "citations": len(d.citations),
                            }
                        )
                for row in store.call_rows().rows:
                    if row.run_id in run_ids:
                        out["calls"].append(
                            {"run_id": row.run_id, **json.loads(row.call.model_dump_json())}
                        )
        except (TraceStoreError, OSError):
            raise ApiError(409, "the trace database cannot be read") from None
        return 200, out

    # -- projects --------------------------------------------------------------------------

    def _register(self, source: str, name: str | None, parent: str | None = None) -> Any:
        """Store a folder or GitHub project in the workspace database. With `parent`, also store a
        declared link parent -> new project, so the new folder appears in the dependency graph."""
        if looks_like_url(source):
            url = normalise_github_url(source)
            owner, repo = parse_github_url(url)
            cache = self.config.home / "workspace-cache"
            directory, commit = fetch_github(url, cache)
            identity = derive_identity(str(directory), declared=f"{owner}/{repo}")
            kind, location, label = ProjectKind.GITHUB, url, name or repo
        else:
            path = Path(source).resolve()
            if path.is_file():
                raise WorkspaceError(
                    "that is a file; add the folder it lives in (files inside a project change "
                    "only through an approved change)"
                )
            if not path.is_dir():
                raise WorkspaceError("the folder does not exist")
            identity = derive_identity(str(path))
            kind, location, commit = ProjectKind.LOCAL, str(path), None
            label = name or (identity.app_id if identity else path.name)
        with WorkspaceStore.create(self.config.workspace) as store:
            project = store.add_project(
                label,
                kind,
                location,
                commit=commit,
                app_id=identity.app_id if identity else None,
                app_id_how=identity.how.value if identity else None,
            )
            if parent is not None:
                store.add_link(
                    parent, project.name, "part of", f"added to {parent} in the chat", PERSON
                )
        return project

    def projects_add(self, req: Request) -> Reply:
        body = _parse(_Add, req.body)
        try:
            project = self._register(body.source, body.name)
        except (WorkspaceError, FetchError) as exc:
            raise ApiError(400, clean_text(str(exc), 300)) from None
        return 200, {"added": project.name, "kind": project.kind.value, "commit": project.commit}

    def projects_attach(self, req: Request) -> Reply:
        body = _parse(_Attach, req.body)
        if body.parent not in {p.name for p in self._stored_projects()}:
            raise ApiError(404, "no project has that name")
        try:
            project = self._register(body.source, body.name, body.parent)
        except (WorkspaceError, FetchError) as exc:
            raise ApiError(400, clean_text(str(exc), 300)) from None
        return 200, {"added": project.name, "parent": body.parent, "kind": project.kind.value}

    def projects_link(self, req: Request) -> Reply:
        body = _parse(_Link, req.body)
        try:
            with WorkspaceStore.create(self.config.workspace) as store:
                link = store.add_link(body.source, body.target, body.kind, body.note, PERSON)
        except WorkspaceError as exc:
            raise ApiError(400, clean_text(str(exc), 300)) from None
        return 200, {"link": link.id, "declared": True}

    def projects_confirm(self, req: Request) -> Reply:
        body = _parse(_Confirm, req.body)
        stored = self._stored_projects()
        if not stored:
            raise ApiError(409, "add projects first")
        match = [p for p in stored if p.name == body.project]
        if not match:
            raise ApiError(404, "no project has that name")
        if match[0].kind is not ProjectKind.LOCAL:
            raise ApiError(409, "only a local folder can be changed; a GitHub project is read-only")
        return 200, {"session": self.chat.new_session(body.project)}

    # -- chat ------------------------------------------------------------------------------

    def chat_send(self, req: Request) -> Reply:
        body = _parse(_Send, req.body)
        try:
            workflow.validate_request(body.text)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        if not self.chat.session_exists(body.session):
            raise ApiError(404, "no such session")
        if self.config.catalog is None or self.config.conductor is None:
            raise ApiError(
                409, "no model is configured: start the server with --catalog and --conductor"
            )
        return 200, {"turn": self._start_turn(body.session, body.text, body.memory, body.mode)}

    def _session_project(self, session_id: int) -> str:
        for row in self.chat.sessions():
            if row["id"] == session_id:
                return str(row["project"])
        raise ApiError(404, "no such session")

    def _start_turn(self, session_id: int, text: str, memory: bool, mode: str) -> int:
        project = self._session_project(session_id)
        running = self.chat.running_turn()
        if running is not None:
            raise ApiError(409, f"turn {running} is still running; one task runs at a time")
        turn_id = self.chat.begin_turn(session_id, project, text, memory, mode)
        thread = threading.Thread(
            target=self._worker, args=(turn_id, project, text, memory, mode), daemon=True
        )
        self._threads.append(thread)
        thread.start()
        return turn_id

    def _worker(self, turn_id: int, project: str, text: str, memory: bool, mode: str) -> None:
        try:
            self._run_turn(turn_id, project, text, memory, mode)
        except _Stop as stop:
            self.chat.add_event(turn_id, stop.kind, {"message": stop.message})
        except (ChangeError, ConfigError, CatalogError, IngestionError, WorkspaceError) as exc:
            self.chat.add_event(turn_id, "failed", {"message": clean_text(str(exc), 400)})
        except (TraceStoreError, ChatStoreError, DecisionError, ProviderError, OSError) as exc:
            self.chat.add_event(turn_id, "failed", {"message": f"stopped: {type(exc).__name__}"})
        except Exception as exc:  # a worker must always close its turn
            self.chat.add_event(turn_id, "failed", {"message": f"stopped: {type(exc).__name__}"})
        finally:
            self.chat.end_worker(turn_id)

    # -- the turn --------------------------------------------------------------------------

    def _run_turn(self, turn_id: int, project: str, text: str, memory: bool, mode: str) -> None:
        cfg = self.config
        assert cfg.catalog is not None and cfg.conductor is not None  # noqa: S101 - chat_send
        loaded = self._workspace()
        if loaded is None:
            raise _Stop("failed", "the workspace is gone")
        graph, reads = loaded
        read = reads.get(project)
        if read is None:
            raise _Stop("failed", "the project is not on disk; run `workspace refresh`")
        root = read.root
        from cognitive_mesh.workspace.access import WorkspaceAccess

        access = WorkspaceAccess(project, graph, reads)
        self.chat.add_event(
            turn_id, "started", {"project": project, "memory": memory, "mode": mode}
        )
        settings = load_settings(provider="catalog", model=cfg.conductor, model_catalog=cfg.catalog)
        assert settings.model_catalog is not None  # noqa: S101 - required with catalog
        catalog = load_catalog(settings.model_catalog)
        ingestion = ingest_application(root)
        dep_graph = build_dependency_graph(ingestion)
        identity = derive_identity(ingestion.root, None)
        calls = CallLog()
        conductor_client = self._client(cfg.conductor, catalog)
        registry = default_registry()
        pending: tuple[ChangeRecord, list[ChangeEvent]] | None = None
        with TraceStore.create(cfg.trace) as store:
            evidence = EvidenceTable(store.call_rows())
            inputs = ConductorInputs(
                registry=registry,
                catalog=catalog,
                problem=lambda entry: entry_problem(settings, entry),
                evidence=evidence,
                files=[f.ref.path for f in ingestion.files],
                workspace=_workspace_summary(graph),
                current_project=project,
            )
            task_id = "chat"
            try:
                conducted = conduct(
                    _conductor_request(text, mode),
                    inputs,
                    RecordingClient(conductor_client, calls, cfg.conductor),
                    task_id=task_id,
                    begin_draft=lambda n: calls.begin_conductor(task_id, n),
                    timeout_s=settings.model_timeout_s,
                )
                validated = plan_task(conducted.plan.plan, registry)
            except (ConductorError, PlanError) as exc:
                reasons = "; ".join(getattr(exc, "refusals", ()))
                raise _Stop(
                    "failed",
                    "the conductor made no usable plan: "
                    + clean_text(str(exc) + (f" ({reasons})" if reasons else ""), 900),
                ) from None
            except ProviderError as exc:
                raise _Stop("failed", f"the conductor model failed ({exc.kind.value})") from None
            roles = model_roles(registry)
            steps = conducted.plan.plan.steps
            rows = describe_assignments(conducted, None, steps, roles, evidence)
            self.chat.add_event(
                turn_id,
                "plan",
                {
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "capability": s.capability,
                            "role": roles.get(s.capability, ""),
                        }
                        for s in steps
                    ],
                    "assignments": rows,
                    "reasoning": clean_text(conducted.plan.reasoning, 2000),
                    "basis": "model_unverified",
                },
            )
            self._run_plan(
                turn_id,
                text,
                conducted,
                validated,
                access,
                store,
                ingestion,
                dep_graph,
                identity,
                catalog,
                settings,
                calls,
                roles,
                evidence,
            )
            if mode == "ask":
                self.chat.add_event(turn_id, "done", {"kind": "question"})
            else:
                known = {f.ref.path for f in ingestion.files}
                pending = self._change_flow(
                    turn_id, project, text, memory, steps, known, access, store, catalog, settings
                )
        # the experience is always recorded; `memory` only controls what the coder is offered
        self._refresh(turn_id, text)
        if pending is not None:  # asked only now, so the person never races the memory refresh
            with TraceStore.create(cfg.trace) as store:
                self._request_approval(turn_id, store, pending[0], pending[1], "approve")

    def _client(self, entry_id: str, catalog: Any) -> Any:
        if self.config.client_factory is not None:
            return self.config.client_factory(entry_id)
        return build_entry_client(entry_id, catalog.models[entry_id])

    def _refresh(self, turn_id: int, text: str) -> None:
        try:
            with TraceStore.create(self.config.trace) as store:
                refresh_memory(store, self.config.paths, self.embedder.get())
        except Exception as exc:  # recording must not break the answer already given
            self.chat.add_event(
                turn_id, "note", {"message": f"memory was not refreshed ({type(exc).__name__})"}
            )
        else:
            self.chat.add_event(turn_id, "note", {"message": "this turn was added to memory"})

    def _change_flow(
        self,
        turn_id: int,
        project: str,
        text: str,
        memory: bool,
        steps: Any,
        known: set[str],
        access: Any,
        store: TraceStore,
        catalog: Any,
        settings: Any,
    ) -> tuple[ChangeRecord, list[ChangeEvent]]:
        cfg = self.config
        # The conductor never plans a write (ADR-0040, ADR-0042), so code picks the coder: the
        # first usable catalog entry that has the coder role. The files it read are the hint.
        usable = eligible_entries(catalog, "coder", lambda e: entry_problem(settings, e))
        if not usable:
            raise _Stop("failed", "no usable catalog entry has the coder role")
        entry = usable[0]
        wanted = [str(s.inputs.get("path") or "") for s in steps]
        wanted += [str(f) for s in steps for f in (s.inputs.get("files") or ())]
        files = tuple(dict.fromkeys(p for p in wanted if p in known))[:3]
        self.chat.add_event(
            turn_id,
            "coder",
            {
                "model": entry,
                "files": list(files),
                "why": "the first usable catalog entry with the coder role; the conductor "
                "does not plan writes",
            },
        )
        recalled: RecalledContext | None = None
        past: tuple[PastChange, ...] = ()
        if memory:
            try:
                recalled, past = memory_for_task(
                    text, access=access, store=store, paths=cfg.paths, embedder=self.embedder.get()
                )
            except Exception as exc:
                self.chat.add_event(
                    turn_id, "note", {"message": f"memory was not used ({type(exc).__name__})"}
                )
        self.chat.add_event(
            turn_id,
            "memory",
            {
                "used": recalled is not None,
                "claims": 0 if recalled is None else len(recalled.claims) + len(recalled.raw),
                "past_changes": len(past),
            },
        )

        def factory(calls: CallLog) -> Any:
            client = self._client(entry, catalog)
            return default_registry(
                RecordingClient(client, calls, entry), model_timeout_s=settings.model_timeout_s
            )

        with WorkspaceStore.open_existing(cfg.workspace) as ws:
            check = ws.check_command(project)
        result = make_change(
            ChangeRequest(
                request=text,
                project=project,
                files=files,
                worktrees=cfg.paths.worktrees,
                allow_unisolated_checks=cfg.allow_unisolated_checks,
                recalled=recalled,
                past_changes=past,
            ),
            access=access,
            check=check,
            registry_factory=factory,
            store=store,
            outcomes=cfg.paths.outcomes,
            runner=cfg.check_runner,
        )
        change_id = result.change.change_id
        for d in result.drafts:
            self.chat.add_event(
                turn_id,
                "draft",
                {
                    "draft_no": d.draft_no,
                    "outcome": d.outcome,
                    "run_id": d.run_id,
                    "change_id": change_id,
                    "decision_id": d.decision_id,
                    "model": entry,
                },
            )
        found = next((ev for rec, ev in store.read_changes() if rec.change_id == change_id), [])
        for e in found:
            self.chat.add_event(
                turn_id,
                "change_event",
                {
                    "kind": e.kind.value,
                    "detail": e.detail,
                    "commit": e.commit,
                    "change_id": change_id,
                },
            )
        head = current_commit(found)
        if head is None or head.commit is None or head.diff_sha256 is None:
            raise _Stop("failed", "no draft could be applied; the reasons are in the draft list")
        return result.change, found

    def _request_approval(
        self,
        turn_id: int,
        store: TraceStore,
        record: ChangeRecord,
        events: list[ChangeEvent],
        action: str,
        note: str = "",
    ) -> None:
        head = current_commit(events)
        assert head is not None and head.commit is not None and head.diff_sha256 is not None  # noqa: S101
        diff, cut = change_diff(record, head.commit)
        check = latest_check(events, head.commit)
        summary, reasoning = _draft_text(store, head.ref)
        approval_id = self.chat.open_approval(turn_id, action, record.change_id, head.diff_sha256)
        self.chat.add_event(
            turn_id,
            "approval_request",
            {
                "approval_id": approval_id,
                "action": action,
                "change_id": record.change_id,
                "diff_sha256": head.diff_sha256,
                "commit": head.commit,
                "branch": record.branch,
                "diff": diff,
                "diff_cut": cut,
                "status": derive_status(events).value,
                "check": check.detail if check else "no check ran for this commit",
                "summary": summary,
                "reasoning": reasoning,
                "basis": "model_unverified",
                "note": note,
                "impact": [
                    e.detail
                    for e in events
                    if e.kind.value == "impact" and e.diff_sha256 == head.diff_sha256
                ],
            },
        )

    def _run_plan(
        self,
        turn_id: int,
        text: str,
        conducted: Any,
        validated: Any,
        access: Any,
        store: TraceStore,
        ingestion: Any,
        dep_graph: Any,
        identity: Any,
        catalog: Any,
        settings: Any,
        calls: CallLog,
        roles: dict[str, str],
        evidence: EvidenceTable,
    ) -> None:
        cfg = self.config
        recalled = None
        steps = conducted.plan.plan.steps
        assignments = {sid: a.model for sid, a in conducted.plan.assignments.items()}
        router = RoutedClient(
            catalog,
            settings,
            calls,
            assignments,
            build=lambda entry_id, entry: self._client(entry_id, catalog),
        )
        run_registry = default_registry(router, model_timeout_s=settings.model_timeout_s)
        recorder = TraceRecorder(store)
        pack = None
        run_id = recorder.begin(
            validated, ingestion, recalled=pack, request_length=len(text), identity=identity
        )
        view = FileView(
            source_fingerprint(ingestion), {f.ref.path: f.ref.sha256 for f in ingestion.files}
        )
        decisions = build_conductor_decisions(
            run_id=run_id,
            task_id="chat",
            conducted=conducted,
            conductor_entry=cfg.conductor or "",
            conductor_provider=conducted.response.provider,
            conductor_model=conducted.response.model,
            catalog=catalog,
            problem=lambda entry: entry_problem(settings, entry),
            evidence=evidence,
            capability_roles=roles,
            steps=steps,
            swap=None,
            claim_ids=[],
            view=view,
            app_id=identity.app_id if identity else None,
            now=utc_now(),
        )
        context = AgentContext(
            ingestion=ingestion, graph=dep_graph, recalled=recalled, workspace=access
        )
        run = Orchestrator(run_registry, now=utc_now, on_attempt=router.begin_attempt).run(
            validated, context
        )
        recorder.finish(run_id, run, calls.records, decisions)
        answers = []
        for step in run.steps:
            line: dict[str, Any] = {
                "step_id": step.step_id,
                "capability": step.capability,
                "status": step.status.value,
            }
            if step.failure_kind:
                line["failure"] = step.failure_kind
            if step.attempts:
                last = step.attempts[-1]
                try:
                    data = last.model_dump(mode="json")
                    shown = {"output": data["output"]} if "output" in data else data
                    dumped = json.dumps(_redacted(shown), default=str)
                except (TypeError, ValueError):
                    dumped = "(not shown)"
                line["output"] = dumped
                line["basis"] = "model_unverified"
            answers.append(line)
        self.chat.add_event(
            turn_id, "analysis", {"run_id": run_id, "status": run.status.value, "steps": answers}
        )

    # -- approvals ---------------------------------------------------------------------------

    def _claim(self, body: _Decide, action: str, used_for: str) -> int:
        ok, why, turn_id = self.chat.use_approval(
            body.approval_id, action, body.change_id, body.diff_sha256, used_for
        )
        if not ok or turn_id is None:
            raise ApiError(409, why)
        return turn_id

    def _turn_project_root(self, turn_id: int) -> tuple[str, Path]:
        turn = self.chat.turn(turn_id)
        loaded = self._workspace()
        if turn is None or loaded is None:
            raise ApiError(409, "the turn or the workspace is gone")
        read = loaded[1].get(turn["project"])
        if read is None:
            raise ApiError(409, "the project is not on disk")
        return str(turn["project"]), read.root

    def _reopen(
        self, turn_id: int, store: TraceStore, change_id: str, action: str, note: str
    ) -> None:
        for rec, ev in store.read_changes():
            if rec.change_id == change_id:
                self._request_approval(turn_id, store, rec, ev, action, note)
                return

    def change_approve(self, req: Request) -> Reply:
        body = _parse(_Decide, req.body)
        turn_id = self._claim(body, "approve", "approve")
        _, root = self._turn_project_root(turn_id)
        with TraceStore.create(self.config.trace) as store:
            try:
                _, head, decision = approve_change(
                    store, root, body.change_id, PERSON, body.note or None
                )
            except ChangeError as exc:
                message = clean_text(str(exc), 300)
                self.chat.add_event(turn_id, "note", {"message": message})
                self._reopen(turn_id, store, body.change_id, "approve", message)
                raise ApiError(409, message) from None
            self.chat.add_event(
                turn_id,
                "approved",
                {
                    "change_id": body.change_id,
                    "commit": head.commit,
                    "decision_id": decision.decision_id,
                    "by": PERSON,
                },
            )
            self._reopen(
                turn_id,
                store,
                body.change_id,
                "pr",
                "approved. Opening a pull request pushes the branch to GitHub and sends "
                "the diff there.",
            )
        return 200, {"approved": True, "turn": turn_id}

    def change_reject(self, req: Request) -> Reply:
        body = _parse(_Decide, req.body)
        turn_id = self._claim(body, "approve", "reject")
        with TraceStore.create(self.config.trace) as store:
            found = next(
                ((r, e) for r, e in store.read_changes() if r.change_id == body.change_id), None
            )
            if found is not None:
                record, events = found
                head = current_commit(events)
                try:
                    decision = prepare_decision(
                        summary=f"Reject change {record.change_id}",
                        options=["approve this commit", "do not approve"],
                        chosen="do not approve",
                        reasoning=(body.note or "rejected after reading the diff")
                        + f" (diff sha256 {body.diff_sha256})",
                        made_by=MadeBy.PERSON,
                        recorder=PERSON,
                        cite_decisions=[head.ref] if head and head.ref else [],
                        app_id=record.app_id,
                        now=utc_now(),
                    )
                    store.add_decision(decision)
                    self.chat.add_event(
                        turn_id,
                        "note",
                        {
                            "message": "the rejection was recorded as a decision",
                            "decision_id": decision.decision_id,
                        },
                    )
                except DecisionError:
                    self.chat.add_event(
                        turn_id,
                        "note",
                        {"message": "the rejection could not be recorded as a decision"},
                    )
        self.chat.add_event(
            turn_id,
            "rejected",
            {"change_id": body.change_id, "by": PERSON, "note": clean_text(body.note, 500)},
        )
        return 200, {"rejected": True, "turn": turn_id}

    def change_revise(self, req: Request) -> Reply:
        body = _parse(_Decide, req.body)
        if not body.note.strip():
            raise ApiError(400, "say what should change")
        turn_id = self._claim(body, "approve", "revise")
        turn = self.chat.turn(turn_id)
        assert turn is not None  # noqa: S101 - _claim found it
        self.chat.add_event(turn_id, "rejected", {"change_id": body.change_id, "revised": True})
        text = f"{turn['text']} (revise: {body.note})"[:4000]
        try:
            new_turn = self._start_turn(turn["session_id"], text, turn["memory"], turn["mode"])
        except ApiError:
            raise
        return 200, {"turn": new_turn}

    def change_pr(self, req: Request) -> Reply:
        body = _parse(_Decide, req.body)
        turn_id = self._claim(body, "pr", "pr")
        _, root = self._turn_project_root(turn_id)
        with TraceStore.create(self.config.trace) as store:
            try:
                url, already = open_pull_request(store, root, body.change_id, None, False)
            except (ChangeError, PublishError) as exc:
                message = clean_text(str(exc), 300)
                self.chat.add_event(turn_id, "note", {"message": message})
                self._reopen(turn_id, store, body.change_id, "pr", message)
                raise ApiError(409, message) from None
        self.chat.add_event(
            turn_id,
            "done",
            {"kind": "change", "pr_url": url, "already_open": already, "change_id": body.change_id},
        )
        return 200, {"pr_url": url, "turn": turn_id}


class _Stop(Exception):  # noqa: N818 - control flow inside a worker, not an error type
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _conductor_request(text: str, mode: str) -> str:
    """What the conductor is asked. A plan may never contain a write step, so in change mode the
    conductor is told that a separate coder makes the edit afterwards and it must only plan the
    read-only steps that find the relevant files (the request is still the person's own text)."""
    if mode != "change":
        return text
    wrapped = (
        "Plan ONLY read-only steps (for example explain_file or dependencies_of) on the files "
        "this change needs. Do NOT plan a step that edits, writes, proposes or applies a change: "
        "a separate coder makes the edit after your plan runs. Request: " + text
    )
    return wrapped if len(wrapped) <= 2000 else text  # the conductor's own request limit


def _redacted(value: Any, depth: int = 0) -> Any:
    """The same value with every string redacted and cut, so the JSON stays parseable."""
    if isinstance(value, str):
        return clean_text(value, 3000)
    if depth > 6:
        return "(not shown)"
    if isinstance(value, dict):
        return {str(k)[:100]: _redacted(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list | tuple):
        return [_redacted(v, depth + 1) for v in list(value)[:40]]
    return value


def _workspace_summary(graph: Any) -> dict[str, object]:
    from cognitive_mesh.orchestrator.conduct import workspace_summary

    return workspace_summary(to_json(graph))


def _draft_text(store: TraceStore, decision_id: str | None) -> tuple[str, str]:
    if decision_id is None:
        return "no summary was recorded", ""
    for d in store.read_decisions().decisions:
        if d.decision_id == decision_id:
            return clean_text(d.summary, 500), clean_text(d.reasoning, 2000)
    return "no summary was recorded", ""


__all__ = ["ACTIONS", "AppApi", "ChangeStatus", "need"]
