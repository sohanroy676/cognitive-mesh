"""Read and judge a model-written unified diff before git sees it (ADR-0042).

Pure functions. Only a plain text unified diff that edits or adds files inside the current
project is accepted. Every refusal has a fixed code and a fixed message; diff text is never
echoed (it is model output, treated as data).
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from cognitive_mesh.ingestion.rules import sensitive_rule

DEFAULT_MAX_FILES: Final = 3
DEFAULT_MAX_LINES: Final = 100
HARD_MAX_FILES: Final = 10
HARD_MAX_LINES: Final = 500
MAX_DIFF_CHARS: Final = 200_000
MAX_PATH_CHARS: Final = 300

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BAD_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f\\:*?\"<>|]")


class Refusal(StrEnum):
    EMPTY = "empty"
    TOO_LARGE_TEXT = "diff_text_too_large"
    NOT_UNIFIED = "not_a_unified_diff"
    MALFORMED = "malformed_diff"
    BINARY = "binary_change"
    RENAME_OR_COPY = "rename_or_copy"
    MODE_CHANGE = "mode_change"
    SYMLINK = "symlink"
    DELETION = "file_deletion"
    BAD_PATH = "bad_path"
    SENSITIVE_PATH = "sensitive_path"
    OTHER_PROJECT = "other_project_path"
    TOO_MANY_FILES = "too_many_files"
    TOO_MANY_LINES = "too_many_lines"
    DOES_NOT_APPLY = "does_not_apply"
    NOT_FOUND = "file_not_found"


_MESSAGES: Final = {
    Refusal.EMPTY: "the diff is empty",
    Refusal.TOO_LARGE_TEXT: "the diff text is too long",
    Refusal.NOT_UNIFIED: "the text is not a unified diff",
    Refusal.MALFORMED: "the diff is malformed (hunk header or line counts do not match)",
    Refusal.BINARY: "binary changes are not accepted",
    Refusal.RENAME_OR_COPY: "renames and copies are not accepted",
    Refusal.MODE_CHANGE: "mode changes are not accepted",
    Refusal.SYMLINK: "symbolic links are not accepted",
    Refusal.DELETION: "deleting a whole file is not accepted",
    Refusal.BAD_PATH: "a path in the diff is not a plain relative path inside the project",
    Refusal.SENSITIVE_PATH: "a path in the diff names a sensitive file",
    Refusal.OTHER_PROJECT: "a path in the diff lies inside another project of the workspace",
    Refusal.TOO_MANY_FILES: "the diff changes more files than the limit allows",
    Refusal.TOO_MANY_LINES: "the diff changes more lines than the limit allows",
    Refusal.DOES_NOT_APPLY: "the diff does not apply to the current files",
    Refusal.NOT_FOUND: "a named file is not in the project",
}


class DiffError(Exception):
    """The diff is refused. `code` is fixed; the message never holds diff text."""

    def __init__(self, code: Refusal) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


@dataclass(frozen=True)
class Limits:
    max_files: int = DEFAULT_MAX_FILES
    max_lines: int = DEFAULT_MAX_LINES

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= HARD_MAX_FILES:
            raise ValueError(f"max files must be 1 to {HARD_MAX_FILES}")
        if not 1 <= self.max_lines <= HARD_MAX_LINES:
            raise ValueError(f"max lines must be 1 to {HARD_MAX_LINES}")


@dataclass(frozen=True)
class FileChange:
    path: str  # relative to the project root, POSIX separators
    is_new: bool
    added: int
    removed: int


@dataclass(frozen=True)
class ParsedDiff:
    files: tuple[FileChange, ...]

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def removed(self) -> int:
        return sum(f.removed for f in self.files)

    @property
    def changed_lines(self) -> int:
        return self.added + self.removed


def clean_path(raw: str, forbidden_prefixes: tuple[str, ...] = ()) -> str:
    """A plain relative POSIX path inside the project, or DiffError."""
    text = raw.split("\t", 1)[0].strip()
    if text.startswith('"'):  # git quotes odd names; refuse them rather than unescape
        raise DiffError(Refusal.BAD_PATH)
    if not text or len(text) > MAX_PATH_CHARS or text.startswith("/") or text.endswith("/"):
        raise DiffError(Refusal.BAD_PATH)
    if _BAD_PATH_CHARS.search(text):
        raise DiffError(Refusal.BAD_PATH)
    parts = text.split("/")
    if any(p in ("", ".", "..") or p.lower() == ".git" for p in parts):
        raise DiffError(Refusal.BAD_PATH)
    if any(p.lower() in (".gitmodules", ".gitattributes", ".gitignore") for p in parts[-1:]):
        raise DiffError(Refusal.BAD_PATH)
    if sensitive_rule(parts[-1]) is not None:
        raise DiffError(Refusal.SENSITIVE_PATH)
    for prefix in forbidden_prefixes:
        base = prefix.strip("/")
        if base and (text == base or text.startswith(base + "/")):
            raise DiffError(Refusal.OTHER_PROJECT)
    return text


def _strip_prefix(raw: str, side: str) -> str:
    text = raw.split("\t", 1)[0].strip()
    if text == "/dev/null":
        return text
    if text.startswith(f"{side}/"):
        return text[2:]
    return text


def parse_diff(
    text: str,
    limits: Limits | None = None,
    *,
    forbidden_prefixes: tuple[str, ...] = (),
) -> ParsedDiff:
    """Parse and judge `text`; raise `DiffError` with a fixed code on the first problem."""
    limits = limits or Limits()
    if not text.strip():
        raise DiffError(Refusal.EMPTY)
    if len(text) > MAX_DIFF_CHARS:
        raise DiffError(Refusal.TOO_LARGE_TEXT)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[FileChange] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\r")
        if line.startswith("diff --git "):
            i = _file_section(lines, i, files, forbidden_prefixes)
        elif line.startswith("--- "):
            i = _file_section(lines, i, files, forbidden_prefixes, headerless=True)
        elif line.strip() == "" and not files:
            i += 1
        else:
            raise DiffError(Refusal.NOT_UNIFIED)
        if len(files) > limits.max_files:
            raise DiffError(Refusal.TOO_MANY_FILES)
    if not files:
        raise DiffError(Refusal.NOT_UNIFIED)
    seen = [f.path for f in files]
    if len(set(seen)) != len(seen):
        raise DiffError(Refusal.MALFORMED)
    parsed = ParsedDiff(tuple(files))
    if parsed.changed_lines > limits.max_lines:
        raise DiffError(Refusal.TOO_MANY_LINES)
    if parsed.changed_lines == 0:
        raise DiffError(Refusal.EMPTY)
    return parsed


def _file_section(
    lines: list[str],
    start: int,
    out: list[FileChange],
    forbidden: tuple[str, ...],
    *,
    headerless: bool = False,
) -> int:
    n = len(lines)
    i = start
    is_new = False
    if not headerless:
        i += 1
        while i < n and not lines[i].startswith(("--- ", "@@ ", "diff --git ")):
            head = lines[i].rstrip("\r")
            if head.startswith(("rename ", "copy ", "similarity ", "dissimilarity ")):
                raise DiffError(Refusal.RENAME_OR_COPY)
            if head.startswith(("old mode", "new mode")):
                raise DiffError(Refusal.MODE_CHANGE)
            if head.startswith("deleted file mode"):
                raise DiffError(Refusal.DELETION)
            if head.startswith("new file mode"):
                mode = head.rsplit(" ", 1)[-1]
                if mode == "120000":
                    raise DiffError(Refusal.SYMLINK)
                if mode != "100644":
                    raise DiffError(Refusal.MODE_CHANGE)
                is_new = True
            elif head.startswith(("GIT binary patch", "Binary files")):
                raise DiffError(Refusal.BINARY)
            elif not head.startswith("index "):
                raise DiffError(Refusal.NOT_UNIFIED)
            i += 1
    if i + 1 >= n or not lines[i].startswith("--- ") or not lines[i + 1].startswith("+++ "):
        raise DiffError(Refusal.NOT_UNIFIED)
    old = _strip_prefix(lines[i][4:].rstrip("\r"), "a")
    new = _strip_prefix(lines[i + 1][4:].rstrip("\r"), "b")
    i += 2
    if new == "/dev/null":
        raise DiffError(Refusal.DELETION)
    if old == "/dev/null":
        is_new = True
    elif old != new:
        raise DiffError(Refusal.RENAME_OR_COPY)
    path = clean_path(new, forbidden)
    added = removed = 0
    hunks = 0
    while i < n:
        head = lines[i].rstrip("\r")
        if head.startswith(("diff --git ", "--- ")) and hunks:
            # a "--- " inside a hunk is a removed line starting with "-- "; hunks are counted below
            break
        match = _HUNK.fullmatch(head.split(" @@", 1)[0] + " @@") if head.startswith("@@") else None
        if match is None:
            raise DiffError(Refusal.MALFORMED)
        old_left = int(match.group(2) if match.group(2) is not None else 1)
        new_left = int(match.group(4) if match.group(4) is not None else 1)
        if match.group(3) == "0" and new_left == 0 and old_left > 0:
            raise DiffError(Refusal.DELETION)
        hunks += 1
        i += 1
        while old_left > 0 or new_left > 0 or (i < n and lines[i].startswith("\\")):
            if i >= n:
                raise DiffError(Refusal.MALFORMED)
            body = lines[i]
            mark = body[:1]
            if mark == "\\":
                pass
            elif mark == "+":
                added += 1
                new_left -= 1
            elif mark == "-":
                removed += 1
                old_left -= 1
            elif mark == " " or body == "":
                old_left -= 1
                new_left -= 1
            else:
                raise DiffError(Refusal.MALFORMED)
            if old_left < 0 or new_left < 0:
                raise DiffError(Refusal.MALFORMED)
            i += 1
    if hunks == 0:
        raise DiffError(Refusal.BINARY if is_new and added == 0 else Refusal.MALFORMED)
    out.append(FileChange(path=path, is_new=is_new, added=added, removed=removed))
    return i
