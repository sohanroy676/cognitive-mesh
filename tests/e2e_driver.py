"""One CLI session in its own process, with the fake embedder (used by test_e2e_sessions).

Run as `python e2e_driver.py ARGS...`. The only difference from `cognitive-mesh ARGS...` is that the
embedding model is the deterministic fake, so no model is downloaded and the index built in one
session can be searched by another.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cognitive_mesh import cli  # noqa: E402
from memory_support import FakeEmbedder  # noqa: E402

setattr(cli, "_make_embedder", lambda: FakeEmbedder())  # noqa: B010

raise SystemExit(cli.main(sys.argv[1:]))
