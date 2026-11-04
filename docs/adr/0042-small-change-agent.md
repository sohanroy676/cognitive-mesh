# ADR-0042: Small-change agent: branch, checks, pull request (v1.12.0) (amends ADR-0005)

## Status
Accepted by the user 2026-11-04 (drafted in plan mode; the four marked user decisions were taken before drafting). Implementation in progress on `feature/extended-roadmap-v1.7`. Extends ADR-0036, ADR-0040 and ADR-0041 (workspace, roles and `writes`, impact check). Amends ADR-0005 (the never-execute rule) for one case only: the person's declared check command, run on a change the tool has just made. ADR-0005 stays in force for ingestion, the graph, the agents and every other command. ADR-0011 (traces), ADR-0013 (remote content opt-in), ADR-0017 (request text not stored) and ADR-0018 (outcomes) stay in force.

## Date
2026-11-04

## Context
Extended roadmap v1.12.0: a **coder** role writes a unified diff for the current project only, within a size limit. The diff is applied in a separate git worktree on a new branch. The v1.11.0 contract diff runs on the result and a breaking change to a used contract is refused and sent back, up to a bounded number of revisions. The project's declared test command runs in the worktree with a timeout and no network by default; its exit code becomes a `check` outcome and is the only thing that validates. After the person approves, the branch is pushed and a pull request opened. Nothing is merged by the tool.

Facts from the code (read 2026-11-04):
- `workspace/access.py::WorkspaceAccess.require_writable(name)` raises `ReadOnlyProjectError` for every project except the current one. Nothing calls it yet. The current project is the `--project` whose folder is `--root`.
- `CapabilitySpec.writes` (`agents/models.py`) is checked only in `orchestrator/conduct.py` and has holes: a step without a `project` input passes, values passed through `input_refs` are not checked, and `plan_from_steps` has no check. No capability sets `writes=True`.
- `workspace/fetch.py::run_git` runs git with a fixed argument list, no shell, a sanitised environment and a 300 s timeout. Its docstring says it is the only module that runs git, which is no longer true. Nothing runs `git worktree`, `apply`, `commit` or `push`, a test command, or `gh`.
- `impact.contract_diff(current, base_ref, wg, reads)` compares a `git archive` of a ref with the project's working tree. `Verdict` is `breaking|compatible|unknown|unused`.
- Outcomes (schema 3) allow `source=check`, results `succeeded|failed|ambiguous`, and `decision:` subjects. `experience outcome` records a check command as text; nothing runs it.
- Trace schema is 7 and the workspace store schema is 1 (no field for a check command). The highest exit code is 23.
- No GitHub token is read anywhere today. The machine has no Docker or Podman, and Windows offers no standard-library way to block a child process's network.

Constraints unchanged: memory is evidence, not truth; model-written text (the diff, its summary and reasoning) is `model_unverified`; files, memories and model output are data, never instructions; no secret value is stored or printed; default tests need no network and no real GitHub.

## Decision

User decisions (2026-11-04):
1. **Isolation:** best effort, labelled honestly as not isolated; running needs an explicit opt-in.
2. **Test command:** declared by the person in the workspace file, never read from the repository.
3. **GitHub access:** a token in the environment or the ignored `.env`.
4. **Size limit:** 3 files and 100 changed lines by default, configurable.

Design:

0. **Frame.** A new package `src/cognitive_mesh/change/` (diff validation, worktree, checks, pipeline, GitHub). New exit code **24 `EXIT_CHANGE_ERROR`**. No new dependency (standard-library `subprocess` and the existing `providers/http.py` transport).

1. **Coder role.** A new agent `coder` (added to `AGENT_ROLES`) with capability `propose_change` (`uses_model=True`, `writes=True`). Input: the request, the files the person names with `--file` (at most 3, read through `WorkspaceAccess.reader(current)`, redacted, sensitive files never sent), and the previous refusal reason. Output is strict JSON `{diff, summary, reasoning}`, `model_unverified`. The agent touches no disk; the pipeline applies the diff. A `writes=True` capability is refused by `plan_from_steps` (so by `run` and `task --plan`) and by the conductor in v1.12.0; only `change propose` runs it. This closes the `writes` holes listed above rather than patching around them.

