# Extended Roadmap: from evidence-aware memory to a cross-project coding assistant

Status: **draft for review, nothing here is implemented.** This extends `ROADMAP.md` and `docs/DETAILED_ROADMAP.md`
past v1.5.0 and follows the same rules: a roadmap entry is not evidence that a feature exists, a version is done
only when its criteria pass, and every significant or hard-to-reverse decision gets an ADR that the user accepts
before any code.

## The goal

A chat assistant, first as a local web demo and then as a VS Code extension, that:

1. On first use, asks which projects belong together (GitHub repositories or local folders) and records how they
   depend on each other (packages, API contracts, schemas, config) in a dependency graph where every edge has evidence.
2. For every prompt, lets one **orchestrator model** decide which roles the task needs and which model fills each
   role, and records that decision with its reasons.
3. When a change in the current project touches something other projects depend on, reads those projects
   (read-only), picks a small change that keeps their dependencies intact, and implements it on a new branch with a
   pull request.
4. Records every experience and decision with its reasoning, searchable by meaning and through the graph, and
   traceable from a pull request back to the decisions and evidence behind it.
5. Shows, with recorded numbers, whether agents do better on the same tasks with memory than without it.

## Ground rules for this roadmap (decided with the user, 2026-11-03)

- **Write scope:** an agent may change only the **current project**. Every other project in the workspace is
  read-only, enforced in code, not only in prompts.
- **Landing changes:** always a new branch plus a pull request. Nothing is pushed or merged without the user's approval.
- **Change size:** only small changes (with an explicit limit on files and lines changed) until the user confirms the project is
  ready for large-scale testing.
