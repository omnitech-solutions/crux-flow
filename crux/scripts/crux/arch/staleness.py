"""ADR-0096 clause 7 — the `possibly_stale` annotation, and what it cannot know.

A committed artifact is a file the target project's own toolchain emitted: an
OpenAPI document, a route dump, a Rails schema file. It goes out of date when
the sources it was emitted from move on, and this module reports that by reading
the repository's commit graph. When a source in a concern's declared set was
committed after the artifact the concern consumed, the deriver reports
`possibly_stale` and prints the one command that refreshes it.

**This module is the REPORTED channel and nothing else.** It writes nothing
under `arch/`, changes no recorded verdict, and changes neither the drift gate's
exit status nor the strict gate's. That is not tidiness; it is the reason the
annotation is allowed to read the commit graph at all. A byte-compared tree that
varied with the history would report drift on a fresh clone and clean on the
machine that wrote it, which is exactly the flap clause 2 exists to prevent. For
the same reason nothing here runs on the derive path: `core.py`, `derive.py` and
the packs do not import this module, and a test holds that line.

## The error, in both directions

Commit order is a necessary and not a sufficient condition for an artifact being
out of date, so this advisory is wrong in two ways and the ADR states both.

**It false-positives.** A source edit that would not change the emitted output —
a comment, a rename inside a function body, a test-only change to a file the
generator also reads — is a commit after the artifact, so the advisory fires and
the artifact is in fact current. The only way to know otherwise is to run the
target project's own generator, which is the code execution the unattended
pipeline must not perform.

**It stays silent where the history cannot establish order at all.** Four such
conditions, and this module detects each and reports nothing rather than
guessing:

  * a **shallow** clone, whose truncated graph attributes files to the graft
    rather than to the commit that wrote them;
  * a **squashed** or rebased history, where the artifact and the source that
    followed it now sit in one rewritten commit and no order survives;
  * a **same commit** update, which is the shape a correct refresh produces —
    the artifact and its sources committed together;
  * an uncommitted **working tree** touching the concern's inputs, where the
    bytes on disk are not the bytes the graph describes.

The dirty-tree check is scoped to the concern's own declared inputs and the
artifacts it consumed, not to the whole repository. A repository is dirty almost
all of the time during development, and silencing every concern on any unrelated
edit would make the advisory dead rather than careful.

There is a fifth silence, and it is a declaration rather than a history: a
concern that has not declared WHICH of its globs is the emitted artifact is
never annotated. Without that line the comparison degenerates into asking
whether one source was committed after another source in the same set, which the
ADR index would answer "yes" for every repository whose newest ADR postdates its
oldest. `InputClass.artifact` draws the line, and three shipped pack-concern pairs
declare one: the python pack's api-surface (`openapi.json`) and the ruby pack's
data-model and api-surface (`db/schema.rb`, `arch-inputs/routes.txt`). The two
artifacts crux itself consumes stay undeclared — both are already gated on their
content, so asking the weaker commit-order question of them would produce noise.

Refresh is the user's act, not crux's. The refresh command runs the target's own
framework, which is the code execution the unattended pipeline must not perform,
so crux prints the command and never runs it. A pack that declares no refresh
command gets an advisory that says so rather than one that invents one.

Git is invoked read-only, through one place, with both config layers pointed at
`/dev/null` — mirroring `crux/scripts/tests/arch-corpus/fetch.py::_git`, and for
the same reason: a `~/.gitconfig` can rewrite what a git invocation does, and an
advisory that inherited an operator's config would report on a different
repository than the one it was pointed at.

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

#: The reported channel's only annotation. The vocabulary itself lives in
#: `core.ANNOTATIONS`, which is what the coverage table and the strict gate read;
#: this constant is the value this module appends.
POSSIBLY_STALE = "possibly_stale"

_NULL_CONFIG = "/dev/null"


class Advisory(NamedTuple):
    """One reported staleness finding. Never serialized under `arch/`.

    `source_commit` is the newest commit touching the concern's declared source
    set that the artifact's own commit does not reach — the evidence, so a
    reader can check the claim rather than take it. `command` is what the pack
    declared refreshes the artifact, and is empty when the pack declared none.
    """

    concern: str
    artifact: str
    source_commit: str
    command: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    """The one git invocation site. Read-only, and config-pinned.

    An explicit minimal environment rather than an inherited one. `HOME` is
    forwarded because git looks there for ssh config, and both config layers are
    pointed at `/dev/null` so a `~/.gitconfig` cannot change what this reads.
    `GIT_OPTIONAL_LOCKS=0` keeps a read from taking the index lock, so an
    advisory can never block a concurrent editor.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", str(root)),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_GLOBAL": _NULL_CONFIG,
            "GIT_CONFIG_SYSTEM": _NULL_CONFIG,
        },
    )


def _ok(root: Path, *args: str) -> str | None:
    """Run a read and return its stdout, or None if git could not answer."""
    try:
        proc = _git(root, *args)
    except OSError:                       # no git on this machine: silent, not fatal
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _usable_history(root: Path) -> bool:
    """False when the graph cannot establish order for the whole repository.

    Two whole-repository conditions live here: the directory is not a checkout
    at all, and the checkout is shallow. A shallow graph attributes every file
    to the graft commit, so every comparison it supports is an artifact of the
    clone depth rather than of the project's history.
    """
    if _ok(root, "rev-parse", "--git-dir") is None:
        return False
    if _ok(root, "rev-parse", "--is-shallow-repository") == "true":
        return False
    return not (root / ".git" / "shallow").exists()


