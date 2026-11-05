"""Models get hunk header counts wrong; `normalise_hunk_counts` fixes only the numbers."""

import pytest

from cognitive_mesh.change.diffparse import DiffError, normalise_hunk_counts, parse_diff

HEAD = "--- a/calc.py\n+++ b/calc.py\n"
BODY = (
    ' def add(a, b):\n-    return a + b\n+    """Sum."""\n+    return a + b\n \n \n'
    " def sub(a, b):\n     return a - b\n"
)


def test_wrong_counts_are_refused_before_and_accepted_after() -> None:
    bad = HEAD + "@@ -1,4 +1,5 @@\n" + BODY
    with pytest.raises(DiffError):
        parse_diff(bad)
    fixed = normalise_hunk_counts(bad)
    assert "@@ -1,6 +1,7 @@" in fixed
    assert fixed.endswith(BODY)  # every body line is kept as written
    parsed = parse_diff(fixed)
    assert (parsed.changed_lines, parsed.files[0].path) == (3, "calc.py")


def test_a_correct_diff_is_unchanged() -> None:
    good = HEAD + "@@ -1,6 +1,7 @@\n" + BODY
    assert normalise_hunk_counts(good) == good


def test_two_hunks_and_a_second_file_are_counted_separately() -> None:
    text = (
        HEAD
        + "@@ -1,9 +1,9 @@\n-a\n+b\n@@ -20,9 +20,9 @@\n x\n-y\n+z\n"
        + "--- a/other.py\n+++ b/other.py\n@@ -1,5 +1,5 @@\n-q\n+r\n"
    )
    fixed = normalise_hunk_counts(text)
    assert "@@ -1,1 +1,1 @@\n-a\n+b\n" in fixed
    assert "@@ -20,2 +20,2 @@\n x\n-y\n+z\n" in fixed
    assert "@@ -1,1 +1,1 @@\n-q\n+r" in fixed
    parse_diff(fixed)


def test_an_unusual_body_line_leaves_the_text_alone() -> None:
    odd = HEAD + "@@ -1,1 +1,1 @@\n-a\n+b\nnot a diff line\n"
    assert normalise_hunk_counts(odd) == odd
    with pytest.raises(DiffError):
        parse_diff(odd)


def test_text_without_hunks_is_unchanged() -> None:
    assert normalise_hunk_counts("hello\nworld") == "hello\nworld"
