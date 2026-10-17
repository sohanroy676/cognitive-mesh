"""Private HTTP transport for provider clients (ADR-0012). Standard library only.

One monotonic deadline covers connecting, sending and reading the whole body. A timer shuts the
socket down at the deadline, so a silent or slow-drip server cannot run past `timeout_s`. Failures
are reported as `TransportError(kind)` only; exception text is never kept.
"""

import contextlib
import http.client
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

TransportErrorKind = Literal["timeout", "unavailable", "too_large"]

_CHUNK = 64 * 1024


class TransportError(Exception):
    def __init__(self, kind: TransportErrorKind) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes


class HttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpReply:
        """POST `body`; raise TransportError. Never follows redirects, never retries."""
        ...


def _shutdown(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        sock.close()


class StdlibTransport:
    def post_json(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpReply:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise TransportError("unavailable")
        deadline = time.monotonic() + timeout_s
        conn: http.client.HTTPConnection
        try:
            if parts.scheme == "https":
                conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout_s)
            else:
                conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout_s)
        except ValueError:  # an invalid port
            raise TransportError("unavailable") from None
        timed_out = threading.Event()
        timer: threading.Timer | None = None
        try:
            conn.connect()
            sock = conn.sock
            if sock is None:
                raise TransportError("unavailable")

            def expire() -> None:
                timed_out.set()
                _shutdown(sock)

            timer = threading.Timer(max(deadline - time.monotonic(), 0.0), expire)
            timer.daemon = True
            timer.start()
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn.request("POST", path, body, dict(headers))
            response = conn.getresponse()
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if not response.isclosed():  # http.client closes the socket at the end
                    sock.settimeout(remaining)
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_body_bytes:
                    raise TransportError("too_large")
                chunks.append(chunk)
            if response.length:  # read(amt) does not raise on a body shorter than declared
                raise TransportError("unavailable")
            return HttpReply(response.status, b"".join(chunks))
        except TransportError:
            raise
        except TimeoutError:
            raise TransportError("timeout") from None
        except (OSError, http.client.HTTPException, ValueError):
            if timed_out.is_set() or time.monotonic() >= deadline:
                raise TransportError("timeout") from None
            raise TransportError("unavailable") from None
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()
