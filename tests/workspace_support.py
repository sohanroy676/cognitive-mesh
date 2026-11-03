"""Helpers for the workspace tests."""

import shutil
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "workspace"
PROJECTS = (
    "lib_core",
    "web_app",
    "ui_kit",
    "shop_ui",
    "orphan_app",
    "import_only",
    "dup_a",
    "dup_b",
    "needs_twin",
)


def copy_projects(dest: Path, names: tuple[str, ...] = PROJECTS) -> dict[str, Path]:
    """Copy fixture projects so a test can edit them."""
    out: dict[str, Path] = {}
    for name in names:
        target = dest / name
        shutil.copytree(FIXTURE / name, target)
        out[name] = target
    return out
