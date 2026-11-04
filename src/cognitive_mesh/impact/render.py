"""Plain-text and JSON views of the impact analysis (ADR-0041). Deterministic and escaped.

Verdict groups are shown apart and in this order: breaking, unknown, compatible, unused. `unknown`
is never merged into `compatible`; every line carries its reason code and its sites.
"""

from typing import Any

from cognitive_mesh.impact.models import ContractDiff, Dependent, Finding, ImpactReport, Verdict
from cognitive_mesh.workspace.models import Site

_GROUP_TITLES = {
    Verdict.BREAKING: "BREAKING (a dependent uses what changed)",
    Verdict.UNKNOWN: "UNKNOWN (the extractors cannot tell; not a pass)",
    Verdict.COMPATIBLE: "COMPATIBLE (reason and evidence given; contracts we can read only)",
    Verdict.UNUSED: "UNUSED (no dependent uses it)",
}


def _clean(text: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in text)


def site_text(site: Site) -> str:
    where = f"{site.path}:{site.line}" if site.line else site.path
    detail = f" ({_clean(site.detail)})" if site.detail else ""
    return f"{site.project} {_clean(where)}{detail}"


def _finding_lines(item: Finding) -> list[str]:
    lines = [f"- [{item.kind}] {_clean(item.subject)}: {item.reason}"]
    if item.note:
        lines.append(f"    note: {_clean(item.note)}")
    if item.provider is not None:
        lines.append(f"    provided at: {site_text(item.provider)}")
    lines.extend(f"    used by: {site_text(d.site)}" for d in item.dependents)
    return lines


def diff_text(diff: ContractDiff) -> str:
    lines = [
        f"contract diff for {diff.project} against {_clean(diff.base_ref)} "
        f"({diff.base_commit[:12]})",
        "dependents' use is matched against the base contract; "
        "request and response bodies are not compared",
        "counts: " + ", ".join(f"{v.value} {diff.count(v)}" for v in _GROUP_TITLES),
    ]
    if not diff.findings:
        lines.append("no difference found in the contracts that could be read")
    for verdict, title in _GROUP_TITLES.items():
        group = [f for f in diff.findings if f.verdict is verdict]
        if not group:
            continue
        lines.extend(["", title])
        for item in group:
            lines.extend(_finding_lines(item))
    return "\n".join(lines) + "\n"


def diff_json(diff: ContractDiff) -> dict[str, Any]:
    return {
        "project": diff.project,
        "base_ref": diff.base_ref,
        "base_commit": diff.base_commit,
        "counts": {v.value: diff.count(v) for v in Verdict},
        "findings": [f.model_dump(mode="json") for f in diff.findings],
    }


def _dependent_lines(item: Dependent) -> list[str]:
    head = f"- {item.project}: [{item.kind}] {_clean(item.subject)} ({item.label})"
    if item.caveats:
        head += f" caveats: {', '.join(item.caveats)}"
    lines = [head]
    if item.provider is not None:
        lines.append(f"    provided at: {site_text(item.provider)}")
    lines.extend(f"    used at: {site_text(s)}" for s in item.consumers)
    return lines


def impact_text(report: ImpactReport) -> str:
    lines = [f"impact of {_clean(report.target)} in {report.project}"]
    detected = [d for d in report.dependents if d.status == "detected"]
    unknown = [d for d in report.dependents if d.status != "detected"]
    if not report.dependents:
        lines.append("no other workspace project depends on it that could be detected")
    if detected:
        lines.extend(["", "DEPENDENTS (detected, with evidence)"])
        for item in detected:
            lines.extend(_dependent_lines(item))
    if unknown:
        lines.extend(["", "MAY DEPEND (unknown: could not be resolved)"])
        for item in unknown:
            lines.extend(_dependent_lines(item))
    if report.declared:
        lines.extend(["", "DECLARED BY A PERSON (not detected evidence)"])
        lines.extend(
            f"- {link.source} -> {link.target} [{_clean(link.kind)}] {_clean(link.note)} "
            f"(by {_clean(link.by)})"
            for link in report.declared
        )
    return "\n".join(lines) + "\n"


def impact_json(report: ImpactReport) -> dict[str, Any]:
    return {
        "project": report.project,
        "target": report.target,
        "dependents": [
            d.model_dump(mode="json") for d in report.dependents if d.status == "detected"
        ],
        "may_depend": [
            d.model_dump(mode="json") for d in report.dependents if d.status != "detected"
        ],
        "declared": [link.model_dump(mode="json") for link in report.declared],
    }
