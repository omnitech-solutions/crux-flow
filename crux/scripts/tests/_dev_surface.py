"""Staged-artifact guard for lock-step tests that read dev-only surfaces.

Several lock-step suites pin phrases across BOTH plugin surfaces (crux/**,
which crosses the sync boundary) AND dev-repo-only surfaces (docs/AGENTS.md,
docs/log.md, .claude/skills/*.md — which never cross, per ADR-0036 §3).
sync.sh runs this whole test package as a release gate against the STAGED
tree, where the dev-only surfaces legitimately do not exist.

Discriminator: `sync_stage.stage()` writes a `.generated` marker at the
staged root; the dev checkout never has one. In a staged (or published)
artifact the dev-only-surface assertions are skipped; in the dev checkout a
missing surface still fails loudly, so the lock-step guarantee keeps full
strength where the surfaces live.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

IS_STAGED_ARTIFACT = (REPO_ROOT / ".generated").exists()


def _resolve_tree() -> str:
    """The tree directory name, resolved rather than hardcoded.

    Lock-step suites pin phrases across the schema file, which lives inside the
    tree. Hardcoding `docs` here meant every one of them broke when the tree
    moved; resolving it once means a future relocation touches this line only.
    """
    import importlib.util as _ilu
    import sys as _sys
    try:
        _p = REPO_ROOT / "crux" / "scripts" / "bionic_config.py"
        _s = _ilu.spec_from_file_location("_bionic_config_t", _p)
        _m = _ilu.module_from_spec(_s); _sys.modules.setdefault("_bionic_config_t", _m)
        _s.loader.exec_module(_m)
        return _m.resolve_tree_name(REPO_ROOT)
    except Exception:  # noqa: BLE001
        return "bionic"


TREE = _resolve_tree()
TREE_AGENTS_MD = REPO_ROOT / TREE / "AGENTS.md"
TREE_LOG_MD = REPO_ROOT / TREE / "log.md"


def require_dev_surface(tc: unittest.TestCase, path: Path, label: str) -> None:
    """No-op when `path` exists; skip in a staged artifact; fail in dev."""
    if path.exists():
        return
    if IS_STAGED_ARTIFACT:
        tc.skipTest(
            f"dev-only surface {label} absent in staged artifact (ADR-0036 boundary)"
        )
    tc.fail(f"dev-only surface missing from dev checkout: {label} (expected at {path})")
