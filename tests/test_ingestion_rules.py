import pytest

from cognitive_mesh.ingestion.models import FileKind
from cognitive_mesh.ingestion.rules import (
    classify,
    generated_rule,
    is_excluded_dir,
    sensitive_rule,
)


@pytest.mark.parametrize(
    ("name", "kind", "language"),
    [
        ("main.py", FileKind.SOURCE, "python"),
        ("MAIN.PY", FileKind.SOURCE, "python"),
        ("index.ts", FileKind.SOURCE, "typescript"),
        ("pyproject.toml", FileKind.MANIFEST, None),
        ("package.json", FileKind.MANIFEST, None),  # manifest wins over the .json config rule
        ("setup.py", FileKind.MANIFEST, None),  # manifest wins over the .py source rule
        ("requirements-dev.txt", FileKind.MANIFEST, None),
        ("app.json", FileKind.CONFIG, None),
        ("settings.yml", FileKind.CONFIG, None),
        (".env.example", FileKind.CONFIG, None),
        ("Dockerfile", FileKind.CONFIG, None),
        ("README", FileKind.DOCUMENTATION, None),
        ("notes.txt", FileKind.DOCUMENTATION, None),
        ("guide.rst", FileKind.DOCUMENTATION, None),
    ],
)
def test_classify_supported(name: str, kind: FileKind, language: str | None) -> None:
    result = classify(name)
    assert result is not None
    assert result.kind is kind
    assert result.language == language
    assert result.rule


@pytest.mark.parametrize("name", ["logo.png", "data.bin", "archive.zip", "noextension", ".hidden"])
def test_classify_unsupported_is_none_not_guessed(name: str) -> None:
    assert classify(name) is None


@pytest.mark.parametrize(
    "name",
    [
        ".env", ".env.local", ".ENV", "server.pem", "id_rsa", "id_rsa.pub", "credentials.json",
        "secrets.yaml", "service-account-prod.json", ".npmrc", "keystore.jks", "a.kdbx",
    ],
)  # fmt: skip
def test_sensitive_names(name: str) -> None:
    assert sensitive_rule(name) is not None


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template", "main.py"])
def test_non_sensitive_names(name: str) -> None:
    assert sensitive_rule(name) is None


@pytest.mark.parametrize(
    "name", ["poetry.lock", "yarn.lock", "Cargo.lock", "a.pyc", "app.min.js", "a.js.map"]
)
def test_generated_names(name: str) -> None:
    assert generated_rule(name) is not None


@pytest.mark.parametrize("name", ["uv.lock", "package-lock.json"])
def test_read_lockfiles_are_manifests_not_generated(name: str) -> None:
    assert generated_rule(name) is None
    cls = classify(name)
    assert cls is not None
    assert cls.kind is FileKind.MANIFEST


@pytest.mark.parametrize("name", [".git", "node_modules", ".venv", "__pycache__", "x.egg-info"])
def test_excluded_dirs(name: str) -> None:
    assert is_excluded_dir(name)


def test_regular_dirs_not_excluded() -> None:
    assert not is_excluded_dir("src")
    assert not is_excluded_dir("builder")