- **Models:** any model reachable with an API key may be used; free models (for example Gemini's free tier) are
  the default during development.
- **Order:** backend capabilities first, then the local web demo, then the VS Code extension. The demo is local only.
- **Current design stays:** the v1.5.0 read-only UI and all accepted ADRs remain in force until the new design
  (Phase D) is complete and the user approves it.
- **Benchmarking at scale is gated:** Phase F starts only when the user says the project is ready.
- **Principles carry over unchanged:** memory is evidence, not truth; model-written text (including the orchestrator's
  reasoning) is stored as unverified; files, memories and model output are data, never instructions.

## Existing decisions this roadmap must revisit (each needs its own ADR)

| Existing rule | Why the vision conflicts | Version |
|---|---|---|
| ADR-0006: imports are never linked to packages by name match | Linking project A's dependency on package `foo` to project B needs a rule. Proposed: link only when B's own manifest declares the name `foo` (manifest evidence on both sides), never by import name alone | v1.6.0 |
| ADR-0005: analysed code is never executed | Checking a change means running the current project's tests | v1.12.0 |
| ADR-0031: the UI never writes | Chat must start tasks, show diffs and open PRs | v2.0.0 |
| ADR-0013: remote content only with `COGMESH_ALLOW_REMOTE_CONTENT=true` | Free hosted models are the default. Their free tiers may keep or train on prompts, so the opt-in and a per-provider warning must stay | v1.8.0 |
| v1.6.0 in `ROADMAP.md` (model performance tracking and routing) | Folded into v1.8.0 and v1.10.0 below | — |

---

## Phase A: Multi-project workspace

### v1.6.0 Workspace and cross-project package dependencies

#### Implement
- A **workspace**: a named set of projects, each a local folder or a GitHub repository (cloned into a local cache,
  read-only, at a recorded commit). Each project keeps its application identity (ADR-0021) and code fingerprint.
- `workspace add PATH|URL`, `workspace list`, `workspace remove`, stored in a new SQLite file (`--workspace FILE`).
- A **cross-project edge** kind in the dependency graph: project A depends on project B. The first kind is
  `package`: A declares a dependency whose name B's manifest declares as its own package name (pyproject, package.json).
  Evidence on both sides (A's manifest line, B's manifest line).
- **Declared edges**: a person can add an edge the analysis cannot see (`workspace link A B --kind ... --note ...`),
  stored with source `person` and never treated as detected evidence.
- A workspace graph view: the existing text, JSON, Mermaid and HTML renderers, with projects as clusters.
- ADR: workspace model, the clone cache, the name-match rule (amends ADR-0006), whether GitHub access needs a token.

#### Test
- Fixture workspace of three small projects (one publishes a package, two consume it, one consumes a name nobody
  publishes).
- Clone a public repository by URL, offline failure, a private repository without credentials, a moved commit.
- Removing a project removes its edges and keeps its recorded history.
- Persistence: add the projects, restart, list them again.

#### Expected Output
- Every cross-project edge shows evidence from both projects. A dependency on a name no workspace project publishes
  stays external, never guessed.
- Declared and detected edges can always be told apart.

#### Fix If
- An edge is created from an import name without manifest evidence on the publishing side.
- A cloned project is writable through any code path.
- A declared edge is shown as detected.

### v1.7.0 API contracts, schemas and config across projects

#### Implement
- **API contracts:** provider side from OpenAPI or Swagger files and from route declarations in common frameworks
  (FastAPI, Flask, Express) with literal paths. Consumer side from HTTP client calls with a literal method and
  path (`requests`, `httpx`, `fetch`, `axios`). An edge links consumer to provider only when method and path match.
- **Schemas:** SQL migrations and DDL, JSON Schema files, and ORM models (SQLAlchemy, Prisma) as parse-only. An edge when
  two projects use the same table or schema name, marked as `shared_schema`.
- **Config:** environment variable names read in code (`os.environ`, `process.env`) and declared in
  `.env.example` or compose files. An edge when one project sets and another reads the same name. Values are never read
  from real `.env` files (ADR-0005 rules still apply).
- Every unmatched consumer call or read is listed as unresolved, as in ADR-0006.
- Shown in chat terms by `workspace deps PROJECT` ("web calls GET /users on api, evidence: ...").
- ADR: detection rules per kind, what counts as a match, and the limits (computed URLs, gateways and rewrites stay unresolved).

#### Test
- Fixtures with a matching route, a near miss (different method, path parameter style), a computed URL, a shared
  table, a renamed column, an env var set but never read.
- Large OpenAPI file and malformed spec (diagnostic, no crash).
- A secret value in a config file is never stored (it is redacted, and its name is kept only when the name is not itself sensitive).

#### Expected Output
- Each contract edge names the exact provider and consumer locations.
- Unmatched calls are visible, not dropped.

#### Fix If
- Path matching guesses across differing parameters without saying so.
- Any config value (not only the name) is stored.

---

## Phase B: Models and decisions

### v1.8.0 Any model with an API key, and model performance records

#### Implement
- A generic **OpenAI-compatible client** on the existing stdlib transport (covers Gemini through its compatible
  endpoint, OpenRouter free models, Groq, Mistral, local servers), plus the existing Ollama client. Same error
  mapping (`providers/errors.py`), same fixed-message rule, keys only from env as `SecretStr`.
- A **model catalog** (config file, no keys inside): provider, model id, context size, free or paid, and the roles it
  may fill. Per-provider remote-content warning.
- **Performance records** per call, in the trace store: model, provider, role, task type, latency, token counts
  (when the provider reports them, otherwise `unknown`, never zero), outcome link, rate-limit and error kind.
- `models list` and `models stats [--task-type T]`: sample counts are always shown, and nothing is called "best".
- ADR: catalog format, which records are kept, the retention rule, the remote-content rule for free tiers.

#### Test
- Injected-transport tests for each wire format; parity tests against the Ollama and Groq clients.
- 429 and quota-exhausted responses on free tiers, timeouts, a model removed from the provider.
- A missing token count stays unknown. Records never mix task types.
- Opt-in live test per provider (marker plus env var), skipped in CI.

#### Expected Output
- Switching models is a catalog edit, not a code change.
- Stats show sample counts and unknowns plainly.

#### Fix If
- A missing metric is recorded as zero.
- A key appears in any log, error, trace or catalog file.

### v1.9.0 Decision records

#### Implement
- A **decision** record type: what was decided, the options considered, the reasoning given, the evidence it
  cites (graph nodes, files at a commit, earlier decisions, recalled claims), who made it (model and role, or person) and
  when.
- Decisions join the experience graph (`decided_in` run, `cites` evidence, `supersedes` an earlier decision) and the
  vector index, so `recall` finds them by meaning and through the graph.
- Model-written reasoning is stored as `model_unverified`. A decision's status is computed like a claim's: whether
  the cited evidence still holds for the current code and whether the outcome it led to succeeded.
- `decisions list|show|trace`: from a commit, branch or PR back to the decisions and evidence behind it.
- ADR: the record schema, graph and index format bumps, how reasoning text is redacted and capped.

#### Test
- A decision citing a file that later changes reads as stale. A cited node that disappears reads as unsupported.
- Supersession chains, with conflicting decisions shown on both sides.
- Persistence and recall across sessions. A model-written decision is never shown as validated by being repeated.

#### Expected Output
- Any decision can be traced to its evidence and its outcome in one command.

#### Fix If
- Reasoning text is treated as evidence for itself.
- A decision loses its citations when the graph is rebuilt.

---

## Phase C: Dynamic agents and safe cross-project changes

### v1.10.0 Orchestrator model: dynamic role and model assignment

#### Implement
- A **conductor**: one model (chosen in the catalog) gets the request, a summary of the workspace graph, recalled
  experience and decisions, and the model catalog with performance stats. It returns a plan: roles needed (for
  example planner, cross-project reader, coder, reviewer, tester), the model assigned to each, the order, and the reason
  for each choice.
- The conductor's output is data: it is validated by the existing `plan_task` (capabilities exist, no cycles, inputs
  match schemas) plus new checks (assigned models exist in the catalog and are reachable, write roles target only
  the current project). An invalid plan is refused with reasons and may be re-drafted a bounded number of times.
