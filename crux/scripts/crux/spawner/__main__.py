#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
# ]
# ///
"""CLI entry point for the crux Spawner.

Usage:
    uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/spawner/__main__.py" /path/to/repo
    python -m crux.spawner /path/to/repo --task "Build feature X"
        (the -m form needs the PEP 723 deps already installed and scripts/
        on sys.path; prefer the self-contained uv form)
"""

import sys
from pathlib import Path

# Defensive: make the bundled package importable when this file is run as a
# plain script (`uv run …/scripts/crux/spawner/__main__.py`) rather than as
# `python -m crux.spawner`. The package root is `scripts/` (`parents[2]`:
# spawner/ -> crux/ -> scripts/) — the directory holding both the entry
# scripts and the `crux` package (ADR-0035 §2). Survives -P / PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Absolute import (not `from .crux_spawner import main`) so this file also
# works when executed as a plain script, where `__package__` is unset and
# relative imports fail. Under `python -m crux.spawner` both forms resolve to
# the same module.
from crux.spawner.crux_spawner import main

if __name__ == "__main__":
    main()
