"""Judging a model-written diff before git sees it (ADR-0042): adversarial inputs."""

import pytest

from cognitive_mesh.change.diffparse import DiffError, Limits, Refusal, parse_diff

GOOD = """diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,3 @@
 x = 1
-y = 2
+y = 3
 z = 4
"""

NEW_FILE = """diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+a = 1
+b = 2
"""


def code(text: str, **kw: object) -> Refusal:
    with pytest.raises(DiffError) as info:
        parse_diff(text, **kw)  # type: ignore[arg-type]
    return info.value.code


def test_good_and_new_file() -> None:
    parsed = parse_diff(GOOD)
    assert [(f.path, f.added, f.removed, f.is_new) for f in parsed.files] == [
        ("src/a.py", 1, 1, False)
    ]
    assert parse_diff(NEW_FILE).files[0].is_new
    assert parse_diff(GOOD + NEW_FILE).changed_lines == 4


def test_headerless_unified_diff_is_accepted() -> None:
    plain = GOOD.split("\n", 2)[2]
    assert parse_diff(plain).files[0].path == "src/a.py"


@pytest.mark.parametrize("text", ["", "   \n\n"])
def test_empty(text: str) -> None:
    assert code(text) is Refusal.EMPTY


def test_prose_is_not_a_diff() -> None:
    assert code("Sure! Here is the change you wanted.\n") is Refusal.NOT_UNIFIED


@pytest.mark.parametrize(
    "path",
    [
        "../evil.py",
        "src/../../evil.py",
        "/etc/passwd",
        ".git/config",
        "a/.git/hooks/pre-commit",
        "C:/x.py",
        "src\\a.py",
        "src//a.py",
        "./a.py",
        '"a b.py"',
        "a\x01b.py",
        ".gitmodules",
    ],
)
def test_bad_paths(path: str) -> None:
    text = GOOD.replace("src/a.py", path)
    assert code(text) in (Refusal.BAD_PATH, Refusal.NOT_UNIFIED, Refusal.MALFORMED)
    assert code(text) is not None


@pytest.mark.parametrize("path", [".env", "src/.env.local", "keys/id_rsa", "cert.pem"])
def test_sensitive_paths(path: str) -> None:
    assert code(NEW_FILE.replace("src/new.py", path)) is Refusal.SENSITIVE_PATH


def test_other_project_prefix() -> None:
    assert code(GOOD, forbidden_prefixes=("src",)) is Refusal.OTHER_PROJECT
    assert code(GOOD, forbidden_prefixes=("src/",)) is Refusal.OTHER_PROJECT
    assert parse_diff(GOOD, forbidden_prefixes=("srcx",)).files  # prefix is a path, not a string


def test_rename_copy_mode_symlink_binary_deletion() -> None:
    rename = "diff --git a/a b/b\nsimilarity index 100%\nrename from a\nrename to b\n"
    assert code(rename) is Refusal.RENAME_OR_COPY
    mode = "diff --git a/a b/a\nold mode 100644\nnew mode 100755\n"
    assert code(mode) is Refusal.MODE_CHANGE
    assert code(NEW_FILE.replace("100644", "100755")) is Refusal.MODE_CHANGE
    assert code(NEW_FILE.replace("100644", "120000")) is Refusal.SYMLINK
    binary = "diff --git a/a.png b/a.png\nGIT binary patch\nliteral 3\nabc\n"
    assert code(binary) is Refusal.BINARY
    assert (
        code("diff --git a/a.png b/a.png\nBinary files a/a.png and b/a.png differ\n")
        is Refusal.BINARY
    )
    gone = (
        "diff --git a/a b/a\ndeleted file mode 100644\n--- a/a\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    assert code(gone) is Refusal.DELETION
    emptied = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +0,0 @@\n-x\n-y\n"
    assert code(emptied) is Refusal.DELETION
    differ = GOOD.replace("+++ b/src/a.py", "+++ b/src/b.py")
    assert code(differ) is Refusal.RENAME_OR_COPY


def test_limits() -> None:
    four = "".join(
        f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-a\n+b\n"
        for i in range(4)
    )
    assert code(four) is Refusal.TOO_MANY_FILES
    assert len(parse_diff(four, Limits(max_files=4)).files) == 4
    big = "--- a/a.py\n+++ b/a.py\n@@ -1,60 +1,60 @@\n" + "-a\n" * 60 + "+b\n" * 60
    assert code(big) is Refusal.TOO_MANY_LINES
    assert parse_diff(big, Limits(max_lines=120)).changed_lines == 120


def test_hard_caps() -> None:
    with pytest.raises(ValueError, match="max files"):
        Limits(max_files=11)
    with pytest.raises(ValueError, match="max lines"):
        Limits(max_lines=501)
    with pytest.raises(ValueError, match="max lines"):
        Limits(max_lines=0)


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("@@ -1,3 +1,3 @@", "@@ -1,9 +1,9 @@"),  # counts promise more lines
        GOOD.replace("@@ -1,3 +1,3 @@", "@@ -1,1 +1,1 @@"),  # counts promise fewer lines
        GOOD.replace("@@ -1,3 +1,3 @@", "@@ nonsense @@"),
        "--- a/a.py\n+++ b/a.py\n",  # no hunk
        "--- a/a.py\n",  # no +++ line
        GOOD + GOOD,  # same file twice
    ],
)
def test_malformed(text: str) -> None:
    assert code(text) in (Refusal.MALFORMED, Refusal.NOT_UNIFIED)


def test_diff_text_is_never_echoed() -> None:
    marker = "MARKER-IN-DIFF-9f3a"
    with pytest.raises(DiffError) as info:
        parse_diff(GOOD.replace("src/a.py", f"../{marker}"))
    assert marker not in str(info.value)


def test_text_size_cap() -> None:
    assert code("x" * 200_001) is Refusal.TOO_LARGE_TEXT


def test_no_newline_marker_and_crlf() -> None:
    marked = GOOD.replace("+y = 3\n", "+y = 3\n\\ No newline at end of file\n")
    assert parse_diff(marked).changed_lines == 2
    assert parse_diff(GOOD.replace("\n", "\r\n")).changed_lines == 2
