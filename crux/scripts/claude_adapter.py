"""claude_adapter.py — the legacy Claude compatibility adapter, and its audit.

An adapter is a `CLAUDE.md` DERIVED from the canonical `AGENTS.md` of the same
scope, for a host that cannot read the canonical name. It is a projection, never a
source: it carries a generated header naming its source and that source's digest,
it is ignored rather than committed, and it is regenerated rather than maintained.

Review found the adapter documented and unimplemented — a reference file asserting
three times that "the audit reports" an adapter as stale, removable or incomplete,
with no code performing any of the three, and no generator behind the word
"regenerable". What shipped was instructions for a human to hand-write a file
carrying a digest nobody computed. That is the second maintained file the decision
forbids, so the three reports and the generator live here.

**The adapter works by suppressing the canonical file.** That is its whole function
and its whole hazard, and it is why the three reports exist:

  stale       its recorded digest no longer matches its source, so the effective
              instructions are an old copy nobody is maintaining
  removable   the host became supported, so the adapter is now shadowing a file
              the host would otherwise read correctly
  incomplete  one managed scope has an adapter and another does not. A root
              adapter suppresses every AGENTS.md beneath it, so a root-only
              adapter restores the root instructions while silencing the tree's
              schema — the larger file, and the one carrying the contract

`managed-only` is repaired by configuration alone; no adapter helps, because that
mode drops private and checked-in project files alike.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ADAPTER_NAME = "CLAUDE.md"
CANONICAL_NAME = "AGENTS.md"

_HEADER_RE = re.compile(
    r"<!--\s*GENERATED compatibility adapter\.\s*"
    r"Source:\s*(?P<source>\S+)\s*\(sha256:(?P<digest>[0-9a-f]{64})\)\.",
    re.S,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def header(source_rel: str, digest: str) -> str:
    return (
        f"<!-- GENERATED compatibility adapter. Source: {source_rel} "
        f"(sha256:{digest}).\n"
        f"     Edits here are discarded. Edit {source_rel} instead. -->\n\n"
    )


def render(canonical_text: str, source_rel: str) -> str:
    """The adapter's bytes: the header, then the canonical content verbatim."""
    return header(source_rel, _sha(canonical_text.encode("utf-8"))) + canonical_text


@dataclass
class AdapterReport:
    scope: str                  # the directory holding the pair, "." for the root
    path: str                   # the adapter's repo-relative path
    source: str                 # the canonical file it derives from
    status: str                 # current | stale | removable | orphan
    detail: str = ""


@dataclass
class AdapterAudit:
    adapters: list[AdapterReport] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def clean(self) -> bool:
        return not self.findings


def managed_scopes(root: Path, tracked: list[str]) -> list[str]:
    """Scopes holding a canonical file, as posix dirs ('.' for the root).

    Prefers the tracked set. Falls back to a filesystem walk when that is empty —
    this audit only reads, and reporting nothing because git was unavailable would
    make the three findings silently unreachable wherever the adapter is most
    likely to be used: an unsupported host, often an export rather than a clone.
    """
    out = []
    for rel in tracked:
        p = Path(rel)
        if p.name == CANONICAL_NAME:
            out.append(p.parent.as_posix() or ".")
    if out:
        return sorted(set(out))

    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if here != root and (here / ".git").exists():
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules"}]
        if CANONICAL_NAME in filenames:
            rel = here.relative_to(root).as_posix()
            out.append(rel or ".")
    return sorted(set(out))


def _adapter_path(root: Path, scope: str) -> Path:
    return root / ADAPTER_NAME if scope == "." else root / scope / ADAPTER_NAME


def _canonical_path(root: Path, scope: str) -> Path:
    return root / CANONICAL_NAME if scope == "." else root / scope / CANONICAL_NAME


def is_adapter(text: str) -> bool:
    return _HEADER_RE.search(text) is not None


def recorded_digest(text: str) -> str | None:
    m = _HEADER_RE.search(text)
    return m.group("digest") if m else None


class RefusedWrite(Exception):
    """A generate that would have destroyed or escaped something."""


def _contained(root: Path, target: Path) -> bool:
    try:
        resolved = target.resolve()
        base = root.resolve()
    except (OSError, RuntimeError):
        return False
    return base in resolved.parents


