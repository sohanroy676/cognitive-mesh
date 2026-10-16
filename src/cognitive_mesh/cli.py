"""Minimal command-line entry point."""

import sys
from collections.abc import Sequence

from cognitive_mesh import __version__
from cognitive_mesh.config import ConfigError, load_settings

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration and report status. Makes no provider network calls."""
    del argv  # no arguments yet
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"cognitive-mesh {__version__}: config OK (provider={settings.provider})")
    return EXIT_OK
