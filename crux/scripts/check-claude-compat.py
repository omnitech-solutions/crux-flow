#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""check-claude-compat.py — does this host actually load the canonical AGENTS.md?

A version number alone never establishes that, and neither does the absence of an
error. This reports the effective instruction files the host loads, and names the
inputs it derived that from so a reader can audit the verdict rather than trust it.

What it reads, and which surface is the authority for each:

  version floor 2.1.277   the release changelog. The compatibility module's own
                          README states no minimum version.
  distribution exclusion  the release changelog again — Bedrock, Vertex and
                          Foundry were excluded at that release. The README states
                          no exclusion, so the changelog is the only authority.
  the four modes          the module README: `instructionFiles` takes
                          `claude-md`, `claude-md-or-agents-md` (the default),
                          `claude-md-and-agents-md`, and `managed-only`. The
                          legacy key `projectInstructions` maps onto the same four.
  the suppression set     the module README: `CLAUDE.md`, `.claude/CLAUDE.md` and
                          `CLAUDE.local.md`, in any directory from the repository
                          root to the working directory.

Reporting a suppressor and failing on one are two different acts. Every suppressor
under the checkout is reported; only one on the chain from the root to the working
directory, under a mode in which it suppresses, fails the verdict. A suppressor off
that chain changes nothing about what this host loads now.

A checkout in which the host would load no instruction file is unsupported too.
The verdict then carries a reason worded as a missing AGENTS.md and exits 1. That
cause alone never sets `adapter_applies`, because an adapter derives from AGENTS.md
and cannot create it; `adapter_applies` reads the host-configuration causes only.

`managed-only` cannot be repaired by the compatibility adapter, because it drops
private and checked-in project files alike. Its remedy is a configuration change.

Exit codes: 0 supported, 1 unsupported with JSON on stdout, 2 capability error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION_FLOOR = (2, 1, 277)
EXCLUDED_DISTRIBUTIONS = ("bedrock", "vertex", "foundry")

MODES = ("claude-md", "claude-md-or-agents-md", "claude-md-and-agents-md",
         "managed-only")
DEFAULT_MODE = "claude-md-or-agents-md"
LEGACY_MODE_MAP = {
    "none": "managed-only",
    "claude": "claude-md",
    "agents-fallback": "claude-md-or-agents-md",
    "both": "claude-md-and-agents-md",
}
# Modes in which a Claude-named file suppresses AGENTS.md.
SUPPRESSING_MODES = {"claude-md-or-agents-md"}
# Modes that load AGENTS.md at all.
LOADS_AGENTS = {"claude-md-or-agents-md", "claude-md-and-agents-md"}

_HERE = Path(__file__).resolve().parent