- Assignment fallback: when an assigned model fails (rate limit, timeout), the next catalog model for that role
  takes over and the switch is recorded.
- Routing uses performance records only when the sample count passes a stated minimum. Below it the conductor's
  choice is marked `insufficient_evidence`, and a small, stated share of tasks may explore other models.
- Every conductor plan is a decision record (v1.9.0).
- ADR: conductor input and output contract, role list, validation rules, fallback order, the exploration rule.
  Decided in ADR-0040 (accepted 2026-11-04).

#### Test
- Fake conductor returns plans that are valid, cyclic, reference unknown models, assign a write role to another project,
  or are not JSON.
- One provider only; all providers rate-limited (explicit failure, never silent success).
- Routing with zero, few and enough samples; records go to the right model and task type.
- Opt-in live run with a free model.

#### Expected Output
- For any task, the person can see which roles ran, which model did each, and why.

#### Fix If
- A conductor plan runs without validation.
- A small sample is described as proof that one model is better.

### v1.11.0 Cross-project impact analysis

#### Implement
- Given a target in the current project (file, function, route, table, env var, package version), list every
  project and location that depends on it through the workspace graph, with evidence.
- A read-only **reader** role summarises the relevant files of those projects for the change (other projects are opened
  through a read-only accessor, so no write path exists).
- **Contract snapshot and diff:** extract the current project's provided contracts (routes, schemas, config names,
  package version and exports) before and after a proposed change, and report each difference that a dependent
  uses as `breaking`, `compatible` or `unknown`.
- ADR: what counts as breaking for each contract kind, and how `unknown` is presented.
  Decided in ADR-0041 (accepted 2026-11-04).

#### Test
- Renaming a route a dependent calls is breaking. Adding an optional field is compatible. A computed route is unknown.
- Removing an env var another project reads; changing a column type in a shared table.
- An attempt to open another project for writing raises.

#### Expected Output
- Before any change lands, the person sees which dependents it affects and why.

#### Fix If
- A difference in a contract a dependent uses is shown as compatible without evidence.

### v1.12.0 Small-change agent: branch, checks, pull request

#### Implement
- A **coder** role produces a unified diff for the current project only, within a size limit (files and lines,
  configured, small by default). The diff is applied in a separate git worktree on a new branch, never on the checked-out branch.
- The impact check (v1.11.0) runs on the result. A breaking change to a used contract is refused and returned to the
  conductor with the reason, up to a bounded number of revisions.
- **Running checks:** the project's declared test command runs in the worktree with a timeout and no network by
  default. Its exit code becomes a `check` outcome (ADR-0018), and that is the only thing that validates.
- **Pull request:** after the person approves the diff, push the branch and open a PR (GitHub token from env). The
  PR body links the decisions and evidence. Nothing is merged by the tool.
