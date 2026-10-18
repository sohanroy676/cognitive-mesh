"""Application identity (ADR-0021): a local label for which application a run analysed.

The id is declared by the person or derived from the manifest and directory name. Nothing proves it:
two applications can share one, and renaming a project splits its history.
"""

import json
import re
import tomllib
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict

MAX_ID_LENGTH = 100
_MANIFEST_LIMIT = 1_000_000  # bytes; a larger manifest is ignored, not parsed
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@/+-]*")


class IdentityError(ValueError):
    """A declared application id is not acceptable."""


class IdentityHow(StrEnum):
    DECLARED = "declared"
    MANIFEST = "manifest"
    DIRECTORY = "directory"


class AppIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_id: str
    how: IdentityHow

    @property
    def weak(self) -> bool:
        """A directory name alone says little: many unrelated applications share one."""
        return self.how is IdentityHow.DIRECTORY


def validate_app_id(value: str) -> str:
    if len(value) > MAX_ID_LENGTH or _ID.fullmatch(value) is None:
        raise IdentityError(
            "the application id must be 1-100 characters of letters, digits and . _ @ / + -, "
            "starting with a letter or digit"
        )
    return value


def directory_name(root: str) -> str:
    return PureWindowsPath(root).name or PurePosixPath(root).name


def _manifest_name(root: Path) -> str | None:
    """The project name from `pyproject.toml` or `package.json`; None if absent or unusable."""
    for filename in ("pyproject.toml", "package.json"):
        path = root / filename
        try:
            if not path.is_file() or path.stat().st_size > _MANIFEST_LIMIT:
                continue
            raw = path.read_bytes()
            if filename == "pyproject.toml":
                name = tomllib.loads(raw.decode("utf-8")).get("project", {}).get("name")
            else:
                name = json.loads(raw.decode("utf-8")).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(name, str) and _ID.fullmatch(name) and len(name) <= MAX_ID_LENGTH // 2:
            return name
    return None


def derive_identity(root: str, declared: str | None = None) -> AppIdentity | None:
    """`declared` wins; else manifest name plus directory name; else directory name; else None."""
    if declared is not None:
        return AppIdentity(app_id=validate_app_id(declared), how=IdentityHow.DECLARED)
    directory = directory_name(root)
    name = _manifest_name(Path(root))
    if name is not None:
        combined = f"{name}@{directory}" if directory else name
        if len(combined) <= MAX_ID_LENGTH and _ID.fullmatch(combined):
            return AppIdentity(app_id=combined, how=IdentityHow.MANIFEST)
    if directory and len(directory) <= MAX_ID_LENGTH and _ID.fullmatch(directory):
        return AppIdentity(app_id=directory, how=IdentityHow.DIRECTORY)
    return None
