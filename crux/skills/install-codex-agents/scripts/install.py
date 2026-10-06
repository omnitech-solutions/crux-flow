#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install Crux-generated Codex agents personally or into an explicit project."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from codex_agents import SpecViolation, diff, generate, pin_directory, write  # noqa: E402


def _is_contained(candidate: Path, root: Path) -> bool:
    try:
        return candidate == root or candidate.is_relative_to(root)
    except AttributeError:  # pragma: no cover - Python >= 3.11
        try:
            return os.path.commonpath([str(root), str(candidate)]) == str(root)
        except ValueError:
            return False


def _containment_error(root: Path, output_dir: Path, filenames: list[str]) -> str | None:
    """Reject a target or managed leaf that resolves outside its selected scope."""
    try:
        resolved_output = output_dir.resolve()
    except OSError as exc:
        return f"cannot resolve agent target {output_dir}: {exc}"
    if not _is_contained(resolved_output, root):
        return f"refusing to write: {output_dir} resolves outside selected root {root}"
    for name in filenames:
        try:
            resolved_file = (output_dir / name).resolve()
        except OSError as exc:
            return f"cannot resolve managed agent {name}: {exc}"
        if not _is_contained(resolved_file, root):
            return f"refusing to write: {name} resolves outside selected root {root}"
    return None


def _target(args: argparse.Namespace) -> tuple[str, Path, Path]:
    if args.repo_root is not None:
        repo = args.repo_root.resolve()
        if not repo.is_dir():
            raise SpecViolation(f"repo root is not a directory: {repo}")
        return "project", repo / ".codex" / "agents", repo
    codex_home = (args.codex_home if args.codex_home is not None else Path.home() / ".codex").resolve()
    return "personal", codex_home / "agents", codex_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument("--repo-root", type=Path, help="explicit project installation root")
    targets.add_argument("--codex-home", type=Path, help="personal Codex home (defaults to ~/.codex)")
    parser.add_argument("--project-context", type=Path, help="repository to inspect for same-name agent shadows")
    parser.add_argument("--check", action="store_true", help="report static health and managed drift without writing")
    parser.add_argument("--force", action="store_true", help="replace changed or stale Crux-managed agent files")
    args = parser.parse_args(argv)
    try:
        scope, output_dir, root = _target(args)
        containment = _containment_error(root, output_dir, [])
        if containment:
            raise SpecViolation(containment)
        # Resolve every source and resource before reading target state. This
        # preserves the fail-before-write contract and makes a damaged plugin
        # report its source/catalog fault even when its manifest is absent.
        generated = generate(PLUGIN_ROOT / "agents", skill_root=PLUGIN_ROOT / "skills")
        from codex_agent_health import exit_status, inspect_install  # noqa: E402

        if args.check:
            report = inspect_install(
                target=output_dir, plugin_root=PLUGIN_ROOT, scope=scope, project_context=args.project_context
            )
            print(json.dumps(report, indent=2))
            return exit_status(report)
        # Pin the resolved, contained target before drift validation.  The
        # descriptor flows into `write`, so a later output-directory or parent
        # substitution cannot make the --force decision inspect one directory
        # and replacement mutate another.
        with pin_directory(output_dir, containment_root=root) as target:
            added, changed, removed = diff(target, generated)
            if (changed or removed) and not args.force:
                print(json.dumps({
                    "error": "generated Crux agents differ; review and rerun with --force",
                    "added": added, "changed": changed, "removed": removed,
                }, indent=2))
                return 1
            written, removed = write(target, generated)
            # Inspect through the same directory descriptor that `write` used.
            # A visible-path substitution after replacement cannot make a
            # successful result describe an attacker-selected directory.
            report = inspect_install(
                target=target, plugin_root=PLUGIN_ROOT, scope=scope, project_context=args.project_context
            )
            report.update({"written": written, "removed": removed})
    except SpecViolation as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
