"""New secret rules (ADR-0005 amendment): provider formats, URL credentials, random tokens."""

import hashlib

import pytest

from cognitive_mesh.ingestion.secrets import redact_secrets

# Assembled at runtime so no secret-looking literal sits in the repository.
GOOGLE = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
STRIPE = "sk_" + "live_" + "A1b2C3d4E5f6G7h8"
NPM = "npm_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
JWT = "eyJ" + "hbGciOiJIUzI1" + ".eyJ" + "zdWIiOiIxMjM0" + "." + "SflKxwRJSMeKKF2QT4fw"
RANDOM = "q7Zk2LmX9vB4nR8sT1wY5cD3fG6hJ0aE"


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        ("google_api_key", GOOGLE),
        ("stripe_key", STRIPE),
        ("npm_token", NPM),
        ("jwt", JWT),
        ("high_entropy_token", RANDOM),
    ],
)
def test_new_rules_redact_and_name_the_rule(rule: str, value: str) -> None:
    redacted, found = redact_secrets(f"first\nthe thing {value} here\n")
    assert value not in redacted
    assert [(r.rule, r.line) for r in found] == [(rule, 2)]
    assert f"[REDACTED:{rule}]" in redacted


def test_only_the_password_of_a_url_is_replaced() -> None:
    redacted, found = redact_secrets("db = postgres://admin:hunter2pw@db.internal:5432/app")
    assert "hunter2pw" not in redacted
    assert "postgres://admin:[REDACTED:url_credentials]@db.internal:5432/app" in redacted
    assert [r.rule for r in found] == ["url_credentials"]


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/docs/page",  # no credentials
        "postgres://db.internal:5432/app",  # port, not a password
        "sha = " + hashlib.sha256(b"x").hexdigest(),  # hex hashes are not secrets
        "a_very_long_snake_case_identifier_name_for_a_helper",  # one case, no digits
        "ThisIsAVeryLongCamelCaseIdentifierNameForAHelper",  # no digits
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1a",  # low entropy
        "src/cognitive_mesh/ingestion/secrets.py:123",  # a path
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",  # a character table
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    redacted, found = redact_secrets(text)
    assert redacted == text
    assert found == ()


def test_a_named_rule_wins_over_the_entropy_rule_on_the_same_text() -> None:
    value = "gsk_" + RANDOM
    redacted, found = redact_secrets(f"key {value}")
    assert [r.rule for r in found] == ["groq_key"]
    assert value not in redacted


def test_the_value_never_appears_in_the_report() -> None:
    _, found = redact_secrets(f"{RANDOM}\n{GOOGLE}")
    assert RANDOM not in repr(found)
    assert GOOGLE not in repr(found)


def test_a_random_token_inside_a_longer_word_run_is_judged_as_a_whole() -> None:
    # The run is bounded by non-token characters, so the dash-joined text is judged as a whole.
    redacted, _ = redact_secrets("id-" + RANDOM + "-tail")
    assert RANDOM not in redacted