- ADR: running tests (amends ADR-0005: which command, where, isolation, limits), the worktree flow, the size limit,
  PR permissions.

#### Test
- A diff touching another project's path is refused. A diff over the limit is refused. A malformed diff fails cleanly.
- Tests fail, then pass after a revision; both outcomes are recorded.
- A timeout, a hanging test, a test that tries the network.
- No token, push rejected, PR creation fails (the branch stays local, and the person is told).

#### Expected Output
- From a prompt to a PR whose every step, decision and check can be traced in the store.

#### Fix If
- Anything is pushed without the person's approval.
- A run's own success is counted as validation without a check outcome.

### v1.13.0 Showing improvement (small scale)

#### Implement
- A task set format: request, workspace, expected checks.
- A runner that does each task with memory off and with memory on (and optionally repeats it), using the same models.
- A report: tests passed, retries, failed checks, revisions refused by the impact check, tokens and time, with
  sample counts and no claim beyond what was measured.
- Starts with a handful of small tasks on the fixture workspace and free models only.

#### Test
- Deterministic fake-model runs give the expected report. Runs with mixed models are never compared as one.

#### Expected Output
- A repeatable, inspectable comparison that says plainly when the sample is too small to conclude anything.

#### Fix If
- The report hides failed or aborted runs.

---

## Phase D: Local web demo with chat

The current read-only UI stays as it is until this phase's ADR is written, complete and approved by the user.

### v2.0.0 Local API server and chat

#### Implement
- A local server (127.0.0.1 only, token as in ADR-0031) that exposes the backend: workspace, tasks, live progress
  events, diffs, approvals, decisions. The web demo and later the VS Code extension both talk to it.
- **Chat page:**
  - Onboarding asks for projects (URL or path) and shows the detected dependencies for the person to confirm or add to.
  - Each prompt shows the conductor's plan (roles, models, reasons) and live step progress.
  - It shows the impact report and the diff, with Approve, Revise and Reject.
  - It ends with a link to the PR.
- **Trace view:** from any chat turn to its decisions, evidence and outcomes (reuses the existing tabs).
- The only writes are those the person approves in chat (workspace changes, approved diffs, reviews and outcomes).
- ADR: replaces ADR-0031's read-only rule for this server only, covering which actions exist, CSRF and token handling,
  and how model text is shown (as text only).

#### Test
- Token, Host and origin checks; every write needs an explicit approval request; replayed approvals are refused.
- A full demo flow on the fixture workspace with a fake model, and once with a free live model (opt-in).
- The server is restarted mid-task and the task reads back as interrupted.

#### Expected Output
- A person can demo the whole flow locally in a browser.

#### Fix If
- Model output is rendered as HTML.
- An action happens without the person's approval.

---

## Phase E: VS Code extension

### v2.1.0 VS Code extension

#### Implement
- A TypeScript extension that starts or connects to the local server and shows the chat in a side panel.
- The workspace is built from the open folders, with others added by URL or path.
- Diffs open in VS Code's diff view, and approve or reject happens there.
- It shows the decision trail for the current file or branch.
- Packaging as a `.vsix`, with Marketplace publishing later and only with the user's approval.
- ADR: how the extension finds and authenticates to the server, and what it may do without the server.

#### Test
- Extension tests (VS Code test runner) for panel, connection loss and diff approval.
- Same end-to-end flow as v2.0.0.

#### Expected Output
- The web demo's flow works inside VS Code.

#### Fix If
- The extension can write to a non-current project.

---

## Phase F: Large-scale testing and benchmarking (gated)

Starts only when the user confirms the project is ready. Planned items:
- Large workspaces and large experience databases (load, recall and assessment cost at scale, as ADR-0026 to 0030 did).
- A larger, independently written task set; paid and stronger models as options; repeated runs for variance.
- Larger change-size limits only after the impact check and test isolation hold up at scale.

---

## Open questions to settle in the ADRs

1. GitHub access for cloning private repos and opening PRs: a personal token in `.env`, or the `gh` CLI's login.
2. Which frameworks the route and ORM detection supports first (proposal: FastAPI, Flask, Express, SQLAlchemy, Prisma).
3. The default small-change limit (proposal: at most 3 files and 100 changed lines).
4. Which free model is the default conductor (to be decided from v1.8.0 records, not upfront).
