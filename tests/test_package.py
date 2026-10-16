import cognitive_mesh


def test_package_imports_and_has_version() -> None:
    assert cognitive_mesh.__version__ == "0.1.0"
