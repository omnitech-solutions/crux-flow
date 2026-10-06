#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""CLI entry for the ADR-0075 runtime arch introspection executor.

    gate → harness → reentry

The ONLY programmatic entry point is the human-invoked `escalate-arch-runtime`
skill; this CLI is what that skill runs. It is never auto-triggered by `derive`
and never dispatched by the model from chat (ADR-0075 decision 4).

Two mutually exclusive grammars (the harness never auto-detects):
    --app module:attr        FastAPI/Flask instance or zero-arg factory
    --settings module.path   Django DJANGO_SETTINGS_MODULE

The child runs under the TARGET's interpreter — the one that can import the
app's dependencies — via `--python` (default: this process's interpreter). The
parent needs no third-party dependency (the `crux` package heavy chain is only
loaded on the parent path and is absent from this script's PEP 723 block); the
whole executor is stdlib-only, so it runs under a project venv's plain python.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import types
from pathlib import Path


def _load_runtime_modules():
    """Load the parent-side runtime modules by FILE PATH, never via the real
    `crux` package (ADR-0075). An absolute `from crux.arch.runtime import ...`
    would execute `crux/__init__`, whose council → llm_caller chain does
    `import httpx` — a third-party dependency this stdlib-only entry point must
    not require (its PEP 723 block declares `dependencies = []`). This mirrors
    the file-path load `capture.py` and `child.py` already use, extended to a
    synthetic package context because `harness.py` and `reentry.py` use relative
    imports (`from . import capture`, `from .child import ...`).

    A synthetic parent package is registered under a UNIQUE PRIVATE name so it
    never shadows an installed `crux` in a target repo; its `__path__` is the
    trusted runtime dir derived from `__file__`; and each leaf module is
    registered as its submodule with `__package__` set, so the relative imports
    resolve to the already-loaded stdlib-only siblings. This runs in the PARENT
    bootstrap ONLY — it does not change what the confined child imports.
    """
    runtime_dir = Path(__file__).resolve().parent
    pkg_name = "_crux_runtime"

    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(runtime_dir)]      # trusted runtime dir from __file__
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg

    def _load(leaf: str):
        full = f"{pkg_name}.{leaf}"
        # PATH DERIVATION: always from __file__, never a preexisting sys.modules
        # entry that could point elsewhere.
        path = runtime_dir / f"{leaf}.py"
        spec = importlib.util.spec_from_file_location(full, path)
        module = importlib.util.module_from_spec(spec)
        # Register BEFORE exec so a sibling's relative import resolves mid-exec.
        sys.modules[full] = module
        spec.loader.exec_module(module)
        setattr(pkg, leaf, module)
        return module

    # LOAD ORDERING: `capture` and `child` must be in sys.modules before
    # `harness`/`reentry` exec — harness:27 imports capture at top level and
    # harness:152 lazily imports child; reentry:27 imports capture.
    _load("capture")
    _load("child")
    gate_mod = _load("gate")
    harness_mod = _load("harness")
    reentry_mod = _load("reentry")
    return gate_mod, harness_mod, reentry_mod


gate, harness, reentry = _load_runtime_modules()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="escalate-arch-runtime", description=__doc__)
    grammar = p.add_mutually_exclusive_group(required=True)
    grammar.add_argument("--app", help="module:attr — FastAPI/Flask instance or zero-arg factory")
    grammar.add_argument("--settings", help="dotted DJANGO_SETTINGS_MODULE path")
    p.add_argument("--timeout", type=int, default=harness.DEFAULT_TIMEOUT,
                   help=f"wall-clock seconds, clamped {harness.MIN_TIMEOUT}-{harness.MAX_TIMEOUT} "
                        f"(default {harness.DEFAULT_TIMEOUT})")
    p.add_argument("--repo-root", default=".",
                   help="where the target package lives (search root; default cwd)")
    p.add_argument("--docs-dir", default="bionic",
                   help="documentation tree root; the advisory lands in <docs-dir>/inbox/")
    p.add_argument("--python", default=None,
                   help="interpreter to run the confined child under (default: this interpreter)")
    p.add_argument("--no-foldback", action="store_true",
                   help="skip the OpenAPI-shaped fold-back candidate")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Factor (a) + the fail-closed CI/TTY floor. Factor (b) is enforced OUTSIDE
    # this executor (the tool-boundary permission event or the human's shell).
    try:
        gate.check_gate()
    except gate.GateRefusal as e:
        sys.stderr.write(f"refused: {e.reason}\n")
        return 2

    framework = "django" if args.settings else "app"
    target = args.settings or args.app
    result = harness.run(
        app=args.app, settings=args.settings,
        search_root=args.repo_root, timeout=args.timeout, interpreter=args.python,
    )

    if not result.ok:
        sys.stderr.write(f"no-capture: {result.reason}\n")
        if result.child_stderr.strip():
            sys.stderr.write(result.child_stderr)
        return 1

    advisory = reentry.write_advisory(
        result.capture, docs_dir=args.docs_dir, target=target, framework=framework)
    sys.stdout.write(f"advisory written: {advisory}\n")
    if not args.no_foldback:
        fold = reentry.write_foldback_candidate(result.capture, docs_dir=args.docs_dir)
        sys.stdout.write(f"fold-back candidate written: {fold}\n")
    n_routes = len(result.capture.get("routes", []))
    n_models = len(result.capture.get("models", []))
    sys.stdout.write(f"captured {n_routes} route(s), {n_models} model(s) — untrusted advisory, review before filing\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
