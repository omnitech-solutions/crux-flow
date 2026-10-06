#!/usr/bin/env python3
"""Whose tree is this regenerator looking at?

WHY THIS EXISTS. A regenerator ships inside the plugin and runs inside a project.
Those are two different directories, and six regenerators used to conflate them by
deriving their target root from ``__file__``. In the authoring checkout that lands on
the repo root and looks correct. In an installed plugin it lands on the plugin cache,
so a drift gate run from a consuming project inspected the plugin's own copy of itself:
``generate-runtime-compat.py`` reported 56 targets clean without reading one file the
project owns, ``generate-opencode-agents.py`` reported ten phantom additions and named a
remedy that would have written into the reader's plugin cache, and three more crashed on
paths inside a checkout that was never there.

Two rules close it, and this module is the one implementation of both.

* The target root comes from ``--repo-root``, defaulting to the current working
  directory. A script's own location is the PLUGIN root and is never the PROJECT root.
* A regenerator whose output belongs to the plugin's own source checkout reports the
  SURFACE-ABSENT lane when it is not running in one: exit 0, ``surface_absent``, no
  write. Exiting nonzero would file an inapplicable check as a failure; exiting 0 clean
  would file it as verified. Neither is true, and the second is the worse lie.

The lane has TWO triggers, and this module answers only the first. ``is_authoring_checkout``
asks whether plugin source is present, which settles the consuming-project case and
nothing else. A public clone and the staged release artifact both carry ``crux/`` and so
pass it, while carrying neither the repo-root ``AGENTS.md``, the documentation tree, nor
the ``opencode/`` directory. The three gates whose canonical input lives outside ``crux/``
therefore take the lane on their own second test -- their input file is absent -- rather
than on this probe. Do not read a passing probe as "this is the authoring repo"; read it
as "plugin source is here".

``generate-rules-catalog.py`` carried the first copy of this lane. It now calls here
instead, so the six scripts cannot drift apart on what the authoring checkout means.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "is_authoring_checkout",
    "resolve_repo_root",
    "surface_absent_payload",
    "print_surface_absent",
    "NOT_AUTHORING_REASON",
]

NOT_AUTHORING_REASON = "not the plugin's authoring checkout; nothing to project"


def resolve_repo_root(explicit: str | Path | None) -> Path:
    """The project root: ``--repo-root`` when given, otherwise the working directory.

    Never ``Path(__file__).parents[2]``. That expression answers "where do I live",
    and the caller is asking "what am I looking at".
    """
    return Path(explicit).resolve() if explicit else Path.cwd().resolve()


def is_authoring_checkout(repo_root: Path, script_file: str | Path) -> bool:
    """True when ``repo_root`` holds the plugin source this script belongs to.

    The probe is ``<repo_root>/crux/scripts/<this script's filename>``. A consuming
    project does not carry one, because its plugin lives in the plugin cache. A public
    clone and the staged release artifact both do -- so this probe passes there, and a
    gate needing a surface those trees do not ship must test for that surface itself.
    """
    return (Path(repo_root) / "crux" / "scripts" / Path(script_file).name).is_file()


def surface_absent_payload(reason: str = NOT_AUTHORING_REASON, **extra) -> dict:
    """The payload every out-of-scope regenerator prints. ``check-drift`` reads
    ``surface_absent`` to file the row N/A, and ``reason`` to say why it does not apply."""
    payload = {"surface_absent": True, "drift": False, "reason": reason}
    payload.update(extra)
    return payload


def print_surface_absent(reason: str = NOT_AUTHORING_REASON, **extra) -> int:
    """Print the payload and return the exit code (0). Out of scope is not a failure."""
    print(json.dumps(surface_absent_payload(reason, **extra), sort_keys=True))
    return 0
