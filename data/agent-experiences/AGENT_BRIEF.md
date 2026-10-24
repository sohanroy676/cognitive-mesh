# Brief for an agent that adds a feature and logs its decisions

You are one of five agents. Each works on a different project, in its own git clone, on its own branch.
Your work will be stored as **experiences** in the Cognitive Mesh experience database. An experience is one
step of past work, recorded as it happened, so that a later person (or agent) can read what was learned,
what was decided and why, what was changed and how it was checked. Nothing you write is treated as proven.
Honesty is the whole point: a recorded dead end or a failed check is worth more than a tidy story.

## Rules (all of them, no exceptions)
1. Work **only** inside your clone (the absolute path in your task). Never read or change the original
   project under `E:\Projects\...`, any other project, or this brief's folder except your own report file.
2. Never run `git push`, never add a remote, never delete branches, never rewrite history. Commit to the branch
   that already exists in your clone (small commits, Conventional Commits style, the author is already set).
3. Follow the project's own rules files if it has any (`AGENTS.md`, `CLAUDE.md`, `memory-bank/`, `.clinerules`,
   `.claude/rules`): read them first. Keep the change small and in scope.
4. Do not start servers, GUIs or long-running processes. Do not download models. Do not install packages
   from the network unless the task cannot be checked otherwise; if you do, say so in a step.
5. Run the project's own tests for what you changed. Use the project's interpreter when it has one (look for a
   `venv` or `.venv` in the original project folder and use its `python.exe` *read-only*, from your clone as the
   working directory). Before trusting a result, check that the code under test is **your clone's** code
   (for example print the module's `__file__`). If you cannot run something, record that as a failed or
   skipped check; never claim a result you did not see.
6. Never put secrets, tokens or long file contents into the report. Quote at most a few lines.

## What to log: a report file, written as you go
Write `E:\experience-db\reports\<slug>.json` (your slug is in your task). **Create it at the start and add each
step the moment it happens**, not at the end. In particular write each decision down *before* you implement
it, and write every check result *right after* you run it, including failures. Rewrite the whole file each
time; it must always be valid JSON. If you change your mind, add a new step; do not edit old steps.

```json
{
  "project": "NAME",
  "slug": "SLUG",
  "task": "the task text you were given",
  "clone": "E:/experience-db/work/NAME",
  "branch": "agent/SLUG",
  "base_commit": "short hash before you started",
  "head_commit": "short hash when you finished (fill in at the end)",
  "model": "claude-sonnet-5-5",
  "finished": false,
  "steps": [
    {
      "step_id": "read-1",
      "capability": "read_project",
      "input": {"question": "what do the project rules and structure require for this task?"},
      "outcome": "success",
      "output": {
        "found": "two or three sentences of what you learned",
        "files_read": ["README.md", "AGENTS.md"],
        "constraints": ["rules that limit the design"]
      }
    },
    {
      "step_id": "decide-1",
      "capability": "decide",
      "input": {"question": "the exact question you had to answer"},
      "outcome": "success",
      "output": {
        "decision": "what you chose",
        "why": "the reasons, tied to what you read",
        "alternatives_considered": [{"option": "...", "rejected_because": "..."}],
        "assumptions": ["things you assumed and did not verify"],
        "risks": ["what could go wrong with this choice"]
      }
    },
    {
      "step_id": "change-1",
      "capability": "change_code",
      "input": {"goal": "what this edit is meant to achieve"},
      "outcome": "success",
      "output": {
        "summary": "what you changed",
        "files_changed": [{"path": "relative/path.py", "what": "the change in a phrase", "line": 42}]
      }
    },
    {
      "step_id": "check-1",
      "capability": "run_checks",
      "input": {"command": "the exact command you ran"},
      "outcome": "failure",
      "failure_kind": "internal_error",
      "failure_message": "tests failed: 2 failures in tests/test_x.py (short, no file content)"
    }
  ]
}
```

### Step rules
- `capability` is one of: `read_project` (what you learned), `decide` (one decision), `change_code` (one edit or
  group of edits), `run_checks` (one command and its result), `reflect` (what you would tell the next person:
  limits, things left undone, surprises).
- `outcome` is `success` or `failure`. A failure has `failure_kind` (one of `invalid_input`, `missing_context`,
  `internal_error`, `unsupported_capability`) and `failure_message` (under 200 characters, no file content).
  Use `internal_error` for a failed command or test, `invalid_input` when the task or an assumption turned out
  to be wrong, `missing_context` when you lacked information you needed. A failure has no `output`.
- A success has `output` (an object) as in the example. Keep each `output` under 3,000 characters.
- Every non-obvious choice is its own `decide` step: which approach, where the code goes, what to name it, what
  to leave out, how to test it. Aim for at least three decisions. Every step id is unique.
- `files_changed[].path` is relative to your clone; `line` is the first line of the change in the final file.
- Log failed attempts honestly: a test that failed before you fixed it is a `run_checks` failure step followed
  by the `change_code` step that fixed it and a new `run_checks` step.
- End with one `reflect` step, then set `"finished": true` and fill `head_commit` (`git rev-parse --short HEAD`).

## When you are done
Reply with five lines: what you built, the branch and head commit, which checks you ran and their results, what
you did not do, and the path of your report. Do not paste the report.
