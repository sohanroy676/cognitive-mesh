"""Records of the impact analysis (ADR-0041). Immutable, JSON-ready, and every one carries sites."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.workspace.models import DeclaredLink, Site


class ImpactError(Exception):
    """A target, ref or project cannot be used. Messages are written by us and never contain the
    text the person gave, file content or output of other programs."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Verdict(StrEnum):
    BREAKING = "breaking"
    COMPATIBLE = "compatible"
    UNKNOWN = "unknown"
    UNUSED = "unused"  # a difference no dependent uses


class DependentSite(_Frozen):
    project: str
    site: Site


class Finding(_Frozen):
    """One difference between the base and the working tree, judged against what dependents use."""

    kind: str  # api | table | json_schema | config | package | export
    verdict: Verdict
    reason: str  # a fixed code such as route_removed_or_changed
    subject: str  # what changed, for example `GET /users`
    provider: Site | None  # where the current project states it (working tree or base)
    dependents: tuple[DependentSite, ...] = ()
    note: str = ""


class ContractDiff(_Frozen):
    project: str
    base_ref: str
    base_commit: str
    findings: tuple[Finding, ...]

    def count(self, verdict: Verdict) -> int:
        return sum(1 for f in self.findings if f.verdict is verdict)


class Dependent(_Frozen):
    """A project and location that depends on the target, or may (`status` unknown)."""

    status: str  # detected | unknown
    project: str
    kind: str  # api | table | json_schema | config | package | export
    subject: str
    label: str  # how it matched (exact, param_style, ...) or why it is unknown
    provider: Site | None
    consumers: tuple[Site, ...]
    caveats: tuple[str, ...] = ()


class ImpactReport(_Frozen):
    project: str
    target: str
    dependents: tuple[Dependent, ...]
    declared: tuple[DeclaredLink, ...]  # stated by a person, never detected evidence
