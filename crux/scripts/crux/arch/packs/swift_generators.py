"""Swift stack pack (ADR-0129/ADR-0130 clause 13) — the Tuist and XcodeGen
generator checks: presence-only Tuist qualification, and a bounded,
non-evaluating XcodeGen manifest shape check. Neither runs a process,
evaluates a manifest, resolves a dependency, or expands an `include:`.

`yaml` is imported INSIDE functions only, never at module scope — the same
reason `swift.py` defers `tree_sitter`: the core imports every pack module to
read its registry, so a module-scope import here would make every other
pack's derive depend on a wheel only this check declares. ADR-0130 clause 13
is what admits PyYAML at all, the one third-party parser this decision names.

The public functions are `check_tuist` and `check_xcodegen`. Each takes the
checkout root and one candidate directory (repo-relative, `""` for the
checkout root) and returns a `GeneratorResult`. `swift.py` reads it in
`detect()` and in `_project_facts`; this module renders no text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core import _contained, _safe_read_bytes
from . import swift_prune

#: The untagged/standard YAML scalar, sequence and mapping tags the XcodeGen
#: shape check admits (ADR-0130 clause 13). Any other tag — explicit or,
#: for the `<<` merge key, implicit — refuses the file before composition.
_ALLOWED_TAGS = frozenset({
    "tag:yaml.org,2002:str", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:bool", "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:seq", "tag:yaml.org,2002:map",
})


@dataclass(frozen=True)
class Residual:
    """One classified condition from a Tuist or XcodeGen manifest check.

    `klass` is a residual class from the closed ADR-0129 clause 7 / ADR-0130
    clause 14 vocabulary. `path` is the manifest's repo-relative POSIX path.
    `lines` is `None` here (the closed vocabulary this module renders,
    `missing-input`, carries no line). `detail` names the tool, never a
    manifest value or a host path.
    """

    klass: str
    path: str
    lines: tuple[int, int] | None
    detail: str


@dataclass(frozen=True)
class GeneratorResult:
    """The outcome of checking one candidate directory for a Tuist or
    XcodeGen manifest.

    `tool` is `"tuist"` or `"xcodegen"`. `qualifies` is True only when the
    presence pair (Tuist) or the bounded, non-evaluating shape check
    (XcodeGen) both hold. `manifest` is the qualifying manifest's
    repo-relative path, or `None` when nothing qualifies. `residuals` holds
    zero or one `missing-input` record — present only when `qualifies` is
    True and no sibling `*.xcodeproj/project.pbxproj` sits in the checked
    directory (ADR-0130 clause 13).
    """

    tool: str
    qualifies: bool
    manifest: str | None
    residuals: tuple[Residual, ...]


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _dir_path(root: Path, directory_rel: str) -> Path:
    return root / directory_rel if directory_rel else root


def _clean_components(base: Path, parts) -> bool:
    """True when no directory component in `parts`, walked from `base`, is a
    pruned name (ADR-0129 clause 8) or a symlink (ADR-0130 clause 3: no
    directory symlink is descended). `is_symlink` answers with `lstat`, so
    the check follows nothing."""
    current = base
    for part in parts:
        current = current / part
        if swift_prune.pruned_name(part):
            return False
        try:
            if current.is_symlink():
                return False
        except OSError:
            return False
    return True


def _has_companion_project(root: Path, directory_rel: str) -> bool:
    """True iff `directory_rel` holds a `*.xcodeproj` directory whose
    `project.pbxproj` is a regular, non-symlinked file — the one condition
    that suppresses `missing-input` (ADR-0130 clause 13). Real-path
    containment (`core._contained`) is checked before every stat: once this
    check is reachable from project content (a workspace/XcodeGen/Tuist
    reference), `directory_rel` can name a symlinked path that escapes the
    checkout, and neither `d.iterdir()` nor the `.xcodeproj`/`project.pbxproj`
    stats below may ever run against it."""
    d = _dir_path(root, directory_rel)
    if not _contained(root, d):
        return False
    if not d.is_dir():
        return False
    for entry in d.iterdir():
        if entry.is_symlink():
            continue
        if entry.name.endswith(".xcodeproj") and entry.is_dir():
            pbx = entry / "project.pbxproj"
            if not _contained(root, pbx):
                continue
            if pbx.is_file() and not pbx.is_symlink():
                return True
    return False


def _missing_input(tool: str, root: Path, directory_rel: str, manifest_rel: str) -> tuple[Residual, ...]:
    if _has_companion_project(root, directory_rel):
        return ()
    return (Residual("missing-input", manifest_rel, None, tool),)


def check_tuist(root: Path, directory_rel: str) -> GeneratorResult:
    """The presence-only Tuist qualifier (ADR-0130 clause 13).

    `directory_rel` qualifies when it holds `Project.swift` or
    `Workspace.swift` alongside `Tuist.swift` or `Tuist/Config.swift`. No
    manifest content is read — presence only, checked through `Path.is_file`
    with the final component never followed as a symlink. Real-path
    containment (`core._contained`) is checked before every stat: once this
    check is reachable from project content, `directory_rel` (and each
    candidate file beneath it) can name a path that escapes the checkout
    through a symlinked component, and no stat may run against it first.
    """
    d = _dir_path(root, directory_rel)
    if not _contained(root, d):
        return GeneratorResult("tuist", False, None, ())
    if not d.is_dir():
        return GeneratorResult("tuist", False, None, ())

    def present(*parts: str) -> Path | None:
        # A directory between `d` and the file is never a symlink or a
        # pruned name, so `Tuist/Config.swift` is never found through a
        # symlinked `Tuist`.
        if not _clean_components(d, parts[:-1]):
            return None
        p = d.joinpath(*parts)
        if not _contained(root, p):
            return None
        return p if p.is_file() and not p.is_symlink() else None

    manifest = present("Project.swift") or present("Workspace.swift")
    companion = present("Tuist.swift") or present("Tuist", "Config.swift")
    if manifest is None or companion is None:
        return GeneratorResult("tuist", False, None, ())
    manifest_rel = _rel(root, manifest)
    return GeneratorResult("tuist", True, manifest_rel,
                            _missing_input("Tuist", root, directory_rel, manifest_rel))


#: The nesting bound: 64 levels are read and a 65th is refused, checked
#: before the descent that would open it. It is the 64-level bound ADR-0130
#: clause 2 sets for project files, applied to `project.yml` so that no
#: manifest content exits 2 (ADR-0130 clause 13). Sequence and mapping
#: depth is counted as the Parser yields events, and a document that passes
#: the bound is refused before `check_xcodegen` calls `yaml.safe_load`. The
#: Parser is an explicit state machine, so counting here cannot raise
#: `RecursionError`; the Composer and Constructor recurse, and this bound
#: keeps a 600-deep bracket nest from reaching them.
_MAX_NESTING = 64


def _scan_events_safe(raw: bytes) -> bool:
    """True iff every event in the YAML event stream for `raw` carries only
    a standard scalar/sequence/mapping tag, no anchor, and is not an alias,
    no scalar's literal value is the `<<` merge-key token, and the
    sequence/mapping nesting never exceeds `_MAX_NESTING`.

    `yaml.parse` drives the Parser alone — it yields events, never composing
    a node or constructing a Python value — so a `!!python/object/apply` tag
    is seen and rejected here before any constructor could ever run. An
    implicit tag (no `!` prefix in the source) always resolves, under
    `SafeLoader`, to one of the standard tags or to the `<<` merge tag; the
    literal-value check below catches the merge case at the same pre-
    composition point, since PyYAML resolves implicit tags only during
    composition, after this scan has already run.

    The `except Exception` below is deliberately broad, not
    `except yaml.YAMLError`: a pathological document can raise
    `RecursionError` or `MemoryError` out of the Parser itself, or
    `ValueError` (an out-of-range int/float/date literal token), none of
    which subclasses `YAMLError`. Any of those means the document is not a
    marker, exactly as a `YAMLError` does.
    """
    import yaml  # noqa: PLC0415

    depth = 0
    try:
        for event in yaml.parse(raw, Loader=yaml.SafeLoader):
            if isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
                depth += 1
                if depth > _MAX_NESTING:
                    return False
            elif isinstance(event, (yaml.SequenceEndEvent, yaml.MappingEndEvent)):
                depth -= 1
            if isinstance(event, yaml.AliasEvent):
                return False
            if getattr(event, "anchor", None):
                return False
            tag = getattr(event, "tag", None)
            if tag is not None and tag not in _ALLOWED_TAGS:
                return False
            if isinstance(event, yaml.ScalarEvent) and event.value == "<<":
                return False
        return True
    except Exception:
        return False


def _xcodegen_shape(value) -> bool:
    """The shape check: a mapping carrying a string `name` and a `targets`
    mapping with at least one entry, each entry itself a mapping carrying
    both `type` and `platform` (ADR-0130 clause 13)."""
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("name"), str):
        return False
    targets = value.get("targets")
    if not isinstance(targets, dict) or not targets:
        return False
    for entry in targets.values():
        if not isinstance(entry, dict):
            return False
        if "type" not in entry or "platform" not in entry:
            return False
    return True


def check_xcodegen(root: Path, directory_rel: str, raw: bytes | None = None) -> GeneratorResult:
    """The bounded, non-evaluating XcodeGen shape check (ADR-0130 clause 13).

    Reads `directory_rel/project.yml` through the core's safe-read contract
    (containment, a regular file, `O_NOFOLLOW` on the final component, the
    2 MB bound checked before the read). A file the contract refuses is not
    a marker and renders nothing. The event stream is then scanned
    (`_scan_events_safe`) for anything outside the closed tag set, an
    anchor, an alias or a `<<` merge key BEFORE any node is composed; a file
    that fails that scan is not a marker either. Only then is the manifest
    safely loaded and shape-checked. `include:` is never read at all — this
    check never looks at any key but `name` and `targets`.

    `raw`, when given, is the manifest's bytes a concern's bounded scan
    already read and hashed; the check then reads nothing itself, so every
    byte it interprets is counted against that concern's caps and hashed
    into its `sources` (ADR-0129 clause 2). `detect()` passes none and reads
    through the safe-read contract here.
    """
    manifest_rel = f"{directory_rel}/project.yml" if directory_rel else "project.yml"
    manifest_path = root / manifest_rel
    if raw is None:
        oversize: list = []
        raw = _safe_read_bytes(root, manifest_path, oversize)
    if raw is None:
        return GeneratorResult("xcodegen", False, None, ())
    if not _scan_events_safe(raw):
        return GeneratorResult("xcodegen", False, None, ())

    import yaml  # noqa: PLC0415

    try:
        value = yaml.safe_load(raw)
    except Exception:
        # Broad, not `yaml.YAMLError`: a deeply nested-but-shape-legal
        # document reaching this point (within `_MAX_NESTING`) can still
        # raise `RecursionError`/`MemoryError` from the Composer/Constructor,
        # or `ValueError` from an out-of-range timestamp or an int literal
        # past the interpreter's digit-string limit. Each makes the file not
        # a marker, and none exits 2 (ADR-0130 clause 13).
        return GeneratorResult("xcodegen", False, None, ())
    if not _xcodegen_shape(value):
        return GeneratorResult("xcodegen", False, None, ())
    return GeneratorResult("xcodegen", True, manifest_rel,
                            _missing_input("XcodeGen", root, directory_rel, manifest_rel))
