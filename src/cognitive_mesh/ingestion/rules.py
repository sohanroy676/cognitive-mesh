"""Pure, name-based exclusion and classification rules. Nothing here reads file contents."""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from cognitive_mesh.ingestion.models import FileKind

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "build",
        "dist",
        ".idea",
        ".vscode",
        "site-packages",
    }  # fmt: skip
)

GENERATED_NAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pipfile.lock",
        "cargo.lock",
    }  # fmt: skip
)
GENERATED_PATTERNS = ("*.pyc", "*.min.js", "*.map")

ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
SENSITIVE_PATTERNS = (
    ".env", ".env.*",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.ppk", "*.kdbx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "credentials*", "secrets.*", "*.secret", "service-account*.json",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
)  # fmt: skip

_MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "pipfile",
        "package.json",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gemfile",
        "composer.json",
    }  # fmt: skip
)
_CONFIG_NAMES = (
    frozenset({".gitignore", ".dockerignore", ".editorconfig", ".python-version"})
    | {"dockerfile", "makefile"}
    | ENV_TEMPLATES
)
_DOC_NAME_PATTERNS = ("readme*", "license*", "changelog*")
_CONFIG_EXTS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml"})
_DOC_EXTS = frozenset({".md", ".rst", ".txt"})
SOURCE_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".go": "go", ".java": "java", ".rs": "rust", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".sh": "shell", ".ps1": "powershell", ".sql": "sql",
}  # fmt: skip


@dataclass(frozen=True)
class Classification:
    kind: FileKind
    language: str | None
    rule: str  # which rule matched, kept on the record for traceability


def is_excluded_dir(name: str) -> bool:
    lower = name.lower()
    return lower in EXCLUDED_DIRS or lower.endswith(".egg-info")


def sensitive_rule(name: str) -> str | None:
    """Return the matching sensitive-file pattern, or None. Such files are never opened."""
    lower = name.lower()
    if lower in ENV_TEMPLATES:
        return None
    for pattern in SENSITIVE_PATTERNS:
        if fnmatchcase(lower, pattern):
            return pattern
    return None


def generated_rule(name: str) -> str | None:
    lower = name.lower()
    if lower in GENERATED_NAMES:
        return lower
    for pattern in GENERATED_PATTERNS:
        if fnmatchcase(lower, pattern):
            return pattern
    return None


def _suffix(lower: str) -> str:
    dot = lower.rfind(".")
    return lower[dot:] if dot > 0 else ""


def classify(name: str) -> Classification | None:
    """Classify by file name first, then extension. None means unsupported; never guessed."""
    lower = name.lower()
    if lower in _MANIFEST_NAMES or fnmatchcase(lower, "requirements*.txt"):
        return Classification(FileKind.MANIFEST, None, f"name:{lower}")
    if lower in _CONFIG_NAMES:
        return Classification(FileKind.CONFIG, None, f"name:{lower}")
    for pattern in _DOC_NAME_PATTERNS:
        if fnmatchcase(lower, pattern):
            return Classification(FileKind.DOCUMENTATION, None, f"name:{pattern}")
    ext = _suffix(lower)
    if ext in SOURCE_LANGUAGES:
        return Classification(FileKind.SOURCE, SOURCE_LANGUAGES[ext], f"ext:{ext}")
    if ext in _CONFIG_EXTS:
        return Classification(FileKind.CONFIG, None, f"ext:{ext}")
    if ext in _DOC_EXTS:
        return Classification(FileKind.DOCUMENTATION, None, f"ext:{ext}")
    return None
