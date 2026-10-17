"""StdlibTransport against real local sockets (127.0.0.1 only; no external network)."""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager

import pytest

from cognitive_mesh.providers.http import StdlibTransport, TransportError

HEADERS = {"Content-Type": "application/json"}


@contextmanager
def _server(handler: Callable[[socket.socket], None]) -> Iterator[str]:
    """A one-connection-at-a-time TCP server; `handler` gets each accepted socket."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    listener.settimeout(0.1)
    stop = threading.Event()
    clients: list[socket.socket] = []

    def loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            clients.append(conn)
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/api/chat"
    finally:
        stop.set()
        thread.join()
        for c in clients:
            c.close()
        listener.close()


def _read_request(conn: socket.socket) -> None:
    conn.settimeout(2)
    data = b""
    while b"\r\n\r\n" not in data:
        data += conn.recv(4096)
    head, _, rest = data.partition(b"\r\n\r\n")
    length = int(
        next(
            line.split(b":")[1]
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length")
        )
    )
    while len(rest) < length:
        rest += conn.recv(4096)


def _respond(status: str, body: bytes) -> Callable[[socket.socket], None]:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        head = f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        conn.sendall(head.encode() + body)
        conn.close()

    return handler


def _post(url: str, timeout_s: float = 2.0, cap: int = 1000) -> object:
    return StdlibTransport().post_json(
        url, b'{"a": 1}', HEADERS, timeout_s=timeout_s, max_body_bytes=cap
    )


def test_reads_status_and_body() -> None:
    with _server(_respond("200 OK", b'{"ok": true}')) as url:
        reply = _post(url)
    assert (reply.status, reply.body) == (200, b'{"ok": true}')  # type: ignore[attr-defined]


def test_an_error_status_is_returned_not_raised() -> None:
    with _server(_respond("404 Not Found", b"nope")) as url:
        assert _post(url).status == 404  # type: ignore[attr-defined]


def test_redirects_are_not_followed() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(
            b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:9/\r\nContent-Length: 0\r\n\r\n"
        )
        conn.close()

    with _server(handler) as url:
        assert _post(url).status == 302  # type: ignore[attr-defined]


def test_connection_refused_is_unavailable() -> None:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(TransportError) as exc:
        _post(f"http://127.0.0.1:{port}/x", timeout_s=10)  # Windows refuses after ~2 s
    assert exc.value.kind == "unavailable"


def test_a_server_that_closes_early_is_unavailable() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.close()

    with _server(handler) as url, pytest.raises(TransportError) as exc:
        _post(url)
    assert exc.value.kind == "unavailable"


def test_a_truncated_body_is_unavailable() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort")
        conn.close()

    with _server(handler) as url, pytest.raises(TransportError) as exc:
        _post(url)
    assert exc.value.kind == "unavailable"


def test_an_oversized_body_is_too_large() -> None:
    with _server(_respond("200 OK", b"x" * 5000)) as url, pytest.raises(TransportError) as exc:
        _post(url, cap=1000)
    assert exc.value.kind == "too_large"


def test_a_body_exactly_at_the_cap_is_accepted() -> None:
    with _server(_respond("200 OK", b"x" * 1000)) as url:
        assert len(_post(url, cap=1000).body) == 1000  # type: ignore[attr-defined]


def _elapsed_for_timeout(handler: Callable[[socket.socket], None], timeout_s: float) -> float:
    with _server(handler) as url:
        start = time.monotonic()
        with pytest.raises(TransportError) as exc:
            _post(url, timeout_s=timeout_s)
        elapsed = time.monotonic() - start
    assert exc.value.kind == "timeout"
    return elapsed


def test_a_server_that_never_answers_stops_at_the_timeout() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        time.sleep(3)

    assert _elapsed_for_timeout(handler, 0.4) < 1.5


def test_a_slow_drip_body_is_stopped_at_the_total_deadline() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
            for _ in range(100):  # each gap is below the per-read timeout
                conn.sendall(b"x")
                time.sleep(0.15)
        except OSError:
            pass

    assert _elapsed_for_timeout(handler, 0.5) < 1.5


def test_slow_drip_headers_are_stopped_at_the_total_deadline() -> None:
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        try:
            for ch in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 100:
                conn.sendall(bytes([ch]))
                time.sleep(0.15)
        except OSError:
            pass

    assert _elapsed_for_timeout(handler, 0.5) < 1.5


@pytest.mark.parametrize(
    "url", ["ftp://127.0.0.1/x", "file:///etc/passwd", "http:///x", "http://127.0.0.1:99999/x", "x"]
)
def test_bad_urls_are_unavailable_without_a_connection(url: str) -> None:
    with pytest.raises(TransportError) as exc:
        _post(url)
    assert exc.value.kind == "unavailable"
