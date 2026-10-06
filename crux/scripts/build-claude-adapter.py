#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""build-claude-adapter.py — the adapter's generator and its audit CLI.

Named `build-*` rather than `generate-*` on the `compile-doctrine.py` precedent:
the `generate-*.py` glob behind `RegeneratorEnrollmentTests` means "a derived
artifact under version control that owes a drift gate", and an adapter is
deliberately ignored and never committed, so it has no drift to gate. Enrolling
it would put a row in the roster that no gate could ever check.

Opt-in, and never part of a normal install. An adapter exists only for a host that
cannot read the canonical `AGENTS.md`, and it works by suppressing that file — so
this refuses to generate one on a supported host unless told to, and reports an
existing one as stale, removable, incomplete or orphan.

  --audit     report only (default)
  --generate  write an adapter for EVERY managed scope, never the root alone

Generating at the root alone would restore the root instructions while silencing
every AGENTS.md beneath it, so `--generate` covers all scopes or none.

Exit codes: 0 clean, 1 findings with JSON on stdout, 2 capability error on stderr.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="generate even though the host reads AGENTS.md already")
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"repo root is not a directory: {root}", file=sys.stderr)
        return 2

    adapters = _load("claude_adapter")
    im = _load("instruction_migration")
    compat = importlib.util.spec_from_file_location(
        "check_claude_compat", _HERE / "check-claude-compat.py")
    cc = importlib.util.module_from_spec(compat)
    sys.modules["check_claude_compat"] = cc
    compat.loader.exec_module(cc)

    try:
        tracked = im.git_tracked_files(root)
    except im.CapabilityError:
        tracked = []

    verdict = cc.evaluate(root, root)
    supported = verdict["supported"]
    # `--generate`'s refusal asks whether the host reads AGENTS.md NOW. The audit
    # asks whether it WOULD with the adapters gone — an adapter suppresses by
    # design, so the live verdict would let it mask its own removability.
    scopes = adapters.managed_scopes(root, tracked)

    payload = {"repo_root": str(root), "host_supported": supported,
               "managed_scopes": scopes}

    # The host reads AGENTS.md when no host-configuration cause stands. A missing
    # AGENTS.md makes the verdict unsupported without being such a cause, and an
    # adapter cannot repair it, so it never unlocks `--generate`. `adapter_applies`
    # is false both for "no host cause" and for managed-only; the mode separates them.
    host_reads_agents = supported or (
        not verdict.get("adapter_applies", False)
        and verdict.get("derived_from", {}).get("mode") != "managed-only")

    if args.generate:
        if host_reads_agents and not args.force:
            payload["generated"] = []
            if supported:
                payload["refused"] = (
                    "this host already reads AGENTS.md; an adapter here would only "
                    "shadow it. Pass --force if you are generating for a different host.")
            else:
                # Unsupported with no host cause: the only remaining cause is the
                # missing canonical file, which an adapter derives from and cannot create.
                payload["refused"] = (
                    "no root AGENTS.md exists, so the host loads no instruction file "
                    "from this tree; an adapter derives from AGENTS.md and cannot "
                    "replace it. Create a root AGENTS.md first.")
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1
        # Every refusal is reported, and one refusal never stops the others:
        # a scope this writer must not touch is not a reason to leave the rest
        # of the tree half-adapted.
        deny = set(im.load_denylist(root))
        written, refused = [], []
        for scope in scopes:
            try:
                written.append(
                    adapters.generate(root, scope, deny=deny)
                    .relative_to(root).as_posix())
            except adapters.RefusedWrite as exc:
                refused.append({"scope": scope, "reason": str(exc)})
        payload["generated"] = written
        payload["refused"] = refused
        payload["reminder"] = (
            "add one root-anchored ignore entry per adapter; these are never "
            "committed, and a stale one becomes the effective instruction source")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if refused else 0

    audit = adapters.audit(root, tracked,
                           host_supported=verdict.get("adapters", {})
                           .get("basis_supported_without_adapters", supported))
    payload["adapters"] = [
        {"scope": a.scope, "path": a.path, "source": a.source,
         "status": a.status, "detail": a.detail} for a in audit.adapters]
    payload["incomplete_scopes"] = audit.incomplete
    payload["findings"] = audit.findings
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if audit.clean() else 1


if __name__ == "__main__":
    raise SystemExit(main())
