"""Importing the light memory modules must not load chromadb (ADR-0015)."""

import subprocess
import sys

import pytest


def _loaded_after(statement: str) -> dict[str, bool]:
    code = (
        f"{statement}\nimport sys\n"
        "print(','.join(m for m in ('chromadb', 'sentence_transformers') if m in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    loaded = set(out.split(",")) if out else set()
    return {m: m in loaded for m in ("chromadb", "sentence_transformers")}


def test_documents_import_is_light() -> None:
    assert _loaded_after("import cognitive_mesh.memory.documents") == {
        "chromadb": False,
        "sentence_transformers": False,
    }


def test_experience_import_is_light() -> None:
    statement = "import cognitive_mesh.experience, cognitive_mesh.experience.verify"
    assert _loaded_after(statement) == {"chromadb": False, "sentence_transformers": False}


def test_memory_index_is_still_importable_from_the_package() -> None:
    from cognitive_mesh.memory import MemoryIndex
    from cognitive_mesh.memory.index import MemoryIndex as Direct

    assert MemoryIndex is Direct


def test_unknown_memory_attribute_raises() -> None:
    import cognitive_mesh.memory as memory

    with pytest.raises(AttributeError, match="nope"):
        _ = memory.nope
