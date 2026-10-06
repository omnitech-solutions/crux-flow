#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""fetch.py — materialize the pinned arch stack-pack corpus into `.cache/`.

Reads `corpus.yml` beside this file and shallow-clones every entry at its
pinned commit SHA into `.cache/<name>/`, which is gitignored. The corpus is
fetched, never vendored: no submodules, no third-party bytes in the tree.

Usage (always through uv, per docs/AGENTS.md §10.A):
  uv run python3 crux/scripts/tests/arch-corpus/fetch.py
  uv run python3 crux/scripts/tests/arch-corpus/fetch.py --only rubygems-org
  uv run python3 crux/scripts/tests/arch-corpus/fetch.py --verify     # no network
  uv run python3 crux/scripts/tests/arch-corpus/fetch.py --list

Idempotent: an entry whose cached checkout is already at the pinned SHA is
left untouched and reported `ok`. `--force` re-clones it regardless.

Fail-closed, in three places:
  * the manifest is validated before any network call — a name outside
    `[a-z0-9-]+`, a URL that is not `https://github.com/<owner>/<repo>`, or a
    SHA that is not 40 hex characters aborts the whole run;
  * a fetch that cannot reach the pinned SHA leaves nothing behind (the
    partial checkout is removed) and the entry is reported `failed`;
  * the checked-out `HEAD` is compared to the pin AFTER checkout, so a server
    that resolved the ref to something else is caught rather than measured.

Exit codes (the house convention):
  0  every requested entry is present at its pinned SHA
  1  at least one entry failed (findings; the JSON report is on stdout)
  2  environment or usage error, no verdict (git missing, manifest unreadable)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# `_git` passes an explicit minimal environment rather than inheriting one (no
# credential helper, no proxy from a shell the caller forgot about) while still
# finding git and the user's ssh config.
#
# HOME is forwarded, so git would otherwise read `~/.gitconfig`. That file can
# carry `url.<base>.insteadOf`, which rewrites a clone URL AFTER `URL_RE` has
# already allowed it — the allowlist would pass on the manifest's github.com URL
# and git would fetch somewhere else. `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM`
# point git's two config layers at /dev/null so the allowlisted URL is the URL
# that is fetched.
_PATH = os.environ.get("PATH", "/usr/bin:/bin")
_HOME = os.environ.get("HOME", "")
_NULL_CONFIG = os.devnull

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "corpus.yml"
CACHE = HERE / ".cache"

NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
URL_RE = re.compile(r"\Ahttps://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

REQUIRED_KEYS = ("name", "pack", "url", "sha", "license", "why", "expect")

# Corpus repositories must carry a permissive license. Their derived facts —
# expectation records and goldens — are committed under `crux/**`, which the
# public artifact ships, so a copyleft license (GPL, LGPL, AGPL, SSPL) or an
# unknown one disqualifies a repository outright: it is refused here, before
# anything is fetched, and never deferred as a later release decision. The
# value is the SPDX id GitHub reports; check it from repository metadata before
# adding an entry. Widening this set is an owner decision.
PERMISSIVE_LICENSES = frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC"})

# A `source: self` entry derives THIS repository rather than a fetched clone:
# it carries no `url`/`sha` (there is nothing to shallow-clone) and instead
# pins `arch_stack:` — the pack the derive is forced to resolve, since this
# checkout's own `.bionic.yml` pins `arch_stack: crux`. `ARCH_STACK_RE` mirrors
# `bionic_config._validate_arch_stack`'s pattern; duplicated rather than
# imported for the same standalone-script reason `STUB_REASONS` is duplicated
# from `core.StubReason` above.
SELF_REQUIRED_KEYS = ("name", "pack", "license", "why", "expect", "arch_stack")
ARCH_STACK_RE = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")

# ADR-0096 clause 8: every entry carries an expectation record, and it is the
# acceptance gate. Validated HERE rather than only in the acceptance suite so a
# malformed record is an unusable manifest — the same class of fault as a bad
# SHA — instead of a test that skips or reads `None` and passes.
CONCERNS = ("data-model", "api-surface", "module-graph", "decision-index")

# ADR-0129 clause 9(a): a Swift entry's expectation record also names its
# facts file and SHA-256 in `corpus.yml`. `path` stays inside the
# `expectations/` directory (no escape, no absolute path) and is a plain
# lowercase-kebab `.yml` name; `sha256` is a full 64-character lowercase hex
# digest so a truncated or re-cased value is refused rather than silently
# under-checked.
FACTS_PATH_RE = re.compile(r"\Aexpectations/[a-z0-9-]+\.yml\Z")
FACTS_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
FACTS_KEYS = frozenset({"path", "sha256"})

# The closed reason set (clause 3). Duplicated from `core.StubReason` rather
# than imported: this script runs standalone under `uv run --no-project` before
# anything of `crux.arch` is importable, and a validator that cannot run without
# the package it validates records for is a validator that stops running.
# `test_arch_pack_acceptance.py` checks the record against the enum itself, so a
# member added there and not here is caught by the suite.
STUB_REASONS = frozenset({
    "precondition_missing", "unsupported_stack", "ambiguous_stack",
    "ambiguous_package", "parse_failed", "no_entities",
})


class ManifestError(RuntimeError):
    """The manifest is unusable — an environment/usage fault, not a finding."""


def _validate_expect(expect: object, where: str) -> list[str]:
    """Shape-check one entry's `expect` block. Returns every problem it finds.

    Shape only. Whether the record is TRUE of the repository is what the
    acceptance suite measures against a real derive; whether it is well-formed
    is checkable with no clone, no network and no extractor, so it is checked
    here.
    """
    if not isinstance(expect, dict):
        return [f"{where}: `expect` is not a mapping"]

    problems: list[str] = []
    if not isinstance(expect.get("pack"), str) or not expect["pack"]:
        problems.append(f"{where}: `expect.pack` must be a non-empty string")
    packages = expect.get("packages")
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        problems.append(f"{where}: `expect.packages` must be a list of strings")

    concerns = expect.get("concerns")
    if not isinstance(concerns, dict):
        return problems + [f"{where}: `expect.concerns` must be a mapping"]
    for extra in sorted(set(concerns) - set(CONCERNS)):
        problems.append(f"{where}: unknown concern {extra!r}")

    for concern in CONCERNS:
        at = f"{where}.concerns.{concern}"
        rec = concerns.get(concern)
        if not isinstance(rec, dict):
            problems.append(f"{at}: missing")
            continue
        has_pop, has_stub = "populated" in rec, "stubbed" in rec
        if has_pop == has_stub:
            problems.append(f"{at}: exactly one of `populated` / `stubbed` is required")
            continue
        if has_pop:
            floor = (rec["populated"] or {}).get("min_entities")
            if not isinstance(floor, int) or isinstance(floor, bool) or floor < 1:
                problems.append(f"{at}: `populated.min_entities` must be an int >= 1")
        else:
            reason = (rec["stubbed"] or {}).get("reason")
            if reason not in STUB_REASONS:
                problems.append(f"{at}: `stubbed.reason` {reason!r} is outside the closed "
                                f"set {sorted(STUB_REASONS)}")
            because = rec.get("because")
            if not isinstance(because, str) or not because.strip():
                problems.append(f"{at}: a `stubbed` expectation requires a `because:` "
                                "(ADR-0096 clause 8 postcondition (b))")

    # `facts` is absent for the five pre-Swift packs and required for a Swift
    # entry (ADR-0129 clause 9(a)): without it the digest test and the matcher
    # would never see that entry.
    if "facts" not in expect and expect.get("pack") == "swift":
        problems.append(f"{where}.expect: a `pack: swift` entry requires a `facts` block "
                        "naming its facts file and SHA-256 (ADR-0129 clause 9(a))")
    if "facts" in expect:
        facts = expect["facts"]
        at = f"{where}.expect.facts"
        if not isinstance(facts, dict):
            problems.append(f"{at}: must be a mapping")
        else:
            extra = sorted(set(facts) - FACTS_KEYS)
            if extra:
                problems.append(f"{at}: unknown key(s) {', '.join(extra)}")
            missing = sorted(FACTS_KEYS - set(facts))
            if missing:
                problems.append(f"{at}: missing key(s) {', '.join(missing)}")
            path = facts.get("path")
            if "path" in facts and (not isinstance(path, str) or not FACTS_PATH_RE.match(path)):
                problems.append(f"{at}.path: {path!r} must match "
                                "^expectations/[a-z0-9-]+\\.yml$")
            sha256 = facts.get("sha256")
            if "sha256" in facts and (not isinstance(sha256, str)
                                       or not FACTS_SHA256_RE.match(sha256)):
                problems.append(f"{at}.sha256: {sha256!r} must be 64 lowercase hex characters")
    return problems


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """Parse and validate `corpus.yml`. Raises ManifestError on anything odd.

    Validation runs to completion and reports every problem at once, because a
    manifest is hand-edited and one-error-at-a-time is a bad loop.
    """
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"corpus manifest not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"corpus manifest is not valid YAML: {exc}") from exc

    if not isinstance(doc, dict) or not isinstance(doc.get("repos"), list):
        raise ManifestError(f"{path}: expected a mapping with a `repos` list")

    problems: list[str] = []
    seen: set[str] = set()
    repos: list[dict] = []
    for i, entry in enumerate(doc["repos"]):
        where = f"repos[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: not a mapping")
            continue
        is_self = entry.get("source") == "self"
        required = SELF_REQUIRED_KEYS if is_self else REQUIRED_KEYS
        missing = [k for k in required if not entry.get(k)]
        if missing:
            problems.append(f"{where}: missing key(s) {', '.join(missing)}")
            continue
        if is_self:
            carried = [k for k in ("url", "sha") if entry.get(k)]
            if carried:
                problems.append(
                    f"{where}: source: self must not carry {', '.join(carried)} "
                    "(nothing is cloned for a self-hosted entry)")
        name = entry["name"]
        if entry["license"] not in PERMISSIVE_LICENSES:
            problems.append(
                f"{where}: license {entry['license']!r} is not permissive; a corpus "
                f"repository must carry one of {', '.join(sorted(PERMISSIVE_LICENSES))}")
        if not NAME_RE.match(str(name)):
            problems.append(f"{where}: name {name!r} is not [a-z0-9-]+")
        if name in seen:
            problems.append(f"{where}: duplicate name {name!r}")
        seen.add(name)
        if is_self:
            arch_stack = entry.get("arch_stack")
            if not ARCH_STACK_RE.match(str(arch_stack)):
                problems.append(
                    f"{where}: arch_stack {arch_stack!r} must match "
                    "^[a-z][a-z0-9_-]{0,31}$")
        else:
            url, sha = entry["url"], entry["sha"]
            if not URL_RE.match(str(url)):
                problems.append(f"{where}: url {url!r} is not https://github.com/<owner>/<repo>")
            if not SHA_RE.match(str(sha)):
                problems.append(f"{where}: sha {sha!r} is not a full 40-hex commit SHA")
        problems.extend(_validate_expect(entry.get("expect"), f"{where}[{name}].expect"))
        repos.append(entry)

    if problems:
        raise ManifestError(f"{path}:\n  " + "\n  ".join(problems))
    if not repos:
        raise ManifestError(f"{path}: the corpus is empty")
    return repos


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env={
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": _PATH,
            "HOME": _HOME,
            "GIT_CONFIG_GLOBAL": _NULL_CONFIG,
            "GIT_CONFIG_SYSTEM": _NULL_CONFIG,
        },
    )