def _load_adapter_module():
    spec = importlib.util.spec_from_file_location(
        "claude_adapter", _HERE / "claude_adapter.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("claude_adapter", mod)
    spec.loader.exec_module(mod)
    return mod


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "instruction_migration", _HERE / "instruction_migration.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("instruction_migration", mod)
    spec.loader.exec_module(mod)
    return mod


def detect_version(explicit: str | None = None) -> tuple[str | None, tuple | None]:
    if explicit:
        raw = explicit
    else:
        exe = shutil.which("claude")
        if not exe:
            return None, None
        try:
            raw = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None, None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return raw, None
    return raw, tuple(int(g) for g in m.groups())


def detect_distribution(explicit: str | None = None) -> str:
    """Anthropic API unless the environment names an excluded distribution."""
    if explicit:
        return explicit
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
        return "bedrock"
    if os.environ.get("CLAUDE_CODE_USE_VERTEX"):
        return "vertex"
    if os.environ.get("CLAUDE_CODE_USE_FOUNDRY"):
        return "foundry"
    return "anthropic"


def _settings_files(home: Path | None, repo_root: Path) -> list[Path]:
    """Settings sources in PRECEDENCE order, strongest first.

    Claude Code resolves local > project > user. Reading them user-first and
    returning on the first hit reports a mode the host does not use, whenever a
    project pins the option and the user's home settings also names it.
    """
    home = home or Path.home() / ".claude"
    return [repo_root / ".claude" / "settings.local.json",
            repo_root / ".claude" / "settings.json",
            home / "settings.json"]


def detect_mode(repo_root: Path, home: Path | None = None
                ) -> tuple[str, str, bool]:
    """Return (mode, source, exposed).

    `exposed` is False when no settings file names the option at all, in which
    case the host default applies. Saying "the default applies" is different from
    observing the setting, and the verdict reports which one it had.
    """
    for path in _settings_files(home, repo_root):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cfg = (data.get("pluginConfigs", {}) or {}).get("agents-md@builtin", {})
        opts = (cfg or {}).get("options", {}) or {}
        if "instructionFiles" in opts:
            return str(opts["instructionFiles"]), str(path), True
        legacy = opts.get("projectInstructions") or data.get("projectInstructions")
        if legacy:
            return LEGACY_MODE_MAP.get(str(legacy), str(legacy)), str(path), True
    return DEFAULT_MODE, "host default (no settings file names instructionFiles)", False


def detect_builtin_disabled(repo_root: Path, home: Path | None = None) -> bool:
    """The built-in is on unless a settings file disables that plugin."""
    for path in _settings_files(home, repo_root):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("disabledPlugins", "enabledPlugins"):
            val = data.get(key)
            if isinstance(val, list) and "agents-md@builtin" in val:
                return key == "disabledPlugins"
            if isinstance(val, dict) and "agents-md@builtin" in val:
                enabled = bool(val["agents-md@builtin"])
                return not enabled if key == "enabledPlugins" else enabled
    return False


def evaluate(repo_root: Path, working_dir: Path, *, version=None,
             distribution=None, home=None) -> dict:
    im = _load_migration_module()
    repo_root = Path(repo_root).resolve()
    working_dir = Path(working_dir).resolve()

    raw_version, parsed = detect_version(version)
    dist = detect_distribution(distribution)
    mode, mode_source, exposed = detect_mode(repo_root, home)
    builtin_disabled = detect_builtin_disabled(repo_root, home)

    adapters = _load_adapter_module()
    # S1: the denylist belongs to BOTH callers of this helper. Reading it here
    # keeps the compatibility verdict from calling a withheld path "suppresses
    # until migrated", which is false — it will never migrate.
    deny = set(im.load_denylist(repo_root))
    suppressors = im._scan_suppressors(repo_root, working_dir, deny)
    on_chain = [s for s in suppressors if s.on_chain]
    off_chain = [s for s in suppressors if not s.on_chain]

    reasons: list[str] = []

    if parsed is None:
        reasons.append(
            "claude version could not be determined; the 2.1.277 floor is "
            "unverified (the release changelog is its only authority)")
    elif parsed < VERSION_FLOOR:
        reasons.append(
            f"claude {raw_version} is below the 2.1.277 floor the release "
            "changelog sets for AGENTS.md support")

    if dist in EXCLUDED_DISTRIBUTIONS:
        reasons.append(
            f"the {dist} distribution was excluded from AGENTS.md support at "
            "the 2.1.277 release; the module README states no exclusion, so the "
            "changelog is the authority here")

    if builtin_disabled:
        reasons.append("the agents-md@builtin plugin is disabled, so no AGENTS.md loads")

    if mode not in MODES:
        reasons.append(f"unrecognised instructionFiles mode {mode!r}")
    elif mode not in LOADS_AGENTS:
        if mode == "managed-only":
            reasons.append(
                "mode managed-only drops the project's checked-in and private "
                "files alike; the remedy is a configuration change and never the "
                "compatibility adapter")
        else:
            reasons.append(f"mode {mode} never loads AGENTS.md")
    elif mode in SUPPRESSING_MODES and on_chain:
        named = ", ".join(s.path.as_posix() for s in on_chain)
        reasons.append(
            f"mode {mode} is suppressed by a Claude-named file on the chain from "
            f"the root to {working_dir}: {named}")

    # Interpretation of clause 10 (rule:compatibility-is-measured-not-inferred).
    # The verdict names the effective instruction files the host loads, and "the
    # absence of an error" never establishes that; the postcondition says the
    # verdict reports whether the host loads the managed instruction file. So a
    # host with no configuration cause is still unsupported when the files it
    # would load form an EMPTY list. The predicate is the empty list, not "no
    # AGENTS.md": under claude-md-and-agents-md a loaded CLAUDE.md is a file the
    # host loads, and whether that mode should also demand AGENTS.md is left open
    # in the brief. The cause is worded as a missing canonical file; it is never
    # an unsupported runtime and never recommends the adapter, which derives from
    # AGENTS.md and cannot create it.
    #
    # `host_reasons` are the configuration causes above. Everything that speaks of
    # the adapter (adapter_applies, the removal basis) reads host_reasons only.
    host_reasons = list(reasons)

    # The measured candidate list: the files the host would load if its
    # configuration were fine. Name only files that exist. Asserting a path
    # without stat'ing it is the inferred-verdict shape clause 10 refuses.
    candidates: list[str] = []

    def _add(path: Path) -> None:
        rel = (path.relative_to(repo_root).as_posix()
               if repo_root in path.parents or path.parent == repo_root
               else str(path))
        if rel not in candidates:
            candidates.append(rel)

    def _entries(directory: Path) -> list[str]:
        # The directory's real entry names. A path probe cannot tell `AGENTS.md`
        # from `agents.md` on a filesystem that folds case, and whether a host
        # loads such a variant is unobserved, so spelling is judged from here.
        try:
            return sorted(os.listdir(directory))
        except OSError:
            return []

    probe = working_dir
    while True:
        if "AGENTS.md" in _entries(probe) and (probe / "AGENTS.md").is_file():
            _add(probe / "AGENTS.md")
        if probe == repo_root or repo_root not in probe.parents:
            break
        probe = probe.parent
    if mode == "claude-md-and-agents-md":
        # This mode loads both names, so a CLAUDE.md that exists is also
        # effective. Stat it rather than asserting it. Judge the spelling from
        # the directory's entries, as for AGENTS.md: a variant such as
        # `claude.md` is never counted, because whether a host loads one is
        # unobserved, and a path probe would find it only on a volume that
        # folds case.
        for d in (repo_root, working_dir):
            if "CLAUDE.md" in _entries(d) and (d / "CLAUDE.md").is_file():
                _add(d / "CLAUDE.md")
        # Every CLAUDE.md the suppressor scan places on the chain loads too: one
        # in an intermediate directory, and `<dir>/.claude/CLAUDE.md`, which is
        # that scope's project file. The scan matches names in any letter case,
        # because a variant still fails the verdict as a suppressor; only the
        # exact spelling is counted as a file the host loads.
        for s in on_chain:
            if s.path.name == "CLAUDE.md" and (repo_root / s.path).is_file():
                _add(repo_root / s.path)

    if not candidates:
        reasons.append(
            f"no AGENTS.md on the chain from the root to {working_dir} (and, "
            "under claude-md-and-agents-md, no CLAUDE.md), so the host loads no "
            "canonical instruction file; create an AGENTS.md spelled exactly "
            "so (init-docs creates one only while it initialises a tree in a "
            "root that holds no AGENTS.md or CLAUDE.md in any letter case, and a "
            "CLAUDE.local.md does not block that create)")

    supported = not reasons

    # While a host configuration cause stands, the effective list stays [] as it
    # did before. That is a separate, deferred misreport: the host may still load
    # a file the report omits.
    effective = [] if host_reasons else candidates

    # Clause 11's three reports. They read only; nothing here writes or deletes
    # an adapter, and a Claude-named file without the generated header is a legacy
    # instruction file for the migration to handle rather than an adapter.
    try:
        tracked = im.git_tracked_files(repo_root)
    except im.CapabilityError:
        tracked = []

    # MF#3: `removable` asks "would this host read AGENTS.md if the adapters were
    # gone?", NOT "is it reading AGENTS.md right now". Those differ precisely when
    # an adapter is present, because the adapter suppresses by design — so passing
    # the live verdict made an adapter mask its own removability, and the report
    # could never fire in the default mode the ADR names as supported.
    non_adapter_on_chain = []
    for sup in on_chain:
        full = repo_root / sup.path
        try:
            is_ad = full.is_file() and adapters.is_adapter(
                full.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            is_ad = False
        if not is_ad:
            non_adapter_on_chain.append(sup)
    supported_without_adapters = not [
        r for r in host_reasons
        if "suppressed by a Claude-named file" not in r
    ] and not non_adapter_on_chain
    adapter_audit = adapters.audit(repo_root, tracked,
                                   host_supported=supported_without_adapters)

    return {
        "supported": supported,
        "effective_instruction_files": effective,
        "adapters": {
            "basis_supported_without_adapters": supported_without_adapters,
            "present": [
                {"scope": a.scope, "path": a.path, "source": a.source,
                 "status": a.status, "detail": a.detail}
                for a in adapter_audit.adapters
            ],
            "incomplete_scopes": adapter_audit.incomplete,
            "findings": adapter_audit.findings,
        },
        "reasons": reasons,
        "derived_from": {
            "version": raw_version,
            "version_floor": ".".join(str(n) for n in VERSION_FLOOR),
            "version_authority": "the v2.1.277 release changelog",
            "distribution": dist,
            "distribution_excluded": dist in EXCLUDED_DISTRIBUTIONS,
            "builtin_disabled": builtin_disabled,
            "mode": mode,
            "mode_source": mode_source,
            "mode_setting_exposed": exposed,
            "repo_root": str(repo_root),
            "working_dir": str(working_dir),
        },
        "suppressors": {
            "on_chain": [{"path": s.path.as_posix(), "reason": s.reason,
                          "remedy": s.remedy} for s in on_chain],
            "off_chain": [{"path": s.path.as_posix(), "reason": s.reason,
                           "remedy": s.remedy} for s in off_chain],
        },
        "adapter_applies": bool(host_reasons) and mode != "managed-only",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--working-dir", default=None)
    ap.add_argument("--claude-version", default=None,
                    help="override detection (for tests and CI)")
    ap.add_argument("--distribution", default=None,
                    help="override detection (for tests and CI)")
    ap.add_argument("--claude-home", default=None,
                    help="an alternate personal settings root")
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"repo root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        verdict = evaluate(
            root, Path(args.working_dir).resolve() if args.working_dir else root,
            version=args.claude_version, distribution=args.distribution,
            home=Path(args.claude_home) if args.claude_home else None)
    except Exception as exc:  # noqa: BLE001
        print(f"compatibility check failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
