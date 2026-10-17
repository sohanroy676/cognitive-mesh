"""Failure mapping shared by every real client (ADR-0012, ADR-0013).

One place decides which `ProviderErrorKind` an HTTP status or transport failure becomes, so two
clients cannot disagree about the same failure. Messages hold the provider name and a status number
or a fixed phrase only: never a request, reply, body, header or exception text.
"""

from cognitive_mesh.providers.base import ProviderError, ProviderErrorKind
from cognitive_mesh.providers.http import TransportError

_RATE_LIMITED = frozenset({429, 498})  # 498: Groq flex tier at capacity


def status_error(provider: str, status: int) -> ProviderError | None:
    """None for HTTP 200; otherwise the error every client raises for this status."""
    if status == 200:
        return None
    message = f"{provider} returned HTTP {status}"
    if status in _RATE_LIMITED:
        return ProviderError(ProviderErrorKind.RATE_LIMITED, message)
    if status >= 500:
        return ProviderError(ProviderErrorKind.UNAVAILABLE, message)
    if status in (401, 403):
        return ProviderError(ProviderErrorKind.AUTH, message)
    if 400 <= status < 500:  # e.g. an unknown model, a rejected request
        return ProviderError(ProviderErrorKind.OTHER, message)
    return ProviderError(ProviderErrorKind.BAD_RESPONSE, message)


def bad_reply(provider: str, reason: str) -> ProviderError:
    return ProviderError(ProviderErrorKind.BAD_RESPONSE, f"{provider} reply rejected: {reason}")


def transport_error(provider: str, exc: TransportError) -> ProviderError:
    if exc.kind == "timeout":
        return ProviderError(ProviderErrorKind.TIMEOUT, f"{provider} call timed out")
    if exc.kind == "too_large":
        return bad_reply(provider, "body too large")
    return ProviderError(ProviderErrorKind.UNAVAILABLE, f"{provider} could not be reached")
