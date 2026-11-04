"""Does a package version fit the range a dependent declares? (ADR-0041)

PyPI specs use `packaging` (PEP 440). npm specs use a small subset written here: exact versions,
`^`, `~`, comparison operators and `x`/`*` wildcards, joined by spaces. Anything else (`||`, hyphen
ranges, tags, pre-releases, URLs) is `unknown`, never guessed.
"""

import json
import re
from enum import StrEnum

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

_SCOPE_SUFFIX = re.compile(r"\s\[[^\[\]]*\]$")
_WILD = frozenset({"x", "X", "*"})
_NPM_TOKEN = re.compile(r"^(\^|~|>=|<=|>|<|=)?\s*v?([0-9xX*][0-9xX*.]*)$")


class Fit(StrEnum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


def spec_of(ecosystem: str, detail: str) -> str | None:
    """The version range text of a manifest evidence detail, or None when it is not readable."""
    text = _SCOPE_SUFFIX.sub("", detail.strip())
    if ecosystem == "pypi":
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            return None
        return str(requirement.specifier)
    _, sep, raw = text.partition(": ")
    if not sep:
        return None
    try:
        spec = json.loads(raw)
    except ValueError:
        return None
    return spec if isinstance(spec, str) else None


def _pep440(spec: str, version: str) -> Fit:
    try:
        requirement = Requirement(f"x{spec}")
        found = Version(version)
    except (InvalidRequirement, InvalidVersion):
        return Fit.UNKNOWN
    return Fit.INSIDE if requirement.specifier.contains(found, prereleases=True) else Fit.OUTSIDE


Parts = tuple[int | None, int | None, int | None]


def _parts(text: str) -> Parts | None:
    pieces = text.split(".")
    if not 1 <= len(pieces) <= 3:
        return None
    out: list[int | None] = []
    for piece in pieces:
        if piece in _WILD:
            out.append(None)
        elif piece.isdigit():
            out.append(int(piece))
        else:
            return None
    while len(out) < 3:
        out.append(None)
    return out[0], out[1], out[2]


def _fill(parts: Parts) -> tuple[int, int, int]:
    return parts[0] or 0, parts[1] or 0, parts[2] or 0


def _holds(op: str | None, parts: Parts, have: tuple[int, int, int]) -> Fit:
    major, minor, patch = parts
    lower = _fill(parts)
    if op in (None, "="):
        wanted = (major, minor, patch)
        return (
            Fit.INSIDE
            if all(w is None or w == h for w, h in zip(wanted, have, strict=True))
            else Fit.OUTSIDE
        )
    if op == "^":
        if major is None:
            return Fit.INSIDE
        if major > 0 or minor is None:
            upper = (major + 1, 0, 0)
        elif minor > 0 or patch is None:
            upper = (0, minor + 1, 0)
        else:
            upper = (0, 0, (patch or 0) + 1)
        return Fit.INSIDE if lower <= have < upper else Fit.OUTSIDE
    if op == "~":
        if major is None:
            return Fit.INSIDE
        upper = (major + 1, 0, 0) if minor is None else (major, minor + 1, 0)
        return Fit.INSIDE if lower <= have < upper else Fit.OUTSIDE
    if None in parts and op in (">", "<="):
        return Fit.UNKNOWN  # `>1.2` means different things for different readers: do not guess
    checks = {
        ">=": have >= lower,
        "<": have < lower,
        ">": have > lower,
        "<=": have <= lower,
    }
    return Fit.INSIDE if checks[op or ""] else Fit.OUTSIDE


def _npm(spec: str, version: str) -> Fit:
    have_parts = _parts(version)
    if have_parts is None or None in have_parts:
        return Fit.UNKNOWN
    have = _fill(have_parts)
    text = spec.strip()
    if text in ("", "*", "x", "X"):
        return Fit.INSIDE
    if "||" in text or " - " in text:
        return Fit.UNKNOWN
    tokens = re.sub(r"(\^|~|>=|<=|>|<|=)\s+", r"\1", text).split()
    outcome = Fit.INSIDE
    for token in tokens:
        match = _NPM_TOKEN.match(token)
        parts = _parts(match.group(2)) if match else None
        if match is None or parts is None:
            return Fit.UNKNOWN
        fit = _holds(match.group(1), parts, have)
        if fit is Fit.UNKNOWN:
            return Fit.UNKNOWN
        if fit is Fit.OUTSIDE:
            outcome = Fit.OUTSIDE
    return outcome


def version_fits(ecosystem: str, spec: str, version: str) -> Fit:
    """Whether `version` satisfies the range `spec`; UNKNOWN when either is not understood."""
    if ecosystem == "pypi":
        return _pep440(spec, version)
    return _npm(spec, version)
