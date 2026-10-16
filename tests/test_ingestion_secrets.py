import pytest

from cognitive_mesh.ingestion.secrets import redact_secrets

# Fake values assembled at runtime so no secret-looking literal sits in the repository.
GROQ = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
OPENAI = "sk-" + "proj_abcdefghijklmnopqrstuv"
SLACK = "xoxb-" + "1234567890-abcdefghij"
BEGIN = "-----BEGIN "
END = "-----END "
PRIVATE_KEY = BEGIN + "RSA PRIVATE KEY-----\nMIIEfake\nlines\n" + END + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        ("groq_key", GROQ),
        ("github_token", GITHUB),
        ("aws_access_key", AWS),
        ("openai_style_key", OPENAI),
        ("slack_token", SLACK),
        ("private_key_block", PRIVATE_KEY),
    ],
)
def test_token_patterns_are_redacted(rule: str, value: str) -> None:
    text = f"line one\nvalue is {value}\nlast"
    redacted, found = redact_secrets(text)
    assert value not in redacted
    assert f"[REDACTED:{rule}]" in redacted
    assert [(r.rule, r.line) for r in found] == [(rule, 2)]
    assert redacted.startswith("line one\n") and redacted.endswith("\nlast")


def test_unterminated_private_key_redacts_to_end() -> None:
    redacted, found = redact_secrets(BEGIN + "PRIVATE KEY-----\nMIIEfakebody\nmore")
    assert "MIIEfakebody" not in redacted
    assert found[0].rule == "private_key_block"


@pytest.mark.parametrize(
    "line",
    [
        "API_KEY=hunter2hunter2hunter2",
        'password: "correct-horse-battery"',
        "export SECRET_TOKEN = abcdefghijklmnop",
        "db.passwd=averylongpasswordvalue",
    ],
)
def test_generic_assignments_are_redacted(line: str) -> None:
    redacted, found = redact_secrets(line)
    assert [r.rule for r in found] == ["generic_assignment"]
    assert "[REDACTED:generic_assignment]" in redacted
    # the key name stays so the structure remains readable
    assert redacted.split("=")[0].split(":")[0].strip()


@pytest.mark.parametrize(
    "line",
    [
        "APP_API_KEY=your-api-key-here",
        "GROQ_API_KEY=",
        "token = <paste token>",
        "password: ${DB_PASSWORD}",
        "secret=changeme-please-change",
        "api_key = os.environ.get('SOME_API_KEY_NAME')",
        "token: str = None",
        "password=short",
        "just prose about a password and a token",
    ],
)
def test_placeholders_and_code_are_not_redacted(line: str) -> None:
    redacted, found = redact_secrets(line)
    assert redacted == line
    assert found == ()


def test_overlapping_matches_redact_once() -> None:
    redacted, found = redact_secrets(f"GROQ_API_KEY={GROQ}")
    assert GROQ not in redacted
    assert len(found) == 1


def test_multiple_secrets_report_each_line() -> None:
    text = f"a={AWS}\nnothing\nb={GITHUB}\n"
    _, found = redact_secrets(text)
    assert [(r.rule, r.line) for r in found] == [("aws_access_key", 1), ("github_token", 3)]


def test_clean_text_unchanged() -> None:
    text = "def f():\n    return 1\n"
    assert redact_secrets(text) == (text, ())
