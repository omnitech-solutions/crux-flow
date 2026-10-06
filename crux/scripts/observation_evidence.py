"""observation_evidence.py — the one `path:line-range` grammar and the one
containment check for observation evidence (docs/AGENTS.md §17.1, §17.3).

Two things live here because they were previously three copies each:

  * the evidence GRAMMAR — `<repo-relative-path>:<start>-<end>`, decimal line
    numbers — which `check_observations.py`, `doctrine_projection.py` and
    `survey.py` each declared separately; and
  * the CONTAINMENT check, which two of those three got wrong in the same way.

The containment bug is the reason this module exists. Both call sites did the
TEXTUAL refusal — reject an absolute path, a `~` prefix, or a `..` component —
and then called `(root / path).is_file()`. `is_file()` follows symlinks, so a
committed `link -> /etc` inside the repository made `link/passwd:1-1` a
"resolving" evidence path: it is textually repo-relative, and the stat succeeds.
A ratified record citing it audited clean. The textual leg alone cannot decide
containment, because containment is a property of the RESOLVED path, not of its
spelling.

`resolve_contained` adds the leg that decides it, on the pattern
`summaries_projection.resolve_tree` already used for `docs_dir`: resolve both
sides and require the root to be an ancestor of the result. Textual refusal
stays first, so a hostile path is rejected without touching the filesystem at
all; the resolved leg then catches what spelling cannot express.
"""
from __future__ import annotations

import re
from pathlib import Path

# §17.1 `evidence` grammar: repo-relative path, ':', decimal start-end.
# THE declaration. Importers must not re-derive it.
EVIDENCE_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)-(?P<end>\d+)$")


def valid_evidence_path(path: str) -> bool:
    """The TEXTUAL leg: repo-relative only — no absolute path, no `~`, no `..`
    component, no Windows drive letter. Filesystem-free by design, so a hostile
    spelling is refused before any stat happens. Never sufficient on its own;
    `resolve_contained` decides containment."""
    if not path or path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", path):
        return False
    return ".." not in Path(path).parts


def parse_evidence(entry) -> tuple[str, int, int] | None:
    """`(path, start, end)` for a well-formed, textually repo-relative entry;
    None for anything else. Does not touch the filesystem."""
    m = EVIDENCE_RE.match(str(entry))
    if not m:
        return None
    path = m.group("path")
    if not valid_evidence_path(path):
        return None
    return path, int(m.group("start")), int(m.group("end"))


def resolve_contained(root: Path, path: str) -> Path | None:
    """The RESOLVED path of `path` under `root`, or None when it escapes.

    Containment is asserted after `resolve()`, which follows every symlink in
    the chain — that is the whole point. `root` itself is resolved too, so a
    symlinked repo root (a `/tmp` -> `/private/tmp` checkout on macOS, say)
    compares against its real location rather than failing spuriously.

    Returns None rather than raising: both call sites report a finding, and a
    traceback would exit non-zero with empty stdout, the lane their exit
    conventions reserve for a crash.
    """
    if not valid_evidence_path(path):
        return None
    try:
        root_r = Path(root).resolve()
        resolved = (root_r / path).resolve()
    except (OSError, RuntimeError):      # unreadable, or a symlink loop
        return None
    if resolved == root_r or root_r not in resolved.parents:
        return None
    return resolved


def evidence_path_resolves(root: Path, path: str) -> bool:
    """Whether `path` names a real file INSIDE `root`. A path that reaches out
    of the repository through a symlink is not contained, however ordinary its
    spelling, and is refused here."""
    resolved = resolve_contained(root, path)
    return resolved is not None and resolved.is_file()


def evidence_entry_resolves(root: Path | None, entry: str) -> bool:
    """Whole-entry convenience: grammar, then containment, then existence.
    No root, no resolution."""
    if root is None:
        return False
    parsed = parse_evidence(entry)
    if parsed is None:
        return False
    return evidence_path_resolves(root, parsed[0])
