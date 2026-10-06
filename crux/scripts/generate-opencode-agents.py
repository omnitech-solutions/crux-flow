#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Regenerate opencode/agents/ from crux/agents/ (the third regenerative output).

Per ADR-0043: crux/agents/*.md (Claude Code format) is the single source of
truth; opencode/agents/*.md is a committed, regenerated projection in OpenCode
agent format. Hand-edits to opencode/agents/ are blown away on every run.

The projection logic lives in the sibling `opencode_agents.py` module, shared
with `install-opencode-agents`'s installer exactly as `codex_agents.py` is
shared with `install-codex-agents`. This file is the dev-repo driver: it pins
the source/output directories and the CLI contract, nothing more. The locked
transformation rules are documented on that module.

Exit-code contract (matches validate-catalog.py / extract-code-docs.py):
  0  clean (no drift on --dry-run; regeneration succeeded otherwise)
  1  drift (--dry-run only): valid JSON {added,changed,removed} on stdout
  2  crash / spec violation (asymmetric Edit/Write, unmapped tool, an agent
     with no catalog roster entry, a roster that has drifted from the agent
     files, malformed source, a managed output leaf that is a symlink, an
     output path that is not a directory): message on stderr, nothing on
     stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from opencode_agents import (  # noqa: E402,F401
    MODE_DEFAULT,
    MODE_MAP,
    managed_filenames,
    ACTION_UNIVERSE,
    TOOL_MAP,
    SpecViolation,
    diff,
    parse_source,
    transform,
    write,
)
from opencode_agents import generate as _project  # noqa: E402

import authoring_scope as _scope  # noqa: E402

# These name the checkout this script SHIPS IN, which is the right answer only when
# that checkout is also the one under inspection. `main` re-points them at the
# resolved `--repo-root` before using them; see the note there.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_DIR = REPO_ROOT / "crux" / "agents"
OUTPUT_DIR = REPO_ROOT / "opencode" / "agents"

# Captured at import so `main` can tell an untouched default from a test's retarget.
_IMPORT_SOURCE_DIR = SOURCE_DIR
_IMPORT_OUTPUT_DIR = OUTPUT_DIR


def crash(msg: str) -> NoReturn:
    print(f"generate-opencode-agents: {msg}", file=sys.stderr)
    sys.exit(2)


def generate() -> dict[str, str]:
    """Return {filename: generated_content} for every source agent.

    Reads the module-level SOURCE_DIR at CALL time (not import time) so a
    caller can retarget it; the test suite relies on exactly that.
    """
    return _project(SOURCE_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report drift as JSON; write nothing; exit 1 on drift")
    parser.add_argument("--repo-root", default=None,
                        help="repo root to inspect (default: the working directory)")
    args = parser.parse_args()

    root = _scope.resolve_repo_root(args.repo_root)

    # SURFACE-ABSENT LANE. `opencode/agents/` is the plugin's own projection of its own
    # `crux/agents/`; a consuming project owns neither side. Run from one, this used to
    # read the INSTALLED plugin's agents and compare them against an `opencode/agents/`
    # inside the plugin cache -- ten phantom additions reported as that project's drift,
    # whose named remedy would have written into the reader's plugin cache.
    if not _scope.is_authoring_checkout(root, __file__):
        return _scope.print_surface_absent()

    # Follow the root under inspection, not the checkout this file happens to live in.
    # LOCALS, never a rebind of the module globals: an earlier version assigned them,
    # so one call with a foreign --repo-root left every later call in the same process
    # pointed at that foreign tree -- the "still holds its import-time value" guard is
    # false forever after the first assignment. Read fresh from the globals each call,
    # so a test that retargets either one keeps its target and nothing leaks between
    # calls.
    source_dir = root / "crux" / "agents" if SOURCE_DIR == _IMPORT_SOURCE_DIR else SOURCE_DIR
    output_dir = root / "opencode" / "agents" if OUTPUT_DIR == _IMPORT_OUTPUT_DIR else OUTPUT_DIR

    # Every filesystem step goes through the shared `opencode_agents` diff/write,
    # exactly as generate-codex-agents.py goes through `codex_agents`. This driver
    # used to keep its own `mkdir` + `write_text` loop, which meant it had neither
    # the leaf-symlink refusal nor the non-directory guard those functions carry:
    # replacing a managed leaf (say `opencode/agents/architect.md`) with a symlink
    # made this regenerator clobber the link's target and report success. One
    # implementation, one set of guards, both callers.
    #
    # Consequence of the shared path: diff/write are scoped to the managed set,
    # so a stray `*.md` here — a role dropped from BOTH crux/agents/ and the catalog roster
    # — is no longer reported or deleted. That scoping is required in a target
    # repo, where an unmanaged file belongs to the user. In this repo the whole
    # directory is generated, so the stray case is netted by a test instead
    # (test_generate_opencode_agents.py::CommittedOutputRosterTests).
    #
    # One try/except covers generate() AND diff()/write(): a SpecViolation from
    # any of them is the same class of failure and owes the caller exit 2 with a
    # message on stderr, never a traceback.
    try:
        generated = _project(source_dir)
        added, changed, removed = diff(output_dir, generated)
        if args.dry_run:
            print(json.dumps(
                {"added": added, "changed": changed, "removed": removed},
                indent=2))
            return 1 if (added or changed or removed) else 0
        written, removed = write(output_dir, generated)
    except SpecViolation as exc:
        crash(str(exc))

    print(json.dumps({"written": written, "removed": removed}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
