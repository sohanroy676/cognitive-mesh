"""Errors raised by the web UI's routes."""


class ApiError(Exception):
    """A failure with the HTTP status to report and a message safe to show."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