def head_sha(repo: Path) -> str | None:
    """The checked-out commit, or None when `repo` is not a git checkout."""
    if not (repo / ".git").exists():
        return None
    proc = _git("rev-parse", "HEAD", cwd=repo)
    return proc.stdout.strip() if proc.returncode == 0 else None


def clone_at(url: str, sha: str, dest: Path) -> tuple[bool, str]:
    """Shallow-clone `url` at exactly `sha` into `dest`. Returns (ok, detail).

    `git fetch --depth 1 origin <sha>` is the one-round-trip path and works on
    GitHub (which serves reachable SHAs). The fallback deepens the default
    branch when a host refuses a bare-SHA want. Either way the checkout is
    verified against the pin by the caller.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    init = _git("init", "--quiet", cwd=dest)
    if init.returncode != 0:
        return False, f"git init failed: {init.stderr.strip()}"
    _git("remote", "add", "origin", url, cwd=dest)

    fetch = _git("fetch", "--depth", "1", "--quiet", "origin", sha, cwd=dest)
    if fetch.returncode != 0:
        deep = _git("fetch", "--depth", "250", "--quiet", "origin", cwd=dest)
        if deep.returncode != 0:
            return False, f"fetch failed: {fetch.stderr.strip() or deep.stderr.strip()}"

    checkout = _git("checkout", "--quiet", "--detach", sha, cwd=dest)
    if checkout.returncode != 0:
        return False, f"checkout of {sha[:12]} failed: {checkout.stderr.strip()}"
    return True, "cloned"


def fetch_one(entry: dict, *, force: bool, verify_only: bool) -> dict:
    """Bring one entry to its pinned SHA. Returns a result record."""
    name, sha, url = entry["name"], entry["sha"], entry["url"]
    dest = CACHE / name
    record = {"name": name, "pack": entry["pack"], "sha": sha, "path": str(dest)}

    current = head_sha(dest)
    if current == sha and not force:
        return {**record, "status": "ok", "detail": "already at pinned SHA"}
    if verify_only:
        detail = "absent" if current is None else f"at {current[:12]}, expected {sha[:12]}"
        return {**record, "status": "missing", "detail": detail}

    ok, detail = clone_at(url, sha, dest)
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return {**record, "status": "failed", "detail": detail}

    got = head_sha(dest)
    if got != sha:
        # Never leave a checkout that is not what the manifest pinned: a
        # measurement against the wrong tree is worse than no measurement.
        shutil.rmtree(dest, ignore_errors=True)
        return {**record, "status": "failed",
                "detail": f"HEAD is {got} after checkout, expected {sha}"}
    return {**record, "status": "fetched", "detail": detail}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the pinned arch stack-pack corpus.")
    ap.add_argument("--only", action="append", default=None, metavar="NAME",
                    help="fetch just this entry (repeatable)")
    ap.add_argument("--force", action="store_true", help="re-clone even when the SHA matches")
    ap.add_argument("--verify", action="store_true",
                    help="report cache state without touching the network")
    ap.add_argument("--list", action="store_true", help="print the manifest and exit 0")
    args = ap.parse_args(argv)

    if shutil.which("git") is None:
        print("git is not on PATH", file=sys.stderr)
        return 2
    try:
        repos = load_manifest()
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.only:
        wanted = set(args.only)
        unknown = wanted - {r["name"] for r in repos}
        if unknown:
            print(f"--only names no corpus entry: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        repos = [r for r in repos if r["name"] in wanted]

    if args.list:
        print(json.dumps([{k: r.get(k) for k in
                           ("name", "pack", "url", "sha", "license", "source", "arch_stack")}
                          for r in repos], indent=2))
        return 0

    CACHE.mkdir(parents=True, exist_ok=True)
    # `source: self` entries derive this checkout in place; there is nothing to
    # clone, so the fetch loop reports them `skipped` rather than running them
    # through `fetch_one`, whose `dest = CACHE / name` path assumes a clone.
    results = [
        {"name": r["name"], "pack": r["pack"], "sha": None, "path": None,
         "status": "skipped", "detail": "source: self — nothing to clone"}
        if r.get("source") == "self"
        else fetch_one(r, force=args.force, verify_only=args.verify)
        for r in repos
    ]
    bad = [r for r in results if r["status"] in ("failed", "missing")]
    print(json.dumps({"cache": str(CACHE), "results": results,
                      "ok": not bad}, indent=2, sort_keys=True))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
