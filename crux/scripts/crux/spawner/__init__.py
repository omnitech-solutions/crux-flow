"""Crux Spawner Module.

Tools for spawning the crux verification substrate into other
repositories. The spawner analyzes the target repo, copies the crux
Python runtime into a `.crux-runtime/` directory (distinct from the
repo-root `.crux` config FILE, which the spawner never touches), and
generates a customized `AGENTS.md` so future agents working in that
repo have project-specific context. It refuses to deploy over a target
path it did not create (pre-rename trees: `mv .crux .crux-runtime`).

Usage:
    from crux.spawner import spawn_crux

    spawn_crux(
        target_repo="/path/to/repo",
        prime_directive="Build feature X",
    )

CLI Usage:
    python -m crux.spawner /path/to/repo --task "Build feature X"
"""

from .crux_spawner import (
    RepoAnalysis,
    RepoAnalyzer,
    Spawner,
    SpawnTargetConflictError,
    spawn_crux,
)

__all__ = [
    "spawn_crux",
    "Spawner",
    "SpawnTargetConflictError",
    "RepoAnalyzer",
    "RepoAnalysis",
]
