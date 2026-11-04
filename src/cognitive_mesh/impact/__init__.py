"""Cross-project impact analysis and contract diff (v1.11.0, ADR-0041).

Pure functions over the workspace reads; nothing is stored. The only external call is the
read-only `git archive` of `base_export`.
"""

from collections.abc import Mapping

from cognitive_mesh.impact.base_export import exported_base
from cognitive_mesh.impact.dependents import Target, list_dependents, parse_target
from cognitive_mesh.impact.diff import diff_contracts
from cognitive_mesh.impact.models import (
    ContractDiff,
    Dependent,
    DependentSite,
    Finding,
    ImpactError,
    ImpactReport,
    Verdict,
)
from cognitive_mesh.impact.snapshot import Snapshot, snapshot_of, snapshot_of_tree
from cognitive_mesh.ingestion import IngestionError
from cognitive_mesh.workspace.graph import ProjectRead, WorkspaceGraph
from cognitive_mesh.workspace.models import ProjectKind


def contract_diff(
    current: str, base_ref: str, wg: WorkspaceGraph, reads: Mapping[str, ProjectRead]
) -> ContractDiff:
    """Findings between the project at `base_ref` and its working tree, judged against how the
    other workspace projects use it now. The current project must be a local folder in git."""
    read = reads.get(current)
    if read is None:
        raise ImpactError("the current project could not be read")
    if read.project.kind is not ProjectKind.LOCAL:
        raise ImpactError("the current project must be a local folder")
    others = {name: r for name, r in reads.items() if name != current}
    try:
        with exported_base(read.root, base_ref) as (tree, commit):
            base = snapshot_of_tree(current, tree)
    except IngestionError:
        raise ImpactError("the project at the base ref cannot be read") from None
    now = snapshot_of(current, read.result, read.facts)
    findings = diff_contracts(current, base, now, others, wg.detected)
    return ContractDiff(
        project=current, base_ref=base_ref, base_commit=commit, findings=tuple(findings)
    )


__all__ = [
    "ContractDiff",
    "Dependent",
    "DependentSite",
    "Finding",
    "ImpactError",
    "ImpactReport",
    "Snapshot",
    "Target",
    "Verdict",
    "contract_diff",
    "diff_contracts",
    "list_dependents",
    "parse_target",
]
