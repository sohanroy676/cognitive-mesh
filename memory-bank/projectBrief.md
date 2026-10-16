# Project Brief

## What the Project Is

This project is an application-aware AI agent system that analyzes an application's
implementation and supporting configuration, selects and coordinates specialized agents,
and maintains evidence-aware persistent memory across tasks and sessions.

It combines a custom Python orchestrator, configurable model providers, vector retrieval,
a knowledge graph, and structured persistent storage to improve continuity and traceability.

## Core Goal for the First End-to-End Prototype

Demonstrate a complete workflow in which the system receives a task, gathers relevant
application context, coordinates agent execution, records the experience, and successfully
retrieves and reuses relevant past experience in a separate session.

The demonstration should make it possible to inspect the evidence and context used,
rather than relying only on a claim that the system performed correctly.

## Intended Capabilities

- Understand an application using its source code, configuration, dependency manifests,
  and documentation within the supported ingestion scope.
- Distinguish observed implementation details from assumptions and missing information.
- Select and coordinate suitable specialized agents.
- Record task executions and their outcomes.
- Store semantic memories and structured knowledge with provenance.
- Retrieve relevant memories and expose conflicts or uncertainty.
- Update reusable knowledge based on new evidence and task outcomes.
- Support legacy applications through targeted changes where appropriate, rather than
  assuming a wholesale rewrite is necessary.

## Success Criteria

The first end-to-end prototype should demonstrate:

1. A task can be processed through the intended orchestration workflow.
2. Execution traces and their provenance persist across application sessions.
3. Relevant previous experiences can be retrieved for a later task.
4. Retrieved knowledge can be traced to its supporting records.
5. Conflicting or insufficient evidence is not silently presented as established truth.
6. The workflow produces observable, testable outputs and handles failures explicitly.

Specific quantitative thresholds and benchmark targets are TBD.

## Explicitly Out of Scope

The project does not claim to:
- Determine absolute truth or guarantee that model outputs are correct.
- Reliably detect every hallucination.
- Automatically resolve every contradiction.
- Guarantee convergence among agents.
- Eliminate adversarial behavior or prompt injection completely.
- Treat repeated or semantically similar model outputs as independent proof.

These limitations do not prevent the system from tracking evidence, provenance, uncertainty,
and disagreement to support better-informed decisions.