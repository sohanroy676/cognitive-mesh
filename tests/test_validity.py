"""Scope, environment comparison and expiry (ADR-0022)."""

from datetime import date

import pytest
from pydantic import ValidationError

from cognitive_mesh.validity import (
    Comparison,
    Environment,
    EnvScope,
    ValidityState,
    assess_validity,
    compare_environment,
)

REC = Environment(values={"python_version": "3.12.3", "platform": "Windows"})
TODAY = date(2026, 10, 18)


def env(**values: str) -> Environment:
    return Environment(values=values)


def test_same_environment_compares_same() -> None:
    assert compare_environment(REC, REC).result is Comparison.SAME


def test_a_different_value_is_different_and_named() -> None:
    got = compare_environment(REC, env(python_version="3.13.0", platform="Windows"))
    assert got.result is Comparison.DIFFERENT and got.different == ("python_version",)


def test_a_missing_current_identifier_is_unknown_not_same() -> None:
    got = compare_environment(REC, env(platform="Windows"))
    assert got.result is Comparison.UNKNOWN and got.unknown == ("python_version",)


def test_different_beats_unknown() -> None:
    got = compare_environment(REC, env(platform="Linux"))
    assert got.result is Comparison.DIFFERENT and got.unknown == ("python_version",)


def test_an_empty_recorded_environment_is_unknown() -> None:
    assert compare_environment(env(), REC).result is Comparison.UNKNOWN


@pytest.mark.parametrize(
    "values",
    [{"hostname": "x"}, {"platform": ""}, {"platform": "x" * 101}, {"platform": "a\nb"}],
)
def test_only_known_printable_bounded_identifiers_are_accepted(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Environment(values=values)


def test_a_code_claim_ignores_the_environment_and_age() -> None:
    got = assess_validity(EnvScope.CODE, REC, env(platform="Linux"), None, TODAY)
    assert got.state is ValidityState.IN_FORCE


def test_an_unscoped_claim_is_labelled_not_guessed() -> None:
    got = assess_validity(EnvScope.UNSCOPED, env(), REC, None, TODAY)
    assert got.state is ValidityState.UNSCOPED


def test_an_environment_claim_follows_the_comparison() -> None:
    same = assess_validity(EnvScope.ENVIRONMENT, REC, REC, None, TODAY)
    changed = assess_validity(EnvScope.ENVIRONMENT, REC, env(platform="Linux"), None, TODAY)
    unverified = assess_validity(EnvScope.ENVIRONMENT, REC, env(), None, TODAY)
    assert same.state is ValidityState.IN_FORCE
    assert changed.state is ValidityState.ENVIRONMENT_CHANGED
    assert unverified.state is ValidityState.ENVIRONMENT_UNVERIFIED


def test_expiry_is_strictly_before_today() -> None:
    on_day = assess_validity(EnvScope.CODE, REC, REC, TODAY, TODAY)
    past = assess_validity(EnvScope.CODE, REC, REC, date(2026, 10, 17), TODAY)
    future = assess_validity(EnvScope.CODE, REC, REC, date(2026, 12, 1), TODAY)
    assert on_day.state is ValidityState.IN_FORCE
    assert past.state is ValidityState.EXPIRED
    assert future.state is ValidityState.IN_FORCE


def test_expiry_wins_over_the_environment() -> None:
    got = assess_validity(EnvScope.ENVIRONMENT, REC, env(platform="Linux"), date(2020, 1, 1), TODAY)
    assert got.state is ValidityState.EXPIRED


def test_no_clock_with_an_expiry_is_unknown_never_expired() -> None:
    got = assess_validity(EnvScope.CODE, REC, REC, date(2020, 1, 1), None)
    assert got.state is ValidityState.UNKNOWN


def test_no_expiry_means_no_time_based_effect_even_without_a_clock() -> None:
    assert assess_validity(EnvScope.CODE, REC, REC, None, None).state is ValidityState.IN_FORCE
