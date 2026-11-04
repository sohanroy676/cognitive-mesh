"""Errors of the change pipeline (ADR-0042). Messages are fixed text: they never hold diff text,
model output, file content, git's own output or a token."""


class ChangeError(Exception):
    """A change cannot be made, kept or published. Maps to exit code 24."""