def _glob_spec(pattern: str) -> str:
    return f":(glob){pattern}"


def _literal_spec(path: str) -> str:
    return f":(literal){path}"


def _dirty(root: Path, pathspecs: list[str]) -> bool:
    """True when any path in *pathspecs* differs from what the graph describes.

    Conservative on failure: a status this cannot read is treated as dirty, so
    the advisory goes silent rather than reporting against bytes it could not
    confirm.
    """
    out = _ok(root, "status", "--porcelain", "--", *pathspecs)
    return out is None or bool(out)


def _artifact_commit(root: Path, artifact: str) -> str | None:
    """The newest commit touching *artifact*, or None when it has none."""
    out = _ok(root, "log", "-1", "--format=%H", "--", _literal_spec(artifact))
    return out or None


def _newer_source(root: Path, base: str, pathspecs: list[str],
                  excludes: list[str]) -> str | None:
    """The newest commit touching a declared source that *base* does not reach.

    `base..HEAD` is the ancestry question, not a timestamp comparison, and that
    is deliberate: a rebase rewrites dates freely while reachability stays a
    property of the graph as it now stands. It also gives three of the four
    silences for free — a same-commit update leaves the range empty because the
    range excludes `base` itself, and a squashed history collapses `base` onto
    `HEAD` so the range is empty too.

    The artifact patterns and the artifact paths are both excluded, so an
    artifact can never be its own newer source.
    """
    out = _ok(root, "log", "-1", "--format=%H", f"{base}..HEAD", "--",
              *pathspecs, *excludes)
    return out or None


def annotate(root, records: list, *, cfg=None, declared=None) -> list[Advisory]:
    """Attach clause 7's annotation to the REPORTED records. Returns advisories.

    `records` are the reported-channel records `core.reported_coverage` built.
    Each record whose concern is found stale gains `possibly_stale` in its
    `annotations` list, which is a reported-only key — it is never serialized
    into `coverage.json` or into any spine file.

    `declared` is the per-concern `InputClass` map. It is a parameter so a
    caller that already resolved the pack does not resolve it twice, and so a
    test can state the declaration it is testing rather than build a repository
    that happens to produce one. Absent, the pack is resolved from *root*.

    Only a `committed-artifact` concern that DECLARES which of its globs is the
    artifact can be stale. A `parser` concern reads the authored source directly,
    so there is no artifact for a source to be newer than; a `stub` concern
    consumed nothing at all; and a concern that declares no artifact pattern has
    not said which of its inputs was emitted and which was authored, so asking
    the question would compare two sources to each other. That last case is not
    hypothetical — the ADR index declares `committed-artifact` and its glob set
    covers both the index and every ADR, and without the distinction every
    repository whose newest ADR postdates its oldest would report itself stale.

    At most one advisory per concern. A concern whose several artifacts are all
    behind their sources has one problem and one refresh command, and reporting
    it once per artifact would bury that under repetition.
    """
    root = Path(root)
    if not _usable_history(root):
        return []
    if declared is None:
        from . import core                       # local: keeps this off core's import graph
        declared = core.input_classes(core.resolve_stack(root, cfg).pack_name)

    out: list[Advisory] = []
    for rec in records:
        ic = declared.get(rec.get("concern"))
        if ic is None or ic.kind != "committed-artifact" or not ic.globs:
            continue
        if not getattr(ic, "artifact", ()):
            continue
        consumed = [a for a in rec.get("inputs_found", ()) if a and not a.startswith(":")]
        artifacts = [a for a in consumed
                     if any(fnmatch.fnmatch(a, pat) for pat in ic.artifact)]
        if not artifacts:
            continue
        pathspecs = [_glob_spec(g) for g in ic.globs]
        excludes = ([f":(exclude,glob){pat}" for pat in ic.artifact]
                    + [f":(exclude,literal){a}" for a in artifacts])
        if _dirty(root, pathspecs + [_literal_spec(a) for a in artifacts]):
            continue
        for artifact in artifacts:
            base = _artifact_commit(root, artifact)
            if base is None:
                continue
            newer = _newer_source(root, base, pathspecs, excludes)
            if newer is None:
                continue
            out.append(Advisory(rec["concern"], artifact, newer, ic.refresh))
            annotations = rec.setdefault("annotations", [])
            if POSSIBLY_STALE not in annotations:
                annotations.append(POSSIBLY_STALE)
            break
    return out


def advisory_lines(advisories: list) -> list[str]:
    """One human-readable line per advisory, for stderr beside the table.

    Names the artifact, the evidence, and the refresh command — or says the pack
    declares none, which is the honest output rather than an invented command.
    """
    lines = []
    for a in advisories:
        tail = (f"refresh with: {a.command}" if a.command
                else "the pack declares no refresh command for it")
        lines.append(
            f"{POSSIBLY_STALE}: {a.concern}'s `{a.artifact}` predates a change to "
            f"its declared sources (newest at {a.source_commit[:12]}); {tail}"
        )
    return lines
