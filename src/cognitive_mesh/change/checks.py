"""Run the person's declared check command on a change (ADR-0042, amends ADR-0005).

This is the only place the tool executes a project's command. It is not isolation: the command runs
with the user's privileges, and raw sockets still work. What is done: a reduced environment without
inherited secrets, proxy and offline variables that make well-behaved tools fail rather than reach
the network, a timeout, and a kill of the whole process tree. The result always carries the
isolation label `env_only`.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Final

from cognitive_mesh.ingestion.secrets import redact_secrets

ISOLATION: Final = "env_only"
TAIL_CHARS: Final = 20_000
# redact_secrets is quadratic in the length of an unbroken run, so a long line is not shown at all
MAX_LINE_CHARS: Final = 1_000
FEEDBACK_CHARS: Final = 4_000
_DEAD_PROXY: Final = "http://127.0.0.1:9"
_ENV_KEEP: Final = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
)
_OFFLINE_ENV: Final = {
    "HTTP_PROXY": _DEAD_PROXY,
    "HTTPS_PROXY": _DEAD_PROXY,
    "ALL_PROXY": _DEAD_PROXY,
    "http_proxy": _DEAD_PROXY,
    "https_proxy": _DEAD_PROXY,
    "all_proxy": _DEAD_PROXY,
    "NO_PROXY": "",
    "no_proxy": "",
    "PIP_NO_INDEX": "1",
    "UV_OFFLINE": "1",
    "npm_config_offline": "true",
    "HF_HUB_OFFLINE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class CheckStatus(StrEnum):
    SUCCEEDED = "succeeded"  # exit code 0
    FAILED = "failed"  # a non-zero exit code
    TIMEOUT = "timeout"  # killed after the timeout
    NOT_STARTED = "not_started"  # the command could not be started


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    exit_code: int | None
    duration_s: float
    output_tail: str  # redacted; the last TAIL_CHARS characters
    isolation: str = ISOLATION


def check_env(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a check receives: an allowlist plus the offline settings. No secrets."""
    source = os.environ if parent is None else parent
    env = {k: v for k, v in source.items() if k in _ENV_KEEP}
    env.update(_OFFLINE_ENV)
    return env


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(  # noqa: S603 - fixed argv, a pid we started
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=30,
        )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined,unused-ignore]
    with contextlib.suppress(OSError):
        proc.kill()


def _tail(stream: IO[bytes]) -> str:
    stream.flush()
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - 4 * TAIL_CHARS))
    raw = stream.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    kept = [
        line if len(line) <= MAX_LINE_CHARS else f"[a line of {len(line)} characters is not shown]"
        for line in lines
    ]
    text, _ = redact_secrets(chr(10).join(kept))
    return text[-TAIL_CHARS:]


def _found_in_the_project(word: str, exe: str, cwd: Path) -> bool:
    """A bare command name that resolved to a file in the project folder itself.

    Windows searches the current folder first, and a change may add a text file (`pytest.cmd`)
    there; a declared bare name must come from PATH. A path the person wrote (`./run-tests`) is
    theirs and is allowed.
    """
    if os.sep in word or "/" in word:
        return False
    try:
        return Path(exe).resolve().parent == cwd.resolve()
    except OSError:
        return True


def run_check(
    argv: Sequence[str],
    cwd: Path,
    timeout_s: int,
    *,
    env: Mapping[str, str] | None = None,
) -> CheckResult:
    """Run `argv` (no shell) in `cwd`; never raises for a failing, hanging or missing command."""
    environment = dict(env) if env is not None else check_env()
    exe = shutil.which(argv[0], path=environment.get("PATH"))
    started = time.monotonic()
    if exe is None or _found_in_the_project(argv[0], exe, cwd):
        return CheckResult(CheckStatus.NOT_STARTED, None, 0.0, "")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(  # noqa: S603 - the person-declared argv, no shell
                [exe, *argv[1:]],
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        except OSError:
            return CheckResult(CheckStatus.NOT_STARTED, None, 0.0, "")
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=30)
            return CheckResult(CheckStatus.TIMEOUT, None, time.monotonic() - started, _tail(sink))
        status = CheckStatus.SUCCEEDED if code == 0 else CheckStatus.FAILED
        return CheckResult(status, code, time.monotonic() - started, _tail(sink))
