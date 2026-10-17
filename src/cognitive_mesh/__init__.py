"""Cognitive Mesh: application-aware, evidence-aware AI agent system."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cognitive-mesh")
except PackageNotFoundError:  # source tree that was never installed
    __version__ = "0+unknown"
