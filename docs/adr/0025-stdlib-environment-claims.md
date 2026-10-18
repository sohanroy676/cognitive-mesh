# ADR-0025: The first environment-dependent claim type: standard-library membership

## Status
Proposed (awaits review and acceptance before any code)

## Date
2026-10-18

## Context
ADR-0022 added the scope `environment`, the three-valued environment comparison and the statuses
`environment_changed` / `environment_unverified`, but no claim type uses them: every claim is `code`-scoped, so
the environment path has been exercised with synthetic claims only.

A real, small candidate already exists. The dependency graph decides that an import is standard library with
`sys.stdlib_module_names` of the interpreter that ran the analysis (`graph/builders.py`), and records the target as
`stdlib:NAME`. That classification depends on the Python version: `tomllib` is `stdlib:` on 3.11+ and an unresolved
third-party import on 3.10; `distutils` is `stdlib:` up to 3.11 and gone on 3.12. A `depends_on` claim whose object
is `stdlib:*` is therefore a fact about code under one interpreter, not about the code alone.

## Decision
- Claims `depends_on` whose **object** id starts with `stdlib:` get scope `environment`; all other claims keep
  scope `code`. The claim id keeps its form (it encodes subject, object and edge kind), so a claim recorded on
  3.12 and the same edge seen on 3.10 are the same claim with different run environments.
- The identifier compared is the Python major.minor taken from the run's recorded `TraceEnvironment.python_version`.
  `platform` and analyser version are not compared for this claim type, because they do not change stdlib
  membership. If `ENV_IDENTIFIERS` has no major.minor form, add `python_minor`, derived from the recorded string;
  nothing new is read from the host.
- Status follows ADR-0022 unchanged: current minor differs from the recorded one: `environment_changed` (withheld
  from the agent pack with a reason); recorded or current value missing or unparsable: `environment_unverified`
  (offered, labelled); same minor: the normal statuses apply. Re-derivation (ADR-0016) can make such a claim
  `stale` only for a code change; it never turns an environment change into `validated`.
- No graph format change is needed if `scope` is already stored per claim (it is). The build change affects new
  graphs only; old graph files keep their claims as `code`, and a rebuild from the trace database reclassifies them.
- Tests: a `stdlib:` edge gives an `environment` claim and a first-party edge a `code` claim; same minor keeps
  `supported` / `validated`; a different minor (injected environment) gives `environment_changed` and the pack
  withholds it with the reason; a missing recorded version gives `environment_unverified`; unresolved edges stay
  `code`; a claim seen on two Python minors stays one claim with both environments visible and is not silently
  merged into `validated`; a hand run of the real CLI with a recorded run whose environment is edited in a scratch
  database.

## Alternatives considered
- **Platform-dependent edges** (conditional imports on `sys.platform`): the analyser does not evaluate the
  condition, so there is nothing real to scope. Deferred.
- **Locked package versions as the first environment claim:** lockfile data is in the repository, so it is
  code-scoped, not environment-dependent. Deferred.
- **Scope every `depends_on` claim as environment:** over-broad; it would withhold valid code facts after any
  Python upgrade, which ADR-0022 forbids.

## Consequences
- The environment path is exercised by real claims; the withheld and unverified states can now occur in real use.
- A claim about `stdlib:x` recorded on one Python minor is withheld after the interpreter changes until a new run
  records it on the new minor. That is intended: the classification was a fact about the old interpreter.
- Limits: only the Python minor is compared; backports of stdlib modules are not modelled; runs recorded before
  this change only have the `python_version` string, which is parsed, and a string that cannot be parsed gives
  `environment_unverified`.