2. **Diff rules.** Our own parser runs before git sees the text. Only a unified text diff is accepted. Every path must be relative, normalised, free of `..`, not under `.git`, not a sensitive name (the ADR-0005 list), inside the current project's root, and not inside any other workspace project's root (a monorepo folder is mapped to its owner and `require_writable` is called for it). Refused in v1.12.0: binary patches, renames, copies, mode changes, symlinks and whole-file deletions. Size: changed lines (added plus removed) at most 100 and files at most 3 by default, set with `--max-files` and `--max-lines`, hard caps 10 and 500. Then `git apply --check` in the worktree. Every refusal has a fixed reason code and a message that never echoes diff text.

3. **Worktree flow.** `git worktree add -b cogmesh/change-<id> DIR <base commit>`, where DIR is under an explicit `--worktrees DIR` that must not lie inside the project's repository. The checked-out branch and working tree are never touched. The diff is applied with `git apply` and committed with `-c core.hooksPath=<empty dir>` (the repository's hooks never run), a fixed author `cognitive-mesh <noreply@localhost>` and a `Cognitive-Mesh-Change: <id>` trailer, all through a generalised `run_git` (same sanitised environment, per-call timeout, extra environment variables). The tool never deletes a branch. `change discard ID` removes only the worktree directory registration.

