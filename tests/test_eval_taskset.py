"""The task-set file is validated before anything runs (ADR-0043)."""

from pathlib import Path

import pytest

from cognitive_mesh.evaluate.errors import EvalError
from cognitive_mesh.evaluate.taskset import MAX_TASKS, load_taskset

HEAD = '[set]\nname = "demo"\nworkspace = "ws.sqlite3"\nproject = "api"\nroot = "api"\n'


def task(task_id: str = "t1", **over: str) -> str:
    fields = {
        "id": f'"{task_id}"',
        "request": '"change it"',
        "files": '["app.py"]',
        "check": '["python", "-c", "pass"]',
        **over,
    }
    return "[[task]]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items()) + "\n"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tasks.toml"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def test_valid_set_resolves_paths_and_hashes(tmp_path: Path) -> None:
    ts = load_taskset(write(tmp_path, HEAD + task() + task("t2")))
    assert [t.id for t in ts.tasks] == ["t1", "t2"]
    assert ts.root == (tmp_path / "api").resolve()
    assert len(ts.sha256) == 64
    assert ts.tasks[0].max_files == 3 and ts.tasks[0].check_timeout_s == 300


@pytest.mark.parametrize(
    "body",
    [
        HEAD + task() + task(),  # duplicate id
        HEAD + task(check="[]"),  # empty argv
        HEAD + task(check='[""]'),  # empty word
        HEAD + task(files='["/etc/passwd"]'),  # absolute path
        HEAD + task(files='["../outside.py"]'),  # outside the project
        HEAD + task(files='["a\\\\b.py"]'),  # backslash
        HEAD + task(request='""'),  # empty request
        HEAD + task(max_files="99"),  # over the hard cap
        HEAD + task(check_timeout_s="0"),
        HEAD,  # no tasks
        HEAD + task() + "surprise = 1\n",  # unknown key inside a task
        "not toml [",
    ],
)
def test_bad_sets_are_refused(tmp_path: Path, body: str) -> None:
    with pytest.raises(EvalError):
        load_taskset(write(tmp_path, body))


def test_too_many_tasks_refused(tmp_path: Path) -> None:
    body = HEAD + "".join(task(f"t{i}") for i in range(MAX_TASKS + 1))
    with pytest.raises(EvalError):
        load_taskset(write(tmp_path, body))


def test_error_message_never_echoes_request_text(tmp_path: Path) -> None:
    marker = "this-must-not-appear"
    with pytest.raises(EvalError) as caught:
        load_taskset(write(tmp_path, HEAD + task(request=f'"{marker}"', check="[]")))
    assert marker not in str(caught.value)


def test_missing_and_oversized_files(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        load_taskset(tmp_path / "nope.toml")
    big = write(tmp_path, HEAD + "#" * 300_000)
    with pytest.raises(EvalError):
        load_taskset(big)