def generate(root: Path, scope: str, *, deny: set[str] | None = None) -> Path:
    """Write (or refresh) the adapter for one scope. Returns its path.

    **This is the one place in the concern that writes a Claude-named file, so
    every refusal the rest of the concern relies on has to be repeated here.**
    A review of the first version found it a bare `write_text` that would, on the
    documented happy path, silently destroy the user's un-migrated `CLAUDE.md` —
    because in the default mode the only thing making an otherwise-current host
    unsupported IS a legacy `CLAUDE.md`, so "unsupported, therefore generate" aimed
    the writer straight at the file whose content had not been merged yet.

    Four refusals, each fail-closed:

      not-an-adapter  the target exists and carries no generated header. It is a
                      legacy instruction file, or something else entirely; either
                      way it belongs to the migration and not to this writer.
      denylisted      the path is withheld from instruction migration.
      symlink         never followed, on `instruction_migration._classify`'s terms.
      escapes-root    the resolved target lies outside the repository.
    """
    root = Path(root)
    canonical = _canonical_path(root, scope)
    if canonical.is_symlink():
        raise RefusedWrite(f"{canonical} is a symlink; canonical instructions must be a regular file")
    if not _contained(root, canonical):
        raise RefusedWrite(f"{canonical} resolves outside the repository root")
    if not canonical.is_file():
        raise RefusedWrite(f"no {CANONICAL_NAME} at scope {scope!r}")

    target = _adapter_path(root, scope)
    rel = target.relative_to(root).as_posix()

    if deny and rel in deny:
        raise RefusedWrite(
            f"{rel} is withheld by the instruction-migration denylist")
    if target.is_symlink():
        raise RefusedWrite(
            f"{rel} is a symlink; writing through it would land outside the file "
            "the caller named")
    if not _contained(root, target):
        raise RefusedWrite(f"{rel} resolves outside the repository root")
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RefusedWrite(f"{rel} exists and could not be read: {exc}") from exc
        if not is_adapter(existing):
            raise RefusedWrite(
                f"{rel} exists and is NOT a generated adapter — it carries no "
                "generated header, so it is a legacy instruction file whose "
                "content may never have been merged. Migrate it with "
                "`audit-docs --migrate` first; this writer never overwrites it")

    source_rel = canonical.relative_to(root).as_posix()
    body = render(canonical.read_text(encoding="utf-8"), source_rel)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def audit(root: Path, tracked: list[str], *, host_supported: bool) -> AdapterAudit:
    """The three reports clause 11 states. Reads; never writes and never deletes."""
    root = Path(root)
    result = AdapterAudit()
    scopes = managed_scopes(root, tracked)

    present: list[str] = []
    for scope in scopes:
        target = _adapter_path(root, scope)
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not is_adapter(text):
            # A Claude-named file that is NOT a generated adapter is a legacy
            # instruction file. That is the migration's concern, not this one.
            continue
        present.append(scope)
        rel = target.relative_to(root).as_posix()
        canonical = _canonical_path(root, scope)
        source_rel = canonical.relative_to(root).as_posix()

        if not canonical.is_file():
            result.adapters.append(AdapterReport(
                scope, rel, source_rel, "orphan",
                "its source no longer exists, so nothing can refresh it"))
            result.findings.append(f"{rel}: orphan adapter, source {source_rel} is gone")
            continue

        want = _sha(canonical.read_text(encoding="utf-8").encode("utf-8"))
        got = recorded_digest(text)
        if got != want:
            result.adapters.append(AdapterReport(
                scope, rel, source_rel, "stale",
                f"recorded sha256:{got} but {source_rel} is sha256:{want}"))
            result.findings.append(
                f"{rel}: STALE — the effective instructions are an old copy; "
                f"regenerate it or delete it")
        elif host_supported:
            result.adapters.append(AdapterReport(
                scope, rel, source_rel, "removable",
                "this host reads the canonical file, so the adapter only shadows it"))
            result.findings.append(
                f"{rel}: REMOVABLE — the host is supported, so this adapter "
                f"shadows {source_rel} for no benefit")
        else:
            result.adapters.append(AdapterReport(
                scope, rel, source_rel, "current", "digest matches its source"))

    if present:
        missing = [s for s in scopes if s not in present]
        if missing:
            result.incomplete = missing
            result.findings.append(
                "INCOMPLETE — an adapter exists at "
                f"{', '.join(present)} but not at {', '.join(missing)}. A parent "
                "adapter suppresses every AGENTS.md beneath it, so the scopes "
                "without one are silenced rather than merely unhelped")
    return result