4. **Impact check.** `contract_diff` gains a parameter naming the tree to compare (the worktree's project folder) against the change's base commit. Any `breaking` finding is a refusal, with the findings as the reason sent back. `unknown` findings are shown and written into the PR body; they are neither a pass nor a refusal.

5. **Running checks (the ADR-0005 amendment).** Ingestion still executes nothing. The only command the tool ever executes is the one the person stored with `workspace set-check PROJECT [--timeout S] -- ARGV...` (workspace schema 1 to 2, a `checks` table holding an argv list, no shell, recorder and date; `workspace show-check` prints it). It runs only for the current project, which must be local, with the worktree's project folder as working directory. Default timeout 300 s, maximum 3600. On timeout the whole process tree is killed (POSIX: new session and `killpg`; Windows: new process group and `taskkill /T /F`). The environment is an allowlist (PATH, SYSTEMROOT, temp and home-like variables), with no inherited secrets, plus `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` set to `http://127.0.0.1:9`, an empty `NO_PROXY`, and `PIP_NO_INDEX=1`, `UV_OFFLINE=1`, `npm_config_offline=true`, `HF_HUB_OFFLINE=1`.
   **This is not isolation.** A test that opens raw sockets, or ignores proxy variables, can still reach the network, and the command can read and write anything the user account can. The recorded isolation label is `env_only`. Because of that, every `change propose` run that would execute the check needs `--allow-unisolated-checks`; without it the pipeline stops after the impact check, runs nothing and stores no check outcome. Output is redacted and the last 20,000 characters are kept.
   **Outcome:** subject is the change's decision id; `source=check`; exit 0 is `succeeded`, a non-zero exit is `failed`, a timeout or kill is `ambiguous`; `detail` is the argv text plus the isolation label; the fingerprint is that of the worktree. A run's own success never validates.

6. **Revisions.** Up to 2 revisions (3 drafts). Sent back to the coder as data: the refusal code, the impact findings, or the redacted last 4,000 characters of failing check output. Each draft is its own orchestrator run with one `propose_change` step, so traces, `model_calls` and decision records reuse the existing machinery; each draft's decision is `MadeBy.MODEL`. A check outcome is stored for every draft that ran, so a failure followed by a pass keeps both.

7. **State.** Trace schema 7 to 8: `changes` (id, project, application id, base branch, base commit, worktree path, branch, latest commit, diff sha256, limits) and append-only `change_events` (drafted, refused with reason, applied, impact, check with outcome id, approved, pushed, pr_opened, pr_failed). Status is derived from the events and never stored. The request text is not stored (ADR-0017), only its length.

8. **Approval and pull request.** `change approve ID --by NAME` stores a `MadeBy.PERSON` decision approving exactly the latest commit and diff sha256 (an unauthenticated label, as elsewhere). `change pr ID` refuses unless an approval names the current commit, and refuses unless the latest check succeeded (`--without-passing-check` overrides and the PR body says so). The token is `GITHUB_TOKEN` (`SecretStr`, printable ASCII check, environment or ignored `.env`). Owner and repository are parsed from `origin` (github.com over https or ssh only). The push goes to `https://github.com/OWNER/REPO.git refs/heads/B:refs/heads/B`, never forced, with the credential passed through `GIT_CONFIG_COUNT` / `http.extraHeader` environment variables (never in argv, never printed; git's stderr is redacted). The PR is `POST https://api.github.com/repos/OWNER/REPO/pulls` over the standard-library transport, with the branch the change started from as base. The body holds the model's summary labelled unverified, the change, run and decision ids, impact counts, the check outcome and isolation label, and the diff sha256. Failures (no token, push rejected, 401, 403, 404, 422) leave the branch local or already pushed and the message says which; `change pr` can be run again. Nothing is merged. GitHub receives the diff and the PR body, so `COGMESH_ALLOW_REMOTE_CONTENT=true` is required for `change pr`, as for other remote content.

9. **CLI.** `change propose "REQUEST" --root APP --workspace WS --project NAME --file PATH... --worktrees DIR --db T --outcomes O [--model M --catalog FILE] [--allow-unisolated-checks] [--max-files N] [--max-lines N]`; `change show|list|approve|pr|discard`; `workspace set-check|show-check`.

10. **Not in v1.12.0:** real network isolation (container or namespace), changes planned by the conductor, renames, deletions and binary files, writes to any other project, auto-merge, a UI view, the `gh` CLI, private-repository fetch for other projects.

## Alternatives considered
- **Isolation by container only:** honest and strong, but nothing could run on this machine until Docker is installed. Kept as a later upgrade; the label field leaves room for it.
- **Linux `unshare`, refuse elsewhere:** real isolation on one platform; the person develops on Windows. Not chosen.
- **Test command read from the repository** (pyproject, package.json): a cloned repository would choose what the tool executes. Rejected by the user in favour of a person-declared command.
- **`gh` login instead of a token:** our code would never see a secret, but the user chose the token.
- **Applying the change in the checked-out branch:** risks the person's uncommitted work. Rejected; worktree only.
- **Letting the conductor plan changes:** needs the `writes` rule to be sound first; deferred.

## Consequences
- A check run executes code from the model's diff and the project's tests with the user's privileges. The `--allow-unisolated-checks` flag and the `env_only` label say so on every run; nothing here makes that safe.
- The size limit and the path rules are the main write guard, together with `require_writable`. Neither can judge whether a small change is a good change.
- A token with push rights exists in the environment. It is held as `SecretStr`, absent from argv, output, traces and the store, and tests must prove that.
- Trace schema and workspace schema both advance; the pinned migration tests change on purpose.
- The `check` outcome validates only the decision "this change passes the declared command"; it says nothing about whether the declared command is a good test.
- Windows process-tree kill and POSIX `killpg` are different code paths; CI covers Linux only for the second, and the first is covered by a local Windows run.
- Tests required (from the roadmap): a diff touching another project's path is refused; a diff over the limit is refused; a malformed diff fails cleanly; tests fail then pass after a revision and both outcomes are recorded; a timeout, a hanging test, a test that tries the network; no token, push rejected and PR creation failing (branch stays local, the person is told). Added: path traversal, `.git` and sensitive paths, rename/binary/mode/symlink diffs, repository hooks never run, `change pr` without approval or with a stale approval, token absent from every output and store, `writes` capability refused in plain plans and by the conductor.

## Fix if
- Anything is pushed without the person's approval.
- A run's own success is counted as validation without a check outcome.
- A check runs without `--allow-unisolated-checks`, or its outcome omits the isolation label.
- A write reaches any project other than the current one.

## Implementation notes (2026-11-04)
- Built as written, with these differences found while building:
  - **One choke point for `writes`.** `plan_task(plan, registry, *, allow_writes=False)` refuses any step whose capability writes, so `run`, `task --plan`, drafted plans and the conductor all refuse it with the same message; only the change pipeline passes `allow_writes=True`. The older check inside `orchestrator/conduct.py` can no longer be reached and is left as a second guard. `AgentRegistry.writes(capability)` answers the question.
  - **The impact check runs before the commit.** The draft is applied (staged) in the worktree, `contract_diff(..., tree=worktree project folder)` compares it with the base commit, and only then is it committed. A breaking draft is reset, so the branch never holds it. `contract_diff` gained the keyword `tree`.
  - **Git must agree with our parser.** After `git apply`, `git diff --cached --numstat` has to list exactly the paths and line counts our parser read; any difference is `malformed_diff` and the worktree is reset. `git apply` runs with `--ignore-whitespace` (a project checked out with CRLF line endings would otherwise never match a model's LF context) and, for a project in a repository subfolder, `--directory=SUBFOLDER`.
  - **A failed check rewinds the branch.** The next draft starts again from the base commit: the failed commit stays in the events (its sha, diff hash and outcome) and in git's reflog until `git gc`, but the branch no longer holds it. The last draft is never rewound, so a change whose every check failed ends with its last commit and a `check_failed` status.
  - **Which identity.** The change header and the draft decisions carry the application label derived from the project folder (the same one the runs carry), not the workspace row's, so a person's approval decision can cite the draft decision.
  - **`--outcomes` is required** on `change propose`: a check outcome that cannot be stored would otherwise be dropped silently.
  - **A bare check command is never taken from the project folder.** Windows searches the current folder first, and a change may add a text file such as `pytest.cmd`; a declared bare name that resolves into the project folder is `not_started`. A path the person wrote is theirs.
  - **Output handling.** `redact_secrets` takes time quadratic in the length of an unbroken run (20,000 characters took 15 s), so a check output line longer than 1,000 characters is replaced by `[a line of N characters is not shown]` before redaction; only the last 20,000 characters are kept.
  - **`GithubSettings`** (`GITHUB_TOKEN`, `COGMESH_ALLOW_REMOTE_CONTENT`) is separate from `Settings`, so `change pr` does not need `COGMESH_PROVIDER`.
  - **A migration bug the eighth schema exposed.** `_migrate` ran each step with `executescript`, and the 6 to 7 rebuild succeeds when run twice, so a process that still believed the version was 6 (the race test starts eight at once) re-ran it after another had reached 8 and set the version back to 7; the next process then failed on `table changes already exists`. The runner now re-reads the version under the write lock and runs the statements one by one; a test calls the stale step by hand.
  - `run_git` now lives beside `run_git_with(argv, cwd, *, env_extra, timeout_s)`; the workspace file is schema 2 (`checks` table, a version 1 file reads as "no check" and is upgraded on the next write); the trace database is schema 8.
- Not built: the `change` commands have no `--json` for `approve`, `pr` and `discard`; the check command is shown with its words joined by spaces (the stored form is the argv list).
- Limits that are still true: the check is not isolated (it runs with the user's rights, the environment keeps `HOME`, `USERPROFILE`, `APPDATA` and `LOCALAPPDATA`, and raw sockets work); the process-tree kill was run on Windows only (`taskkill /T /F`), the POSIX `killpg` path is exercised by CI on Linux; no change was ever pushed to, or a pull request opened on, the real github.com (the push is tested against a local bare repository and the API against a local stub server); no real model has written a diff (the walkthrough used a scratch stub server); the base is the `HEAD` commit, so uncommitted edits in the project folder are not part of the change; the worktree is a checkout of the whole repository, so a very large repository costs disk; a repository's own `.git/config` is still read by git.
