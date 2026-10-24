"""`cognitive-mesh ui`: start-up, the printed URL, the browser, a busy port, and Ctrl+C."""

import socket
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import EXIT_UI_ERROR, main
from cognitive_mesh.ui.server import UiServer


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    def record(url: str, *args: object, **kwargs: object) -> bool:
        urls.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", record)
    return urls


def interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def stop(self: UiServer, *args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(UiServer, "serve_forever", stop)


def test_ui_prints_a_tokenised_loopback_url_and_stops_cleanly_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opened: list[str]
) -> None:
    interrupt(monkeypatch)
    assert main(["ui", "--port", "0", "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "serving read-only on http://127.0.0.1:" in out and "/?token=" in out
    assert "access token" in out
    assert opened == []


def test_ui_opens_the_browser_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opened: list[str]
) -> None:
    interrupt(monkeypatch)
    assert main(["ui", "--port", "0"]) == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")
    assert opened[0] in capsys.readouterr().out


def test_each_start_makes_a_new_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    interrupt(monkeypatch)
    urls = []
    for _ in range(2):
        assert main(["ui", "--port", "0", "--no-browser"]) == 0
        urls.append(capsys.readouterr().out.split("?token=")[1].split()[0])
    assert urls[0] != urls[1] and len(urls[0]) >= 32


def test_a_busy_port_exits_18_naming_the_port(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert main(["ui", "--port", str(port), "--no-browser"]) == EXIT_UI_ERROR == 18
    err = capsys.readouterr().err
    assert str(port) in err and "in use" in err


@pytest.mark.parametrize("port", ["-1", "65536", "abc"])
def test_a_bad_port_is_a_usage_error(port: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ui", "--port", port])
    assert exc.value.code == 2


def test_there_is_no_host_option() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ui", "--host", "example.invalid", "--port", "0"])
    assert exc.value.code == 2


def test_ui_reads_no_config_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    interrupt(monkeypatch)
    monkeypatch.setenv("COGMESH_PROVIDER", "bogus")
    missing = tmp_path / "nope.sqlite3"
    assert main(["ui", "--port", "0", "--no-browser", "--db", str(missing)]) == 0
    assert not missing.exists() and cli.EXIT_UI_ERROR == 18
