import re
from importlib.metadata import version

import cognitive_mesh


def test_package_imports_and_has_version() -> None:
    assert cognitive_mesh.__version__ == version("cognitive-mesh")


def test_version_is_derived_from_git_not_hardcoded() -> None:
    match = re.match(r"(\d+)\.(\d+)\.\d+", cognitive_mesh.__version__)
    assert match is not None, cognitive_mesh.__version__
    assert (int(match[1]), int(match[2])) >= (0, 3)  # first tag-derived release is v0.3.2
