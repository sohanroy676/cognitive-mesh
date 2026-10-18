"""Claim scope, environment comparison and expiry (ADR-0022). Pure functions; no clock, no I/O.

The one staleness rule: a claim is expired when its `valid_until` is before the date used, and
environment-changed when an environment-scoped claim compares as different. Age, similarity and
model opinion are never reasons. A missing clock or identifier gives an unknown, not a verdict.
"""

import platform
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from cognitive_mesh import __version__

# The fixed identifier list (ADR-0022): nothing that identifies a host or a person is recorded.
ENV_IDENTIFIERS: Final = ("analyser_version", "python_version", "platform")
MAX_VALUE_CHARS: Final = 100


class EnvScope(StrEnum):
    CODE = "code"  # valid for the code version it was observed on, on any machine or date
    ENVIRONMENT = "environment"  # valid only for the recorded environment
    UNSCOPED = "unscoped"  # nothing recorded: never treated as either by guess


class Comparison(StrEnum):
    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


class ValidityState(StrEnum):
    IN_FORCE = "in_force"  # nothing in this module counts against the claim
    EXPIRED = "expired"
    ENVIRONMENT_CHANGED = "environment_changed"
    ENVIRONMENT_UNVERIFIED = "environment_unverified"
    UNSCOPED = "unscoped"
    UNKNOWN = "unknown"  # an expiry exists but no clock was available


class Environment(BaseModel):
    """Recorded or current environment: only identifiers from `ENV_IDENTIFIERS`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: dict[str, str] = {}

    @field_validator("values")
    @classmethod
    def _only_known_identifiers(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if key not in ENV_IDENTIFIERS:
                raise ValueError("unknown environment identifier")
            if not value or len(value) > MAX_VALUE_CHARS or not value.isprintable():
                raise ValueError("environment value is empty, too long or not printable")
        return values


class EnvComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: Comparison
    different: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


class Validity(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ValidityState
    reason: str  # fixed text naming the rule that fired; never a stored value
    comparison: EnvComparison | None = None


def utc_today() -> date:
    """The one clock read of a command (UTC); it is printed with the result."""
    return datetime.now(UTC).date()


def current_environment() -> Environment:
    """This process's environment as the fixed identifiers; nothing names a host or a person."""
    try:
        return Environment(
            values={
                "analyser_version": __version__,
                "python_version": platform.python_version(),
                "platform": platform.system(),
            }
        )
    except ValidationError:
        return Environment()


def compare_environment(recorded: Environment, current: Environment) -> EnvComparison:
    """Per recorded identifier: same, different, or unknown when the current side lacks it.

    An empty recorded environment is unknown for every identifier, not `same`.
    """
    different: list[str] = []
    unknown: list[str] = []
    for key in ENV_IDENTIFIERS:
        if key not in recorded.values:
            continue
        here = current.values.get(key)
        if here is None:
            unknown.append(key)
        elif here != recorded.values[key]:
            different.append(key)
    if different:
        result = Comparison.DIFFERENT
    elif unknown or not recorded.values:
        result = Comparison.UNKNOWN
    else:
        result = Comparison.SAME
    return EnvComparison(result=result, different=tuple(different), unknown=tuple(unknown))


def assess_validity(
    scope: EnvScope,
    recorded: Environment,
    current: Environment,
    valid_until: date | None,
    today: date | None,
) -> Validity:
    """Apply the ADR-0022 rule. Precedence: expired, environment changed, unverified, unscoped."""
    if valid_until is not None:
        if today is None:
            return Validity(state=ValidityState.UNKNOWN, reason="the clock was not available")
        if valid_until < today:
            return Validity(state=ValidityState.EXPIRED, reason="its valid_until date has passed")
    if scope is EnvScope.ENVIRONMENT:
        comparison = compare_environment(recorded, current)
        if comparison.result is Comparison.DIFFERENT:
            return Validity(
                state=ValidityState.ENVIRONMENT_CHANGED,
                reason="the environment differs from the one recorded",
                comparison=comparison,
            )
        if comparison.result is Comparison.UNKNOWN:
            return Validity(
                state=ValidityState.ENVIRONMENT_UNVERIFIED,
                reason="the environment could not be fully compared",
                comparison=comparison,
            )
        return Validity(
            state=ValidityState.IN_FORCE, reason="the environment matches", comparison=comparison
        )
    if scope is EnvScope.UNSCOPED:
        return Validity(state=ValidityState.UNSCOPED, reason="no scope was recorded")
    return Validity(state=ValidityState.IN_FORCE, reason="valid for its code version")
