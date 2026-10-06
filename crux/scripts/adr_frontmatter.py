"""adr_frontmatter.py — the one fence reader the three ADR generators share.

`generate-adr-index.py`, `generate-lineage.py` and `generate-index-rollup.py`
each build a derived output from ADR frontmatter, and each used to carry its
own copy of the fence regex plus a bare `continue` on no match. A file that
passed the `ADR-*.md` glob but failed that regex was dropped in silence: the
output was written without it, and the next drift check compared the
shortened output against what the generator would now write and found them
equal. The gate was correct about the only thing it measured.

ONE TRAILING SPACE IS ENOUGH. `--- ` does not match `^---\\n`, while
`yaml.safe_load` still parses the block underneath. The corruption is
invisible in a diff viewer and survives review.

THIS MODULE MAKES THE PARSER NO MORE PERMISSIVE. `FENCE_RE` is the regex the
three generators already used, unchanged. A file this module reports is a
file they already refused to read; the difference is that the refusal is now
said out loud, against the INPUT, rather than resolved by omission.

WHY THE INPUT AND NOT THE OUTPUT. The release suite already caught this, via
a lineage test that derives ADR ids from filenames. Its message names
`lineage.md`, so the remedy it implies — regenerate lineage — cannot work:
the generator is the thing that cannot read the file. Every message here
names the path that will not parse.

REFUSAL BEFORE WRITING, the discipline `generate-reviews-index.py` states in
its own docstring: a drift gate that passes on a corpus the writer would
refuse is a crash. Callers validate the WHOLE glob, both tiers, before any
output is written. One unparseable ADR therefore cannot leave a half-written
index, and cannot create an output that did not exist. `generate-index-rollup.py`
reads its output before validating, to locate the region it owns; that read has
no side effect, and its write still follows validation.

Stdlib only. Importers are the three generators named above and the tests
under `crux/scripts/tests/` that drive them.
"""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path

#: The ADR frontmatter fence. Byte-for-byte what the three generators carried
#: before this module existed — the opening `---` on a line of its own, the
#: block, then a closing `---` on a line of its own. Deliberately strict:
#: widening it here would change which documents the generators accept, which
#: is a different decision from reporting the ones they already reject.
FENCE_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

#: The input glob all three generators walk, in both tiers.
ADR_GLOB = "ADR-*.md"

#: The one explanation every refusal carries. It names the glob, so a reader
#: knows why the file was considered at all, and it names the fence, so they
#: know what to repair. It also names the failure mode that produced the
#: incident, because that one is invisible on screen.
FENCE_ERROR = (
    "matched the ADR input glob but its frontmatter fence could not be parsed — the file must "
    "open with a line containing exactly `---`, and that block must close with a line "
    "containing exactly `---`. A trailing space on a fence line is enough to fail this while "
    "the YAML underneath still parses, so check the fence bytes rather than the fields."
)


class FenceValidationError(Exception):
    """Raised by a loader when one or more inputs fail `FENCE_RE`.

    Carries `errors`, a list of `{"file", "error"}` mappings — one per
    offending file, never one per run, because each file is repaired
    separately and a reader needs every path named.
    """

    def __init__(self, errors: list[dict]) -> None:
        super().__init__(f"{len(errors)} ADR file(s) with an unparseable frontmatter fence")
        self.errors = errors


def adr_paths(adrs_dir: Path, *, include_archive: bool = True) -> list[Path]:
    """Every file matching the ADR glob under `adrs_dir`, both tiers, sorted.

    The archive tier is included by default because all three generators read
    it: an archived ADR keeps its row in the index, its node in the lineage
    graph and its line in the rollup.
    """
    found = _glob.glob(str(Path(adrs_dir) / ADR_GLOB))
    if include_archive:
        found += _glob.glob(str(Path(adrs_dir) / "archive" / ADR_GLOB))
    return [Path(p) for p in sorted(found)]


def frontmatter_block(text: str) -> str | None:
    """The YAML block between the fences, or None when the fence does not parse."""
    m = FENCE_RE.match(text)
    return m.group(1) if m else None


def fence_validation_errors(paths, rel) -> list[dict]:
    """`{"file", "error"}` for every path in `paths` whose fence does not parse.

    `rel` renders a path for reporting — callers pass `repo_relative(root)`, so
    a path inside the checkout is reported relative to it.

    A file that cannot be READ at all — a permission error, a non-UTF-8 byte
    sequence — is NOT reported here. It raises, and the caller maps it to the
    environment lane (exit 2). The distinction is the one the sibling
    `generate-reviews-index.py` already draws: a fence that does not parse is a
    document a human repairs, while a file the process cannot open is a fact
    about the checkout with nothing in the document to fix. `check-drift` reads
    an exit-1 `validation_errors` payload as "the named input must be repaired
    first", which is advice no one can follow for an EACCES.
    """
    errors: list[dict] = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        if frontmatter_block(text) is None:
            errors.append({"file": rel(Path(path)), "error": FENCE_ERROR})
    return errors


def repo_relative(root):
    """A path reporter: renders relative to `root`, never absolute.

    Owned here rather than pasted into each generator, because how a refusal
    names a file is part of the reporting contract this module defines.
    """
    root = Path(root)

    def _rel(p) -> str:
        p = Path(p)
        return str(p.relative_to(root)) if p.is_relative_to(root) else str(p)

    return _rel


def validated_adr_paths(adrs_dir: Path, rel, *, include_archive: bool = True) -> list[Path]:
    """`adr_paths`, after refusing the whole run if any input fails the fence.

    The single entry point a loader calls: it walks BOTH tiers, validates all
    of them, and raises before the caller has read an output to diff against
    or opened one to write.
    """
    paths = adr_paths(adrs_dir, include_archive=include_archive)
    errors = fence_validation_errors(paths, rel)
    if errors:
        raise FenceValidationError(errors)
    return paths


def print_validation_errors(errors: list[dict], script: str, out, err) -> int:
    """Emit the exit-1 payload and a stderr summary. Returns 1.

    JSON on stdout on every lane, because `check-drift` and `audit-docs` parse
    each gate's stdout as JSON regardless of exit code.
    """
    import json

    print(json.dumps({"validation_errors": errors}, sort_keys=True), file=out)
    err.write(f"{script}: " + "; ".join(f"{e['file']}: {e['error']}" for e in errors) + "\n")
    return 1
